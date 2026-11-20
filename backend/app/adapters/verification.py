"""Email verification adapters. ZeroBounce runs inside its free monthly allowance
(ZEROBOUNCE_MONTHLY_CAP); when that is used up or ZeroBounce fails, the local syntax + MX check
takes over."""

from __future__ import annotations

from typing import ClassVar, Protocol

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.db.models import VerificationStatus
from app.orchestrator import budget, quota
from app.orchestrator.budget import BudgetExceededError
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

    CREDITS = "https://api.zerobounce.net/v2/getcredits"

    def __init__(
        self, api_key: str, client: httpx.AsyncClient | None = None, *, free_tier: bool = True
    ) -> None:
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=30)
        self._free_tier = free_tier

    async def verify(self, session: AsyncSession, email: str, *, agent: str) -> VerificationResult:
        if self._free_tier:
            await quota.consume(session, "zerobounce", agent=agent, description="verify email")
        else:
            rs = await runtime_settings.load(session)
            await budget.charge(
                session,
                provider="zerobounce",
                agent=agent,
                amount_usd=rs.provider_prices.get("zerobounce", 0.008),
                description="verify email",
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
            email=email,
            status=_ZB_MAP.get(raw, VerificationStatus.UNKNOWN),
            provider_status=raw,
            sub_status=body.get("sub_status") or None,
        )

    async def credits(self) -> int | None:
        """Credits left on the account (free call)."""
        resp = await self._client.get(self.CREDITS, params={"api_key": self._key})
        resp.raise_for_status()
        raw = resp.json().get("Credits")
        return int(raw) if raw is not None and int(raw) >= 0 else None


class FallbackVerifier:
    """ZeroBounce first; the local syntax + MX check when its cap is used up or it fails."""

    is_mock: ClassVar[bool] = False

    def __init__(self, primary: VerificationAdapter, fallback: VerificationAdapter) -> None:
        self.primary, self.fallback = primary, fallback
        self.name = f"{primary.name}+{fallback.name}"

    async def verify(self, session: AsyncSession, email: str, *, agent: str) -> VerificationResult:
        try:
            async with session.begin_nested():
                return await self.primary.verify(session, email, agent=agent)
        except (BudgetExceededError, httpx.HTTPError, RuntimeError) as exc:
            await quota.record_provider_error(self.primary.name, f"{type(exc).__name__}: {exc}")
            return await self.fallback.verify(session, email, agent=agent)


class MockVerifier:
    """Valid only for reserved example domains; never contacts anyone."""

    name: ClassVar[str] = "mock_verifier"
    is_mock: ClassVar[bool] = True

    async def verify(self, session: AsyncSession, email: str, *, agent: str) -> VerificationResult:
        domain = email.rsplit("@", 1)[-1].lower()
        ok = domain.endswith(("example.com", "example.org", "example.net"))
        status = VerificationStatus.VALID if ok else VerificationStatus.UNKNOWN
        return VerificationResult(email=email, status=status, provider_status=f"mock_{status}")


class DisabledVerifier:
    name: ClassVar[str] = "verification_disabled"
    is_mock: ClassVar[bool] = False

    async def verify(self, session: AsyncSession, email: str, *, agent: str) -> VerificationResult:
        raise AdapterUnavailableError("Email verification", ("EMAIL_VERIFICATION_API_KEY",))
