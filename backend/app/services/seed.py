"""`make seed`: runtime settings, pricing packages, source rows (idempotent).

Country rules live in app/policy/country_rules.yaml (edited by the legal review).
Pricing values are STARTER values for the dashboard pricing sheet — confirm/edit them in
Settings before they are ever quoted to a client.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.discovery.runner import ensure_source_rows
from app.config import Settings
from app.db.models import Business, Lead, PricingPackage, SettingRow, Source
from app.services.runtime_settings import RuntimeSettings

STARTER_PRICING: list[tuple[str, str, str, float, float, float]] = [
    # service, package, description, USD, EUR, GBP
    ("logo", "Essential", "2 logo concepts, 2 revision rounds, SVG/PNG files", 250, 230, 200),
    ("logo", "Brand kit", "3 concepts, palette, typography, social assets", 600, 550, 480),
    (
        "website",
        "Starter",
        "Up to 5 pages, mobile-first, contact form, basic SEO",
        1500,
        1400,
        1200,
    ),
    (
        "website",
        "Business",
        "Up to 10 pages, booking/quote form, local SEO setup",
        3000,
        2800,
        2400,
    ),
    ("redesign", "Refresh", "Same structure, modern design, speed + mobile fixes", 1200, 1100, 950),
    (
        "redesign",
        "Rebuild",
        "New design and copy, structure kept for SEO, analytics",
        2500,
        2300,
        2000,
    ),
    ("seo", "Technical fix", "Audit fixes: titles, meta, speed, schema, sitemap", 450, 420, 360),
    ("seo", "Local SEO monthly", "Monthly local SEO, Business Profile, reporting", 300, 280, 240),
]


async def seed(session: AsyncSession, env: Settings) -> dict[str, int]:
    defaults = RuntimeSettings.defaults_from_env(env).model_dump()
    for key, value in defaults.items():
        await session.execute(
            insert(SettingRow)
            .values(key=key, value=value)
            .on_conflict_do_nothing(index_elements=[SettingRow.key])
        )
    for order, (service, package, desc, usd, eur, gbp) in enumerate(STARTER_PRICING):
        await session.execute(
            insert(PricingPackage)
            .values(
                service=service,
                package=package,
                description=desc,
                price_usd=usd,
                price_eur=eur,
                price_gbp=gbp,
                sort_order=order,
            )
            .on_conflict_do_nothing(constraint="uq_pricing")
        )
    await ensure_source_rows(session, env)
    await session.flush()
    return {"settings": len(defaults), "pricing_packages": len(STARTER_PRICING)}


async def purge_demo_data(session: AsyncSession) -> dict[str, int]:
    """Delete everything a mock/fixture adapter ever produced (leads tagged with
    `mock_adapters_used` or from the fixture source), their evidence/briefs/drafts (FK
    cascades), businesses left without leads that were created by the fixtures, and the
    fixture source row. Real data is untouched."""
    mock_leads = (
        (
            await session.execute(
                select(Lead.id, Lead.business_id).where(
                    or_(
                        Lead.source == "mock_demo",
                        Lead.pipeline_state.has_key("mock_adapters_used"),
                    )
                )
            )
        )
        .tuples()
        .all()
    )
    lead_ids = [lead_id for lead_id, _ in mock_leads]
    business_ids = {b for _, b in mock_leads if b is not None}
    if lead_ids:
        await session.execute(delete(Lead).where(Lead.id.in_(lead_ids)))
    orphans: list[object] = []
    if business_ids:
        with_leads = set(
            (
                await session.execute(
                    select(Lead.business_id).where(Lead.business_id.in_(business_ids))
                )
            )
            .scalars()
            .all()
        )
        orphans = [b for b in business_ids if b not in with_leads]
        if orphans:
            await session.execute(delete(Business).where(Business.id.in_(orphans)))
    src = await session.execute(delete(Source).where(Source.key == "mock_demo"))
    await session.flush()
    return {
        "leads_deleted": len(lead_ids),
        "businesses_deleted": len(orphans),
        "fixture_source_rows_deleted": int(src.rowcount or 0),  # type: ignore[attr-defined]
    }
