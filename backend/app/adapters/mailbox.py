"""Mailbox adapter (IMAP over TLS). Used for job-board/classified alert emails now and for
reply ingestion in the outreach phase. Mail content is untrusted data."""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import ClassVar, Protocol

from bs4 import BeautifulSoup

from app.adapters.base import AdapterUnavailableError

_LINK = re.compile(r"https?://[^\s<>\"')]+")


@dataclass(slots=True)
class MailItem:
    uid: str
    message_id: str
    from_email: str
    subject: str
    received_at: datetime
    text: str
    links: list[str] = field(default_factory=list)
    in_reply_to: str | None = None
    raw: bytes = b""


class MailboxAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

    async def fetch_unseen(self, folder: str, limit: int = 50) -> list[MailItem]: ...
    async def mark_seen(self, folder: str, uids: list[str]) -> None: ...


def parse_message(uid: str, raw: bytes) -> MailItem:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    body = msg.get_body(preferencelist=("plain", "html"))
    content = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        content = BeautifulSoup(content, "lxml").get_text("\n", strip=True)
    try:
        received = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else datetime.now(UTC)
    except (TypeError, ValueError):
        received = datetime.now(UTC)
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    return MailItem(
        uid=uid,
        message_id=str(msg["Message-ID"] or f"<uid-{uid}>").strip(),
        from_email=parseaddr(str(msg["From"] or ""))[1].lower(),
        subject=str(msg["Subject"] or "")[:500],
        received_at=received,
        text=content[:20_000],
        links=list(dict.fromkeys(_LINK.findall(content)))[:30],
        in_reply_to=str(msg["In-Reply-To"]).strip() if msg["In-Reply-To"] else None,
        raw=raw[:200_000],
    )


class ImapMailbox:
    name: ClassVar[str] = "imap"
    is_mock: ClassVar[bool] = False

    def __init__(self, host: str, user: str, password: str, port: int = 993) -> None:
        self._host, self._user, self._password, self._port = host, user, password, port

    def _connect(self, folder: str) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self._host, self._port, timeout=30)
        conn.login(self._user, self._password)
        status, _ = conn.select(f'"{folder}"')
        if status != "OK":
            conn.logout()
            raise RuntimeError(f"IMAP folder not found: {folder}")
        return conn

    def _fetch_sync(self, folder: str, limit: int) -> list[MailItem]:
        conn = self._connect(folder)
        try:
            status, data = conn.uid("search", "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-limit:]
            items: list[MailItem] = []
            for uid in uids:
                status, parts = conn.uid("fetch", uid, "(BODY.PEEK[])")
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    items.append(parse_message(uid.decode(), parts[0][1]))
            return items
        finally:
            conn.logout()

    def _mark_sync(self, folder: str, uids: list[str]) -> None:
        if not uids:
            return
        conn = self._connect(folder)
        try:
            conn.uid("store", ",".join(uids), "+FLAGS", "(\\Seen)")
        finally:
            conn.logout()

    async def fetch_unseen(self, folder: str, limit: int = 50) -> list[MailItem]:
        return await asyncio.to_thread(self._fetch_sync, folder, limit)

    async def mark_seen(self, folder: str, uids: list[str]) -> None:
        await asyncio.to_thread(self._mark_sync, folder, uids)


class MockMailbox:
    """In-memory mailbox for tests / DEMO_MODE (seeded explicitly; empty by default)."""

    name: ClassVar[str] = "mock_mailbox"
    is_mock: ClassVar[bool] = True

    def __init__(self, items: dict[str, list[MailItem]] | None = None) -> None:
        self.items = items or {}
        self.seen: set[str] = set()

    async def fetch_unseen(self, folder: str, limit: int = 50) -> list[MailItem]:
        return [m for m in self.items.get(folder, []) if m.uid not in self.seen][:limit]

    async def mark_seen(self, folder: str, uids: list[str]) -> None:
        self.seen.update(uids)


class DisabledMailbox:
    name: ClassVar[str] = "mailbox_disabled"
    is_mock: ClassVar[bool] = False

    async def fetch_unseen(self, folder: str, limit: int = 50) -> list[MailItem]:
        raise AdapterUnavailableError(
            "Reply mailbox (IMAP)", ("REPLY_IMAP_HOST", "REPLY_IMAP_USER", "REPLY_IMAP_PASSWORD")
        )

    async def mark_seen(self, folder: str, uids: list[str]) -> None:
        raise AdapterUnavailableError("Reply mailbox (IMAP)")
