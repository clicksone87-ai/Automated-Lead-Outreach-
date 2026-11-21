"""Live account status for every external provider: is the key set, does one small call work,
and what the provider itself says is left. Runs all checks concurrently; never prints keys.

Free account/balance endpoints are used where a provider has one; otherwise one minimal real
call is made and counted against the provider's cap like any other call. Unknown values are
None (shown as "—"), never estimated. Reported balances are stored as live caps
(`quota.save_balance`), so the quota layer stops at what the provider really has left.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import smtplib
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import Settings
from app.db.session import session_scope
from app.orchestrator import quota

UA = {"User-Agent": "LeadsSystemBot/0.1 (account status check)"}
TIMEOUT = httpx.Timeout(40, connect=15)
# A Google search on ScrapingDog costs 5 credits (measured: 4 searches used 20 credits).
SCRAPINGDOG_CREDITS_PER_SEARCH = 5.0
ENSEMBLEDATA_DAILY_UNITS = 50  # free trial
# Checks that cost nothing (account/balance endpoints): safe to run on a schedule.
FREE_CHECKS = frozenset(
    {"Serper", "Serper (2nd key)", "SerpAPI", "ScrapingDog", "Tavily", "Apify", "EnsembleData",
     "ScrapeCreators", "ZeroBounce"}
)  # fmt: skip


@dataclass(slots=True)
class StatusRow:
    provider: str
    key_set: bool | None  # None = no key needed
    result: str  # ok | failed | not_set | limited
    left: str | None = None  # what the provider reports as remaining
    notes: str = ""
    # provider key -> quota.save_balance keyword arguments (stored as the live cap)
    balance: dict[str, Any] | None = None


def _k(v: Any) -> str:
    return (v.get_secret_value() if hasattr(v, "get_secret_value") else str(v or "")).strip()


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip().lstrip("-").replace(".", "", 1).isdigit():
        return float(v)
    return None


async def _count(provider: str, what: str) -> None:
    async with session_scope() as s:
        await quota.consume(s, provider, agent="account_status", description=what[:200])


def _gemini_quota(err: dict[str, Any]) -> str:
    parts = []
    for d in err.get("details", []) or []:
        for v in d.get("violations", []) or []:
            dims = v.get("quotaDimensions") or {}
            model = f"[{dims['model']}]" if dims.get("model") else ""
            parts.append(
                f"{v.get('quotaId') or v.get('quotaMetric')}{model}={v.get('quotaValue', '?')}"
            )
    return "; ".join(parts)


async def check_all(s: Settings, *, free_only: bool = False) -> list[StatusRow]:  # noqa: PLR0915
    c = httpx.AsyncClient(timeout=TIMEOUT, headers=UA)

    def js(r: httpx.Response) -> dict[str, Any]:
        # Some providers send JSON without a JSON content type: parse whatever is valid JSON.
        try:
            b = r.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {"_list": b}

    async def gemini(model: str, label: str) -> StatusRow:
        key = _k(s.gemini_api_key)
        if not key:
            return StatusRow(label, False, "not_set")
        await _count("gemini", f"{model} status")
        r = await c.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key},
            json={"contents": [{"parts": [{"text": "Reply with the word ok."}]}],
                  "generationConfig": {"maxOutputTokens": 5}},
        )  # fmt: skip
        if r.status_code == 200:
            return StatusRow(label, True, "ok", None, f"{model} answered")
        err = js(r).get("error", {})
        if r.status_code == 429:
            return StatusRow(
                label, True, "limited", "0 today", f"{model} 429: {_gemini_quota(err)}"
            )
        msg = str(err.get("message", ""))[:120]
        return StatusRow(label, True, "failed", None, f"{model} HTTP {r.status_code}: {msg}")

    async def groq() -> StatusRow:
        key = _k(s.groq_api_key)
        if not key:
            return StatusRow("Groq", False, "not_set")
        model = s.groq_model or "openai/gpt-oss-120b"
        await _count("groq", "status")
        r = await c.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "Say ok"}],
                  "max_completion_tokens": 64},
        )  # fmt: skip
        h = r.headers
        rem, lim = (
            _num(h.get("x-ratelimit-remaining-requests")),
            _num(h.get("x-ratelimit-limit-requests")),
        )
        left = f"{h.get('x-ratelimit-remaining-requests', '—')}/{h.get('x-ratelimit-limit-requests', '—')} requests/day"
        bal = (
            {"groq": {"remaining": rem, "limit": lim, "unit": "requests", "period": "day_utc"}}
            if rem is not None
            else None
        )
        return StatusRow("Groq", True, "ok" if r.status_code == 200 else "failed", left,
                         f"{model} HTTP {r.status_code}; tokens/min {h.get('x-ratelimit-remaining-tokens', '—')}/{h.get('x-ratelimit-limit-tokens', '—')}", bal)  # fmt: skip

    async def serper(field: str, label: str) -> StatusRow:
        key = _k(getattr(s, field))
        if not key:
            return StatusRow(label, False, "not_set")
        r = await c.get("https://google.serper.dev/account", headers={"X-API-KEY": key})
        if r.status_code != 200:
            return StatusRow(label, True, "failed", None, f"account HTTP {r.status_code}")
        b = js(r)
        return StatusRow(label, True, "ok", f"{b.get('balance')} credits",
                         f"rate limit {b.get('rateLimit')}/s; no search made",
                         {"serper_part": _num(b.get("balance"))})  # fmt: skip

    async def serpapi() -> StatusRow:
        key = _k(s.serpapi_api_key)
        if not key:
            return StatusRow("SerpAPI", False, "not_set")
        r = await c.get("https://serpapi.com/account.json", params={"api_key": key})
        if r.status_code != 200:
            return StatusRow("SerpAPI", True, "failed", None, f"account HTTP {r.status_code}")
        b = js(r)
        rem = _num(b.get("total_searches_left", b.get("plan_searches_left")))
        return StatusRow("SerpAPI", True, "ok", f"{rem:g} searches left" if rem is not None else None,
                         f"plan {b.get('plan_name')} {b.get('searches_per_month')}/month; this month {b.get('this_month_usage')}",
                         {"serpapi": {"remaining": rem, "limit": _num(b.get("searches_per_month")), "period": "month", "unit": "searches"}})  # fmt: skip

    async def scrapingdog() -> StatusRow:
        key = _k(s.scrapingdog_api_key)
        if not key:
            return StatusRow("ScrapingDog", False, "not_set")
        r = await c.get("https://api.scrapingdog.com/account", params={"api_key": key})
        if r.status_code != 200:
            return StatusRow(
                "ScrapingDog", True, "failed", None, f"account HTTP {r.status_code}: {r.text[:100]}"
            )
        b = js(r)
        used, limit = _num(b.get("requestUsed")), _num(b.get("requestLimit"))
        rem = limit - used if used is not None and limit is not None else None
        return StatusRow("ScrapingDog", True, "ok", f"{rem:g} credits" if rem is not None else None,
                         f"used {used} of {limit}; pack {b.get('pack')}; validity {b.get('validity')}; {SCRAPINGDOG_CREDITS_PER_SEARCH:g} credits/search",
                         {"scrapingdog": {"remaining": rem, "limit": limit, "unit": "credits", "cost_per_call": SCRAPINGDOG_CREDITS_PER_SEARCH}})  # fmt: skip

    async def searlo() -> StatusRow:
        key = _k(s.searlo_api_key)
        if not key:
            return StatusRow("Searlo", False, "not_set")
        await _count("searlo", "status search")
        r = await c.get("https://api.searlo.tech/api/v1/search/web", params={"q": "web design", "limit": 1},
                        headers={"x-api-key": key})  # fmt: skip
        b = js(r)
        rem = next((_num(b[k]) for k in ("credits_remaining", "remaining_credits") if k in b), None)
        return StatusRow("Searlo", True, "ok" if r.status_code == 200 else "failed",
                         f"{rem:g} credits" if rem is not None else None,
                         f"1 search; HTTP {r.status_code}; no balance endpoint; response keys {sorted(b)[:6]}")  # fmt: skip

    async def tavily() -> StatusRow:
        key = _k(s.tavily_api_key)
        if not key:
            return StatusRow("Tavily", False, "not_set")
        r = await c.get("https://api.tavily.com/usage", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return StatusRow("Tavily", True, "failed", None, f"usage HTTP {r.status_code}")
        b = js(r)
        acct = b.get("account") or {}
        lim, use = _num(acct.get("plan_limit")), _num(acct.get("plan_usage"))
        rem = lim - use if lim is not None and use is not None else None
        return StatusRow("Tavily", True, "ok", f"{rem:g} credits this month" if rem is not None else None,
                         f"plan {acct.get('current_plan')} {use}/{lim}",
                         {"tavily": {"remaining": rem, "limit": lim, "unit": "credits", "period": "month"}})  # fmt: skip

    async def exa() -> StatusRow:
        key = _k(s.exa_api_key)
        if not key:
            return StatusRow("Exa", False, "not_set")
        await _count("exa", "status search")
        r = await c.post("https://api.exa.ai/search", headers={"x-api-key": key},
                         json={"query": "web design agency", "numResults": 1})  # fmt: skip
        cost = (js(r).get("costDollars") or {}).get("total")
        return StatusRow("Exa", True, "ok" if r.status_code == 200 else "failed", None,
                         f"1 search cost ${cost}; Exa exposes no balance endpoint; HTTP {r.status_code}")  # fmt: skip

    async def pagespeed() -> StatusRow:
        key = _k(s.google_pagespeed_api_key)
        if not key:
            return StatusRow("PageSpeed", False, "not_set")
        await _count("pagespeed", "status")
        r = await c.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                        params={"url": "https://example.com/", "key": key, "category": "seo"},
                        timeout=httpx.Timeout(90, connect=15))  # fmt: skip
        return StatusRow("PageSpeed", True, "ok" if r.status_code == 200 else "failed", None,
                         f"HTTP {r.status_code}; our cap {s.pagespeed_daily_cap}/day; no balance endpoint")  # fmt: skip

    async def companies_house() -> StatusRow:
        key = _k(s.companies_house_api_key)
        if not key:
            return StatusRow("Companies House", False, "not_set")
        r = await c.get("https://api.company-information.service.gov.uk/search/companies",
                        params={"q": "plumbing", "items_per_page": 1}, auth=(key, ""))  # fmt: skip
        rl = f"{r.headers.get('x-ratelimit-remain', '—')}/{r.headers.get('x-ratelimit-limit', '—')} per 5 min"
        return StatusRow(
            "Companies House",
            True,
            "ok" if r.status_code == 200 else "failed",
            rl,
            f"HTTP {r.status_code}",
        )

    async def apify() -> StatusRow:
        key = _k(s.apify_api_token)
        if not key:
            return StatusRow("Apify", False, "not_set")
        r = await c.get(
            "https://api.apify.com/v2/users/me/limits", headers={"Authorization": f"Bearer {key}"}
        )
        if r.status_code != 200:
            return StatusRow("Apify", True, "failed", None, f"limits HTTP {r.status_code}")
        d = js(r).get("data", {})
        used = _num((d.get("current") or {}).get("monthlyUsageUsd"))
        cap = _num((d.get("limits") or {}).get("maxMonthlyUsageUsd"))
        cyc = (d.get("monthlyUsageCycle") or {}).get("endAt")
        if used is None or cap is None:
            return StatusRow("Apify", True, "ok", None, f"cycle ends {cyc}")
        return StatusRow("Apify", True, "ok", f"${cap - used:.2f} of ${cap:g}", f"used ${used:.4f}; cycle ends {cyc}",
                         {"apify": {"remaining": round(cap - used, 4), "limit": cap, "unit": "usd", "period": "month"}})  # fmt: skip

    async def ensembledata() -> StatusRow:
        key = _k(s.ensembledata_api_token)
        if not key:
            return StatusRow("EnsembleData", False, "not_set")
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        r = await c.get(
            "https://ensembledata.com/apis/customer/get-used-units",
            params={"date": today, "token": key},
        )
        if r.status_code != 200:
            return StatusRow(
                "EnsembleData",
                True,
                "failed",
                None,
                f"used-units HTTP {r.status_code}: {r.text[:100]}",
            )
        data = js(r).get("data")
        used = (
            sum(v for v in data.values() if isinstance(v, (int, float)))
            if isinstance(data, dict)
            else _num(data)
        )
        if used is None:
            return StatusRow(
                "EnsembleData", True, "ok", None, f"used-units answered: {str(data)[:80]}"
            )
        rem = ENSEMBLEDATA_DAILY_UNITS - used
        return StatusRow("EnsembleData", True, "ok", f"{rem:g} units today",
                         f"used {used:g} units today (free trial {ENSEMBLEDATA_DAILY_UNITS}/day, resets 00:00 UTC)",
                         {"ensembledata": {"remaining": rem, "limit": ENSEMBLEDATA_DAILY_UNITS, "period": "day_utc"}})  # fmt: skip

    async def scrapecreators() -> StatusRow:
        key = _k(s.scrapecreators_api_key)
        if not key:
            return StatusRow("ScrapeCreators", False, "not_set")
        r = await c.get(
            "https://api.scrapecreators.com/v1/account/credit-balance", headers={"x-api-key": key}
        )
        if r.status_code != 200:
            return StatusRow(
                "ScrapeCreators", True, "failed", None, f"balance HTTP {r.status_code}"
            )
        b = js(r)
        rem = _num(b.get("creditCount", b.get("credits_remaining", b.get("credits"))))
        return StatusRow("ScrapeCreators", True, "ok", f"{rem:g} credits" if rem is not None else None,
                         "balance endpoint (free)", {"scrapecreators": {"remaining": rem, "unit": "credits"}})  # fmt: skip

    async def socialcrawl() -> StatusRow:
        key = _k(s.socialcrawl_api_key)
        if not key:
            return StatusRow("SocialCrawl", False, "not_set")
        await _count("socialcrawl", "status search")
        r = await c.get("https://www.socialcrawl.dev/v1/tiktok/search", params={"query": "need a logo", "limit": 1},
                        headers={"x-api-key": key})  # fmt: skip
        b = js(r)
        rem = _num(b.get("credits_remaining"))
        return StatusRow("SocialCrawl", True, "ok" if r.status_code == 200 else "failed",
                         f"{rem:g} credits" if rem is not None else None,
                         f"1 search used {b.get('credits_used')} (cached={b.get('cached')}); HTTP {r.status_code}",
                         {"socialcrawl": {"remaining": rem, "unit": "credits"}})  # fmt: skip

    async def hikerapi() -> StatusRow:
        key = _k(s.hikerapi_access_key)
        if not key:
            return StatusRow(
                "HikerAPI",
                False,
                "not_set",
                None,
                "HIKERAPI_ACCESS_KEY is empty: Instagram via HikerAPI stays off",
            )
        r = await c.get(
            f"{s.hikerapi_base_url.rstrip('/')}/sys/balance", headers={"x-access-key": key}
        )
        return StatusRow(
            "HikerAPI",
            True,
            "ok" if r.status_code == 200 else "failed",
            r.text[:60],
            f"HTTP {r.status_code}",
        )

    async def zerobounce() -> StatusRow:
        key = _k(s.email_verification_api_key)
        if not key:
            return StatusRow("ZeroBounce", False, "not_set")
        r = await c.get("https://api.zerobounce.net/v2/getcredits", params={"api_key": key})
        rem = _num(js(r).get("Credits"))
        ok = r.status_code == 200 and rem is not None and rem >= 0
        return StatusRow("ZeroBounce", True, "ok" if ok else "failed", f"{rem:g} credits" if rem is not None else None,
                         "getcredits (free)", {"zerobounce": {"remaining": rem, "unit": "credits"}} if ok else None)  # fmt: skip

    async def resend() -> StatusRow:
        key = _k(s.resend_api_key)
        if not key:
            return StatusRow("Resend", False, "not_set")
        r = await c.get(
            "https://api.resend.com/domains", headers={"Authorization": f"Bearer {key}"}
        )
        if r.status_code == 200:
            return StatusRow(
                "Resend", True, "ok", None, f"key valid; {len(js(r).get('data', []))} domains"
            )
        name = js(r).get("name") or ""
        valid = name == "restricted_api_key"  # a sending-only key cannot list domains but is valid
        return StatusRow("Resend", True, "ok" if valid else "failed", None,
                         "key valid (sending-only)" if valid else f"HTTP {r.status_code} {name}")  # fmt: skip

    async def gmail() -> StatusRow:
        sender, pw = s.gmail_sender.strip(), _k(s.gmail_app_password)
        if not sender or not pw:
            return StatusRow(
                "Gmail", bool(pw), "not_set", None, "GMAIL_SENDER or GMAIL_APP_PASSWORD empty"
            )

        def probe() -> str:
            imap = imaplib.IMAP4_SSL(s.gmail_imap_host, s.gmail_imap_port, timeout=30)
            try:
                imap.login(sender, pw)
                _typ, lines = imap.list()
                drafts = next((ln.decode() for ln in lines or []
                               if isinstance(ln, bytes) and rb"\Drafts" in ln), None)  # fmt: skip
            finally:
                with contextlib.suppress(OSError, imaplib.IMAP4.error):
                    imap.logout()
            smtp = smtplib.SMTP(s.gmail_smtp_host, s.gmail_smtp_port, timeout=30)
            try:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(sender, pw)
            finally:
                with contextlib.suppress(OSError, smtplib.SMTPException):
                    smtp.quit()
            folder = drafts.rsplit('"', 2)[-2] if drafts and drafts.count('"') >= 2 else drafts
            return f"IMAP login ok, Drafts folder {folder!r}; SMTP {s.gmail_smtp_port} STARTTLS login ok; nothing sent"

        try:
            note = await asyncio.to_thread(probe)
        except (OSError, imaplib.IMAP4.error, smtplib.SMTPException) as exc:
            return StatusRow(
                "Gmail", True, "failed", None, f"{type(exc).__name__}: {str(exc)[:140]}"
            )
        return StatusRow("Gmail", True, "ok", f"{s.gmail_daily_send_cap}/day (our cap)", note)

    async def mailpit() -> StatusRow:
        if not s.mailpit_api_url:
            return StatusRow("Mailpit", False, "not_set")
        r = await c.get(f"{s.mailpit_api_url.rstrip('/')}/api/v1/info")
        b = js(r)
        return StatusRow("Mailpit", None, "ok" if r.status_code == 200 else "failed", None,
                         f"{b.get('Messages')} messages, version {b.get('Version')}")  # fmt: skip

    async def overpass(url: str) -> StatusRow:
        label = f"Overpass {httpx.URL(url).host}"
        q = '[out:json][timeout:15];node["amenity"="cafe"](51.500,-0.130,51.505,-0.125);out 1;'
        t = time.monotonic()
        try:
            r = await c.post(url, data={"data": q}, timeout=httpx.Timeout(30, connect=10))
        except httpx.HTTPError as exc:
            return StatusRow(
                label,
                None,
                "failed",
                None,
                f"{type(exc).__name__} after {time.monotonic() - t:.0f}s",
            )
        n = len(js(r).get("elements", [])) if r.status_code == 200 else 0
        return StatusRow(label, None, "ok" if r.status_code == 200 else "failed", None,
                         f"HTTP {r.status_code} in {time.monotonic() - t:.1f}s, {n} element(s); no key")  # fmt: skip

    async def fr() -> StatusRow:
        r = await c.get(
            f"{s.fr_recherche_entreprises_url.rstrip('/')}/search",
            params={"q": "boulangerie", "per_page": 1},
        )
        return StatusRow("FR recherche-entreprises", None, "ok" if r.status_code == 200 else "failed", None,
                         f"HTTP {r.status_code}, {js(r).get('total_results')} matches; no key, ≤{s.fr_recherche_max_rps}/s")  # fmt: skip

    async def brreg() -> StatusRow:
        r = await c.get(
            f"{s.no_brreg_api_url.rstrip('/')}/enheter", params={"size": 1, "naeringskode": "43.22"}
        )
        total = (js(r).get("page") or {}).get("totalElements")
        return StatusRow("NO brreg", None, "ok" if r.status_code == 200 else "failed", None,
                         f"HTTP {r.status_code}, {total} units for 43.22; no key")  # fmt: skip

    async def head(label: str, url: str) -> StatusRow:
        r = await c.head(url, follow_redirects=True)
        size = r.headers.get("content-length")
        mb = f"{int(size) / 1_000_000:.1f} MB" if size else "size —"
        return StatusRow(
            label,
            None,
            "ok" if r.status_code == 200 else "failed",
            None,
            f"HEAD {r.status_code}, {mb}; nothing downloaded",
        )

    mirrors = [
        s.overpass_api_url,
        *[u.strip() for u in s.overpass_fallback_urls.split(",") if u.strip()],
    ]
    jobs: list[tuple[str, Callable[[], Awaitable[StatusRow]]]] = [
        ("Gemini text", lambda: gemini(s.gemini_text_model, "Gemini text")),
        ("Gemini logo", lambda: gemini(s.gemini_logo_model, "Gemini logo")),
        ("Groq", groq),
        ("Serper", lambda: serper("serper_api_key", "Serper")),
        ("Serper (2nd key)", lambda: serper("serper_api_key_fallback", "Serper (2nd key)")),
        ("SerpAPI", serpapi), ("ScrapingDog", scrapingdog), ("Searlo", searlo), ("Tavily", tavily),
        ("Exa", exa), ("PageSpeed", pagespeed), ("Companies House", companies_house), ("Apify", apify),
        ("EnsembleData", ensembledata), ("ScrapeCreators", scrapecreators), ("SocialCrawl", socialcrawl),
        ("HikerAPI", hikerapi), ("ZeroBounce", zerobounce), ("Resend", resend), ("Gmail", gmail),
        ("Mailpit", mailpit),
        *[(f"Overpass {u}", (lambda u=u: overpass(u))) for u in mirrors],
        ("FR recherche-entreprises", fr), ("NO brreg", brreg),
        ("GeoNames cities15000", lambda: head("GeoNames cities15000", "https://download.geonames.org/export/dump/cities15000.zip")),
        ("Geofabrik", lambda: head("Geofabrik (norway)", f"{s.geofabrik_base_url.rstrip('/')}/europe/norway-latest.osm.pbf")),
    ]  # fmt: skip
    if free_only:
        jobs = [(n, f) for n, f in jobs if n in FREE_CHECKS]

    async def guarded(name: str, fn: Callable[[], Awaitable[StatusRow]]) -> StatusRow:
        try:
            return await asyncio.wait_for(fn(), timeout=120)
        except Exception as exc:
            return StatusRow(name, True, "failed", None, f"{type(exc).__name__}: {str(exc)[:140]}")

    try:
        rows = list(await asyncio.gather(*(guarded(n, f) for n, f in jobs)))
    finally:
        await c.aclose()
    await save_balances(rows)
    return rows


async def save_balances(rows: list[StatusRow]) -> None:
    """Store every reported balance as a live cap; Serper's two keys are summed."""
    serper = [
        r.balance["serper_part"]
        for r in rows
        if r.balance and r.balance.get("serper_part") is not None
    ]
    if serper:
        await quota.save_balance("serper", remaining=float(sum(serper)), unit="credits",
                                 note=f"{len(serper)} key(s) summed")  # fmt: skip
    for r in rows:
        for provider, kw in (r.balance or {}).items():
            if provider == "serper_part" or not isinstance(kw, dict) or kw.get("remaining") is None:
                continue
            await quota.save_balance(provider, **{k: v for k, v in kw.items() if v is not None})


def render(rows: list[StatusRow]) -> str:
    head = (
        f"{'provider':30} | {'key set':7} | {'live result':11} | {'credits/limit left':30} | notes"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        key = "n/a" if r.key_set is None else "yes" if r.key_set else "no"
        lines.append(
            f"{r.provider[:30]:30} | {key:7} | {r.result:11} | {(r.left or '—')[:30]:30} | {r.notes}"
        )
    return "\n".join(lines)
