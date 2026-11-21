"""The "Not useful" bucket: the mapping of old stopped statuses, the tab's API, and its absence
from every working view and count."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.db.models import ApprovalStatus, Business, Lead, LeadStatus, ManualOutreach, Proposal
from app.db.session import session_scope
from app.services import not_useful_leads
from app.services.not_useful import NotUsefulReason, mark

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

MIGRATION = (
    Path(__file__).resolve().parents[2] / "app/db/migrations/versions/20261121_map_not_useful.py"
)
EXCLUDED = ["PK", "IN", "BD"]

# (status, status_reason, prefilter_reason, country) -> expected (status, not_useful_reason)
CASES: dict[str, tuple[tuple[str, str | None, str | None, str | None], tuple[str, str | None]]] = {
    "stale": (
        ("rejected_low_intent", "prefilter: post is 40 h old (limit 8 h)",
         "post is 40 h old (limit 8 h)", "US"),
        ("not_useful", "older_than_8h"),
    ),
    "undated": (
        ("rejected_low_intent", "prefilter: social post without a timestamp",
         "social post without a timestamp", "US"),
        ("not_useful", "older_than_8h"),
    ),
    "seller": (
        ("rejected_low_intent", "prefilter: seller", "seller offering services", "US"),
        ("not_useful", "low_intent"),
    ),
    "low_score": (("rejected_low_intent", "intent 20 < minimum 60", None, "GB"),
                  ("not_useful", "low_intent")),
    "no_route": (("no_contact_route", "no verified email, contact form or platform route found",
                  None, "US"), ("not_useful", "no_contact")),
    "no_reason": (("no_contact_route", None, None, "US"), ("not_useful", "no_contact")),
    "excluded_text": (("parked_country", "PK is an excluded market", None, "PK"),
                      ("not_useful", "excluded_market")),
    "excluded_code": (("parked_country", "policy: country rules", None, "IN"),
                      ("not_useful", "excluded_market")),
    "not_targeted": (("parked_country", "DE not a target country", None, "DE"),
                     ("parked_country", None)),
    "unknown_country": (("parked_country", "country unknown", None, None),
                        ("parked_country", None)),
    "language": (("parked_language", "language ja not enabled", None, "JP"),
                 ("parked_language", None)),
    "cooldown": (("cooldown", "cooldown until 2026-12-01", None, "US"), ("cooldown", None)),
    "duplicate": (("duplicate", "business already in pipeline", None, "US"), ("duplicate", None)),
    "suppressed": (("suppressed", "unsubscribed", None, "US"), ("suppressed", None)),
    "working": (("awaiting_approval", "proposal v1 waiting", None, "US"),
                ("awaiting_approval", None)),
}  # fmt: skip


async def _seed_cases() -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    async with session_scope() as s:
        for key, ((status, reason, prefilter, country), _) in CASES.items():
            lead = Lead(source="rss", source_url=f"https://forum.test/{key}", source_item_id=key,
                        trigger_type="explicit_request", status=status, status_reason=reason,
                        prefilter_reason=prefilter, country=country)  # fmt: skip
            s.add(lead)
            await s.flush()
            ids[key] = lead.id
    return ids


async def _states(ids: dict[str, uuid.UUID]) -> dict[str, tuple[str, str | None, str | None]]:
    async with session_scope() as s:
        rows = (await s.execute(select(Lead).where(Lead.id.in_(ids.values())))).scalars()
        by_id = {lead.id: lead for lead in rows}
        return {
            key: (by_id[lid].status, by_id[lid].not_useful_reason, by_id[lid].status_reason)
            for key, lid in ids.items()
        }


def _check(states: dict[str, tuple[str, str | None, str | None]]) -> None:
    for key, (_, expected) in CASES.items():
        assert states[key][:2] == expected, key


async def test_map_existing_moves_only_never_useful_statuses_and_is_idempotent() -> None:
    ids = await _seed_cases()
    async with session_scope() as s:
        before = {lead.id: lead.updated_at for lead in (await s.execute(select(Lead))).scalars()}
        moved = await not_useful_leads.map_existing(s, EXCLUDED)
    assert moved == {"no_contact": 2, "older_than_8h": 2, "freelance": 0,
                     "excluded_market": 2, "low_intent": 2}  # fmt: skip
    states = await _states(ids)
    _check(states)
    assert states["stale"][2] == (
        "not useful: older than 8 h (prefilter: post is 40 h old (limit 8 h))"
    )
    assert states["no_reason"][2] == "not useful: no contact route"
    async with session_scope() as s:
        after = {lead.id: lead.updated_at for lead in (await s.execute(select(Lead))).scalars()}
        assert after == before  # the time a lead stopped is kept
        again = await not_useful_leads.map_existing(s, EXCLUDED)
    assert sum(again.values()) == 0
    assert await _states(ids) == states


def _migration() -> Any:
    spec = importlib.util.spec_from_file_location("map_not_useful_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_migration_maps_the_same_way_and_twice_is_harmless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration()
    assert migration.down_revision == "8d1c4e7a2b90"
    monkeypatch.setenv("EXCLUDED_COUNTRIES", "pk, in,BD")
    ids = await _seed_cases()
    for _ in range(2):
        async with session_scope() as s:
            for sql in migration.STATEMENTS:
                params = {"excluded": migration.excluded_countries()} if ":excluded" in sql else {}
                await s.execute(sa.text(sql), params)
    states = await _states(ids)
    _check(states)
    assert states["excluded_text"][2] == "not useful: excluded market (PK is an excluded market)"


async def test_going_forward_mark_records_reason_and_label() -> None:
    async with session_scope() as s:
        lead = Lead(source="rss", source_url="https://forum.test/x", source_item_id="x",
                    trigger_type="explicit_request")  # fmt: skip
        s.add(lead)
        outcome = not_useful_leads.stop(lead, NotUsefulReason.NO_CONTACT, "nothing found", {"a": 1})
    assert not outcome.proceed and outcome.status == LeadStatus.NOT_USEFUL
    assert outcome.reason == "not useful: no contact route (nothing found)"
    assert lead.not_useful_reason == "no_contact" and outcome.output == {"a": 1}


# ── the tab and the working views ───────────────────────────────────────────


async def _lead(
    s: Any, key: str, *, status: str = LeadStatus.AWAITING_APPROVAL, score: int = 80,
    reason: NotUsefulReason | None = None, source: str = "social_x",
) -> Lead:  # fmt: skip
    biz = Business(name=f"Biz {key}", normalized_name=f"biz {key}", country="US")
    s.add(biz)
    await s.flush()
    lead = Lead(business_id=biz.id, source=source, source_url=f"https://x.test/{key}",
                source_item_id=key, trigger_type="explicit_request", status=status,
                intent_score=score, country="US", language="en", title=f"Lead {key}")  # fmt: skip
    if reason is not None:
        mark(lead, reason, f"detail {key}")
    s.add(lead)
    await s.flush()
    return lead


def _proposal(lead: Lead, *, email: str | None, track_b: bool = False) -> Proposal:
    draft: dict[str, Any] = {}
    if track_b:
        draft["track_b"] = {"channel": "platform_reply", "route": lead.source_url,
                            "comment": "c", "dm": "d", "contact_form": "f"}  # fmt: skip
    return Proposal(lead_id=lead.id, version=1, language="en", subject="Hi", body_html="<p>Hi</p>",
                    body_text="Hi", approval_status=ApprovalStatus.PENDING, to_email=email,
                    draft=draft)  # fmt: skip


async def test_not_useful_tab_lists_by_reason_and_never_shows_in_working_views() -> None:
    from app.main import create_app  # noqa: PLC0415

    async with session_scope() as s:
        good = await _lead(s, "good")
        s.add(_proposal(good, email="owner@good.example"))
        manual = await _lead(s, "manual", score=70)
        s.add(_proposal(manual, email=None, track_b=True))
        gone_contact = await _lead(s, "nc1", score=90, reason=NotUsefulReason.NO_CONTACT)
        s.add(_proposal(gone_contact, email="x@nc.example"))
        gone_manual = await _lead(s, "nc2", score=90, reason=NotUsefulReason.NO_CONTACT)
        s.add(_proposal(gone_manual, email=None, track_b=True))
        await _lead(s, "low", score=10, reason=NotUsefulReason.LOW_INTENT)
        await _lead(s, "pk", reason=NotUsefulReason.EXCLUDED_MARKET)
        legacy = await _lead(s, "legacy")
        legacy.status = LeadStatus.NOT_USEFUL  # a reason this code does not know about yet
        legacy.not_useful_reason = None

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        counts = (await client.get("/api/not-useful/counts")).json()
        by_reason = {r["reason"]: r["count"] for r in counts["reasons"]}
        assert by_reason == {"no_contact": 2, "older_than_8h": 0, "freelance": 0,
                             "excluded_market": 1, "low_intent": 1, "unknown": 1}  # fmt: skip
        assert counts["total"] == 5
        labels = {r["reason"]: r["label"] for r in counts["reasons"]}
        assert labels["no_contact"] == "no contact route"

        page = (await client.get("/api/not-useful", params={"reason": "no_contact"})).json()
        assert page["total"] == 2
        assert {i["title"] for i in page["items"]} == {"Lead nc1", "Lead nc2"}
        assert all(i["reason_label"] == "no contact route" for i in page["items"])
        assert page["items"][0]["status_reason"].startswith("not useful: no contact route")
        everything = (await client.get("/api/not-useful")).json()
        assert everything["total"] == 5
        assert (await client.get("/api/not-useful", params={"reason": "unknown"})).json()[
            "total"
        ] == 1
        assert (await client.get("/api/not-useful", params={"q": "Lead pk"})).json()["total"] == 1
        bad = await client.get("/api/not-useful", params={"reason": "x'; drop"})
        assert bad.status_code == 422

        # working views: leads list, family/tier counts, overview, approvals, manual outreach
        leads = (await client.get("/api/leads", params={"page_size": 200})).json()
        assert {i["title"] for i in leads["items"]} == {"Lead good", "Lead manual"}
        stopped = (await client.get("/api/leads", params={"stage": "stopped"})).json()
        assert stopped["total"] == 0
        explicit = (await client.get("/api/leads", params={"status": "not_useful"})).json()
        assert explicit["total"] == 5
        tiers = (await client.get("/api/lead-counts")).json()
        social = next(r for r in tiers if r["family"] == "social")
        assert (social["high"], social["medium"], social["low"]) == (1, 1, 0)
        overview = (await client.get("/api/overview")).json()
        assert "not_useful" not in overview["stopped"]
        assert overview["stages"][0]["reached"] == 2
        assert overview["leads_7d"] == 2  # leads_today depends on the clock (midnight UTC)
        assert overview["awaiting_approval"] == 1  # Track A only, working leads only
        approvals = (await client.get("/api/approvals")).json()
        assert [a["business_name"] for a in approvals] == ["Biz good"]
        queue = (await client.get("/api/manual-outreach")).json()
        assert [q["business_name"] for q in queue] == ["Biz manual"]
        report = (await client.get("/api/reports/live")).json()
        assert report["funnel"]["discovered"] == 2

    async with session_scope() as s:  # the queue row exists, it is only hidden
        rows = (await s.execute(select(ManualOutreach))).scalars().all()
        assert {r.lead_id for r in rows} == {manual.id, gone_manual.id}
