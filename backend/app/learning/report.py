"""Learning loop: weekly report and guarded adjustments.

A weight moves only with at least MIN_SAMPLE sends, by STEP at a time and within fixed bounds;
every change is logged. Sandbox (Mailpit) messages never count.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Business, Lead, LeadStatus, Message, Proposal, Reply, ReplyClass, Report
from app.services import runtime_settings

MIN_SAMPLE = 20
STEP = 0.1
BOUNDS = (0.5, 1.5)
QUALIFIED = {
    LeadStatus.QUALIFIED, LeadStatus.CONTACT_FOUND, LeadStatus.AUDITED, LeadStatus.BRANDED,
    LeadStatus.BUILT, LeadStatus.QA_PASSED, LeadStatus.AWAITING_APPROVAL, LeadStatus.APPROVED,
    LeadStatus.AWAITING_SEND, LeadStatus.SENT, LeadStatus.REPLIED, LeadStatus.WON, LeadStatus.LOST,
}  # fmt: skip


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


async def build_report(
    session: AsyncSession, *, days: int = 7, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    leads = (await session.execute(select(Lead).where(Lead.created_at >= since))).scalars().all()
    industries = {
        b.id: b.industry
        for b in (await session.execute(select(Business).where(Business.id.in_([lead.business_id for lead in leads if lead.business_id])))).scalars()
    }  # fmt: skip
    sent_rows = (
        await session.execute(
            select(Message, Proposal, Lead)
            .join(Proposal, Proposal.id == Message.proposal_id)
            .join(Lead, Lead.id == Proposal.lead_id)
            .where(Message.sent_at >= since, Message.follow_up_number == 0)
        )
    ).all()
    replies = (
        (
            await session.execute(
                select(Reply).where(Reply.received_at >= since, Reply.lead_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    replied: dict[Any, set[str]] = defaultdict(set)
    for r in replies:
        replied[r.lead_id].add(str(r.classification))

    def bucket() -> dict[str, int]:
        return {
            "discovered": 0,
            "qualified": 0,
            "sent": 0,
            "sandbox_sent": 0,
            "replied": 0,
            "positive": 0,
            "won": 0,
        }

    dims: dict[str, dict[str, dict[str, int]]] = {
        k: defaultdict(bucket)
        for k in ("source", "service", "industry", "country", "trigger", "template")
    }
    funnel = bucket()
    for lead in leads:
        keys = {
            "source": [lead.source], "country": [lead.country or "unknown"], "trigger": [lead.trigger_type],
            "industry": [(industries.get(lead.business_id) if lead.business_id else None) or "unknown"], "service": list(lead.service_needed or []) or ["unknown"],
        }  # fmt: skip
        flags = {
            "discovered": 1,
            "qualified": int(lead.status in QUALIFIED),
            "won": int(lead.status == LeadStatus.WON),
        }
        for dim, vals in keys.items():
            for v in vals:
                for f, n in flags.items():
                    dims[dim][v][f] += n
        for f, n in flags.items():
            funnel[f] += n
    for msg, proposal, lead in sent_rows:
        field = "sandbox_sent" if msg.sandbox else "sent"
        got = replied.get(lead.id, set())
        rep = int(bool(got - {ReplyClass.AUTO_REPLY}))
        pos = int(bool(got & {ReplyClass.POSITIVE, ReplyClass.QUESTION}))
        keys = {
            "source": [lead.source], "country": [lead.country or "unknown"], "trigger": [lead.trigger_type],
            "industry": [(industries.get(lead.business_id) if lead.business_id else None) or "unknown"], "service": list(lead.service_needed or []) or ["unknown"],
            "template": [proposal.template_variant or "default"],
        }  # fmt: skip
        for dim, vals in keys.items():
            for v in vals:
                dims[dim][v][field] += 1
                if not msg.sandbox:
                    dims[dim][v]["replied"] += rep
                    dims[dim][v]["positive"] += pos
        funnel[field] += 1
        if not msg.sandbox:
            funnel["replied"] += rep
            funnel["positive"] += pos

    def table(d: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
        rows = [{"key": k, **v, "reply_rate": _rate(v["replied"], v["sent"]),
                 "positive_rate": _rate(v["positive"], v["sent"])} for k, v in d.items()]  # fmt: skip
        return sorted(rows, key=lambda r: (-r["positive"], -r["replied"], -r["discovered"]))

    return {
        "period_start": since.isoformat(),
        "period_end": now.isoformat(),
        "funnel": funnel,
        "by": {dim: table(d) for dim, d in dims.items()},
        "note": "Reply rates use real sends only; Mailpit sandbox sends are counted separately.",
    }


def _nudge(current: float, rate: float, baseline: float) -> float:
    target = current + (STEP if rate > baseline else -STEP if rate < baseline else 0.0)
    return round(min(BOUNDS[1], max(BOUNDS[0], target)), 2)


async def adjust(session: AsyncSession, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Move source and template weights a small step toward what produced replies."""
    rs = await runtime_settings.load(session)
    baseline = _rate(report["funnel"]["replied"], report["funnel"]["sent"])
    changes: list[dict[str, Any]] = []
    new_sources, new_templates = dict(rs.source_weights), dict(rs.template_weights)
    for dim, store in (("source", new_sources), ("template", new_templates)):
        for row in report["by"][dim]:
            if row["sent"] < MIN_SAMPLE:
                continue
            old = float(store.get(row["key"], 1.0))
            new = _nudge(old, row["reply_rate"], baseline)
            if new != old:
                store[row["key"]] = new
                changes.append({"dimension": dim, "key": row["key"], "from": old, "to": new,
                                "reply_rate": row["reply_rate"], "baseline": baseline, "sent": row["sent"]})  # fmt: skip
    if changes:
        await runtime_settings.update(
            session,
            {"source_weights": new_sources, "template_weights": new_templates},
            changed_by="learning_loop",
            reason=f"weekly report: {len(changes)} adjustment(s), min sample {MIN_SAMPLE}, step {STEP}",
        )
    return changes


async def run_weekly(session: AsyncSession, *, now: datetime | None = None) -> Report:
    data = await build_report(session, days=7, now=now)
    changes = await adjust(session, data)
    row = Report(kind="weekly", period_start=datetime.fromisoformat(data["period_start"]),
                 period_end=datetime.fromisoformat(data["period_end"]), data=data, adjustments=changes)  # fmt: skip
    session.add(row)
    await session.flush()
    return row
