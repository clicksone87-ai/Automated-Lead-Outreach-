"""Local mode: real proposals and follow-ups go out without a single localhost address.

Uses the real React Email templates when an installed copy of their node_modules is available
(EMAIL_TEMPLATES_NODE_MODULES, or the renderer cache under ./data), otherwise the fixture
renderer. The check runs on the complete MIME message as it goes over SMTP/IMAP.
"""

from __future__ import annotations

import asyncio
import email
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email import policy
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import select

from app.adapters.email import DeliveryEvent, OutgoingEmail
from app.adapters.email_free import build_mime
from app.adapters.email_render import (
    EmailRenderer,
    FixtureRenderer,
    ReactEmailRenderer,
    Rendered,
)
from app.adapters.registry import build_adapters
from app.agents.website_builder.build import link_dir
from app.config import Settings
from app.db.models import LeadStatus, Message, Preview, Proposal
from app.db.session import session_scope
from app.services import outreach_jobs
from tests.integration.test_outreach import _approve, _lead, _propose, _send
from tests.integration.test_outreach import _settings as _outreach_settings

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]

LOCAL = ("localhost", "127.0.0.1", ".localhost")
REPO = Path(__file__).resolve().parents[3]


class MimeCapture:
    """Builds every message exactly as an SMTP/IMAP adapter would and keeps the bytes."""

    name: ClassVar[str] = "mime_capture"
    is_mock: ClassVar[bool] = False
    is_sandbox = True

    def __init__(self) -> None:
        self.raw: list[bytes] = []

    async def send(self, outgoing: OutgoingEmail) -> str:
        mid = f"<{uuid.uuid4().hex}@agency.example>"
        self.raw.append(build_mime(outgoing, mid).as_bytes(policy=policy.SMTP))
        return mid

    async def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id=provider_id, status="delivered")


class ThreadedReactRenderer(ReactEmailRenderer):
    """Same templates and render.mjs; node runs in a thread because the suite's Windows
    selector loop (needed by psycopg) cannot start subprocesses."""

    async def render(self, template: str, props: dict[str, Any]) -> Rendered:
        work = await self._workdir()
        done = await asyncio.to_thread(
            subprocess.run,
            ["node", "render.mjs"],
            cwd=work,
            input=json.dumps({"template": template, "props": props}).encode(),
            capture_output=True,
            timeout=60,
            check=True,
        )
        data = json.loads(done.stdout)
        return Rendered(html=data["html"], text=data["text"])


def _installed_node_modules() -> Path | None:
    candidates = [os.environ.get("EMAIL_TEMPLATES_NODE_MODULES", "")]
    candidates += [
        str(p / "node_modules") for p in (REPO / "data" / "email-renderer").glob("email-*")
    ]
    for c in candidates:
        if c and Path(c, "@react-email").exists():
            return Path(c)
    return None


@pytest.fixture
def renderer(tmp_path: Path) -> Iterator[EmailRenderer]:
    """The real templates when they can run offline, else the deterministic fixture."""
    modules = _installed_node_modules()
    templates = REPO / "email-templates"
    if modules is None or shutil.which("node") is None or not templates.exists():
        yield FixtureRenderer()
        return
    digest = hashlib.sha256((templates / "package-lock.json").read_bytes()).hexdigest()[:16]
    work = tmp_path / "email-renderer" / f"email-{digest}"
    work.mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(templates / name, work / name)
    link = work / "node_modules"
    link_dir(link, modules)
    (work / ".ok").write_text(digest)  # installed: no npm ci (no network in tests)
    try:
        yield ThreadedReactRenderer(templates, tmp_path / "email-renderer")
    finally:
        # Remove only the link, so a later tmp cleanup can never walk into the shared install.
        if link.is_symlink():
            link.unlink()
        else:
            os.rmdir(link)  # a Windows junction: removes the link, not the target


def _texts(raw: bytes) -> list[str]:
    """Every header and every decoded text part (bodies may be base64/quoted-printable)."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    out = [f"{k}: {v}" for k, v in msg.items()]
    for part in msg.walk():
        out += [f"{k}: {v}" for k, v in part.items()]
        if part.get_content_maintype() == "text":
            out.append(str(part.get_content()))
    return out


def _assert_no_local(raw: bytes) -> None:
    lowered = raw.decode("ascii", errors="replace").lower()
    for needle in LOCAL:
        assert needle not in lowered, needle
    for text in _texts(raw):
        for needle in LOCAL:
            assert needle not in text.lower(), (needle, text[:300])


def _attachment_names(raw: bytes) -> list[str]:
    msg = email.message_from_bytes(raw, policy=policy.default)
    return [str(p.get_filename()) for p in msg.iter_attachments()]  # type: ignore[attr-defined]


def _local_settings(tmp: Path, **kw: Any) -> Settings:
    # A localhost portfolio is a plausible local setting; it must be left out too.
    return _outreach_settings(tmp).model_copy(
        update={"portfolio_url": "http://localhost:3000", **kw}
    )


async def _store_local_preview_url(lead_id: uuid.UUID) -> None:
    """What the builder stores in local mode; it must never be copied into an email."""
    async with session_scope() as s:
        p = (await s.execute(select(Preview).where(Preview.lead_id == lead_id))).scalar_one()
        p.preview_url = f"http://{lead_id.hex}.preview.localhost:8080/"


async def test_local_mode_proposal_and_follow_up_carry_no_local_address(
    tmp_path: Path, renderer: EmailRenderer
) -> None:
    settings = _local_settings(tmp_path)
    adapters = build_adapters(settings)
    adapters.renderer = renderer
    capture = MimeCapture()
    adapters.email = capture
    lead_id = await _lead(settings)
    await _store_local_preview_url(lead_id)
    await _propose(settings, lead_id, adapters)
    async with session_scope() as s:
        p = (await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))).scalar_one()
        assert p.draft["preview_url"] is None  # no live link without PUBLIC_PREVIEW_BASE_URL
        assert p.draft["unsubscribe_url"].startswith("mailto:")
        assert p.draft["attachment_report"]["count"] == len(p.attachments)
    await _approve(lead_id)
    outcome = await _send(settings, lead_id, capture)
    assert outcome.status == LeadStatus.SENT, outcome.reason

    async with session_scope() as s:
        msg = (await s.execute(select(Message))).scalar_one()
        msg.sent_at = datetime.now(UTC) - timedelta(days=5)
    async with session_scope() as s:
        assert await outreach_jobs.follow_ups(s, settings, adapters) == 1

    proposal_raw, follow_up_raw = capture.raw
    for raw in capture.raw:
        _assert_no_local(raw)
        msg = email.message_from_bytes(raw, policy=policy.default)
        assert msg["List-Unsubscribe"] == "<mailto:replies@test-agency.local?subject=unsubscribe>"
        assert msg["List-Unsubscribe-Post"] is None
    # The recipient still sees the preview: the screenshot travels with the proposal.
    assert "website-preview-desktop.png" in _attachment_names(proposal_raw)
    assert _attachment_names(follow_up_raw) == []


async def test_public_urls_switch_on_live_links_and_keep_the_screenshot(
    tmp_path: Path, renderer: EmailRenderer
) -> None:
    settings = _local_settings(
        tmp_path,
        public_preview_base_url="https://previews.agency.example",
        public_api_base_url="https://api.agency.example",
        portfolio_url="https://agency.example",
    )
    adapters = build_adapters(settings)
    adapters.renderer = renderer
    capture = MimeCapture()
    lead_id = await _lead(settings)
    await _store_local_preview_url(lead_id)
    await _propose(settings, lead_id, adapters)
    async with session_scope() as s:
        p = (await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))).scalar_one()
        live = f"https://previews.agency.example/p/{lead_id.hex}/"
        assert p.draft["preview_url"] == live
        if not isinstance(adapters.renderer, FixtureRenderer):  # fixture HTML has no link
            assert live in p.body_html
    await _approve(lead_id)
    await _send(settings, lead_id, capture)
    raw = capture.raw[0]
    _assert_no_local(raw)
    assert "website-preview-desktop.png" in _attachment_names(raw)
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert "https://api.agency.example/u/" in str(msg["List-Unsubscribe"])


async def test_a_draft_with_a_local_link_is_held_for_review(tmp_path: Path) -> None:
    settings = _local_settings(tmp_path)
    lead_id = await _lead(settings)
    await _propose(settings, lead_id)
    await _approve(lead_id)
    async with session_scope() as s:  # e.g. a draft written before links were checked
        p = (await s.execute(select(Proposal).where(Proposal.lead_id == lead_id))).scalar_one()
        p.body_html += '<a href="http://localhost:8000/u/abc">unsubscribe</a>'
    capture = MimeCapture()
    outcome = await _send(settings, lead_id, capture)
    assert outcome.status == LeadStatus.NEEDS_REVIEW and "local address" in outcome.reason
    assert capture.raw == []
    async with session_scope() as s:
        assert (await s.execute(select(Message))).scalars().all() == []
