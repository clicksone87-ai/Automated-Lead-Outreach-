"""Attachment size policy with generated large images: every email fits Gmail's 25 MB."""

from __future__ import annotations

import io
import os
from email import policy as email_policy
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.adapters.email import EmailAttachment, OutgoingEmail
from app.adapters.email_free import build_mime
from app.adapters.storage import LocalStorage
from app.services import attachments as policy
from app.services.attachments import ENCODED_BUDGET, GMAIL_MESSAGE_LIMIT, Item


def noise_png(width: int, height: int) -> bytes:
    """Random pixels barely compress, so the PNG is about width x height x 3 bytes."""
    img = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def logo_png() -> bytes:
    """A flat logo with transparency: small, like the real 512 px exports."""
    img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((40, 40, 472, 472), fill=(11, 95, 165, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(size: int) -> bytes:
    return b"%PDF-1.4\n" + os.urandom(size)


@pytest.fixture(scope="module")
def big_screens() -> tuple[bytes, bytes]:
    # Full-page screenshots: 1440 x 4200 desktop (~18 MB) and 780 x 5000 mobile (~12 MB).
    return noise_png(1440, 4200), noise_png(780, 5000)


def _proposal_items(desktop: bytes, mobile: bytes, *, audit: int = 2_000_000) -> list[Item]:
    logo = logo_png()
    return [
        Item("logo", "logo-concept-1.png", "image/png", logo),
        Item("logo", "logo-concept-2.png", "image/png", logo),
        Item("preview_desktop", "website-preview-desktop.png", "image/png", desktop),
        Item("preview_mobile", "website-preview-mobile.png", "image/png", mobile),
        Item("preview_pdf", "website-preview.pdf", "application/pdf", pdf_bytes(6_000_000)),
        Item("audit_pdf", "website-audit.pdf", "application/pdf", pdf_bytes(audit)),
    ]


def _mime_size(items: list[Item]) -> int:
    """The message as it goes over SMTP/IMAP (CRLF lines, base64 attachments)."""
    email = OutgoingEmail(
        from_address="Agency <agency@gmail.com>", to="owner@example.com", subject="Drafts",
        html="<p>" + "Hello " * 2000 + "</p>", text="Hello " * 2000, idempotency_key="p:0",
        attachments=[EmailAttachment(filename=i.filename, content=i.data,
                                     content_type=i.content_type) for i in items],
    )  # fmt: skip
    return len(build_mime(email, "<x@gmail.com>").as_bytes(policy=email_policy.SMTP))


def test_encoded_size_matches_real_mime() -> None:
    items = [Item("audit_pdf", "a.pdf", "application/pdf", pdf_bytes(3_000_000))]
    real = _mime_size(items)
    assert policy.encoded_total(items) <= real < policy.encoded_total(items) + 30_000


def test_small_attachments_are_untouched() -> None:
    items = [
        Item("preview_desktop", "d.png", "image/png", noise_png(200, 300)),
        Item("audit_pdf", "a.pdf", "application/pdf", pdf_bytes(1000)),
    ]
    fitted = policy.fit(items)
    assert not fitted.changed and fitted.items == items


def test_large_screenshots_are_compressed_to_fit(big_screens: tuple[bytes, bytes]) -> None:
    items = _proposal_items(*big_screens)
    assert _mime_size(items) > GMAIL_MESSAGE_LIMIT  # would bounce as it is
    fitted = policy.fit(items)
    assert policy.encoded_total(fitted.items) <= ENCODED_BUDGET
    assert _mime_size(fitted.items) <= GMAIL_MESSAGE_LIMIT
    kinds = [i.kind for i in fitted.items]
    assert "preview_desktop" in kinds  # the main screenshot always travels
    desktop = next(i for i in fitted.items if i.kind == "preview_desktop")
    assert desktop.content_type == "image/jpeg" and desktop.filename.endswith(".jpg")
    with Image.open(io.BytesIO(desktop.data)) as img:
        assert img.width <= 1600 and img.height <= 6000
    assert any("compressed from" in a for a in fitted.actions)
    # Logos are small: never re-encoded, so their transparency survives.
    assert [i.data for i in fitted.items if i.kind == "logo"] == [items[0].data, items[1].data]


def test_merge_then_drop_in_a_fixed_order(big_screens: tuple[bytes, bytes]) -> None:
    items = _proposal_items(*big_screens, audit=4_000_000)
    budget = 10_000_000  # compression alone is not enough: merge, then drop
    first = policy.fit(items, budget)
    second = policy.fit(items, budget)
    assert [i.filename for i in first.items] == [i.filename for i in second.items]
    assert first.actions == second.actions  # deterministic
    assert policy.encoded_total(first.items) <= budget
    merged = [i for i in first.items if i.kind == "merged"]
    assert merged and merged[0].filename == "drafts.pdf"
    assert set(merged[0].contains) == {"logo", "preview_mobile"}
    assert any("merged into drafts.pdf" in a for a in first.actions)
    dropped = [a.split(":")[0] for a in first.actions if "left out" in a]
    assert dropped == ["website-preview.pdf", "website-audit.pdf"]  # least important first
    assert [i.kind for i in first.items] == ["merged", "preview_desktop"]


def test_drop_order_keeps_the_screenshot_over_pdfs() -> None:
    items = [
        Item("preview_desktop", "d.png", "image/png", noise_png(200, 300)),
        Item("audit_pdf", "a.pdf", "application/pdf", pdf_bytes(9_000_000)),
        Item("preview_pdf", "p.pdf", "application/pdf", pdf_bytes(9_000_000)),
    ]
    fitted = policy.fit(items, 13_000_000)
    assert [i.filename for i in fitted.items] == ["d.png", "a.pdf"]
    assert fitted.actions == ["p.pdf: left out (9.0 MB) to fit 25 MB"]


def test_an_oversized_main_screenshot_is_shrunk_not_dropped() -> None:
    items = [Item("preview_desktop", "d.png", "image/png", noise_png(1400, 3000))]
    fitted = policy.fit(items, 400_000)
    assert len(fitted.items) == 1 and fitted.items[0].kind == "preview_desktop"
    assert policy.encoded_total(fitted.items) <= 400_000
    assert any("shrunk to" in a for a in fitted.actions)


def test_prepare_stores_changed_files_and_reports(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    desktop = storage.save_bytes("previews/x/screens/desktop.png", noise_png(1440, 9000))
    audit = storage.save_bytes("audits/x/audit.pdf", pdf_bytes(500_000))
    records = [
        {"kind": "preview_desktop", "path": desktop, "filename": "website-preview-desktop.png",
         "content_type": "image/png"},
        {"kind": "audit_pdf", "path": audit, "filename": "website-audit.pdf",
         "content_type": "application/pdf"},
        {"kind": "logo", "path": "logos/missing.png", "filename": "logo.png",
         "content_type": "image/png"},
    ]  # fmt: skip
    out, report = policy.prepare(storage, records, folder="outbox/x")
    assert [a["kind"] for a in out] == ["preview_desktop", "audit_pdf"]  # missing file left out
    shot = out[0]
    assert shot["path"] == "outbox/x/website-preview-desktop.jpg" and storage.exists(shot["path"])
    assert shot["bytes"] == len(storage.read_bytes(shot["path"])) and "compressed" in shot["note"]
    assert out[1]["path"] == audit  # unchanged files keep their original path
    assert report["count"] == 2 and report["bytes"] == shot["bytes"] + out[1]["bytes"]
    assert report["original_bytes"] > report["bytes"] and report["within_gmail_limit"]
    assert report["actions"] and report["mb"] == round(report["bytes"] / 1_000_000, 2)


def test_report_measures_older_records_on_disk(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    path = storage.save_bytes("a/audit.pdf", b"x" * 2_500_000)
    report = policy.report_for([{"kind": "audit_pdf", "path": path, "filename": "a.pdf"}], storage)
    assert report == {"count": 1, "bytes": 2_500_000, "encoded_bytes": policy.encoded_size(2_500_000),
                      "mb": 2.5, "within_gmail_limit": True}  # fmt: skip


def test_attached_kinds_and_template_flags_look_inside_the_merged_pdf() -> None:
    records = [
        {"kind": "preview_desktop"},
        {"kind": "merged", "contains": ["logo", "preview_mobile"]},
    ]
    assert policy.attached_kinds(records) == {"preview_desktop", "logo", "preview_mobile"}
    assert policy.template_flags(records) == {"logos": True, "preview": True, "audit": False}
