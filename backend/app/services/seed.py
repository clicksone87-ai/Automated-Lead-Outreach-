"""`make seed`: runtime settings, pricing packages, source rows (idempotent).

Country rules live in app/policy/country_rules.yaml (edited by the legal review).
Pricing values are STARTER values for the dashboard pricing sheet — confirm/edit them in
Settings before they are ever quoted to a client.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.discovery.runner import ensure_source_rows
from app.config import Settings
from app.db.models import PricingPackage, SettingRow
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
