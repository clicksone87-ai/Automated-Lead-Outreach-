"""Live caps: what a provider reports as left binds the quota layer; Gemini learns each model's
real daily limit from Google's 429 and moves on to the next model. No live calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import select, update

from app.adapters.gemini import BASE, GeminiLLM
from app.adapters.llm import LLMRequest
from app.config import Settings
from app.db.models import Cost, ProviderBalance
from app.db.session import session_scope
from app.orchestrator import quota
from app.orchestrator.quota import FreeQuotaExceededError
from app.services import account_status

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


async def test_reported_balance_binds_with_cost_per_call() -> None:
    await quota.save_balance("scrapingdog", remaining=12, unit="credits", cost_per_call=5)
    async with session_scope() as s:
        await quota.consume(s, "scrapingdog", agent="t")  # 5 of 12
        await quota.consume(s, "scrapingdog", agent="t")  # 10 of 12
        with pytest.raises(FreeQuotaExceededError) as exc:
            await quota.consume(s, "scrapingdog", agent="t")  # would be 15
    assert exc.value.scope == "scrapingdog:live"
    async with session_scope() as s:
        assert (
            len(
                (await s.execute(select(Cost).where(Cost.provider == "scrapingdog")))
                .scalars()
                .all()
            )
            == 2
        )


async def test_daily_balance_stops_applying_after_the_reset_and_usd_uses_credit() -> None:
    await quota.save_balance("ensembledata", remaining=0, period="day_utc")
    async with session_scope() as s:
        await s.execute(update(ProviderBalance).where(ProviderBalance.provider == "ensembledata")
                        .values(checked_at=datetime.now(UTC) - timedelta(days=1)))  # fmt: skip
    async with session_scope() as s:
        await quota.consume(s, "ensembledata", agent="t")  # yesterday's zero no longer applies
    await quota.save_balance("apify", remaining=0.05, unit="usd", period="month")
    async with session_scope() as s:
        await quota.consume(
            s, "apify", agent="t", credit_usd=0.04, settings=_settings(apify_monthly_cap_usd=10)
        )
        with pytest.raises(FreeQuotaExceededError):
            await quota.consume(
                s, "apify", agent="t", credit_usd=0.02, settings=_settings(apify_monthly_cap_usd=10)
            )


class Ping(BaseModel):
    ok: bool


def _gemini() -> GeminiLLM:
    async def no_sleep(_: float) -> None:
        return None

    return GeminiLLM("k", text_model="m-text", vision_model="m-text", logo_model="m-logo",
                     fallback_models=["m-fb"], rpm=6000, sleep=no_sleep)  # fmt: skip


def _daily_429(model: str, limit: int) -> httpx.Response:
    return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
             "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
             "quotaDimensions": {"model": model, "location": "global"}, "quotaValue": str(limit)}]}]}})  # fmt: skip


def _ok() -> httpx.Response:
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({"ok": True})}]}}]}
    )


@respx.mock
async def test_gemini_learns_a_models_daily_limit_and_uses_the_next_model() -> None:
    logo = respx.post(f"{BASE}/models/m-logo:generateContent").mock(
        return_value=_daily_429("m-logo", 20)
    )
    fb = respx.post(f"{BASE}/models/m-fb:generateContent").mock(return_value=_ok())
    llm = _gemini()
    async with session_scope() as s:
        out = await llm.structured(s, LLMRequest(agent="branding", task="x", kind="logo"), Ping)
    assert out.ok and logo.call_count == 1 and fb.call_count == 1
    async with session_scope() as s:
        bal = await s.get(ProviderBalance, "gemini:m-logo")
    assert (
        bal is not None and bal.limit == 20 and bal.remaining == 0 and bal.period == "day_pacific"
    )
    # The exhausted model is skipped today without another call to Google.
    async with session_scope() as s:
        await llm.structured(s, LLMRequest(agent="branding", task="y", kind="logo"), Ping)
    assert logo.call_count == 1 and fb.call_count == 2


@respx.mock
async def test_all_models_at_their_daily_limit_hands_text_to_the_fallback() -> None:
    for m in ("m-text", "m-fb"):
        respx.post(f"{BASE}/models/{m}:generateContent").mock(return_value=_daily_429(m, 500))
    with pytest.raises(FreeQuotaExceededError):  # FallbackLLM catches this and uses Groq
        async with session_scope() as s:
            await _gemini().structured(s, LLMRequest(agent="t", task="x"), Ping)


async def test_learned_limit_counts_todays_calls() -> None:
    await quota.save_balance(
        "gemini:m-text", remaining=None, limit=2, unit="requests", period="day_pacific"
    )
    assert not await quota.model_exhausted("m-text")
    async with session_scope() as s:
        for _ in range(2):
            await quota.consume(s, "gemini", agent="t", description="m-text text")
    assert await quota.model_exhausted("m-text")
    assert not await quota.model_exhausted("m-other")


@respx.mock
async def test_account_status_stores_reported_balances() -> None:
    respx.get("https://google.serper.dev/account").mock(
        return_value=httpx.Response(200, json={"balance": 100, "rateLimit": 5})
    )
    respx.get("https://api.scrapingdog.com/account").mock(
        return_value=httpx.Response(
            200, json={"requestUsed": 20, "requestLimit": 100, "pack": "free"}
        )
    )
    respx.get(url__startswith="https://ensembledata.com/apis/customer/get-used-units").mock(
        return_value=httpx.Response(200, json={"data": {"tiktok": 2, "reddit": 1}})
    )
    respx.get("https://api.tavily.com/usage").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": {"usage": 4, "limit": None},
                "account": {"current_plan": "Researcher", "plan_usage": 4, "plan_limit": 1000},
            },
        )
    )
    for url in (
        "https://serpapi.com/account.json",
        "https://api.apify.com/v2/users/me/limits",
        "https://api.scrapecreators.com/v1/account/credit-balance",
        "https://api.zerobounce.net/v2/getcredits",
    ):
        respx.get(url).mock(return_value=httpx.Response(500))  # failures are reported, not stored
    s = _settings(serper_api_key="a", serper_api_key_fallback="b", scrapingdog_api_key="k", ensembledata_api_token="t",
                  tavily_api_key="k", serpapi_api_key="k", apify_api_token="k", scrapecreators_api_key="k",
                  email_verification_api_key="k")  # fmt: skip
    rows = {r.provider: r for r in await account_status.check_all(s, free_only=True)}
    assert (
        rows["ScrapingDog"].left == "80 credits" and rows["EnsembleData"].left == "47 units today"
    )
    assert rows["SerpAPI"].result == "failed"
    async with session_scope() as db:
        bals = {b.provider: b for b in (await db.execute(select(ProviderBalance))).scalars()}
    assert bals["serper"].remaining == 200  # both keys summed
    assert bals["scrapingdog"].remaining == 80 and bals["scrapingdog"].cost_per_call == 5
    assert bals["ensembledata"].remaining == 47 and bals["ensembledata"].period == "day_utc"
    assert bals["tavily"].remaining == 996 and bals["tavily"].period == "month"
    assert "serpapi" not in bals and "apify" not in bals
