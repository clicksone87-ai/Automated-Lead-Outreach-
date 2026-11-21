"""Light audit for the fast lane: static checks and cached PageSpeed, no browser.

One fetch of the homepage through the safe fetcher, then robots/sitemap, link checks and
PageSpeed run side by side. Technical SEO, security headers, content and outdated-site
signals come from the static HTML. Pages that render everything with JavaScript are marked
inconclusive; the heavy lane's full audit (browser, vision review, competitors, PDF) covers
them. Every finding points at stored evidence, as in the full audit.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import select

from app.adapters.base import AdapterUnavailableError
from app.adapters.fetcher import FetchError, RobotsDisallowedError
from app.adapters.google import PageSpeedResult
from app.agents.audit import checks
from app.agents.audit.agent import AuditAgent
from app.agents.audit.report import (
    before_after,
    bind_evidence,
    overall_scores,
    summarize_for_storage,
)
from app.agents.discovery.proactive_triggers.signals import outdated_signals
from app.db.models import Audit, Brief, Business, EvidenceKind, Lead, LeadStatus
from app.db.session import session_scope
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.orchestrator.budget import BudgetExceededError
from app.policy.engine import PolicyEngine
from app.services import evidence
from app.services.not_useful import NotUsefulReason
from app.services.not_useful_leads import stop

logger = logging.getLogger(__name__)
LINK_LIMIT = 12  # same-host requests are spaced 2 s apart by the safe fetcher

# Outdated-site signals that the SEO/security checks do not already report.
SIGNAL_FINDINGS: dict[str, tuple[checks.Severity, str, str]] = {
    "table_layout": ("medium", "Layout built with tables",
                     "The page layout uses nested tables, a technique from the early 2000s "
                     "that makes sites hard to use on phones."),
    "obsolete_tags": ("high", "Obsolete page elements",
                      "The page uses elements browsers no longer support well (Flash, frames, "
                      "marquee or blink)."),
    "old_jquery": ("low", "Old JavaScript library",
                   "The page loads jQuery 1.x, which is years out of date."),
    "old_generator": ("medium", "Built with an outdated site builder",
                      "The page says it was made with an old editor or CMS version."),
}  # fmt: skip


def signal_findings(signals: list[str]) -> list[checks.FindingDraft]:
    out = []
    for sig in signals:
        if sig.startswith("copyright_"):
            year = sig.removeprefix("copyright_")
            out.append(checks.FindingDraft(
                "content", "low", "Copyright notice is out of date",
                f"The footer still says © {year}, so visitors may think the site is not "
                "looked after.", "html", {"year": year}, "copyright_year",
            ))  # fmt: skip
        elif sig in SIGNAL_FINDINGS:
            sev, title, detail = SIGNAL_FINDINGS[sig]
            out.append(checks.FindingDraft("design", sev, title, detail, "html", {}, sig))
    return out


def looks_script_rendered(facts: checks.PageFacts, html: str) -> bool:
    """Almost no text in the HTML but plenty of script: the content is built in the browser."""
    return facts.word_count < 60 and html.lower().count("<script") >= 3


class LightAuditAgent(AuditAgent):
    name: ClassVar[str] = "audit_light"
    expensive: ClassVar[bool] = False

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        brief_row = await s.get(Brief, lead_id)
        if lead is None or brief_row is None or lead.business_id is None:
            return AgentOutcome(False, None, "missing lead/brief")
        brief = brief_row.data
        if "audit" not in brief.get("agents_to_run", []):
            return AgentOutcome(True, None, "audit not needed for this lead")
        if not lead.pipeline_state.get("contact_id"):
            return stop(lead, NotUsefulReason.NO_CONTACT, "no contact route: audit skipped")
        verdict = await PolicyEngine(s).evaluate_drafting(lead)
        if not verdict.allowed:
            return AgentOutcome(False, None, f"policy: {verdict.failures[0].reason}")
        business = await s.get(Business, lead.business_id)
        assert business is not None
        url = brief.get("existing_website") or (
            f"https://{business.domain}" if business.domain else None
        )
        if not url:
            return AgentOutcome(True, None, "no website to audit")

        ev: dict[str, str] = {}

        async def keep(key: str, kind: EvidenceKind, **kwargs: Any) -> str:
            row = await evidence.capture(s, ctx.adapters.storage, kind=kind, lead_id=lead.id,
                                         business_id=business.id, **kwargs)  # fmt: skip
            ev[key] = str(row.id)
            return str(row.id)

        try:
            page = await ctx.adapters.fetcher.get(url)
        except RobotsDisallowedError:
            return AgentOutcome(True, None, "audit skipped: robots.txt disallows crawling")
        except FetchError as exc:
            outcome = await self._unreachable(ctx, lead, business, url=url, exc=exc, keep=keep)
            await self._set_depth(ctx, lead.id)
            return outcome
        await keep("html", EvidenceKind.PAGE, url=page.final_url, content=page.content,
                   file_ext="html", data={"status": page.status_code})  # fmt: skip
        await keep("headers", EvidenceKind.METRIC, url=page.final_url,
                   data={"headers": page.headers, "status": page.status_code,
                         "redirects": page.redirects, "elapsed_ms": page.elapsed_ms})  # fmt: skip
        facts = checks.page_facts(page.text, page.final_url)
        targets = checks.link_targets(facts, page.final_url, limit=LINK_LIMIT)

        # Network work side by side; database writes stay sequential on this session.
        (robots_ok, sitemap_ok, https_redirect), statuses, speed = await asyncio.gather(
            self._site_files(ctx, page.final_url),
            asyncio.gather(*(ctx.adapters.fetcher.head_status(t) for t in targets)),
            self._pagespeed(ctx, page.final_url),
        )
        drafts = checks.seo_findings(facts, x_robots=page.headers.get("x-robots-tag"),
                                     robots_ok=robots_ok, sitemap_ok=sitemap_ok)  # fmt: skip
        drafts += checks.security_findings(
            requested_url=url, final_url=page.final_url, headers=page.headers,
            http_resources=facts.http_resources, http_redirects_to_https=https_redirect,
        )  # fmt: skip
        drafts += checks.content_findings(facts)
        signals = outdated_signals(page.text, final_url=page.final_url)
        drafts += signal_findings(signals)
        if speed is not None:
            await keep("metric_pagespeed", EvidenceKind.METRIC, url=page.final_url,
                       data={"source": "pagespeed", "scores": speed.scores, "metrics": speed.metrics})  # fmt: skip
            drafts += checks.performance_findings(speed.scores, speed.metrics)
        if targets:
            broken = [{"url": t, "status": st} for t, st in zip(targets, statuses, strict=True)
                      if st is None or st >= 400]  # fmt: skip
            await keep("links", EvidenceKind.METRIC, url=page.final_url,
                       data={"checked": len(targets), "broken": broken})  # fmt: skip
            if broken:
                drafts.append(checks.FindingDraft(
                    "links", "high" if len(broken) >= 5 else "medium", "Broken links",
                    f"{len(broken)} of {len(targets)} links checked are broken.", "links",
                    {"broken": broken[:10]}, "broken_links",
                ))  # fmt: skip

        findings = bind_evidence(drafts, ev)
        scores = overall_scores(speed.scores if speed else {}, None, findings)
        inconclusive = looks_script_rendered(facts, page.text)
        site_status = "broken" if page.status_code >= 400 else (
            "outdated" if len(signals) >= 2 or any(f.severity == "critical" for f in findings) else "ok"
        )  # fmt: skip
        business.site_status, business.site_checked_at = site_status, datetime.now(UTC)
        await self._save(ctx, lead.id, {
            "url": page.final_url, "scores": scores, "findings": summarize_for_storage(findings),
            "screenshots": {}, "competitor_comparison": [],
            "before_after_summary": before_after(findings, None, brief.get("services", [])),
            "report_pdf_path": None, "depth": "light",
        })  # fmt: skip
        return AgentOutcome(
            True,
            LeadStatus.AUDITED,
            f"light audit of {page.final_url}: {len(findings)} findings"
            + ("; page is built by scripts, full audit needed" if inconclusive else ""),
            {
                "findings": len(findings),
                "scores": scores,
                "signals": signals,
                "inconclusive": inconclusive,
                "depth": "light",
            },
        )

    async def _pagespeed(self, ctx: AgentContext, url: str) -> PageSpeedResult | None:
        """Cached PageSpeed lab data in its own session (it runs alongside other work)."""
        try:
            async with session_scope() as own:
                return await ctx.adapters.pagespeed.run(url, "mobile", session=own)
        except (AdapterUnavailableError, BudgetExceededError) as exc:
            logger.info("light audit without PageSpeed for %s: %s", url, exc)
            return None
        except Exception as exc:  # a slow or failing PageSpeed call must not sink the audit
            logger.info("PageSpeed failed for %s: %s", url, exc)
            return None

    @staticmethod
    async def _save(ctx: AgentContext, lead_id: uuid.UUID, values: dict[str, Any]) -> None:
        audit = (
            await ctx.session.execute(select(Audit).where(Audit.lead_id == lead_id))
        ).scalar_one_or_none()
        if audit is None:
            ctx.session.add(Audit(lead_id=lead_id, **values))
        elif audit.depth != "full":
            # A full audit (screenshots, report PDF, deeper findings) is never replaced by a
            # light one: overwriting it used to drop the report PDF from proposals.
            for k, v in values.items():
                setattr(audit, k, v)
        await ctx.session.flush()

    @staticmethod
    async def _set_depth(ctx: AgentContext, lead_id: uuid.UUID) -> None:
        audit = (
            await ctx.session.execute(select(Audit).where(Audit.lead_id == lead_id))
        ).scalar_one_or_none()
        if audit is not None and audit.depth != "full":
            audit.depth = "light"


register(LightAuditAgent())
