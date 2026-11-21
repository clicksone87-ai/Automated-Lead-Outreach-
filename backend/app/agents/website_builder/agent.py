"""Website builder agent: fills a template with verified facts, tests the build and publishes a
private preview.

Redesigns keep the original page structure and reuse only the client's own public text.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import select

from app.adapters.llm import LLMRequest
from app.agents.branding.agent import logo_text
from app.agents.discovery.base import industry_keywords
from app.agents.discovery.sources._html import main_text
from app.agents.website_builder.build import BuildError, build_site, run_preview_tests
from app.agents.website_builder.content import (
    COPY_SYSTEM,
    VARIANTS,
    SiteCopy,
    SiteFacts,
    build_content,
    copy_task,
    find_phone,
    nav_pages,
)
from app.db.models import (
    BrandProfile,
    Brief,
    Business,
    Contact,
    ContactType,
    Evidence,
    EvidenceKind,
    Lead,
    LeadStatus,
    LogoConcept,
    Preview,
    PreviewMode,
)
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.orchestrator.errors import TransientAgentError
from app.policy.engine import PolicyEngine
from app.services import runtime_settings
from app.services.not_useful import NotUsefulReason
from app.services.not_useful_leads import stop

logger = logging.getLogger(__name__)
TEMPLATE = "trades"


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


class WebsiteBuilderAgent:
    name: ClassVar[str] = "website_builder"
    expensive: ClassVar[bool] = True

    def __init__(self, builder: Any = build_site, tester: Any = run_preview_tests) -> None:
        self._build = builder
        self._test = tester

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        brief_row = await s.get(Brief, lead_id)
        if lead is None or brief_row is None or lead.business_id is None:
            return AgentOutcome(False, None, "missing lead/brief")
        brief = brief_row.data
        if "website_builder" not in brief.get("agents_to_run", []):
            return AgentOutcome(True, None, "website not needed for this lead")
        if not lead.pipeline_state.get("contact_id"):
            return stop(lead, NotUsefulReason.NO_CONTACT, "no contact route: build skipped")
        verdict = await PolicyEngine(s).evaluate_drafting(lead)
        if not verdict.allowed:
            return AgentOutcome(False, None, f"policy: {verdict.failures[0].reason}")
        business = await s.get(Business, lead.business_id)
        assert business is not None
        profile = (
            await s.execute(select(BrandProfile).where(BrandProfile.business_id == business.id))
        ).scalar_one_or_none()

        services = list(brief.get("services", []))
        mode = (
            PreviewMode.REDESIGN
            if "redesign" in services and (business.domain or brief.get("existing_website"))
            else PreviewMode.NEW
        )
        facts, site_ev = await self._facts(ctx, lead, business, brief, profile)
        copy = await ctx.adapters.llm.structured(
            s,
            LLMRequest(
                agent=self.name,
                task=copy_task(facts),
                system=COPY_SYSTEM,
                untrusted={"current_website_text": facts.site_text} if facts.site_text else {},
                lead_id=lead.id,
                max_output_tokens=3000,
                mock_context={"name": facts.name, "trade": facts.trade, "city": facts.city},
            ),
            SiteCopy,
        )
        logo = await self._best_logo(ctx, lead)
        logo_svg = self._read_svg(ctx, logo.svg_path if logo else None)
        assets = (logo.assets or {}) if logo else {}
        logo_animated = self._read_svg(ctx, assets.get("animated"))
        logo_reversed = self._read_svg(
            ctx, (assets.get("set") or {}).get("reversed", {}).get("svg")
        )
        colours = [
            c["hex"] for c in (profile.colors if profile else []) if c.get("role") != "neutral"
        ]
        if logo and logo.palette:
            colours = [*logo.palette[:2], *colours]
        fonts = [f["family"] for f in (profile.fonts if profile else [])]
        content = build_content(
            lead_id=str(lead.id),
            mode="redesign" if mode == PreviewMode.REDESIGN else "new",
            facts=facts,
            copy=copy,
            colours=colours,
            fonts=fonts,
            logo_svg=logo_svg,
            logo_svg_animated=logo_animated,
            logo_svg_reversed=logo_reversed,
            agency=ctx.settings.sender_business_name,
        )
        preview_id = lead.id.hex
        data_dir = Path(ctx.settings.data_dir).resolve()  # noqa: ASYNC240 - cheap, no I/O wait
        try:
            built = await self._build(
                template_dir=Path(ctx.settings.site_templates_dir) / TEMPLATE,
                data_dir=data_dir,
                preview_id=preview_id,
                content=content,
                cache_dir=Path(ctx.settings.site_builder_root),
            )
        except BuildError as exc:
            if exc.timed_out:  # a slow machine, not a broken site: retry later with backoff
                raise TransientAgentError(str(exc)[:300]) from exc
            await self._save(ctx, lead, values={"template_id": TEMPLATE, "mode": str(mode),
                             "language": facts.language, "content": content, "qa_status": "failed",
                             "test_results": {"build_error": str(exc)[:2000]}})  # fmt: skip
            return AgentOutcome(False, LeadStatus.NEEDS_REVIEW, f"build failed: {str(exc)[:300]}")
        rs = await runtime_settings.load(s)
        tests, files = await self._test(
            built.site_dir, expected_name=facts.name, thresholds=rs.lighthouse_min
        )
        stored: dict[str, str] = {}
        for name, data in files.items():
            stored[name] = ctx.adapters.storage.save_bytes(
                f"previews/{preview_id}/screens/{name}", data
            )
        reused = (
            [
                {
                    "type": "text",
                    "what": "business facts, services and areas (rewritten, not copied verbatim)",
                    "source_url": facts.site_url,
                    "evidence_id": site_ev,
                    "ownership": "the client's own public website",
                }
            ]
            if facts.site_text
            else []
        )
        await self._save(
            ctx,
            lead,
            values={
                "template_id": TEMPLATE,
                "source_path": _rel(built.work_dir, data_dir),
                "preview_url": self._preview_url(ctx, preview_id),
                "screenshots": {
                    "desktop": stored.get("desktop.png"),
                    "mobile": stored.get("mobile.png"),
                    # all three designs (V2), each at desktop size
                    "designs": {
                        d: stored[f"design-{d}.png"]
                        for d in VARIANTS
                        if f"design-{d}.png" in stored
                    },
                },
                "test_results": {
                    "passed": tests.passed,
                    "checks": tests.checks,
                    "lighthouse": tests.lighthouse,
                    "build_seconds": round(built.seconds, 1),
                },
                "qa_status": "pending",
                "mode": str(mode),
                "language": facts.language,
                "content": content,
                "reused_content": reused,
                "git_commit": built.git_commit,
                "build_log_path": _rel(built.log_path, data_dir),
                "pdf_path": stored.get("preview.pdf"),
                "built_at": datetime.now(UTC),
            },
        )
        failed = [c["name"] for c in tests.checks if not c["passed"]]
        return AgentOutcome(
            True,
            LeadStatus.BUILT,
            f"{mode} preview built ({content['variant']}) in {built.seconds:.0f}s; "
            + ("all preview tests passed" if not failed else f"tests failing: {', '.join(failed)}"),
            output={
                "preview_url": self._preview_url(ctx, preview_id),
                "tests_passed": tests.passed,
                "lighthouse": tests.lighthouse,
                "variant": content["variant"],
            },
            paid=True,
        )

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _preview_url(ctx: AgentContext, preview_id: str) -> str:
        base = ctx.settings.public_preview_base_url.rstrip("/")
        return f"{base}/{preview_id}/" if base else f"http://{preview_id}.preview.localhost:8080/"

    async def _facts(
        self,
        ctx: AgentContext,
        lead: Lead,
        business: Business,
        brief: dict[str, Any],
        profile: BrandProfile | None,
    ) -> tuple[SiteFacts, str | None]:
        s = ctx.session
        site_url = brief.get("existing_website") or (
            f"https://{business.domain}/" if business.domain else None
        )
        page = (
            await s.execute(
                select(Evidence)
                .where(Evidence.lead_id == lead.id, Evidence.kind == EvidenceKind.PAGE,
                       Evidence.file_path.is_not(None), Evidence.file_path.like("%.html"))
                .order_by(Evidence.captured_at)
                .limit(1)
            )
        ).scalar_one_or_none()  # fmt: skip
        html = ""
        if page and page.file_path:
            try:
                html = ctx.adapters.storage.read_bytes(page.file_path).decode(
                    "utf-8", errors="replace"
                )
            except (OSError, ValueError):
                html = ""
        text = main_text(html, site_url)[:6000] if html else ""
        email = (
            (
                await s.execute(
                    select(Contact.value).where(
                        Contact.business_id == business.id,
                        Contact.type == ContactType.EMAIL,
                        Contact.verification_status.in_(["valid", "catch_all"]),
                    )
                )
            )
            .scalars()
            .first()
        )
        trade = str(brief.get("business_type") or industry_keywords(business.industry or "")[0])
        city = str(brief.get("location") or business.address or "").split(",")[0].strip()
        return (
            SiteFacts(
                name=logo_text((profile.brand_name if profile else None) or business.name),
                trade=trade,
                city=city,
                country=lead.country or business.country or "",
                language=lead.language or "en",
                services_hint=list(brief.get("services", [])),
                phone=business.phone or find_phone(text, lead.country),
                email=email or "",
                address=business.address or "",
                site_text=text,
                site_url=site_url,
                nav=nav_pages(html, (page.url if page else None) or site_url or "") if html else [],
            ),
            str(page.id) if page else None,
        )

    @staticmethod
    def _read_svg(ctx: AgentContext, path: str | None) -> str:
        """A stored logo SVG (written by the Branding agent from sanitised output), or ""."""
        if not path:
            return ""
        try:
            return ctx.adapters.storage.read_bytes(path).decode("utf-8")
        except (OSError, ValueError):
            return ""

    @staticmethod
    async def _best_logo(ctx: AgentContext, lead: Lead) -> LogoConcept | None:
        rows = (
            (
                await ctx.session.execute(
                    select(LogoConcept)
                    .where(LogoConcept.lead_id == lead.id)
                    .order_by(LogoConcept.variant)
                )
            )
            .scalars()
            .all()
        )
        ok = [
            r
            for r in rows
            if r.qa_status != "failed" and not (r.similarity or {}).get("too_similar")
        ]
        exact = [r for r in ok if (r.similarity or {}).get("text_check") == "exact"]
        if exact:
            return exact[0]
        return ok[0] if ok else None

    @staticmethod
    async def _save(ctx: AgentContext, lead: Lead, *, values: dict[str, Any]) -> Preview:
        row = (
            await ctx.session.execute(select(Preview).where(Preview.lead_id == lead.id))
        ).scalar_one_or_none()
        if row is None:
            row = Preview(lead_id=lead.id, **values)
            ctx.session.add(row)
        else:
            row.attempts = (row.attempts or 1) + 1
            for k, v in values.items():
                setattr(row, k, v)
        await ctx.session.flush()
        return row


register(WebsiteBuilderAgent())
