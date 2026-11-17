"""Branding agent: brand extraction end to end against a fixture site and Postgres."""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select

from app.adapters.fetcher import SafeFetcher
from app.adapters.registry import build_adapters
from app.agents.branding.agent import BrandingAgent, logo_format
from app.config import Settings
from app.db.models import (
    Audit,
    BrandProfile,
    Brief,
    Business,
    Evidence,
    Lead,
    LeadStatus,
    LogoConcept,
)
from app.db.session import session_scope
from app.orchestrator import runs
from app.orchestrator.agents import AgentContext, AgentOutcome

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

SITE = "https://smith-plumbing.com/"
COMPETITOR = "https://rival-plumbers.com/"


def _png(color: tuple[int, int, int], size: tuple[int, int] = (120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


LOGO_PNG = _png((11, 95, 165))
TOUCH_PNG = _png((242, 140, 40), (180, 180))
RIVAL_PNG = _png((200, 30, 30))

HOME = """<!doctype html><html><head><title>Smith Plumbing | Leeds</title>
<meta name="theme-color" content="#0b5fa5">
<link rel="apple-touch-icon" href="/touch.png">
<link rel="stylesheet" href="/site.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@600">
</head><body>
<header><a href="/"><img src="/logo-missing.png" class="site-logo" alt="logo"></a></header>
<h1>Plumbers</h1>
<a href="https://www.facebook.com/smithplumbing">Facebook</a>
</body></html>"""
CSS = ":root{--brand-primary:#0b5fa5;--accent:#f28c28} body{font-family:'Open Sans',sans-serif}"
RIVAL = """<html><head><title>Rival</title><style>header{background:#c81e1e}</style></head>
<body><header><img src="/rival-logo.png" alt="Rival logo"></header></body></html>"""


class FakeBrowser:
    """Renders any HTML to a small PNG (keeps tests fast and offline)."""

    async def __aenter__(self) -> FakeBrowser:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def render_png(
        self, html: str, *, width: int, height: int, transparent: bool = True
    ) -> bytes:
        colour = (11, 95, 165) if "#0b5fa5" in html else (30, 30, 30)
        return _png(colour, (width, height))


def _fetcher(*, robots: str = "User-agent: *\nAllow: /\n") -> SafeFetcher:
    routes: dict[str, tuple[int, str, bytes]] = {
        SITE: (200, "text/html", HOME.encode()),
        f"{SITE}site.css": (200, "text/css", CSS.encode()),
        # soft 404: an HTML page with status 200 where the logo should be
        f"{SITE}logo-missing.png": (200, "text/html", b"<!doctype html><p>Not found</p>"),
        f"{SITE}touch.png": (200, "image/png", TOUCH_PNG),
        COMPETITOR: (200, "text/html", RIVAL.encode()),
        f"{COMPETITOR}rival-logo.png": (200, "image/png", RIVAL_PNG),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        hit = routes.get(str(request.url))
        if hit is None:
            return httpx.Response(404, text="nope")
        status, ctype, body = hit
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return SafeFetcher("TestBot/1.0", transport=httpx.MockTransport(handler), min_interval_s=0)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        demo_mode=True,
        data_dir=Path("./.test-data"),  # type: ignore[call-arg]
        sender_business_name="Test Studio",
    )


async def _lead(
    *,
    agents: list[str] | None = None,
    website: str | None = SITE,
    contact: bool = True,
    competitor_audit: bool = True,
) -> uuid.UUID:
    async with session_scope() as s:
        biz = Business(
            name="Smith Plumbing Ltd",
            normalized_name="smith plumbing",
            domain="smith-plumbing.com" if website else None,
            country="US",
            address="Austin, TX",
            industry="home_services",
            has_website=bool(website),
        )
        s.add(biz)
        await s.flush()
        lead = Lead(
            business_id=biz.id,
            source="rss",
            source_url="https://forum.test/1",
            source_item_id=uuid.uuid4().hex,
            trigger_type="explicit_request",
            country="US",
            language="en",
            status="contact_found",
            pipeline_state={"contact_id": str(uuid.uuid4())} if contact else {},
        )
        s.add(lead)
        await s.flush()
        s.add(
            Brief(
                lead_id=lead.id,
                data={
                    "agents_to_run": agents
                    or ["contact_enrichment", "branding", "quality_check", "proposal"],
                    "services": ["logo", "redesign"],
                    "existing_website": website,
                    "location": "Austin, TX",
                    "business_type": "plumber",
                },
            )
        )
        if competitor_audit:
            s.add(
                Audit(
                    lead_id=lead.id,
                    url=SITE,
                    competitor_comparison=[{"domain": "rival-plumbers.com", "url": COMPETITOR}],
                )
            )
        return lead.id


async def _run(lead_id: uuid.UUID, fetcher: SafeFetcher | None = None) -> AgentOutcome:
    settings = _settings()
    adapters = build_adapters(settings)
    adapters.fetcher = fetcher or _fetcher()
    async with session_scope() as s:
        return await runs.execute(
            AgentContext(s, adapters, settings), BrandingAgent(browser_factory=FakeBrowser), lead_id
        )


async def _profile(lead_id: uuid.UUID) -> BrandProfile:
    async with session_scope() as s:
        lead = await s.get(Lead, lead_id)
        assert lead is not None
        return (
            await s.execute(
                select(BrandProfile).where(BrandProfile.business_id == lead.business_id)
            )
        ).scalar_one()


async def test_extracts_brand_with_evidence_for_every_value() -> None:
    lead_id = await _lead()
    outcome = await _run(lead_id)
    assert outcome.proceed, outcome.reason
    assert outcome.status == LeadStatus.BRANDED
    p = await _profile(lead_id)

    # 3 logo concepts: sanitised SVG + PNGs, exact wording without "Ltd", similarity checked
    async with session_scope() as s:
        concepts = (
            (
                await s.execute(
                    select(LogoConcept)
                    .where(LogoConcept.lead_id == lead_id)
                    .order_by(LogoConcept.variant)
                )
            )
            .scalars()
            .all()
        )
    assert [c.variant for c in concepts] == [1, 2, 3]
    assert {c.typography["style"] for c in concepts} == {"wordmark", "icon_wordmark", "emblem"}
    for c in concepts:
        assert c.svg_path and c.svg_path.endswith(".svg") and c.png_path.endswith("-1024.png")
        assert c.text == "Smith Plumbing" and c.language == "en"
        assert c.similarity["checked_against"] == 1 and c.similarity["text_check"] == "exact"
        assert c.qa_status == "pending"

    brand = [c for c in p.colors if c["role"] != "neutral"]
    assert brand[0] == {"hex": "#0b5fa5", "role": "primary", "weight": brand[0]["weight"]}
    assert any(c["hex"] == "#f28c28" for c in brand)
    assert {f["family"] for f in p.fonts} >= {"Poppins", "Open Sans"}
    assert p.brand_name == "Smith Plumbing Ltd"  # registered name stays authoritative
    assert p.industry == "home_services"
    assert p.social_profiles == [
        {"network": "facebook", "url": "https://www.facebook.com/smithplumbing"}
    ]

    # The soft-404 "logo" was rejected; the apple-touch-icon PNG was kept as the logo.
    assert p.logo_path and p.logo_path.endswith(".png")
    logo_src = next(src for src in p.sources if src["field"] == "logo")
    assert logo_src["source"] == f"{SITE}touch.png"

    # Competitor style + logo collected for the trademark-similarity check.
    assert [c["domain"] for c in p.competitor_styles] == ["rival-plumbers.com"]
    assert p.competitor_styles[0]["colors"] == ["#c81e1e"]
    assert len(p.competitor_logos) == 1

    async with session_scope() as s:
        ev_ids = {
            str(e)
            for e in (
                await s.execute(select(Evidence.id).where(Evidence.lead_id == lead_id))
            ).scalars()
        }
    assert p.sources and all(src["evidence_id"] in ev_ids for src in p.sources)
    assert str(p.logo_evidence_id) in ev_ids
    css_ev = next(src for src in p.sources if src["value"] == "Open Sans")["evidence_id"]
    assert css_ev != next(src for src in p.sources if src["field"] == "logo")["evidence_id"]


async def test_rerun_is_idempotent() -> None:
    lead_id = await _lead()
    await _run(lead_id)
    first = await _profile(lead_id)
    async with session_scope() as s:
        n_ev = (await s.execute(select(func.count(Evidence.id)))).scalar_one()
    # A fresh agent run on the same lead (orchestrator would skip; call the agent directly).
    settings = _settings()
    adapters = build_adapters(settings)
    adapters.fetcher = _fetcher()
    async with session_scope() as s:
        await BrandingAgent(browser_factory=FakeBrowser).run(
            AgentContext(s, adapters, settings), lead_id
        )
    second = await _profile(lead_id)
    async with session_scope() as s:
        assert (await s.execute(select(func.count(BrandProfile.id)))).scalar_one() == 1
        assert (await s.execute(select(func.count(Evidence.id)))).scalar_one() == n_ev
    assert second.id == first.id and second.logo_evidence_id == first.logo_evidence_id


async def test_no_website_builds_profile_from_brief() -> None:
    lead_id = await _lead(website=None, competitor_audit=False)
    outcome = await _run(lead_id)
    assert outcome.proceed and "no website" in outcome.reason
    p = await _profile(lead_id)
    assert p.brand_name == "Smith Plumbing Ltd"
    assert p.colors == [] and p.logo_path is None


async def test_robots_disallow_is_respected() -> None:
    lead_id = await _lead(competitor_audit=False)
    outcome = await _run(lead_id, _fetcher(robots="User-agent: *\nDisallow: /\n"))
    assert outcome.proceed
    p = await _profile(lead_id)
    assert p.colors == [] and p.logo_path is None and p.sources == []


async def test_gating_contact_route_and_brief() -> None:
    no_contact = await _lead(contact=False, website=None, competitor_audit=False)
    outcome = await _run(no_contact)
    assert not outcome.proceed and outcome.status == LeadStatus.NO_CONTACT_ROUTE

    not_needed = await _lead(agents=["contact_enrichment", "audit", "proposal"])
    outcome = await _run(not_needed)
    assert outcome.proceed and "not needed" in outcome.reason
    async with session_scope() as s:
        assert (await s.execute(select(func.count(BrandProfile.id)))).scalar_one() == 0


def test_logo_format_rejects_non_images() -> None:
    assert logo_format(LOGO_PNG) == "png"
    assert logo_format(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>') == "svg"
    assert logo_format(b"<!doctype html><html></html>") is None
    assert logo_format(_png((0, 0, 0), (8, 8))) is None  # too small to be a logo
    assert logo_format(b"\x00" * 100) is None
    huge: Any = b"\x89PNG" + b"0" * (3 * 1024 * 1024)
    assert logo_format(huge) is None
