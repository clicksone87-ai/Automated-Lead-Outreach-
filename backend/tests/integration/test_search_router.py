"""Search router and LLM fallback with recorded responses (no live calls): SerpAPI, ScrapingDog,
Searlo, Tavily, Serper key rotation, per-provider caps, provider error log, Groq fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import select

from app.adapters.groq import ENDPOINT as GROQ_ENDPOINT
from app.adapters.groq import FallbackLLM, GroqLLM
from app.adapters.llm import LLMOutputError, LLMRequest
from app.adapters.registry import build_adapters
from app.adapters.search_free import FallbackSearch, SerperSearch
from app.adapters.search_router import (
    ScrapingDogSearch,
    SearloSearch,
    SerpApiSearch,
    TavilySearch,
)
from app.config import Settings
from app.db.models import Cost, PolicyDecision
from app.db.session import session_scope
from app.orchestrator import quota
from app.orchestrator.errors import TransientAgentError
from app.orchestrator.quota import FreeQuotaExceededError

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


ORGANIC = [{"title": "Need a site", "link": "https://forum.test/1", "snippet": "s", "date": "1d"}]


async def _providers_used() -> list[str]:
    async with session_scope() as s:
        return list((await s.execute(select(Cost.provider).order_by(Cost.id))).scalars())


@respx.mock
async def test_serpapi_request_shape_and_usage_counted() -> None:
    route = respx.get(SerpApiSearch.ENDPOINT).mock(
        return_value=httpx.Response(200, json={"organic_results": ORGANIC})
    )
    async with session_scope() as s:
        res = await SerpApiSearch("k").search(
            s, "need a website", agent="t", country="UK", language="en", freshness="pw", page=2
        )
    params = route.calls[0].request.url.params
    assert params["engine"] == "google" and params["gl"] == "gb" and params["hl"] == "en"
    assert params["tbs"] == "qdr:w" and params["start"] == "10"
    assert res[0].url == "https://forum.test/1" and res[0].age == "1d"
    assert await _providers_used() == ["serpapi"]


@respx.mock
async def test_scrapingdog_and_searlo_request_shapes() -> None:
    dog = respx.get(ScrapingDogSearch.ENDPOINT).mock(
        return_value=httpx.Response(200, json={"organic_results": ORGANIC})
    )
    searlo = respx.get(SearloSearch.ENDPOINT).mock(
        return_value=httpx.Response(200, json={"organic": ORGANIC})
    )
    async with session_scope() as s:
        a = await ScrapingDogSearch("dk").search(s, "q", agent="t", country="DE", count=50)
        b = await SearloSearch("sk").search(s, "q", agent="t", country="FR", count=5)
    dp = dog.calls[0].request.url.params
    assert dp["query"] == "q" and dp["country"] == "de" and dp["results"] == "10"
    assert dp["page"] == "0"
    sreq = searlo.calls[0].request
    assert sreq.headers["x-api-key"] == "sk" and "sk" not in str(sreq.url)
    assert sreq.url.params["limit"] == "5" and sreq.url.params["gl"] == "fr"
    assert a[0].url == b[0].url == "https://forum.test/1"
    assert await _providers_used() == ["scrapingdog", "searlo"]


@respx.mock
async def test_tavily_body_uses_country_name_and_time_range() -> None:
    route = respx.post(TavilySearch.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://t.test",
                        "content": "c",
                        "published_date": "2026-11-19",
                    }
                ]
            },
        )
    )
    async with session_scope() as s:
        res = await TavilySearch("tk").search(s, "q", agent="t", country="GB", freshness="pd")
    req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer tk"
    assert body["country"] == "united kingdom" and body["time_range"] == "day"
    assert res[0].description == "c" and res[0].age == "2026-11-19"


@respx.mock
async def test_serper_second_key_takes_over_when_first_is_used_up() -> None:
    route = respx.post("https://google.serper.dev/search").mock(
        side_effect=[httpx.Response(402), httpx.Response(200, json={"organic": ORGANIC})]
    )
    async with session_scope() as s:
        res = await SerperSearch("first", fallback_key="second").search(s, "q", agent="t")
    keys = [c.request.headers["x-api-key"] for c in route.calls]
    assert keys == ["first", "second"]
    assert res[0].url == "https://forum.test/1"


@respx.mock
async def test_router_falls_back_in_order_and_logs_the_error() -> None:
    respx.post("https://google.serper.dev/search").mock(return_value=httpx.Response(429))
    respx.get(SerpApiSearch.ENDPOINT).mock(return_value=httpx.Response(401))
    respx.get(ScrapingDogSearch.ENDPOINT).mock(
        return_value=httpx.Response(200, json={"organic_results": ORGANIC})
    )
    router = FallbackSearch([SerperSearch("k"), SerpApiSearch("k"), ScrapingDogSearch("k")])
    async with session_scope() as s:
        res = await router.search(s, "q", agent="t")
    assert res[0].url == "https://forum.test/1"
    async with session_scope() as s:
        errs = (
            await s.execute(
                select(PolicyDecision.action, PolicyDecision.reason)
                .where(PolicyDecision.check == "provider_error")
                .order_by(PolicyDecision.id)
            )
        ).all()
    assert [e.action for e in errs] == ["serper", "serpapi"]
    assert "401" in errs[1].reason


async def test_cap_reached_moves_to_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(serpapi_monthly_cap=1)
    monkeypatch.setattr(quota, "get_settings", lambda: settings)
    async with session_scope() as s:
        await quota.consume(s, "serpapi", agent="t")
        with pytest.raises(FreeQuotaExceededError):
            await quota.consume(s, "serpapi", agent="t")
        rep = {r["scope"]: r for r in await quota.report(s, settings)}
    assert rep["serpapi"]["used"] == 1 and rep["serpapi"]["cap"] == 1

    with respx.mock:
        respx.post(TavilySearch.ENDPOINT).mock(
            return_value=httpx.Response(200, json={"results": [{"url": "https://t.test"}]})
        )
        async with session_scope() as s:
            res = await FallbackSearch([SerpApiSearch("k"), TavilySearch("k")]).search(
                s, "q", agent="t"
            )
    assert res[0].url == "https://t.test"


def test_registry_chain_follows_search_provider_order() -> None:
    keys = {f"{p}_api_key": "k" for p in ("serper", "serpapi", "scrapingdog", "searlo", "tavily")}
    s = _settings(**keys, exa_api_key="k", search_provider_order="tavily,serper,searlo")
    search = build_adapters(s).search
    assert isinstance(search, FallbackSearch)
    assert [a.name for a in search.adapters] == ["tavily", "serper", "searlo"]


class Ping(BaseModel):
    ok: bool


def _groq_ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@respx.mock
async def test_groq_structured_output_with_repair() -> None:
    route = respx.post(GROQ_ENDPOINT).mock(
        side_effect=[_groq_ok('{"nope": 1}'), _groq_ok('{"ok": true}')]
    )
    async with session_scope() as s:
        out = await GroqLLM("gk").structured(s, LLMRequest(agent="t", task="x"), Ping)
    assert out.ok
    first = json.loads(route.calls[0].request.content)
    assert first["model"] == "openai/gpt-oss-120b"
    assert first["response_format"] == {"type": "json_object"}
    assert route.calls[0].request.headers["authorization"] == "Bearer gk"
    assert await _providers_used() == ["groq", "groq"]


class _Primary:
    name = "gemini"
    is_mock = False

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def structured(self, session: Any, request: LLMRequest, schema: Any) -> Any:
        raise self.exc


@respx.mock
async def test_fallback_llm_uses_groq_for_text_when_gemini_is_out() -> None:
    respx.post(GROQ_ENDPOINT).mock(return_value=_groq_ok('{"ok": true}'))
    llm = FallbackLLM(_Primary(FreeQuotaExceededError("gemini", 450, 450, 1)), GroqLLM("gk"))
    async with session_scope() as s:
        assert (await llm.structured(s, LLMRequest(agent="t", task="x"), Ping)).ok
    async with session_scope() as s:
        err = (
            await s.execute(select(PolicyDecision).where(PolicyDecision.check == "provider_error"))
        ).scalar_one()
    assert err.action == "gemini" and "cap reached for gemini" in (err.reason or "")


async def test_fallback_llm_keeps_logos_and_vision_on_the_primary() -> None:
    llm = FallbackLLM(_Primary(TransientAgentError("gemini down")), GroqLLM("gk"))
    async with session_scope() as s:
        with pytest.raises(TransientAgentError):
            await llm.structured(s, LLMRequest(agent="t", task="x", kind="logo"), Ping)
        with pytest.raises(TransientAgentError):
            await llm.structured(s, LLMRequest(agent="t", task="x", images=[b"png"]), Ping)
        with pytest.raises(LLMOutputError):
            await GroqLLM("gk").structured(s, LLMRequest(agent="t", task="x", images=[b"p"]), Ping)
