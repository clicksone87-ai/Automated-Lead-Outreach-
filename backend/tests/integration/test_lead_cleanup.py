"""clean-leads: old or undated social posts and freelance-platform leads move to "Not useful"
with their reason; everything else, and leads a person already acted on, stay as they are."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import Lead, LeadStatus
from app.db.session import session_scope
from app.services import lead_cleanup

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

FOUND = datetime.now(UTC) - timedelta(days=2)


def _lead(name: str, source: str, url: str, posted: timedelta | None, **kw: Any) -> Lead:
    """A lead stored at FOUND whose post was `posted` old at that moment."""
    return Lead(
        title=name,
        source=source,
        source_url=url,
        source_item_id=uuid.uuid4().hex,
        trigger_type=kw.pop("trigger_type", "explicit_request"),
        status=kw.pop("status", LeadStatus.DISCOVERED),
        posted_at=FOUND - posted if posted is not None else None,
        created_at=FOUND,
        pipeline_state={},
        **kw,
    )


FIXTURES = [
    # name, source, url, post age when found, expected reason (None = untouched)
    ("fresh_fb", "social_facebook", "https://www.facebook.com/a/posts/1", timedelta(hours=2), None),
    ("old_x", "social_x", "https://x.com/a/status/2", timedelta(hours=30), "older_than_8h"),
    ("undated_reddit", "social_reddit", "https://www.reddit.com/r/a/comments/3/", None,
     "older_than_8h"),
    ("old_provider", "social_providers", "https://www.tiktok.com/@a/video/4", timedelta(hours=9),
     "older_than_8h"),
    ("old_search_post", "search_api", "https://www.reddit.com/r/b/comments/5/", timedelta(days=3),
     "older_than_8h"),
    ("undated_page", "search_api", "https://www.bakery.co.uk/blog/need-a-site", None, None),
    ("freelancer", "freelancer", "https://www.freelancer.com/projects/web/x", timedelta(hours=1),
     "freelance"),
    ("alert", "alert_emails", "mid:abc@mail", None, "freelance"),
    ("upwork_via_search", "search_api", "https://www.upwork.com/jobs/~01abc", None, "freelance"),
    ("registry", "companies_house", "https://find-and-update.company-information.service.gov.uk/c",
     None, None),
]  # fmt: skip


async def _seed() -> None:
    async with session_scope() as s:
        s.add_all(_lead(name, src, url, age) for name, src, url, age, _ in FIXTURES)
        s.add(_lead("scout", "scout_profiles", "https://www.tiktok.com/@leedsplumbing", None,
                    trigger_type="profile_without_site"))  # fmt: skip
        s.add(_lead("old_but_sent", "social_x", "https://x.com/a/status/9", timedelta(days=1),
                    status=LeadStatus.SENT))  # fmt: skip
        s.add(_lead("already", "freelancer", "https://www.freelancer.com/projects/y",
                    None, status=LeadStatus.NOT_USEFUL, not_useful_reason="no_contact"))  # fmt: skip


async def _state() -> dict[str, tuple[str, str | None, str | None]]:
    async with session_scope() as s:
        rows = (await s.execute(select(Lead))).scalars().all()
        return {r.title or "": (str(r.status), r.not_useful_reason, r.status_reason) for r in rows}


async def test_cleanup_moves_old_social_and_freelance_leads_once() -> None:
    await _seed()
    async with session_scope() as s:
        dry = await lead_cleanup.clean_leads(s, dry_run=True)
    # "already" was filed as no contact; freelance is a source rule and comes first.
    assert (dry.older_than_8h, dry.freelance, dry.kept_acted_on, dry.relabelled) == (4, 4, 1, 1)
    assert all(status != "not_useful" for name, (status, _, _) in (await _state()).items()
               if name != "already")  # fmt: skip

    async with session_scope() as s:
        result = await lead_cleanup.clean_leads(s)
    assert (result.older_than_8h, result.freelance, result.kept_acted_on, result.relabelled) == (
        4,
        4,
        1,
        1,
    )
    assert result.by_source["social_x:older_than_8h"] == 1
    state = await _state()
    for name, *_, expected in FIXTURES:
        status, reason, _ = state[name]
        if expected is None:
            assert status != LeadStatus.NOT_USEFUL, name
        else:
            assert (status, reason) == (LeadStatus.NOT_USEFUL, expected), name
    assert state["old_x"][2] == "not useful: older than 8 h (post was 30 h old)"
    assert state["already"][1] == "freelance"  # a source rule beats an earlier weaker reason
    assert state["undated_reddit"][2] == "not useful: older than 8 h (post has no timestamp)"
    assert state["freelancer"][2] == "not useful: freelance platform (from freelancer)"
    assert state["scout"][0] == LeadStatus.DISCOVERED  # a profile, not a post
    assert state["old_but_sent"][0] == LeadStatus.SENT  # outreach history is kept

    async with session_scope() as s:  # idempotent: nothing left to move
        again = await lead_cleanup.clean_leads(s)
    assert (again.older_than_8h, again.freelance) == (0, 0)
    assert await _state() == state


async def test_stale_posts_filed_as_low_intent_are_relabelled_by_age() -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from app.services import not_useful  # noqa: PLC0415
    from app.services.not_useful import NotUsefulReason  # noqa: PLC0415

    now = datetime.now(UTC)
    async with session_scope() as s:
        stale = Lead(source="social_reddit", source_family="social", source_url="https://www.reddit.com/r/x/comments/1/a/",
                     source_item_id="r1", trigger_type="explicit_request", pipeline_state={},
                     posted_at=now - timedelta(hours=30), created_at=now)  # fmt: skip
        fresh = Lead(source="social_reddit", source_family="social", source_url="https://www.reddit.com/r/x/comments/2/b/",
                     source_item_id="r2", trigger_type="explicit_request", pipeline_state={},
                     posted_at=now - timedelta(hours=2), created_at=now)  # fmt: skip
        s.add_all([stale, fresh])
        await s.flush()
        not_useful.mark(stale, NotUsefulReason.LOW_INTENT)
        not_useful.mark(fresh, NotUsefulReason.LOW_INTENT)
    async with session_scope() as s:
        first = await lead_cleanup.clean_leads(s)
    async with session_scope() as s:
        again = await lead_cleanup.clean_leads(s)
        rows = {lead.source_item_id: lead for lead in (await s.execute(select(Lead))).scalars()}
    assert first.relabelled == 1 and first.older_than_8h == 1
    assert again.relabelled == 0 and again.older_than_8h == 0  # idempotent
    assert rows["r1"].not_useful_reason == "older_than_8h"
    assert rows["r2"].not_useful_reason == "low_intent"  # fresh but low intent stays low intent
