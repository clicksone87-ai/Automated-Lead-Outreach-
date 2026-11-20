"""Manual Track B outreach queue invariants."""

import uuid
from pathlib import Path

from app.agents.contact_enrichment.agent import track_for_contact
from app.db.models import Contact, ContactType, VerificationStatus


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
