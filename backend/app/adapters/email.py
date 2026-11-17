"""Email sending adapter (Resend). The mock adapter only records messages for tests."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Any, ClassVar, Protocol

import httpx
from pydantic import BaseModel, Field

from app.adapters.base import AdapterUnavailableError


class EmailAttachment(BaseModel):
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


class OutgoingEmail(BaseModel):
    from_address: str
    to: str
    subject: str
    html: str
    text: str
    reply_to: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    attachments: list[EmailAttachment] = Field(default_factory=list)
    idempotency_key: str
    tags: dict[str, str] = Field(default_factory=dict)


class DeliveryEvent(BaseModel):
    provider_id: str
    status: str  # sent | delivered | bounced | complained | delayed | failed | ...
    occurred_at: datetime | None = None
    detail: str | None = None


class EmailAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

    async def send(self, email: OutgoingEmail) -> str: ...
    async def status(self, provider_id: str) -> DeliveryEvent: ...


class ResendEmail:
    name: ClassVar[str] = "resend"
    is_mock: ClassVar[bool] = False
    BASE = "https://api.resend.com"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=30)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def send(self, email: OutgoingEmail) -> str:
        payload: dict[str, Any] = {
            "from": email.from_address,
            "to": [email.to],
            "subject": email.subject,
            "html": email.html,
            "text": email.text,
            "headers": email.headers,
            "tags": [{"name": k, "value": v} for k, v in email.tags.items()],
        }
        if email.reply_to:
            payload["reply_to"] = email.reply_to
        if email.attachments:
            payload["attachments"] = [
                {"filename": a.filename, "content": base64.b64encode(a.content).decode()}
                for a in email.attachments
            ]
        resp = await self._client.post(
            f"{self.BASE}/emails",
            json=payload,
            headers={**self._headers, "Idempotency-Key": email.idempotency_key[:256]},
        )
        resp.raise_for_status()
        return str(resp.json()["id"])

    async def status(self, provider_id: str) -> DeliveryEvent:
        resp = await self._client.get(f"{self.BASE}/emails/{provider_id}", headers=self._headers)
        resp.raise_for_status()
        body = resp.json()
        return DeliveryEvent(
            provider_id=provider_id,
            status=str(body.get("last_event", "sent")),
            occurred_at=body.get("created_at"),
        )


class MockEmail:
    name: ClassVar[str] = "mock_email"
    is_mock: ClassVar[bool] = True

    def __init__(self) -> None:
        self.sent: dict[str, OutgoingEmail] = {}
        self._by_key: dict[str, str] = {}

    async def send(self, email: OutgoingEmail) -> str:
        if email.idempotency_key in self._by_key:
            return self._by_key[email.idempotency_key]
        provider_id = f"mock-{uuid.uuid4().hex[:12]}"
        self.sent[provider_id] = email
        self._by_key[email.idempotency_key] = provider_id
        return provider_id

    async def status(self, provider_id: str) -> DeliveryEvent:
        return DeliveryEvent(provider_id=provider_id, status="delivered")


class DisabledEmail:
    name: ClassVar[str] = "email_disabled"
    is_mock: ClassVar[bool] = False

    async def send(self, email: OutgoingEmail) -> str:
        raise AdapterUnavailableError("Resend", ("RESEND_API_KEY", "SENDING_DOMAIN"))

    async def status(self, provider_id: str) -> DeliveryEvent:
        raise AdapterUnavailableError("Resend", ("RESEND_API_KEY", "SENDING_DOMAIN"))
