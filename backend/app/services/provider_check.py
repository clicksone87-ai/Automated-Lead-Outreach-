"""Live provider check: one real call per configured provider. Secrets are never printed."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from app.adapters.email import OutgoingEmail
from app.adapters.email_free import LocalVerifier, SmtpEmail
from app.adapters.gmail import GmailEmail
from app.adapters.llm import LLMRequest
from app.adapters.registry import Adapters
from app.adapters.verification import FallbackVerifier, ZeroBounceVerifier
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
        # Always the Mailpit sandbox, even when outreach goes through Gmail.
        sandbox = SmtpEmail(settings.smtp_host, settings.smtp_port, domain=settings.sender_domain,
                            mailpit_api_url=settings.mailpit_api_url)  # fmt: skip
        mid = await sandbox.send(
            OutgoingEmail(
                from_address=settings.from_address,
                to="provider-check@test-agency.local",
                subject="Provider check (Mailpit sandbox)",
                html="<p>Provider check: SMTP → Mailpit works. Nothing is really sent.</p>",
                text="Provider check: SMTP -> Mailpit works. Nothing is really sent.",
                idempotency_key=f"provider-check-{uuid.uuid4()}",
            )
        )
        ev = await sandbox.status(mid)
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

    async def zerobounce() -> str:
        zb = ZeroBounceVerifier(secret(settings.email_verification_api_key))
        credits = await zb.credits()
        async with session_scope() as s:
            r = await FallbackVerifier(zb, LocalVerifier()).verify(
                s, "postmaster@gmail.com", agent="provider_check"
            )
        return f"{credits} credits left; gmail.com postmaster → {r.status} ({r.provider_status})"

    async def gmail() -> str:
        mode = settings.gmail_mode
        if mode == "compose":
            return f"{settings.gmail_sender}: compose links (no App Password set; nothing called)"
        g = adapters.email
        assert isinstance(g, GmailEmail)
        folder = await asyncio.to_thread(g.check_login)
        return f"{settings.gmail_sender}: IMAP login ok, drafts folder {folder!r}; mode={mode}"

    async def web() -> str:
        page = await adapters.fetcher.get("https://example.com/")
        return f"safe fetcher HTTP {page.status_code}"

    def secret(v: Any) -> str:
        return v.get_secret_value() if v is not None else ""

    def router_check(cls: Any, key: Any) -> Callable[[], Awaitable[str]]:
        async def check() -> str:
            async with session_scope() as s:
                res = await cls(secret(key)).search(
                    s, "looking for a web designer", agent="provider_check", country="GB", count=3
                )
            return f"{len(res)} results" + (f"; first: {res[0].url[:60]}" if res else "")

        return check

    async def serper_fallback() -> str:
        from app.adapters.search_free import SerperSearch  # noqa: PLC0415

        async with session_scope() as s:
            res = await SerperSearch(secret(settings.serper_api_key_fallback)).search(
                s, "need a logo designer", agent="provider_check", country="US"
            )
        return f"fallback key: {len(res)} results"

    async def groq() -> str:
        from app.adapters.groq import GroqLLM  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get("https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {secret(settings.groq_api_key)}"})  # fmt: skip
        r.raise_for_status()
        models = sorted(m["id"] for m in r.json().get("data", []))
        llm = GroqLLM(secret(settings.groq_api_key), settings.groq_model)
        async with session_scope() as s:
            ok = await llm.structured(
                s,
                LLMRequest(agent="provider_check", task="Return ok=true and word='hello'."),
                _Ping,
            )
        assert ok.ok, "model answered ok=false"
        return f"{llm.model}: structured output valid; {len(models)} models: {', '.join(models)}"

    async def apify_fallback() -> str:
        from app.adapters.apify import ApifyClient  # noqa: PLC0415

        usage = await ApifyClient(secret(settings.apify_api_token_fallback)).monthly_usage()
        assert usage is not None, "token rejected"
        return f"fallback token ok; ${usage['used_usd']:.3f} of ${usage['limit_usd']:.0f} used"

    from app.adapters.search_router import (  # noqa: PLC0415
        ScrapingDogSearch,
        SearloSearch,
        SerpApiSearch,
        TavilySearch,
    )

    extra = [
        ("serpapi", router_check(SerpApiSearch, settings.serpapi_api_key)),
        ("scrapingdog", router_check(ScrapingDogSearch, settings.scrapingdog_api_key)),
        ("searlo", router_check(SearloSearch, settings.searlo_api_key)),
        ("tavily", router_check(TavilySearch, settings.tavily_api_key)),
        ("groq", groq),
        ("zerobounce", zerobounce),
    ]
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
        *extra,
    ]
    for name, fn in checks:
        await run(name, fn)
    for name, fn, configured in (
        ("gmail", gmail, bool(settings.gmail_mode)),
        ("serper_fallback", serper_fallback, settings.serper_api_key_fallback is not None),
        ("apify_fallback", apify_fallback, settings.apify_api_token_fallback is not None),
    ):
        if not configured:
            out.append(CheckResult(name, False, "needs_api_key", "not set"))
            continue
        t = time.monotonic()
        try:
            out.append(CheckResult(name, True, "ok", f"{await fn()} ({_ms(t)})"))
        except Exception as exc:
            msg = str(exc).replace(chr(10), " ")[:160]
            out.append(CheckResult(name, True, "failed", f"{type(exc).__name__}: {msg}"))
    for name in ("openai", "brave", "places", "resend", "reddit", "x", "meta",
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
