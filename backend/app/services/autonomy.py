"""Autonomy stages: live move-up metrics and the only way to change stage.

Stage 1 → 2: bounce < 3 %, complaints < 0.1 %, and two clean weeks (≥ 14 days since the
first send with no breach, and enough volume to judge: ≥ 20 sends).
Stage 2 → 3: everything above, plus ≥ 14 days at stage 2 and budget caps working (no spend
above a cap). Moving down is always allowed. Every change is logged with who and why.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeliveryStatus, Message, PolicyDecision, SettingChange
from app.services import runtime_settings

BOUNCE_LIMIT = 0.03
COMPLAINT_LIMIT = 0.001
CLEAN_DAYS = 14
MIN_SENDS = 20


@dataclass(frozen=True, slots=True)
class Condition:
    key: str
    label: str
    met: bool
    value: str


@dataclass(frozen=True, slots=True)
class AutonomyStatus:
    stage: int
    can_move_up: bool
    next_stage: int | None
    conditions: list[Condition]
    sandbox_only: bool

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["conditions"] = [asdict(c) for c in self.conditions]
        return d


async def _stage_since(session: AsyncSession, stage: int) -> datetime | None:
    row = (
        await session.execute(
            select(SettingChange.created_at)
            .where(SettingChange.key == "autonomy_stage")
            .order_by(SettingChange.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def status(session: AsyncSession) -> AutonomyStatus:
    rs = await runtime_settings.load(session)
    now = datetime.now(UTC)
    since30 = now - timedelta(days=30)
    real = Message.sandbox.is_(False)
    sent = int(
        (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.sent_at >= since30, real)
            )
        ).scalar_one()
    )
    bounced = int((await session.execute(select(func.count()).select_from(Message).where(
        Message.sent_at >= since30, real, Message.delivery_status == DeliveryStatus.BOUNCED))).scalar_one())  # fmt: skip
    complained = int((await session.execute(select(func.count()).select_from(Message).where(
        Message.sent_at >= since30, real, Message.complained.is_(True)))).scalar_one())  # fmt: skip
    sandbox_sent = int((await session.execute(select(func.count()).select_from(Message).where(
        Message.sent_at.is_not(None), Message.sandbox.is_(True)))).scalar_one())  # fmt: skip
    first_send = (await session.execute(select(func.min(Message.sent_at)).where(real))).scalar_one()
    bounce_rate = bounced / sent if sent else 0.0
    complaint_rate = complained / sent if sent else 0.0
    clean_days = (now - first_send).days if first_send else 0
    breaches = int((await session.execute(select(func.count()).select_from(PolicyDecision).where(
        PolicyDecision.check == "budget", PolicyDecision.allowed.is_(False),
        PolicyDecision.created_at >= now - timedelta(days=CLEAN_DAYS)))).scalar_one())  # fmt: skip
    conditions = [
        Condition(
            "volume",
            f"At least {MIN_SENDS} real sends (30 days)",
            sent >= MIN_SENDS,
            f"{sent} sent",
        ),
        Condition(
            "bounce",
            "Bounce rate below 3 %",
            sent > 0 and bounce_rate < BOUNCE_LIMIT,
            f"{bounce_rate:.1%}",
        ),
        Condition(
            "complaints",
            "Spam complaints below 0.1 %",
            sent > 0 and complaint_rate < COMPLAINT_LIMIT,
            f"{complaint_rate:.2%}",
        ),
        Condition(
            "clean_weeks",
            "Two clean weeks since the first send",
            clean_days >= CLEAN_DAYS,
            f"{clean_days} days",
        ),
    ]
    if rs.autonomy_stage >= 2:
        since = await _stage_since(session, 2)
        days = (now - since).days if since else 0
        conditions += [
            Condition(
                "stage2_stable", "Stable at stage 2 for 14 days", days >= CLEAN_DAYS, f"{days} days"
            ),
            Condition(
                "budget_caps",
                "Budget caps working (no breach in 14 days)",
                breaches == 0,
                f"{breaches} breaches",
            ),
        ]
    next_stage = rs.autonomy_stage + 1 if rs.autonomy_stage < 3 else None
    return AutonomyStatus(
        stage=rs.autonomy_stage,
        can_move_up=next_stage is not None and all(c.met for c in conditions),
        next_stage=next_stage,
        conditions=conditions,
        sandbox_only=sent == 0 and sandbox_sent > 0,
    )


async def change_stage(
    session: AsyncSession, stage: int, *, changed_by: str, reason: str | None
) -> AutonomyStatus:
    current = await status(session)
    if stage not in (1, 2, 3):
        raise ValueError("stage must be 1, 2 or 3")
    if stage > current.stage:
        if stage != current.stage + 1:
            raise PermissionError("move up one stage at a time")
        if not current.can_move_up:
            unmet = [c.label for c in current.conditions if not c.met]
            raise PermissionError("conditions not met: " + "; ".join(unmet))
    await runtime_settings.update(
        session,
        {"autonomy_stage": stage},
        changed_by=changed_by,
        reason=reason,
        allow_autonomy=True,
    )
    return await status(session)
