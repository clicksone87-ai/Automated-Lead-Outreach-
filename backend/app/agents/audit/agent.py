"""Agent 4 — Website Audit and SEO.

Runs only when the brief says so (lead has a site and needs redesign/SEO), after a contact
route exists. Captures evidence first, derives findings from it, and refuses to store any
finding that does not point at an evidence record.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from sqlalchemy import select

from app.adapters.base import AdapterUnavailableError
from app.adapters.fetcher import FetchError, FetchResult, RobotsDisallowedError
from app.adapters.google import PageSpeedResult, parse_pagespeed
from app.adapters.llm import LLMRequest
from app.agents.audit import checks
from app.agents.audit.browser import Capture, Fixtures, GuardedBrowser
from app.agents.audit.report import (
    before_after,
    bind_evidence,
    overall_scores,
    render_html,
    summarize_for_storage,
)
from app.agents.audit.schemas import Competitor, DesignCritique
from app.db.models import Audit, Brief, Business, EvidenceKind, Lead, LeadStatus
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.orchestrator.runs import TransientAgentError
from app.policy.engine import PolicyEngine
from app.services import evidence
from app.services.dedup import PLATFORM_DOMAINS, normalize_domain

logger = logging.getLogger(__name__)

VISION_SYSTEM = (
    "You are a senior web designer reviewing a small business website for a redesign proposal. "
    "Judge only what is visible in the two screenshots (desktop and mobile). Be specific and "
    "factual; do not guess about anything you cannot see."
)
VISION_TASK = (
    "Image 1 is the desktop view, image 2 the mobile view of the homepage. Score the design, say "
    "whether it looks outdated, whether it works on mobile, whether a call-to-action is visible "
    "above the fold, and list strengths and concrete issues."
)
COMPETITOR_LIMIT = 3
DESIGN_TITLES = {
    "mobile": "Hard to use on phones",
    "cta": "Call to action is not visible",
    "layout": "Layout feels dated or cluttered",
    "typography": "Text is hard to read",
    "colour": "Colours lack contrast or consistency",
    "imagery": "Images look low quality",
    "navigation": "Navigation is unclear",
    "copy": "Wording is unclear",
    "trust": "Few trust signals (reviews, guarantees, credentials)",
}


def _mark_mock(lead: Lead, key: str) -> None:
    used = set(lead.pipeline_state.get("mock_adapters_used", []))
    lead.pipeline_state = {**lead.pipeline_state, "mock_adapters_used": sorted(used | {key})}


async def run_local_lighthouse(url: str) -> PageSpeedResult | None:
    """Fallback when the PageSpeed API is unavailable: Lighthouse CLI in the container."""
    binary = shutil.which("lighthouse")
    if not binary:
        return None
    proc = await asyncio.create_subprocess_exec(
        binary,
        url,
        "--output=json",
        "--quiet",
        "--form-factor=mobile",
        "--only-categories=performance,accessibility,best-practices,seo",
        "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
    except TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    return parse_pagespeed(url, "mobile", {"lighthouseResult": json.loads(out)})


class AuditAgent:
    name: ClassVar[str] = "audit"
    expensive: ClassVar[bool] = True

    def __init__(
        self,
        browser_factory: Callable[[], GuardedBrowser] | None = None,
        fixtures: Fixtures | None = None,
    ) -> None:
        self._browser_factory = browser_factory or (lambda: GuardedBrowser(fulfill=fixtures))

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
            return AgentOutcome(
                False, LeadStatus.NO_CONTACT_ROUTE, "no contact route: audit skipped"
            )
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
            row = await evidence.capture(
                s,
                ctx.adapters.storage,
                kind=kind,
                lead_id=lead.id,
                business_id=business.id,
                **kwargs,
            )
            ev[key] = str(row.id)
            return str(row.id)

        # Static fetch through the safe fetcher (robots.txt respected, SSRF-guarded).
        try:
            page = await ctx.adapters.fetcher.get(url)
        except RobotsDisallowedError:
            # The site opted out of crawling: respect it, continue without an audit.
            return AgentOutcome(True, None, "audit skipped: robots.txt disallows crawling")
        except FetchError as exc:
            return await self._unreachable(ctx, lead, business, url=url, exc=exc, keep=keep)
        await keep(
            "html",
            EvidenceKind.PAGE,
            url=page.final_url,
            content=page.content,
            file_ext="html",
            data={"status": page.status_code},
        )
        await keep(
            "headers",
            EvidenceKind.METRIC,
            url=page.final_url,
            data={
                "headers": page.headers,
                "status": page.status_code,
                "redirects": page.redirects,
                "elapsed_ms": page.elapsed_ms,
            },
        )
        facts = checks.page_facts(page.text, page.final_url)

        capture = await self._capture_with_evidence(page.final_url, keep)
        drafts = await self._static_findings(ctx, url, page, facts, capture)

        lighthouse = await self._performance(ctx, lead, page.final_url, keep)
        if lighthouse:
            drafts += checks.performance_findings(lighthouse.scores, lighthouse.metrics)

        drafts += await self._broken_links(ctx, facts, page.final_url, keep)
        critique = await self._critique(ctx, lead, capture, keep)
        if critique:
            drafts += [
                checks.FindingDraft(
                    "design",
                    issue.severity,
                    DESIGN_TITLES.get(issue.area, issue.area.title()),
                    issue.observation,
                    "vision",
                    {"area": issue.area},
                    f"design_{issue.area}",
                )
                for issue in critique.issues
            ]
        drafts += await self._local_seo(ctx, lead, business, keep)
        competitors = await self._competitors(ctx, lead, business, brief, keep)

        findings = bind_evidence(drafts, ev)
        scores = overall_scores(lighthouse.scores if lighthouse else {}, critique, findings)
        summary = before_after(findings, critique, brief.get("services", []))
        report_path = await self._pdf(
            ctx,
            lead,
            business,
            url=page.final_url,
            scores=scores,
            findings=findings,
            summary=summary,
            competitors=competitors,
            capture=capture,
        )

        site_status = (
            "broken"
            if page.status_code >= 400
            else (
                "outdated"
                if (critique and critique.looks_outdated)
                or any(f.severity == "critical" for f in findings)
                else "ok"
            )
        )
        business.site_status, business.site_checked_at = site_status, datetime.now(UTC)

        audit = (
            await s.execute(select(Audit).where(Audit.lead_id == lead.id))
        ).scalar_one_or_none()
        values = {
            "url": page.final_url,
            "scores": scores,
            "findings": summarize_for_storage(findings),
            "screenshots": {
                "desktop": ev.get("screenshot_desktop"),
                "mobile": ev.get("screenshot_mobile"),
            },
            "competitor_comparison": [c.model_dump() for c in competitors],
            "before_after_summary": summary,
            "report_pdf_path": report_path,
        }
        if audit is None:
            s.add(Audit(lead_id=lead.id, **values))
        else:
            for k, v in values.items():
                setattr(audit, k, v)
        await s.flush()
        counts = {
            sev: sum(1 for f in findings if f.severity == sev)
            for sev in ("critical", "high", "medium", "low")
        }
        return AgentOutcome(
            True,
            LeadStatus.AUDITED,
            f"audited {page.final_url}: {len(findings)} findings "
            f"({counts['critical']} critical, {counts['high']} high)",
            {"findings": len(findings), "scores": scores},
            paid=True,
        )

    async def _unreachable(
        self,
        ctx: AgentContext,
        lead: Lead,
        business: Business,
        *,
        url: str,
        exc: Exception,
        keep: Callable[..., Any],
    ) -> AgentOutcome:
        """A site that is down is itself the finding; the failed fetch is the evidence."""
        checked_at = datetime.now(UTC)
        evidence_id = await keep(
            "fetch_error",
            EvidenceKind.METRIC,
            url=url,
            data={
                "error": f"{type(exc).__name__}: {exc}"[:500],
                "checked_at": checked_at.isoformat(),
            },
        )
        findings = bind_evidence(
            [
                checks.FindingDraft(
                    "performance",
                    "critical",
                    "Website could not be reached",
                    f"The site at {url} did not respond when checked on {checked_at:%d %b %Y}.",
                    "fetch_error",
                    {"url": url},
                    "site_unreachable",
                )
            ],
            {"fetch_error": evidence_id},
        )
        business.site_status, business.site_checked_at = "unreachable", checked_at
        values: dict[str, Any] = {
            "url": url,
            "scores": {},
            "findings": summarize_for_storage(findings),
            "screenshots": {},
            "competitor_comparison": [],
            "before_after_summary": "Today: the website could not be reached. After the work: "
            "a fast, reliable, mobile-first site.",
            "report_pdf_path": None,
        }
        audit = (
            await ctx.session.execute(select(Audit).where(Audit.lead_id == lead.id))
        ).scalar_one_or_none()
        if audit is None:
            ctx.session.add(Audit(lead_id=lead.id, **values))
        else:
            for k, v in values.items():
                setattr(audit, k, v)
        await ctx.session.flush()
        return AgentOutcome(
            True, LeadStatus.AUDITED, f"site unreachable: {exc}"[:300], {"findings": 1}
        )

    # ── evidence-gathering steps ─────────────────────────────────────────

    async def _capture_with_evidence(self, url: str, keep: Callable[..., Any]) -> Capture | None:
        capture = await self._capture(url)
        if capture is None:
            return None
        await keep(
            "screenshot_desktop",
            EvidenceKind.SCREENSHOT,
            url=capture.final_url,
            content=capture.desktop_png,
            file_ext="png",
            data={"viewport": "desktop"},
        )
        await keep(
            "screenshot_mobile",
            EvidenceKind.SCREENSHOT,
            url=capture.final_url,
            content=capture.mobile_png,
            file_ext="png",
            data={"viewport": "mobile"},
        )
        if capture.axe_violations is not None:
            await keep(
                "axe",
                EvidenceKind.METRIC,
                url=capture.final_url,
                data={"axe_violations": capture.axe_violations},
            )
        return capture

    async def _static_findings(
        self,
        ctx: AgentContext,
        url: str,
        page: FetchResult,
        facts: checks.PageFacts,
        capture: Capture | None,
    ) -> list[checks.FindingDraft]:
        robots_ok, sitemap_ok, https_redirect = await self._site_files(ctx, page.final_url)
        drafts = checks.seo_findings(
            facts,
            x_robots=page.headers.get("x-robots-tag"),
            robots_ok=robots_ok,
            sitemap_ok=sitemap_ok,
        )
        drafts += checks.security_findings(
            requested_url=url,
            final_url=page.final_url,
            headers=page.headers,
            http_resources=facts.http_resources,
            http_redirects_to_https=https_redirect,
        )
        drafts += checks.content_findings(facts)
        if capture is not None and capture.axe_violations:
            drafts += checks.axe_findings(capture.axe_violations)
        return drafts

    async def _capture(self, url: str) -> Capture | None:
        try:
            async with self._browser_factory() as browser:
                return await browser.capture(url)
        except PlaywrightError as exc:
            logger.warning("browser capture failed for %s: %s", url, exc)
            if "Executable doesn't exist" in str(exc):
                return None
            raise TransientAgentError(f"browser capture failed: {exc}") from exc

    async def _site_files(
        self, ctx: AgentContext, final_url: str
    ) -> tuple[bool | None, bool | None, bool | None]:
        parts = urlsplit(final_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        robots_ok: bool | None = None
        sitemap_ok: bool | None = None
        https_redirect: bool | None = None
        try:
            robots = await ctx.adapters.fetcher.get(f"{origin}/robots.txt", respect_robots=False)
            if robots.ok:
                lines = [ln.strip().lower() for ln in robots.text.splitlines()]
                robots_ok = not ("user-agent: *" in lines and "disallow: /" in lines)
        except FetchError:
            pass
        try:
            sitemap = await ctx.adapters.fetcher.get(f"{origin}/sitemap.xml")
            sitemap_ok = sitemap.ok and (
                "<urlset" in sitemap.text or "<sitemapindex" in sitemap.text
            )
        except FetchError:
            sitemap_ok = None
        if parts.scheme == "https":
            try:
                plain = await ctx.adapters.fetcher.get(f"http://{parts.netloc}/")
                https_redirect = plain.final_url.startswith("https://")
            except FetchError:
                https_redirect = None
        return robots_ok, sitemap_ok, https_redirect

    async def _performance(
        self, ctx: AgentContext, lead: Lead, url: str, keep: Callable[..., Any]
    ) -> PageSpeedResult | None:
        result: PageSpeedResult | None = None
        source = "pagespeed"
        try:
            result = await ctx.adapters.pagespeed.run(url, "mobile")
            if ctx.adapters.pagespeed.is_mock:
                _mark_mock(lead, "pagespeed")
        except AdapterUnavailableError:
            result, source = await run_local_lighthouse(url), "lighthouse_cli"
        if result is None:
            return None
        await keep(
            "metric_pagespeed",
            EvidenceKind.METRIC,
            url=url,
            data={"source": source, "scores": result.scores, "metrics": result.metrics},
        )
        return result

    async def _broken_links(
        self, ctx: AgentContext, facts: checks.PageFacts, url: str, keep: Callable[..., Any]
    ) -> list[checks.FindingDraft]:
        targets = checks.link_targets(facts, url)
        if not targets:
            return []
        statuses = await asyncio.gather(*(ctx.adapters.fetcher.head_status(t) for t in targets))
        broken = [
            {"url": t, "status": st}
            for t, st in zip(targets, statuses, strict=True)
            if st is None or st >= 400
        ]
        await keep(
            "links", EvidenceKind.METRIC, url=url, data={"checked": len(targets), "broken": broken}
        )
        if not broken:
            return []
        sev: checks.Severity = "high" if len(broken) >= 5 else "medium"
        return [
            checks.FindingDraft(
                "links",
                sev,
                "Broken links",
                f"{len(broken)} of {len(targets)} links checked are broken.",
                "links",
                {"broken": broken[:10]},
                "broken_links",
            )
        ]

    async def _critique(
        self, ctx: AgentContext, lead: Lead, capture: Capture | None, keep: Callable[..., Any]
    ) -> DesignCritique | None:
        if capture is None:
            return None
        try:
            critique = await ctx.adapters.llm.structured(
                ctx.session,
                LLMRequest(
                    agent=self.name,
                    system=VISION_SYSTEM,
                    task=VISION_TASK,
                    images=[capture.desktop_png, capture.mobile_png],
                    kind="vision",
                    lead_id=lead.id,
                    max_output_tokens=900,
                ),
                DesignCritique,
            )
        except AdapterUnavailableError:
            return None
        if ctx.adapters.llm.is_mock:
            _mark_mock(lead, "openai")
        await keep(
            "vision",
            EvidenceKind.METRIC,
            url=capture.final_url,
            data={
                "critique": critique.model_dump(),
                "screenshots": ["screenshot_desktop", "screenshot_mobile"],
            },
        )
        return critique

    async def _local_seo(
        self, ctx: AgentContext, lead: Lead, business: Business, keep: Callable[..., Any]
    ) -> list[checks.FindingDraft]:
        query = ", ".join(filter(None, [business.name, business.address]))
        try:
            place = await ctx.adapters.places.lookup(
                ctx.session, query, agent=self.name, region=business.country
            )
        except AdapterUnavailableError:
            return []
        if ctx.adapters.places.is_mock:
            _mark_mock(lead, "places")
        data = place.model_dump() if place else {"found": False, "query": query}
        await keep("places", EvidenceKind.METRIC, data={"places": data})
        if place is None:
            return [
                checks.FindingDraft(
                    "local",
                    "medium",
                    "No Google Business Profile found",
                    "A search for the business name and address returned no "
                    "profile, so it is hard to find on Maps.",
                    "places",
                    {"query": query},
                    "places_missing",
                )
            ]
        out = []
        site = normalize_domain(place.website) if place.website else None
        if business.domain and site != business.domain:
            out.append(
                checks.FindingDraft(
                    "local",
                    "medium",
                    "Business Profile doesn't link to the website",
                    "The Google profile lists no (or a different) website.",
                    "places",
                    {"profile_website": place.website},
                    "places_website",
                )
            )
        if place.rating_count is not None and place.rating_count < 10:
            out.append(
                checks.FindingDraft(
                    "local",
                    "low",
                    "Few Google reviews",
                    f"Only {place.rating_count} reviews on the profile.",
                    "places",
                    {"reviews": place.rating_count},
                    "places_reviews",
                )
            )
        return out

    async def _competitors(
        self,
        ctx: AgentContext,
        lead: Lead,
        business: Business,
        brief: dict[str, Any],
        keep: Callable[..., Any],
    ) -> list[Competitor]:
        trade = brief.get("business_type") or (business.industry or "").replace("_", " ")
        city = (brief.get("location") or business.address or "").split(",")[0].strip()
        if not trade or not city:
            return []
        try:
            results = await ctx.adapters.search.search(
                ctx.session,
                f"{trade} {city}",
                agent=self.name,
                country=business.country,
                count=10,
            )
        except AdapterUnavailableError:
            return []
        if ctx.adapters.search.is_mock:
            return []  # mock search returns fixture forum URLs, not competitors
        competitors: list[Competitor] = []
        seen = {business.domain}
        for r in results:
            domain = normalize_domain(r.url)
            if not domain or domain in seen or domain in PLATFORM_DOMAINS:
                continue
            seen.add(domain)
            try:
                page = await ctx.adapters.fetcher.get(f"https://{domain}/")
            except FetchError:
                continue
            if not page.ok:
                continue
            f = checks.page_facts(page.text, page.final_url)
            evid = await keep(
                f"competitor_{domain}",
                EvidenceKind.PAGE,
                url=page.final_url,
                content=page.content,
                file_ext="html",
                data={"purpose": "competitor"},
            )
            competitors.append(
                Competitor(
                    domain=domain,
                    url=page.final_url,
                    https=page.final_url.startswith("https://"),
                    mobile_ready=f.viewport,
                    has_structured_data=bool(f.jsonld_types),
                    has_cta=bool(f.cta_candidates),
                    title=f.title,
                    evidence_id=evid,
                )
            )
            if len(competitors) >= COMPETITOR_LIMIT:
                break
        return competitors

    async def _pdf(
        self,
        ctx: AgentContext,
        lead: Lead,
        business: Business,
        *,
        url: str,
        scores: dict[str, float | None],
        findings: list[Any],
        summary: str,
        competitors: list[Competitor],
        capture: Capture | None,
    ) -> str | None:
        html = render_html(
            business_name=business.name,
            url=url,
            scores=scores,
            findings=findings,
            summary=summary,
            competitors=competitors,
            desktop_png=capture.desktop_png if capture else None,
            mobile_png=capture.mobile_png if capture else None,
            sender=ctx.settings.sender_business_name or "our studio",
            generated_on=datetime.now(UTC).strftime("%d %b %Y"),
        )
        ctx.adapters.storage.save_bytes(f"audits/{lead.id}/report.html", html.encode())
        try:
            async with self._browser_factory() as browser:
                pdf = await browser.pdf(html)
        except PlaywrightError as exc:
            logger.warning("PDF rendering failed: %s", exc)
            return None
        return ctx.adapters.storage.save_bytes(f"audits/{lead.id}/report.pdf", pdf)


register(AuditAgent())
