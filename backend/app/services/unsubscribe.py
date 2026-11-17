"""Signed, stateless unsubscribe tokens.

The HMAC ties a link to the address it was sent to; using it adds a permanent suppression and
cancels pending follow-ups.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True, slots=True)
class UnsubscribeTarget:
    business_id: uuid.UUID | None
    email: str


def _secret(settings: Settings) -> bytes:
    if settings.app_secret is not None and settings.app_secret.get_secret_value():
        return settings.app_secret.get_secret_value().encode()
    # Local fallback: stable per installation, never published. Set APP_SECRET on a server.
    return hashlib.sha256(f"unsubscribe:{settings.database_url}".encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(settings: Settings, business_id: uuid.UUID | None, email: str) -> str:
    payload = f"{business_id or ''}|{email.strip().lower()}".encode()
    sig = hmac.new(_secret(settings), payload, hashlib.sha256).digest()[:16]
    return f"{_b64(payload)}.{_b64(sig)}"


def read_token(settings: Settings, token: str) -> UnsubscribeTarget | None:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(_secret(settings), payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return None
        biz, email = payload.decode().split("|", 1)
        return UnsubscribeTarget(uuid.UUID(biz) if biz else None, email)
    except (ValueError, UnicodeDecodeError):
        return None


def unsubscribe_url(settings: Settings, token: str) -> str:
    base = (settings.public_api_base_url or "http://localhost:8000").rstrip("/")
    return f"{base}/u/{token}"
