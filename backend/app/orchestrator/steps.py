"""Which pipeline steps belong to which lane (shared by `pipeline` and `lanes`)."""

from __future__ import annotations

from app.orchestrator.agents import PIPELINE_ORDER

#: Logos, previews and the full browser audit: memory-heavy, run for top leads only.
HEAVY_STEPS: tuple[str, ...] = ("audit", "branding", "website_builder", "quality_check")
#: Every lead: prefilter, scoring, contact, light audit, text draft, outreach.
FAST_LANE: tuple[str, ...] = tuple(s for s in PIPELINE_ORDER if s not in HEAVY_STEPS)
#: The heavy lane ends by redrafting the proposal with its assets.
HEAVY_LANE: tuple[str, ...] = (*HEAVY_STEPS, "proposal", "outreach")
FULL: tuple[str, ...] = PIPELINE_ORDER

#: The heavy lane as run: audit and branding are independent, so they run side by side.
HEAVY_GRAPH: tuple[str | tuple[str, ...], ...] = (
    ("audit", "branding"),
    "website_builder",
    "quality_check",
    "proposal",
    "outreach",
)

FAST_TIMEOUTS: dict[str, float] = {
    "prefilter": 30,
    "requirements": 120,
    "contact_enrichment": 90,
    "audit_light": 120,
    "proposal": 180,
    "outreach": 60,
}
HEAVY_TIMEOUTS: dict[str, float] = {
    "audit": 420,
    "branding": 480,
    "website_builder": 600,
    "quality_check": 180,
    "proposal": 180,
    "outreach": 60,
}
