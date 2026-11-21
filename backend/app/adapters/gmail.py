"""Gmail outreach: the proposal lands in the sender's Gmail and a human presses Send.

Three routes, best first:
- App Password + GMAIL_DELIVERY=draft: the full email (text, HTML, attachments) is put into the
  Gmail Drafts folder over IMAP APPEND; nothing leaves the account until the human sends it.
- App Password + GMAIL_DELIVERY=send: sent through smtp.gmail.com (STARTTLS, port 587; when
  587 is blocked or the TLS upgrade fails, implicit TLS on port 465).
- No App Password: a Gmail compose link (to, subject, plain-text body). Attachments cannot travel
  in a link, so the dashboard offers them for download next to the link.
After a draft or link, the message waits as `drafted` until "Mark as sent" is pressed.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import imaplib
import logging
import re
import smtplib
import time
from collections.abc import Callable
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

from app.adapters.email import DeliveryEvent, OutgoingEmail
from app.adapters.email_free import build_mime

logger = logging.getLogger(__name__)

GMAIL_WEB = "https://mail.google.com/mail/"
# Gmail opens compose links reliably up to about 2,000 characters of URL.
COMPOSE_URL_LIMIT = 1900
_TRUNCATED = "\n\n[…the full message is in the dashboard]"


def compose_url(sender: str, to: str, subject: str, body: str) -> str:
    """A Gmail compose window for `sender`, pre-filled; the body is shortened to fit a link."""

    def build(text: str) -> str:
        params = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": text}
        if sender:
            params["authuser"] = sender
        return f"{GMAIL_WEB}?{urlencode(params, quote_via=quote)}"

    url = build(body)
    if len(url) <= COMPOSE_URL_LIMIT:
        return url
    lo, hi = 0, len(body)
    while lo < hi:  # longest prefix that still fits
        mid = (lo + hi + 1) // 2
        if len(build(body[:mid] + _TRUNCATED)) <= COMPOSE_URL_LIMIT:
            lo = mid
        else:
            hi = mid - 1
    return build(body[:lo].rstrip() + _TRUNCATED)


def draft_url(sender: str, message_id: str) -> str:
    """Opens the Drafts search for one message (Gmail has no stable deep link to a draft)."""
    query = f"in:drafts rfc822msgid:{message_id.strip('<>')}"
    base = f"{GMAIL_WEB}u/?authuser={quote(sender)}" if sender else f"{GMAIL_WEB}u/0/"
    return f"{base}#search/{quote(query, safe='')}"


def _drafts_folder(imap: Any) -> str:
    """The mailbox flagged \\Drafts (its name is localised: [Gmail]/Drafts, [Google Mail]/…)."""
    typ, lines = imap.list()
    if typ == "OK":
        for raw in lines or []:
            line = raw.decode() if isinstance(raw, bytes) else str(raw)
            if "\\Drafts" in line:
                m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                if m:
                    return m.group(1)
    return "[Gmail]/Drafts"


class GmailEmail:
    """Gmail via App Password: IMAP draft (default) or SMTP send."""

    name: ClassVar[str] = "gmail"
    is_mock: ClassVar[bool] = False
    is_sandbox: ClassVar[bool] = False

    def __init__(
        self,
        sender: str,
        app_password: str,
        *,
        delivery: str = "draft",
        imap_host: str = "imap.gmail.com",
        imap_port: int = 993,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: int = 587,
        smtp_ssl_port: int = 465,
        imap_factory: Callable[[str, int], Any] | None = None,
        smtp_factory: Callable[[str, int], Any] | None = None,
        smtp_ssl_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self.sender = sender
        self._password = app_password
        self.delivery = "send" if delivery == "send" else "draft"
        self._imap = (imap_host, imap_port)
        self._smtp = (smtp_host, smtp_port)
        self._smtp_ssl = (smtp_host, smtp_ssl_port)
        self._imap_factory = imap_factory or (lambda h, p: imaplib.IMAP4_SSL(h, p, timeout=30))
        self._smtp_factory = smtp_factory or (lambda h, p: smtplib.SMTP(h, p, timeout=30))
        self._smtp_ssl_factory = smtp_ssl_factory or (
            lambda h, p: smtplib.SMTP_SSL(h, p, timeout=30)
        )

    @property
    def hands_off(self) -> bool:
        """True when a human still has to press Send in Gmail."""
        return self.delivery == "draft"

    def message_id(self, idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        domain = self.sender.rsplit("@", 1)[-1] or "gmail.com"
        return f"<{digest}@{domain}>"

    def _append_draft(self, raw: bytes) -> None:
        imap = self._imap_factory(*self._imap)
        try:
            imap.login(self.sender, self._password)
            folder = _drafts_folder(imap)
            typ, data = imap.append(
                f'"{folder}"', "(\\Draft)", imaplib.Time2Internaldate(time.time()), raw
            )
            if typ != "OK":
                raise RuntimeError(f"Gmail refused the draft: {data!r}"[:200])
        finally:
            with contextlib.suppress(OSError, imaplib.IMAP4.error):
                imap.logout()

    def check_login(self) -> str:
        """Log in and find the Drafts folder; changes nothing (provider check)."""
        imap = self._imap_factory(*self._imap)
        try:
            imap.login(self.sender, self._password)
            return _drafts_folder(imap)
        finally:
            with contextlib.suppress(OSError, imaplib.IMAP4.error):
                imap.logout()

    def _connect_smtp(self) -> Any:
        """A TLS-protected SMTP connection: STARTTLS on 587, else implicit TLS on 465.

        Only connecting and the TLS upgrade fall back; a refused login or a failed send is
        raised as it is, so a message is never handed over twice.
        """
        smtp = None
        try:
            smtp = self._smtp_factory(*self._smtp)
            smtp.starttls()
            return smtp
        except (OSError, smtplib.SMTPException) as exc:  # refused, timeout, disconnect, SSL
            if smtp is not None:
                with contextlib.suppress(OSError, smtplib.SMTPException):
                    smtp.close()
            logger.warning(
                "Gmail SMTP %s:%s with STARTTLS failed (%s); trying SSL on port %s",
                *self._smtp, type(exc).__name__, self._smtp_ssl[1],
            )  # fmt: skip
            return self._smtp_ssl_factory(*self._smtp_ssl)

    def _send(self, msg: Any) -> None:
        smtp = self._connect_smtp()
        try:
            smtp.login(self.sender, self._password)
            smtp.send_message(msg)
        finally:
            with contextlib.suppress(OSError, smtplib.SMTPException):
                smtp.quit()

    async def send(self, email: OutgoingEmail) -> str:
        mid = self.message_id(email.idempotency_key)
        msg = build_mime(email, mid)
        if self.hands_off:
            await asyncio.to_thread(self._append_draft, msg.as_bytes())
        else:
            await asyncio.to_thread(self._send, msg)
        return mid

    def open_url(self, provider_id: str) -> str:
        return draft_url(self.sender, provider_id)

    async def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id=provider_id, status="sent")


class GmailComposeLink:
    """No App Password: the message becomes a pre-filled Gmail compose link."""

    name: ClassVar[str] = "gmail_compose"
    is_mock: ClassVar[bool] = False
    is_sandbox: ClassVar[bool] = False
    hands_off: ClassVar[bool] = True
    delivery: ClassVar[str] = "compose"

    def __init__(self, sender: str) -> None:
        self.sender = sender

    async def send(self, email: OutgoingEmail) -> str:
        digest = hashlib.sha256(email.idempotency_key.encode()).hexdigest()[:32]
        return f"compose:{digest}"

    def compose(self, to: str, subject: str, body: str) -> str:
        return compose_url(self.sender, to, subject, body)

    async def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id=provider_id, status="sent")
