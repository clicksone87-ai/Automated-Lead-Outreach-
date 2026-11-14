"""Email verification adapter (ZeroBounce first; swappable)."""

from __future__ import annotations

from typing import ClassVar, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.db.models import VerificationStatus
from app.orchestrator import budget
from app.services import runtime_settings


class VerificationResult(BaseModel):
    email: str
    status: VerificationStatus
    provider_status: str
    sub_status: str | None = None


class VerificationAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

    async def verify(
        self, session: AsyncSession, email: str, *, agent: str
    ) -> VerificationResult: ...


_ZB_MAP = {
    "valid": VerificationStatus.VALID,
    "catch-all": VerificationStatus.CATCH_ALL,
    "unknown": VerificationStatus.UNKNOWN,
    "invalid": VerificationStatus.INVALID,
    "spamtrap": VerificationStatus.INVALID,
    "abuse": VerificationStatus.INVALID,
    "do_not_mail": VerificationStatus.INVALID,
}


class ZeroBounceVerifier:
    name: ClassVar[str] = "zerobounce"
    is_mock: ClassVar[bool] = False
    ENDPOINT = "https://api.zerobounce.net/v2/validate"

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=30)

    async def verify(
        self, session: AsyncSession, email: str, *, agent: str
    ) -> VerificationResult:
        rs = await runtime_settings.load(session)
        await budget.charge(
            session, provider="zerobounce", agent=agent,
            amount_usd=rs.provider_prices.get("zerobounce", 0.008), description="verify email",
        )
        resp = await self._client.get(
            self.ENDPOINT, params={"api_key": self._key, "email": email, "ip_address": ""}
        )
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"ZeroBounce error: {body['error']}")
        raw = str(body.get("status", "unknown")).lower()
        return VerificationResult(
            email=email, status=_ZB_MAP.get(raw, VerificationStatus.UNKNOWN),
            provider_status=raw, sub_status=body.get("sub_status") or None,
        )


class MockVerifier:
    """Valid only for reserved example domains; never contacts anyone."""

    name: ClassVar[str] = "mock_verifier"
    is_mock: ClassVar[bool] = True

    async def verify(
        self, session: AsyncSession, email: str, *, agent: str
    ) -> VerificationResult:
        domain = email.rsplit("@", 1)[-1].lower()
        ok = domain.endswith(("example.com", "example.org", "example.net"))
        status = VerificationStatus.VALID if ok else VerificationStatus.UNKNOWN
        return VerificationResult(email=email, status=status, provider_status=f"mock_{status}")


class DisabledVerifier:
    name: ClassVar[str] = "verification_disabled"
    is_mock: ClassVar[bool] = False

    async def verify(
        self, session: AsyncSession, email: str, *, agent: str
    ) -> VerificationResult:
        raise AdapterUnavailableError("Email verification", ("EMAIL_VERIFICATION_API_KEY",))
