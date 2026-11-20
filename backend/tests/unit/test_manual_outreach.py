"""Manual Track B outreach queue invariants."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.contact_enrichment.agent import track_for_contact
from app.db.models import (
    Business,
    Contact,
    ContactType,
    Lead,
    ManualOutreach,
    ManualOutreachStatus,
    Proposal,
    VerificationStatus,
)
from app.db.session import session_scope
from app.services.manual_outreach import _track_b_drafts, set_status, sync_queue


def test_manual_outreach_has_no_posting_adapter_call_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    scanned = [
        (root / "app" / "services" / "manual_outreach.py").read_text(encoding="utf-8"),
        (root / "app" / "api" / "outreach_routes.py").read_text(encoding="utf-8"),
    ]
    forbidden = ("requests.post", "httpx.post", "send_dm", "post_comment", "submit_comment")
    text = "\n".join(scanned).lower()
    assert not any(token in text for token in forbidden)


def test_track_routing_verified_email_is_track_a() -> None:
    contact = Contact(
        id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        type=ContactType.EMAIL,
        value="owner@published.example",
        source_url="https://published.example/contact",
        verification_status=VerificationStatus.VALID,
    )
    assert track_for_contact(contact) == "track_a"


def test_track_routing_disposable_or_unverified_is_track_b() -> None:
    disposable = Contact(
        id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        type=ContactType.EMAIL,
        value="owner@mailinator.com",
        source_url="https://published.example",
        verification_status=VerificationStatus.VALID,
    )
    unverified = Contact(
        id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        type=ContactType.PLATFORM_REPLY,
        value="@owner",
        source_url="https://x.example/post",
        verification_status=VerificationStatus.UNVERIFIED,
    )
    assert track_for_contact(disposable) == "track_b"
    assert track_for_contact(unverified) == "track_b"


def test_track_b_drafts_create_queue_payload() -> None:
    proposal = Proposal(
        lead_id=uuid.uuid4(),
        version=1,
        language="en",
        subject="Hello",
        body_html="<p>Hello</p>",
        body_text="Hello",
        draft={
            "track_b": {
                "channel": "platform_reply",
                "route": "https://x.example/post/123",
                "comment": "Useful comment",
                "dm": "Useful DM",
                "contact_form": "Useful form message",
            }
        },
    )
    payload = _track_b_drafts(proposal)
    assert payload is not None
    assert payload["channel"] == "platform_reply"
    assert payload["comment"] == "Useful comment"
    assert payload["post_link"] == "https://x.example/post/123"


@pytest.mark.db
@pytest.mark.usefixtures("db")
async def test_skipped_queue_status_survives_sync(tmp_path) -> None:
    del tmp_path
    async with session_scope() as s:
        business = Business(name="Skip Test", normalized_name="skip test", country="US")
        s.add(business)
        await s.flush()
        lead = Lead(
            business_id=business.id,
            source="social_x",
            source_url="https://x.example/post/status",
            source_item_id="status-test",
            trigger_type="explicit_request",
            status="awaiting_approval",
            country="US",
            language="en",
        )
        s.add(lead)
        await s.flush()
        proposal = Proposal(
            lead_id=lead.id,
            version=1,
            language="en",
            subject="Hello",
            body_html="<p>Hello</p>",
            body_text="Hello",
            draft={
                "track_b": {
                    "channel": "platform_reply",
                    "route": "https://x.example/post/status",
                    "comment": "Comment",
                    "dm": "DM",
                    "contact_form": "Form",
                }
            },
        )
        s.add(proposal)
        await s.flush()
        await sync_queue(s)
        item = (
            await s.execute(
                select(ManualOutreach).where(ManualOutreach.lead_id == lead.id)
            )
        ).scalar_one()
        assert item is not None
        await set_status(s, item.id, ManualOutreachStatus.SKIPPED, "test")
        await sync_queue(s)
        await s.flush()
        saved = await s.get(ManualOutreach, item.id)
        assert saved is not None
        assert saved.status == ManualOutreachStatus.SKIPPED
