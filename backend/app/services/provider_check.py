"""Live provider check: ONE real call per configured provider through the real adapters.

`python -m app.cli check-providers` prints provider | key found | result | detail.
Secrets are never printed. Paid / off-in-testing-mode providers are reported, not called.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from app.adapters.email import OutgoingEmail
from app.adapters.llm import LLMRequest
from app.adapters.registry import Adapters
from app.config import Settings, integration_status
from app.db.session import session_scope


@dataclass(slots=True)
class CheckResult:
    provider: str
    key_found: bool
    result: str  # "ok" | "failed" | "off_testing_mode" | "off" | "needs_api_key"
    detail: str


class _Ping(BaseModel):
    ok: bool
    word: str


def _ms(start: float) -> str:
    return f"{(time.monotonic() - start) * 1000:.0f} ms"


async def check_all(  # noqa: PLR0915 - one small closure per provider
    settings: Settings, adapters: Adapters
) -> list[CheckResult]:
    st = integration_status(settings)
    out: list[CheckResult] = []

    async def run(name: str, fn: Callable[[], Awaitable[str]]) -> None:
        s = st[name]
        if not s.configured:
            out.append(CheckResult(name, s.has_keys, s.state, s.note))
            return
        t = time.monotonic()
        try:
            detail = await fn()
            out.append(CheckResult(name, s.has_keys, "ok", f"{detail} ({_ms(t)})"))
        except Exception as exc:
            msg = str(exc).replace("\n", " ")[:160]
            out.append(CheckResult(name, s.has_keys, "failed", f"{type(exc).__name__}: {msg}"))

    async def gemini() -> str:
        async with session_scope() as s:
            r = await adapters.llm.structured(
                s,
                LLMRequest(agent="provider_check", task="Return ok=true and word='hello'."),
                _Ping,
            )
        assert r.ok, "model answered ok=false"
        return f"{settings.gemini_text_model}: structured output valid"

    async def serper() -> str:
        assert adapters.serper is not None
        async with session_scope() as s:
            res = await adapters.serper.search(
                s, "plumber Leeds website", agent="provider_check", country="GB"
            )
        credits = await adapters.serper.credits()
        return f"{len(res)} results; {credits} credits left"

    async def exa() -> str:
        from app.adapters.search_free import ExaSearch  # noqa: PLC0415

        key = settings.exa_api_key.get_secret_value() if settings.exa_api_key else ""
        async with session_scope() as s:
            res = await ExaSearch(key).search(
                s, "need a website for my plumbing business", agent="provider_check", freshness="pm"
            )
        return f"{len(res)} results"

    async def apify() -> str:
        assert adapters.apify is not None
        usage = await adapters.apify.monthly_usage()
        assert usage is not None, "token rejected"
        return f"token ok; ${usage['used_usd']:.3f} of ${usage['limit_usd']:.0f} used this cycle"

    async def companies_house() -> str:
        key = settings.companies_house_api_key
        assert key is not None
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                "https://api.company-information.service.gov.uk/search/companies",
                params={"q": "plumbing", "items_per_page": 1},
                auth=(key.get_secret_value(), ""),
            )
        r.raise_for_status()
        return f"search ok ({r.json().get('total_results')} matches)"

    async def pagespeed() -> str:
        async with session_scope() as s:
            res = await adapters.pagespeed.run("https://example.com", "mobile", session=s)
        return "lab scores " + ", ".join(f"{k}={v:g}" for k, v in sorted(res.scores.items()))

    async def smtp() -> str:
        mid = await adapters.email.send(
            OutgoingEmail(
                from_address=settings.from_address,
                to="provider-check@test-agency.local",
                subject="Provider check (Mailpit sandbox)",
                html="<p>Provider check: SMTP → Mailpit works. Nothing is really sent.</p>",
                text="Provider check: SMTP -> Mailpit works. Nothing is really sent.",
                idempotency_key=f"provider-check-{uuid.uuid4()}",
            )
        )
        ev = await adapters.email.status(mid)
        return f"sent to Mailpit, status={ev.status}"

    async def mailpit() -> str:
        items = await adapters.mailbox.fetch_unseen("INBOX", limit=5)
        return f"inbox reachable ({len(items)} unread replies)"

    async def verification() -> str:
        async with session_scope() as s:
            r = await adapters.verification.verify(
                s, "postmaster@gmail.com", agent="provider_check"
            )
        return f"gmail.com → {r.status} ({r.provider_status}); syntax + MX only"

    async def web() -> str:
        page = await adapters.fetcher.get("https://example.com/")
        return f"safe fetcher HTTP {page.status_code}"

    checks: list[tuple[str, Callable[[], Awaitable[str]]]] = [
        ("gemini", gemini),
        ("serper", serper),
        ("exa", exa),
        ("apify", apify),
        ("companies_house", companies_house),
        ("pagespeed", pagespeed),
        ("smtp", smtp),
        ("mailpit", mailpit),
        ("verification", verification),
        ("web", web),
    ]
    for name, fn in checks:
        await run(name, fn)
    for name in ("openai", "brave", "places", "resend", "zerobounce", "reddit", "x", "meta",
                 "opencorporates"):  # fmt: skip
        s = st[name]
        out.append(CheckResult(name, s.has_keys, s.state, s.note or "not called"))
    return out


def as_table(rows: list[CheckResult]) -> str:
    lines = ["provider          | key found | test result       | detail", "-" * 100]
    for r in rows:
        lines.append(
            f"{r.provider:<17} | {'yes' if r.key_found else 'no':<9} | {r.result:<17} | {r.detail}"
        )
    return "\n".join(lines)


def as_dicts(rows: list[CheckResult]) -> list[dict[str, Any]]:
    return [
        {"provider": r.provider, "key_found": r.key_found, "result": r.result, "detail": r.detail}
        for r in rows
    ]
