"""Free/testing email stack: SMTP sending (Mailpit in testing — nothing is really sent),
Mailpit inbox reading (replies + alert emails) and a local email verifier (syntax + MX).

Resend and ZeroBounce stay available behind the same interfaces for go-live.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, ClassVar

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.email import DeliveryEvent, OutgoingEmail
from app.adapters.mailbox import MailItem, parse_message
from app.adapters.verification import VerificationResult
from app.db.models import VerificationStatus

# ── SMTP ─────────────────────────────────────────────────────────────────────


def build_mime(email: OutgoingEmail, message_id: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = email.from_address
    msg["To"] = email.to
    msg["Subject"] = email.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = message_id
    if email.reply_to:
        msg["Reply-To"] = email.reply_to
    for k, v in email.headers.items():
        if k.lower() not in {"from", "to", "subject", "date", "message-id", "reply-to"}:
            msg[k] = v
    for k, v in email.tags.items():
        msg[f"X-Tag-{re.sub(r'[^A-Za-z0-9-]', '-', k)}"] = v
    msg.set_content(email.text)
    msg.add_alternative(email.html, subtype="html")
    for a in email.attachments:
        maintype, _, subtype = a.content_type.partition("/")
        msg.add_attachment(
            a.content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=a.filename,
        )
    return msg


class SmtpEmail:
    """Sends through SMTP. In testing the host is Mailpit, which captures every message."""

    name: ClassVar[str] = "smtp"
    is_mock: ClassVar[bool] = False

    def __init__(
        self,
        host: str,
        port: int,
        *,
        domain: str,
        user: str = "",
        password: str = "",
        starttls: bool = False,
        mailpit_api_url: str = "",
    ) -> None:
        self._host, self._port = host, port
        self._domain = domain
        self._user, self._password, self._starttls = user, password, starttls
        self._mailpit = mailpit_api_url.rstrip("/")

    @property
    def is_sandbox(self) -> bool:
        """True when mail goes to Mailpit (captured locally, never delivered)."""
        return bool(self._mailpit) and self._host in ("mailpit", "localhost", "127.0.0.1")

    def message_id(self, idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        return f"<{digest}@{self._domain}>"

    def _send_sync(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
            if self._starttls:
                smtp.starttls()
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(msg)

    async def send(self, email: OutgoingEmail) -> str:
        mid = self.message_id(email.idempotency_key) if email.idempotency_key else make_msgid()
        await asyncio.to_thread(self._send_sync, build_mime(email, mid))
        return mid

    async def status(self, provider_id: str) -> DeliveryEvent:
        """SMTP has no delivery API; in sandbox mode a message found in Mailpit = delivered."""
        if self._mailpit:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    f"{self._mailpit}/api/v1/search",
                    params={"query": f"message-id:{provider_id.strip('<>')}", "limit": 1},
                )
                if r.status_code == 200 and r.json().get("messages"):
                    return DeliveryEvent(
                        provider_id=provider_id,
                        status="delivered",
                        occurred_at=datetime.now(UTC),
                        detail="captured by Mailpit (sandbox — not really sent)",
                    )
        return DeliveryEvent(provider_id=provider_id, status="sent")


# ── Mailpit inbox ────────────────────────────────────────────────────────────


class MailpitMailbox:
    """Reads replies and alert emails from Mailpit's API. Folders map to our addresses:
    'INBOX'/'replies' → replies@<domain>, anything else (e.g. 'Alerts') → alerts@<domain>."""

    name: ClassVar[str] = "mailpit"
    is_mock: ClassVar[bool] = False

    def __init__(self, api_url: str, *, reply_address: str, alerts_address: str) -> None:
        self._api = api_url.rstrip("/")
        self._reply, self._alerts = reply_address, alerts_address

    def _address(self, folder: str) -> str:
        return self._reply if folder.lower() in ("inbox", "replies") else self._alerts

    async def fetch_unseen(self, folder: str, limit: int = 50) -> list[MailItem]:
        addr = self._address(folder)
        items: list[MailItem] = []
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self._api}/api/v1/search",
                params={"query": f"to:{addr} is:unread", "limit": limit},
            )
            r.raise_for_status()
            for m in r.json().get("messages", []):
                raw = await c.get(f"{self._api}/api/v1/message/{m['ID']}/raw")
                if raw.status_code == 200:
                    items.append(parse_message(str(m["ID"]), raw.content))
        return items

    async def mark_seen(self, folder: str, uids: list[str]) -> None:
        if not uids:
            return
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{self._api}/api/v1/messages", json={"IDs": uids, "Read": True})
            r.raise_for_status()

    async def info(self) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self._api}/api/v1/info")
                r.raise_for_status()
                data: dict[str, Any] = r.json()
                return data
        except httpx.HTTPError:
            return None


# ── local verifier ───────────────────────────────────────────────────────────

_EMAIL = re.compile(
    r"^(?=.{3,254}$)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)


class LocalVerifier:
    """Free verification: RFC-ish syntax + the domain must accept mail (MX record, not a
    null MX). It cannot see whether the mailbox itself exists — shown as 'syntax + MX'."""

    name: ClassVar[str] = "local_verifier"
    is_mock: ClassVar[bool] = False

    def __init__(self, resolver: Any | None = None) -> None:
        self._resolver = resolver

    async def _mx(self, domain: str) -> list[tuple[int, str]]:
        resolver = self._resolver or dns.asyncresolver.Resolver()
        answer = await resolver.resolve(domain, "MX", lifetime=8)
        return [(int(r.preference), str(r.exchange).rstrip(".")) for r in answer]

    async def verify(self, session: AsyncSession, email: str, *, agent: str) -> VerificationResult:
        addr = email.strip()
        if not _EMAIL.match(addr) or ".." in addr:
            return VerificationResult(
                email=email, status=VerificationStatus.INVALID, provider_status="bad_syntax"
            )
        domain = addr.rsplit("@", 1)[1].lower()
        try:
            mx = await self._mx(domain)
        except dns.resolver.NXDOMAIN:
            return VerificationResult(
                email=email, status=VerificationStatus.INVALID, provider_status="no_domain"
            )
        except dns.resolver.NoAnswer:
            return VerificationResult(
                email=email, status=VerificationStatus.UNKNOWN, provider_status="no_mx"
            )
        except (dns.exception.DNSException, OSError) as exc:
            return VerificationResult(
                email=email,
                status=VerificationStatus.UNKNOWN,
                provider_status="dns_error",
                sub_status=type(exc).__name__,
            )
        if not mx or all(host in ("", ".") for _, host in mx):
            return VerificationResult(
                email=email, status=VerificationStatus.INVALID, provider_status="null_mx"
            )
        return VerificationResult(
            email=email,
            status=VerificationStatus.VALID,
            provider_status="syntax_mx_ok",
            sub_status=sorted(mx)[0][1],
        )
