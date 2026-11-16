"""Quality check.

Verifies produced work before it can be used: logos (valid files, resolution, exact wording,
trademark similarity), the website preview (build + smoke tests + Lighthouse thresholds) and
evidence links (every audit finding and brand fact points at stored evidence).

The checker never produces or fixes anything, so it can never approve its own failed work.
A failed item goes back to its producer once (that agent's last run is superseded and the
lead re-enters the pipeline at that step); a second failure goes to human review. Every
verdict is recorded in `quality_checks`.
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Any, ClassVar

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select

from app.agents.branding.logos import SvgRejectedError, sanitize_svg, text_matches
from app.db.models import (
    AgentRun,
    Audit,
    BrandProfile,
    Brief,
    Evidence,
    Lead,
    LeadStatus,
    LogoConcept,
    Preview,
    QAItemType,
    QAStatus,
    QualityCheck,
    RunStatus,
)
from app.orchestrator.agents import AgentContext, AgentOutcome, register

logger = logging.getLogger(__name__)

CHECKER = "quality_check"
MIN_LOGO_WIDTH = 1024
REQUIRED_PREVIEW_CHECKS = {
    "build_output", "title", "h1", "business_name", "cta", "viewport_meta", "noindex",
    "internal_links", "renders", "no_external_requests",
}  # fmt: skip
MAX_RETURNS = 1  # back to the producer once, then human review
# Where the lead re-enters the pipeline when an item is returned to its producer.
REENTRY_STATUS = {"branding": LeadStatus.AUDITED, "website_builder": LeadStatus.BRANDED}


def check(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


class QualityCheckAgent:
    name: ClassVar[str] = CHECKER
    expensive: ClassVar[bool] = False

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        brief_row = await s.get(Brief, lead_id)
        if lead is None or brief_row is None:
            return AgentOutcome(False, None, "missing lead/brief")
        agents = brief_row.data.get("agents_to_run", [])
        if CHECKER not in agents:
            return AgentOutcome(True, None, "nothing to quality-check for this lead")

        results: dict[str, str] = {}
        if "branding" in agents:
            results["logos"] = await self._logos(ctx, lead)
        if "website_builder" in agents:
            results["preview"] = await self._preview(ctx, lead)
        results["evidence"] = await self._evidence(ctx, lead)

        failed = {k: v for k, v in results.items() if v != QAStatus.PASSED}
        if not failed:
            return AgentOutcome(
                True, LeadStatus.QA_PASSED, "quality check passed: " + ", ".join(results), results
            )
        # Return to the producer once; afterwards (or for evidence problems) a human decides.
        for item, producer in (("logos", "branding"), ("preview", "website_builder")):
            if (
                failed.get(item) == QAStatus.FAILED
                and await self._returns(ctx, lead, producer) < MAX_RETURNS
            ):
                await self._return_to(ctx, lead, producer)
                return AgentOutcome(
                    False, REENTRY_STATUS[producer],
                    f"quality check failed ({item}) — returned to {producer} for another attempt", results,
                )  # fmt: skip
        return AgentOutcome(
            False, LeadStatus.NEEDS_REVIEW,
            "quality check failed (" + ", ".join(f"{k}: {v}" for k, v in failed.items()) + ") — human review",
            results,
        )  # fmt: skip

    # ── logos ────────────────────────────────────────────────────────────

    async def _logos(self, ctx: AgentContext, lead: Lead) -> str:
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
        if not rows:
            await self._record(ctx, lead, QAItemType.LOGO, lead.id, "branding", QAStatus.FAILED,
                               [check("concepts_exist", False, "no logo concepts")])  # fmt: skip
            return QAStatus.FAILED
        statuses = []
        for row in rows:
            checks = self._logo_checks(ctx, row)
            if all(c["passed"] for c in checks):
                status = QAStatus.PASSED
            elif any(c["name"] == "wording" and c["detail"] == "paths" for c in checks) and all(
                c["passed"] for c in checks if c["name"] != "wording"
            ):
                status = QAStatus.NEEDS_REVIEW  # wording drawn as outlines: a human reads it
            else:
                status = QAStatus.FAILED
            row.qa_status, row.qa_report = str(status), {"checks": checks, "checker": CHECKER}
            await self._record(ctx, lead, QAItemType.LOGO, row.id, "branding", status, checks)
            statuses.append(status)
        if QAStatus.PASSED in statuses:
            return QAStatus.PASSED  # at least one usable concept
        return QAStatus.NEEDS_REVIEW if QAStatus.NEEDS_REVIEW in statuses else QAStatus.FAILED

    @staticmethod
    def _logo_checks(ctx: AgentContext, row: LogoConcept) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        svg = ""
        try:
            raw = ctx.adapters.storage.read_bytes(row.svg_path or "").decode("utf-8")
            svg = sanitize_svg(raw)
            checks.append(check("svg_valid", True, f"{len(raw)} bytes"))
        except (OSError, ValueError, SvgRejectedError) as exc:
            checks.append(check("svg_valid", False, str(exc)[:200]))
        try:
            with Image.open(io.BytesIO(ctx.adapters.storage.read_bytes(row.png_path or ""))) as img:
                img.verify()
            with Image.open(io.BytesIO(ctx.adapters.storage.read_bytes(row.png_path or ""))) as img:
                w, h = img.size
                bbox = img.getchannel("A").getbbox() if img.mode == "RGBA" else (0, 0, w, h)
            checks.append(check("png_resolution", w >= MIN_LOGO_WIDTH, f"{w}x{h}"))
            checks.append(
                check("png_not_blank", bbox is not None, "visible pixels" if bbox else "blank")
            )
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            checks.append(check("png_resolution", False, str(exc)[:200]))
        wording = text_matches(svg, row.text or "") if svg else "missing"
        checks.append(check("wording", wording == "exact", wording))
        sim = row.similarity or {}
        checks.append(
            check(
                "trademark_similarity",
                not sim.get("too_similar"),
                f"nearest {sim.get('nearest')} distance {sim.get('distance')} "
                f"({sim.get('checked_against', 0)} competitor logos checked)",
            )
        )
        return checks

    # ── website preview ──────────────────────────────────────────────────

    async def _preview(self, ctx: AgentContext, lead: Lead) -> str:
        p = (
            await ctx.session.execute(select(Preview).where(Preview.lead_id == lead.id))
        ).scalar_one_or_none()
        if p is None:
            await self._record(ctx, lead, QAItemType.PREVIEW, lead.id, "website_builder", QAStatus.FAILED,
                               [check("preview_exists", False, "no preview built")])  # fmt: skip
            return QAStatus.FAILED
        tr = p.test_results or {}
        checks = [check("build", "build_error" not in tr, str(tr.get("build_error", "ok"))[:200])]
        by_name = {c["name"]: c for c in tr.get("checks", [])}
        for name in sorted(REQUIRED_PREVIEW_CHECKS):
            c = by_name.get(name)
            checks.append(check(name, bool(c and c["passed"]), (c or {}).get("detail", "not run")))
        if "lighthouse" in by_name:
            checks.append(
                check(
                    "lighthouse", by_name["lighthouse"]["passed"], by_name["lighthouse"]["detail"]
                )
            )
        checks.append(
            check("screenshots", bool((p.screenshots or {}).get("desktop")), "desktop + mobile")
        )
        status = QAStatus.PASSED if all(c["passed"] for c in checks) else QAStatus.FAILED
        p.qa_status = str(status)
        p.qa_report = {"checks": checks, "checker": CHECKER}
        await self._record(ctx, lead, QAItemType.PREVIEW, p.id, "website_builder", status, checks)
        return status

    # ── evidence links ───────────────────────────────────────────────────

    async def _evidence(self, ctx: AgentContext, lead: Lead) -> str:
        s = ctx.session
        ids = {
            str(i)
            for i in (
                await s.execute(select(Evidence.id).where(Evidence.lead_id == lead.id))
            ).scalars()
        }
        checks: list[dict[str, Any]] = []
        audit = (
            await s.execute(select(Audit).where(Audit.lead_id == lead.id))
        ).scalar_one_or_none()
        if audit is not None:
            missing = [f.get("id") for f in audit.findings if f.get("evidence_id") not in ids]
            checks.append(check("audit_findings_have_evidence", not missing,
                                f"{len(audit.findings)} findings, {len(missing)} without evidence"))  # fmt: skip
        if lead.business_id:
            profile = (
                await s.execute(
                    select(BrandProfile).where(BrandProfile.business_id == lead.business_id)
                )
            ).scalar_one_or_none()
            if profile is not None and profile.sources:
                all_ids = {str(i) for i in (await s.execute(
                    select(Evidence.id).where(Evidence.business_id == lead.business_id))).scalars()} | ids  # fmt: skip
                missing_src = [
                    x["value"] for x in profile.sources if x.get("evidence_id") not in all_ids
                ]
                checks.append(check("brand_facts_have_evidence", not missing_src,
                                    f"{len(profile.sources)} facts, {len(missing_src)} without evidence"))  # fmt: skip
        if not checks:
            checks.append(check("nothing_to_verify", True, "no claims yet"))
        status = QAStatus.PASSED if all(c["passed"] for c in checks) else QAStatus.NEEDS_REVIEW
        await self._record(
            ctx, lead, QAItemType.AUDIT, audit.id if audit else lead.id, "audit", status, checks
        )
        return status

    # ── bookkeeping ──────────────────────────────────────────────────────

    async def _record(  # noqa: PLR0917 - internal helper, called positionally
        self, ctx: AgentContext, lead: Lead, item_type: QAItemType, item_id: uuid.UUID,
        producer: str, status: str, checks: list[dict[str, Any]],
    ) -> None:  # fmt: skip
        assert producer != CHECKER  # the checker never verifies its own work
        previous = (
            await ctx.session.execute(
                select(func.count())
                .select_from(QualityCheck)
                .where(QualityCheck.item_type == str(item_type), QualityCheck.item_id == item_id)
            )
        ).scalar_one()
        ctx.session.add(
            QualityCheck(
                lead_id=lead.id, item_type=str(item_type), item_id=item_id, producer=producer,
                checker=CHECKER, status=str(status), checks=checks, attempt=int(previous) + 1,
                returned_to=None if status == QAStatus.PASSED else (
                    "human_review" if status == QAStatus.NEEDS_REVIEW else producer),
            )
        )  # fmt: skip
        await ctx.session.flush()

    @staticmethod
    async def _returns(ctx: AgentContext, lead: Lead, producer: str) -> int:
        return int(
            (
                await ctx.session.execute(
                    select(func.count())
                    .select_from(AgentRun)
                    .where(
                        AgentRun.lead_id == lead.id,
                        AgentRun.agent == producer,
                        AgentRun.status == RunStatus.SUPERSEDED,
                    )
                )
            ).scalar_one()
        )

    @staticmethod
    async def _return_to(ctx: AgentContext, lead: Lead, producer: str) -> None:
        """Supersede the producer's last successful run so the pipeline re-runs that step."""
        run = (
            await ctx.session.execute(
                select(AgentRun)
                .where(
                    AgentRun.lead_id == lead.id,
                    AgentRun.agent == producer,
                    AgentRun.status == RunStatus.SUCCEEDED,
                )
                .order_by(AgentRun.finished_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if run is not None:
            run.status = RunStatus.SUPERSEDED
        await ctx.session.flush()


register(QualityCheckAgent())
