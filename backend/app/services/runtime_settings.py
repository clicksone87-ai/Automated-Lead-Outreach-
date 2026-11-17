"""Runtime settings stored in the `settings` table.

Every change is written to `settings_changes`; autonomy stage changes go through
`autonomy.change_stage`, not `update()`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import SettingChange, SettingRow

SUPPORTED_LANGUAGES = ("en", "de", "fr", "es")


class BudgetCap(BaseModel):
    daily_usd: float | None = Field(default=None, ge=0)
    monthly_usd: float | None = Field(default=None, ge=0)


class SendingWindow(BaseModel):
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=17, ge=1, le=24)
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon–Fri
    respect_holidays: bool = True


class ModelPrice(BaseModel):
    """USD per 1M tokens (text) or per image. Verify on the vendor's pricing page."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    per_image: float = 0.0


class RuntimeSettings(BaseModel):
    autonomy_stage: int = Field(default=1, ge=1, le=3)
    daily_send_limit: int = Field(default=10, ge=0)
    hourly_send_limit: int = Field(default=3, ge=0)
    # Domain warm-up: max sends per day by week since first send (last value repeats)
    warmup_schedule: list[int] = Field(default_factory=lambda: [5, 10, 20, 35, 50])
    # Mailpit sandbox delivers immediately unless this is on (real sending always respects
    # the recipient's business hours).
    sandbox_respect_send_window: bool = False
    daily_budget_usd: float = Field(default=4.0, ge=0)
    monthly_budget_usd: float = Field(default=120.0, ge=0)
    # keys: "agent:<name>" or "provider:<name>"
    budget_caps: dict[str, BudgetCap] = Field(default_factory=dict)
    enabled_languages: list[str] = Field(default_factory=lambda: list(SUPPORTED_LANGUAGES))
    sending_languages: list[str] = Field(default_factory=lambda: ["en"])
    target_countries: list[str] = Field(default_factory=lambda: ["US", "GB"])
    active_industry: str = "home_services"
    min_intent_score: int = Field(default=60, ge=0, le=100)
    cooldown_days: int = Field(default=90, ge=0)
    max_follow_ups: int = Field(default=2, ge=0, le=5)
    follow_up_delay_days: list[int] = Field(default_factory=lambda: [4, 9])
    sending_window: SendingWindow = Field(default_factory=SendingWindow)
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)
    # USD per call for non-LLM paid providers. Planning estimates — verify vendor pricing.
    provider_prices: dict[str, float] = Field(
        default_factory=lambda: {"brave": 0.005, "zerobounce": 0.008, "places": 0.035, "x": 0.01}
    )
    # Learning loop
    scoring_weights: dict[str, float] = Field(default_factory=dict)
    source_weights: dict[str, float] = Field(default_factory=dict)
    template_weights: dict[str, float] = Field(default_factory=dict)
    # Quality thresholds
    lighthouse_min: dict[str, int] = Field(
        default_factory=lambda: {
            "performance": 80,
            "accessibility": 90,
            "best-practices": 85,
            "seo": 90,
        }
    )

    @classmethod
    def defaults_from_env(cls, env: Settings) -> RuntimeSettings:
        return cls(
            autonomy_stage=env.autonomy_stage,
            daily_send_limit=env.daily_send_limit,
            daily_budget_usd=env.daily_budget_usd,
            monthly_budget_usd=env.monthly_budget_usd,
            min_intent_score=env.min_intent_score,
            cooldown_days=env.cooldown_days,
        )


async def load(session: AsyncSession) -> RuntimeSettings:
    base = RuntimeSettings.defaults_from_env(get_settings()).model_dump()
    rows = (await session.execute(select(SettingRow))).scalars().all()
    for row in rows:
        if row.key in base:
            base[row.key] = row.value
    return RuntimeSettings.model_validate(base)


async def update(
    session: AsyncSession,
    changes: dict[str, Any],
    *,
    changed_by: str,
    reason: str | None = None,
    allow_autonomy: bool = False,
) -> RuntimeSettings:
    """Validate and persist changes; log each one. Returns the new settings."""
    if "autonomy_stage" in changes and not allow_autonomy:
        raise PermissionError("autonomy_stage changes must go through autonomy.change_stage")
    current = await load(session)
    merged = current.model_dump() | changes
    validated = RuntimeSettings.model_validate(merged)  # raises on invalid input
    new_values = validated.model_dump()
    old_values = current.model_dump()
    for key in changes:
        if key not in new_values:
            raise KeyError(f"unknown setting: {key}")
        if old_values.get(key) == new_values[key]:
            continue
        stmt = insert(SettingRow).values(key=key, value=new_values[key])
        stmt = stmt.on_conflict_do_update(
            index_elements=[SettingRow.key], set_={"value": stmt.excluded.value}
        )
        await session.execute(stmt)
        session.add(
            SettingChange(
                key=key,
                old_value=old_values.get(key),
                new_value=new_values[key],
                changed_by=changed_by,
                reason=reason,
            )
        )
    await session.flush()
    return validated
