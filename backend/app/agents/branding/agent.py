"""Branding agent: brand extraction and logo concepts.

Extracts colours, fonts, logo and social links from the business's site (each value records its
source) and collects competitor logos. The LLM then designs three SVG logo concepts, which are
sanitised, rendered and checked for wording and similarity to the competitor logos.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.adapters.fetcher import FetchError, RobotsDisallowedError
from app.adapters.llm import LLMOutputError, LLMRequest
from app.agents.audit.browser import GuardedBrowser
from app.agents.branding.extract import BrandExtraction, extract
from app.agents.branding.logos import (
    STYLES,
    LogoPrompt,
    LogoSpec,
    SvgRejectedError,
    render_page,
    sanitize_svg,
    text_matches,
)
from app.db.models import (
    Audit,
    BrandProfile,
    Brief,
    Business,
    EvidenceKind,
    Lead,
    LeadStatus,
    LogoConcept,
)
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.policy.engine import PolicyEngine
from app.quality.images import compare
from app.services import evidence
from app.services.competitors import competitor_domains

logger = logging.getLogger(__name__)

COMPETITOR_LIMIT = 3
MAX_LOGO_BYTES = 2 * 1024 * 1024
MIN_LOGO_SIDE = 16
_PIL_EXT = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp", "ICO": "ico", "BMP": "bmp"}
ROLES = ("primary", "accent", "secondary", "extra", "extra")
LOGO_SIZES = (1024, 512)
_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(ltd\.?|limited|llc|l\.l\.c\.|inc\.?|incorporated|co\.?|corp\.?|plc|gmbh|"
    r"s\.?l\.?|s\.?a\.?r\.?l\.?)$",
    re.I,
)


def logo_text(name: str) -> str:
    """The wording for the logo: the business name without legal suffixes."""
    text = name.strip()
    while True:
        stripped = _LEGAL_SUFFIX.sub("", text).strip()
        if stripped == text or not stripped:
            return text
        text = stripped


@dataclass(slots=True)
class LogoAsset:
    path: str
    evidence_id: str
    url: str | None
    how: str
    format: str


@dataclass(slots=True)
class SiteBrand:
    url: str
    extraction: BrandExtraction
    page_evidence_id: str
    logo: LogoAsset | None = None
    stylesheet_evidence: dict[str, str] = field(default_factory=dict)


def logo_format(content: bytes) -> str | None:
    """'svg' or a raster extension if the bytes really are a usable logo image, else None."""
    if len(content) > MAX_LOGO_BYTES or not content:
        return None
    head = content[:2048].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in content[:4096].lower():
        return "svg"
    if head.startswith((b"<!doctype", b"<html")):
        return None  # soft-404 page served with status 200
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.verify()
        with Image.open(io.BytesIO(content)) as img:
            if min(img.size) < MIN_LOGO_SIDE:
                return None
            return _PIL_EXT.get(img.format or "")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        return None


class BrandingAgent:
    name: ClassVar[str] = "branding"
    expensive: ClassVar[bool] = True  # logo generation spends money

    def __init__(self, browser_factory: Any = None) -> None:
        self._browser_factory = browser_factory or GuardedBrowser

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        brief_row = await s.get(Brief, lead_id)
        if lead is None or brief_row is None or lead.business_id is None:
            return AgentOutcome(False, None, "missing lead/brief")
        brief = brief_row.data
        if "branding" not in brief.get("agents_to_run", []):
            return AgentOutcome(True, None, "branding not needed for this lead")
        if not lead.pipeline_state.get("contact_id"):
            return AgentOutcome(
                False, LeadStatus.NO_CONTACT_ROUTE, "no contact route: branding skipped"
            )
        verdict = await PolicyEngine(s).evaluate_drafting(lead)
        if not verdict.allowed:
            return AgentOutcome(False, None, f"policy: {verdict.failures[0].reason}")
        business = await s.get(Business, lead.business_id)
        assert business is not None

        url = brief.get("existing_website") or (
            f"https://{business.domain}" if business.domain else None
        )
        own = await self._site_brand(ctx, lead, business, url) if url else None
        competitors = await self._competitors(ctx, lead, business, brief)
        profile = await self._save_profile(ctx, business, brief, own, competitors)

        found = (
            f"{len(profile.colors)} colours, {len(profile.fonts)} fonts, "
            f"logo {'found' if profile.logo_path else 'not found'}"
            if own
            else "no website — brand built from brief"
        )
        concepts = await self._logo_concepts(ctx, lead, profile)
        flagged = sum(1 for c in concepts if (c.similarity or {}).get("too_similar"))
        output = {
            "brand_profile_id": str(profile.id),
            "colors": [c["hex"] for c in profile.colors],
            "fonts": [f["family"] for f in profile.fonts],
            "logo_path": profile.logo_path,
            "competitors": [c["domain"] for c in competitors],
            "logo_concepts": [str(c.id) for c in concepts],
        }
        reason = (
            f"brand extracted: {found}; {len(competitors)} competitor styles; "
            f"{len(concepts)} logo concepts"
            + (f" ({flagged} too similar to a competitor)" if flagged else "")
        )
        if not concepts:
            return AgentOutcome(True, None, reason + " — logo generation failed", output=output)
        return AgentOutcome(True, LeadStatus.BRANDED, reason, output=output, paid=True)

    # ── logo concepts ────────────────────────────────────────────────────

    async def _logo_concepts(
        self, ctx: AgentContext, lead: Lead, profile: BrandProfile
    ) -> list[LogoConcept]:
        text = logo_text(profile.brand_name or "")
        if not text:
            return []
        language = lead.language or "en"
        colours = [c["hex"] for c in profile.colors if c.get("role") != "neutral"][:3]
        fonts = [f["family"] for f in profile.fonts][:2]
        avoid = sorted({c for comp in profile.competitor_styles for c in comp.get("colors", [])})[
            :6
        ]
        industry = profile.industry or "local business"
        references = await self._reference_logos(ctx, profile)
        made: list[LogoConcept] = []
        async with self._browser_factory() as browser:
            for variant, (style, style_desc) in enumerate(STYLES, start=1):
                concept = await self._one_concept(
                    ctx,
                    lead,
                    browser,
                    variant=variant,
                    style=style,
                    task=LogoPrompt.task(
                        text=text,
                        language=language,
                        industry=industry,
                        style=style,
                        style_desc=style_desc,
                        colours=colours,
                        fonts=fonts,
                        avoid_colours=avoid,
                    ),
                    text=text,
                    language=language,
                    references=references,
                    mock={
                        "text": text,
                        "style": style,
                        "colour": colours[0] if colours else "#1d4ed8",
                    },
                )
                if concept is not None:
                    made.append(concept)
        return made

    async def _one_concept(
        self,
        ctx: AgentContext,
        lead: Lead,
        browser: GuardedBrowser,
        *,
        variant: int,
        style: str,
        task: str,
        text: str,
        language: str,
        references: dict[str, bytes],
        mock: dict[str, Any],
    ) -> LogoConcept | None:
        svg: str | None = None
        spec: LogoSpec | None = None
        attempts = 0
        for attempts in (1, 2):
            try:
                spec = await ctx.adapters.llm.structured(
                    ctx.session,
                    LLMRequest(
                        agent=self.name,
                        task=task,
                        system=LogoPrompt.SYSTEM,
                        kind="logo",
                        lead_id=lead.id,
                        max_output_tokens=6000,
                        mock_context=mock,
                    ),
                    LogoSpec,
                )
                svg = sanitize_svg(spec.svg)
                break
            except (LLMOutputError, SvgRejectedError) as exc:
                logger.info("logo %s attempt %d rejected: %s", style, attempts, exc)
        if spec is None or svg is None:
            return None
        owner = lead.id.hex
        svg_path = ctx.adapters.storage.save_bytes(
            f"logos/{owner}/concept-{variant}.svg", svg.encode("utf-8")
        )
        png_paths: dict[int, str] = {}
        for size in LOGO_SIZES:
            png = await browser.render_png(render_page(svg, size), width=size, height=size // 2)
            png_paths[size] = ctx.adapters.storage.save_bytes(
                f"logos/{owner}/concept-{variant}-{size}.png", png
            )
        main_png = ctx.adapters.storage.read_bytes(png_paths[LOGO_SIZES[0]])
        sims = compare(main_png, references)
        nearest = sims[0] if sims else None
        values: dict[str, Any] = {
            "svg_path": svg_path,
            "png_path": png_paths[LOGO_SIZES[0]],
            "palette": spec.palette,
            "typography": {
                "family": spec.font_family,
                "weight": spec.font_weight,
                "concept_name": spec.concept_name,
                "style": style,
                "rationale": spec.rationale,
                "png_512": png_paths[512],
            },
            "language": language,
            "text": text,
            "prompt": task,
            "similarity": {
                "checked_against": len(references),
                "nearest": nearest.against if nearest else None,
                "distance": nearest.distance if nearest else None,
                "colour_overlap": nearest.colour_overlap if nearest else None,
                "too_similar": bool(nearest and nearest.too_similar),
                "text_check": text_matches(svg, text),
            },
            "attempts": attempts,
            "qa_status": "pending",
            "qa_report": {},
        }
        row = (
            await ctx.session.execute(
                select(LogoConcept).where(
                    LogoConcept.lead_id == lead.id, LogoConcept.variant == variant
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = LogoConcept(lead_id=lead.id, variant=variant, **values)
            ctx.session.add(row)
        else:
            for k, v in values.items():
                setattr(row, k, v)
        await ctx.session.flush()
        return row

    async def _reference_logos(self, ctx: AgentContext, profile: BrandProfile) -> dict[str, bytes]:
        """Competitor logos as PNG bytes (SVG ones are rendered first) for the similarity check."""
        refs: dict[str, bytes] = {}
        svgs: dict[str, str] = {}
        for path in profile.competitor_logos:
            try:
                data = ctx.adapters.storage.read_bytes(path)
            except (OSError, ValueError):
                continue
            if path.endswith(".svg"):
                try:
                    svgs[path] = sanitize_svg(data.decode("utf-8", errors="replace"))
                except SvgRejectedError:
                    continue
            else:
                refs[path] = data
        if svgs:
            async with self._browser_factory() as browser:
                for path, svg in svgs.items():
                    refs[path] = await browser.render_png(
                        render_page(svg, 512), width=512, height=256
                    )
        return refs

    # ── own site ─────────────────────────────────────────────────────────

    async def _site_brand(
        self, ctx: AgentContext, lead: Lead, business: Business, url: str
    ) -> SiteBrand | None:
        try:
            page = await ctx.adapters.fetcher.get(url)
        except RobotsDisallowedError:
            logger.info("branding: robots.txt disallows %s", url)
            return None
        except FetchError as exc:
            logger.info("branding: %s unreachable: %s", url, exc)
            return None
        if not page.ok:
            return None
        page_ev = await evidence.capture(
            ctx.session,
            ctx.adapters.storage,
            kind=EvidenceKind.PAGE,
            lead_id=lead.id,
            business_id=business.id,
            url=page.final_url,
            content=page.content,
            file_ext="html",
            data={"status": page.status_code, "purpose": "brand_extraction"},
        )
        first = extract(page.text, page.final_url, [])
        css, css_ev = await self._stylesheets(ctx, lead, business, first.stylesheets)
        extraction = extract(page.text, page.final_url, css) if css else first
        site = SiteBrand(page.final_url, extraction, str(page_ev.id), stylesheet_evidence=css_ev)
        site.logo = await self._download_logo(
            ctx, lead, business, extraction, page.final_url, purpose="own_logo"
        )
        return site

    async def _stylesheets(
        self, ctx: AgentContext, lead: Lead, business: Business, urls: list[str]
    ) -> tuple[list[tuple[str, str]], dict[str, str]]:
        css: list[tuple[str, str]] = []
        ev: dict[str, str] = {}
        for css_url in urls:
            try:
                res = await ctx.adapters.fetcher.get(css_url)
            except FetchError:
                continue
            if not res.ok or "html" in res.headers.get("content-type", ""):
                continue
            row = await evidence.capture(
                ctx.session,
                ctx.adapters.storage,
                kind=EvidenceKind.PAGE,
                lead_id=lead.id,
                business_id=business.id,
                url=res.final_url,
                content=res.content,
                file_ext="css",
                data={"purpose": "brand_stylesheet"},
            )
            css.append((res.final_url, res.text))
            ev[res.final_url] = str(row.id)
        return css, ev

    async def _download_logo(
        self,
        ctx: AgentContext,
        lead: Lead,
        business: Business,
        extraction: BrandExtraction,
        page_url: str,
        *,
        purpose: str,
    ) -> LogoAsset | None:
        for cand in extraction.logo_candidates:
            if cand.get("svg"):  # inline SVG in the page markup
                content, fmt, src = cand["svg"].encode("utf-8"), "svg", page_url
            else:
                try:
                    res = await ctx.adapters.fetcher.get(cand["url"])
                except FetchError:
                    continue
                if not res.ok:
                    continue
                content, src = res.content, res.final_url
                fmt = logo_format(content) or ""
                if not fmt:
                    continue
            if fmt == "svg" and logo_format(content) != "svg":
                continue
            row = await evidence.capture(
                ctx.session,
                ctx.adapters.storage,
                kind=EvidenceKind.PAGE,
                lead_id=lead.id,
                business_id=business.id,
                url=src,
                content=content,
                file_ext=fmt,
                data={"purpose": purpose, "asset": "logo", "how": cand["how"]},
            )
            assert row.file_path is not None
            return LogoAsset(row.file_path, str(row.id), cand.get("url"), cand["how"], fmt)
        return None

    # ── competitors ──────────────────────────────────────────────────────

    async def _competitors(
        self, ctx: AgentContext, lead: Lead, business: Business, brief: dict[str, Any]
    ) -> list[dict[str, Any]]:
        audit = (
            await ctx.session.execute(select(Audit).where(Audit.lead_id == lead.id))
        ).scalar_one_or_none()
        domains = [c["domain"] for c in (audit.competitor_comparison if audit else [])]
        if not domains:
            domains = await competitor_domains(
                ctx.session, ctx.adapters.search, business, brief, agent=self.name
            )
        out: list[dict[str, Any]] = []
        for domain in domains:
            try:
                page = await ctx.adapters.fetcher.get(f"https://{domain}/")
            except FetchError:
                continue
            if not page.ok:
                continue
            ext = extract(page.text, page.final_url, [])
            logo = await self._download_logo(
                ctx, lead, business, ext, page.final_url, purpose="competitor_logo"
            )
            out.append(
                {
                    "domain": domain,
                    "url": page.final_url,
                    "colors": [c["hex"] for c in ext.colors[:3]],
                    "fonts": [f["family"] for f in ext.fonts[:2]],
                    "logo_path": logo.path if logo else None,
                    "logo_evidence_id": logo.evidence_id if logo else None,
                }
            )
            if len(out) >= COMPETITOR_LIMIT:
                break
        return out

    # ── persistence ──────────────────────────────────────────────────────

    async def _save_profile(
        self,
        ctx: AgentContext,
        business: Business,
        brief: dict[str, Any],
        own: SiteBrand | None,
        competitors: list[dict[str, Any]],
    ) -> BrandProfile:
        sources: list[dict[str, Any]] = []
        colors: list[dict[str, Any]] = []
        fonts: list[dict[str, Any]] = []
        social: list[dict[str, str]] = []
        brand_name = business.name
        if own:
            ex = own.extraction
            ev_for = {**own.stylesheet_evidence}

            def evidence_of(source: str) -> str:
                return ev_for.get(source, own.page_evidence_id)

            for i, c in enumerate(ex.colors):
                colors.append({"hex": c["hex"], "role": ROLES[i], "weight": c["weight"]})
                sources.append(
                    {
                        "field": "color",
                        "value": c["hex"],
                        "source": c["sources"][0] if c["sources"] else own.url,
                        "evidence_id": evidence_of(c["sources"][0] if c["sources"] else ""),
                    }
                )
            colors += [{"hex": n, "role": "neutral", "weight": 0} for n in ex.neutrals]
            for f in ex.fonts:
                fonts.append({"family": f["family"], "role": f["role"]})
                sources.append(
                    {
                        "field": "font",
                        "value": f["family"],
                        "source": f["source"],
                        "evidence_id": evidence_of(f["source"]),
                    }
                )
            if own.logo:
                sources.append(
                    {
                        "field": "logo",
                        "value": own.logo.path,
                        "source": own.logo.url or own.url,
                        "how": own.logo.how,
                        "evidence_id": own.logo.evidence_id,
                    }
                )
            social = ex.social_profiles
            if ex.brand_name:
                sources.append(
                    {
                        "field": "brand_name_on_site",
                        "value": ex.brand_name,
                        "source": own.url,
                        "evidence_id": own.page_evidence_id,
                    }
                )

        values: dict[str, Any] = {
            # The registered/known business name is authoritative; the site's wording is kept
            # in `sources` for the logo text decision.
            "brand_name": brand_name,
            "colors": colors,
            "fonts": fonts,
            "logo_path": own.logo.path if own and own.logo else None,
            "logo_evidence_id": uuid.UUID(own.logo.evidence_id) if own and own.logo else None,
            "industry": business.industry or brief.get("business_type"),
            "competitor_styles": competitors,
            "competitor_logos": [c["logo_path"] for c in competitors if c["logo_path"]],
            "social_profiles": social,
            "sources": sources,
        }
        profile = (
            await ctx.session.execute(
                select(BrandProfile).where(BrandProfile.business_id == business.id)
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = BrandProfile(business_id=business.id, **values)
            ctx.session.add(profile)
        else:
            for k, v in values.items():
                setattr(profile, k, v)
        await ctx.session.flush()
        return profile


register(BrandingAgent())
