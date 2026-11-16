"""Agent 5 — Branding.

Step 1 (this module, 3.02): brand extraction — business name, industry, existing colours,
fonts and logo from the website (safe fetcher only, robots.txt respected), links to the
business's own social profiles (recorded, not scraped: Meta/X/LinkedIn come only via
approved APIs), and competitor styles + logos for the trademark-similarity check.
Every extracted value records its source URL / evidence id in `brand_profiles.sources`.

Steps 2–3 (logo concepts, SVG/PNG) are added in 3.03–3.05.
"""

from __future__ import annotations

import io
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.adapters.fetcher import FetchError, RobotsDisallowedError
from app.agents.branding.extract import BrandExtraction, extract
from app.db.models import Audit, BrandProfile, Brief, Business, EvidenceKind, Lead, LeadStatus
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.policy.engine import PolicyEngine
from app.services import evidence
from app.services.competitors import competitor_domains

logger = logging.getLogger(__name__)

COMPETITOR_LIMIT = 3
MAX_LOGO_BYTES = 2 * 1024 * 1024
MIN_LOGO_SIDE = 16
_PIL_EXT = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp", "ICO": "ico", "BMP": "bmp"}
ROLES = ("primary", "accent", "secondary", "extra", "extra")


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
    expensive: ClassVar[bool] = True  # logo generation (3.03) spends money

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
        return AgentOutcome(
            True,
            None,
            f"brand extracted: {found}; {len(competitors)} competitor styles",
            output={
                "brand_profile_id": str(profile.id),
                "colors": [c["hex"] for c in profile.colors],
                "fonts": [f["family"] for f in profile.fonts],
                "logo_path": profile.logo_path,
                "competitors": [c["domain"] for c in competitors],
            },
        )

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
            # in `sources` for the logo text decision (3.03).
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
