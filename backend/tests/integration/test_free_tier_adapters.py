"""Free-tier adapters with recorded responses (no live calls): Gemini, Serper, Exa, Apify,
quotas, local verifier, SMTP MIME and the Mailpit inbox."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dns.resolver
import httpx
import pytest
import respx
from pydantic import BaseModel
from sqlalchemy import func, select

from app.adapters.apify import ApifyClient, _run_cost
from app.adapters.email import EmailAttachment, OutgoingEmail
from app.adapters.email_free import LocalVerifier, MailpitMailbox, build_mime
from app.adapters.gemini import BASE, GeminiLLM, GeminiUnavailableError, inline_refs
from app.adapters.llm import LLMRequest
from app.adapters.search_free import ExaSearch, FallbackSearch, SerperSearch
from app.agents.discovery.sources.social_apify import ApifyFacebookSource, _parse_dt
from app.config import Settings
from app.db.models import Cost, VerificationStatus
from app.db.session import session_scope
from app.orchestrator import quota
from app.orchestrator.quota import FreeQuotaExceededError

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


class Brief(BaseModel):
    service: str
    score: int


class Outer(BaseModel):
    items: list[Brief]


def _gemini(**kw: Any) -> GeminiLLM:
    async def no_sleep(_: float) -> None:
        return None

    return GeminiLLM(
        "k",
        text_model="m-text",
        vision_model="m-vision",
        logo_model="m-logo",
        fallback_models=["m-fallback"],
        rpm=6000,
        sleep=no_sleep,
        **kw,
    )


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    )


def test_inline_refs_removes_defs() -> None:
    schema = inline_refs(Outer.model_json_schema())
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert schema["properties"]["items"]["items"]["properties"]["score"]["type"] == "integer"


@respx.mock
async def test_gemini_structured_output_and_quota_counted() -> None:
    route = respx.post(f"{BASE}/models/m-text:generateContent").mock(
        return_value=_ok({"service": "website", "score": 80})
    )
    async with session_scope() as s:
        out = await _gemini().structured(
            s, LLMRequest(agent="requirements", task="t", untrusted={"post": "x"}), Brief
        )
        assert out == Brief(service="website", score=80)
        body = json.loads(route.calls[0].request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert "responseJsonSchema" in body["generationConfig"]
        assert "untrusted" in body["systemInstruction"]["parts"][0]["text"].lower()
        n = (
            await s.execute(select(func.count()).select_from(Cost).where(Cost.provider == "gemini"))
        ).scalar_one()
        assert n == 1


@respx.mock
async def test_gemini_429_retry_then_success() -> None:
    err = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"},
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
    ]}}  # fmt: skip
    respx.post(f"{BASE}/models/m-text:generateContent").mock(
        side_effect=[httpx.Response(429, json=err), _ok({"service": "logo", "score": 1})]
    )
    async with session_scope() as s:
        out = await _gemini().structured(s, LLMRequest(agent="a", task="t"), Brief)
        assert out.service == "logo"


@respx.mock
async def test_gemini_daily_quota_pauses_instead_of_retrying() -> None:
    err = {"error": {"code": 429, "details": [{"@type": "x.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}  # fmt: skip
    respx.post(f"{BASE}/models/m-text:generateContent").mock(
        return_value=httpx.Response(429, json=err)
    )
    async with session_scope() as s:
        with pytest.raises(FreeQuotaExceededError):
            await _gemini().structured(s, LLMRequest(agent="a", task="t"), Brief)


@respx.mock
async def test_gemini_falls_back_to_next_model_on_503() -> None:
    respx.post(f"{BASE}/models/m-text:generateContent").mock(
        return_value=httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
    )
    respx.post(f"{BASE}/models/m-fallback:generateContent").mock(
        return_value=_ok({"service": "seo", "score": 5})
    )
    async with session_scope() as s:
        out = await _gemini().structured(s, LLMRequest(agent="a", task="t"), Brief)
        assert out.service == "seo"


@respx.mock
async def test_gemini_all_models_down_raises() -> None:
    respx.post(url__regex=r".*:generateContent").mock(
        return_value=httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}})
    )
    async with session_scope() as s:
        with pytest.raises(GeminiUnavailableError):
            await _gemini().structured(s, LLMRequest(agent="a", task="t"), Brief)


async def test_quota_cap_enforced_and_reported() -> None:
    settings = _settings(serper_free_credits=2)
    async with session_scope() as s:
        await quota.consume(s, "serper", agent="t", settings=settings)
        await quota.consume(s, "serper", agent="t", settings=settings)
        with pytest.raises(FreeQuotaExceededError):
            await quota.consume(s, "serper", agent="t", settings=settings)
        rep = {r["scope"]: r for r in await quota.report(s, settings)}
        assert rep["serper"]["used"] == 2 and rep["serper"]["remaining"] == 0


async def test_apify_per_source_share_caps() -> None:
    settings = _settings(apify_monthly_cap_usd=1.0)  # x share = 0.2
    async with session_scope() as s:
        await quota.consume(
            s, "apify", agent="d", credit_usd=0.15, scope="apify:social_x", settings=settings
        )
        with pytest.raises(FreeQuotaExceededError):
            await quota.consume(
                s, "apify", agent="d", credit_usd=0.1, scope="apify:social_x", settings=settings
            )
        # another source still has its own share
        await quota.consume(
            s, "apify", agent="d", credit_usd=0.1, scope="apify:social_reddit", settings=settings
        )


@respx.mock
async def test_serper_search_and_credits() -> None:
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "Need a website",
                        "link": "https://forum.test/1",
                        "snippet": "s",
                        "date": "2 days ago",
                    }
                ]
            },
        )
    )
    respx.get("https://google.serper.dev/account").mock(
        return_value=httpx.Response(200, json={"balance": 2499, "rateLimit": 5})
    )
    serper = SerperSearch("k")
    async with session_scope() as s:
        res = await serper.search(s, "q", agent="t", country="UK", freshness="pw")
    assert res[0].url == "https://forum.test/1"
    sent = json.loads(respx.calls[0].request.content)
    assert sent["gl"] == "gb" and sent["tbs"] == "qdr:w" and sent["num"] == 10
    assert await serper.credits() == 2499


@respx.mock
async def test_exa_request_shape_and_cost_settled() -> None:
    route = respx.post(ExaSearch.ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://x.test/p",
                        "highlights": ["need a site"],
                        "publishedDate": "2026-11-10",
                    }
                ],
                "costDollars": {"total": 0.007},
            },
        )
    )
    async with session_scope() as s:
        res = await ExaSearch("k").search(s, "need a website plumber", agent="d", freshness="pm")
        cost = (await s.execute(select(Cost.credit_usd).where(Cost.provider == "exa"))).scalar_one()
    body = json.loads(route.calls[0].request.content)
    assert body["type"] == "auto" and body["contents"] == {"highlights": True}
    assert body["objective"] and "startPublishedDate" in body and "numResults" not in body
    assert res[0].description == "need a site" and cost == pytest.approx(0.007)


@respx.mock
async def test_fallback_search_uses_exa_when_serper_fails() -> None:
    respx.post("https://google.serper.dev/search").mock(return_value=httpx.Response(500))
    respx.post(ExaSearch.ENDPOINT).mock(
        return_value=httpx.Response(
            200, json={"results": [{"url": "https://e.test", "title": "E"}]}
        )
    )
    async with session_scope() as s:
        res = await FallbackSearch([SerperSearch("k"), ExaSearch("k")]).search(s, "q", agent="t")
    assert res[0].url == "https://e.test"


def test_apify_cost_from_ppe_events() -> None:
    run = {
        "usageTotalUsd": 0.001,
        "chargedEventCounts": {"post": 4, "actor-start": 1},
        "pricingInfo": {"pricingPerEvent": {"actorChargeEvents": {
            "post": {"eventTieredPricingUsd": {"FREE": {"tieredEventPriceUsd": 0.005}}},
            "actor-start": {"eventPriceUsd": 0.001}}}},
    }  # fmt: skip
    assert _run_cost(run) == pytest.approx(0.021)


@respx.mock
async def test_apify_run_actor_caps_and_settles() -> None:
    respx.get("https://api.apify.com/v2/users/me/limits").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"current": {"monthlyUsageUsd": 0.2}, "limits": {"maxMonthlyUsageUsd": 5}}
            },
        )
    )
    start = respx.post("https://api.apify.com/v2/acts/apify~facebook-search-scraper/runs").mock(
        return_value=httpx.Response(
            201,
            json={
                "data": {
                    "id": "r1",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "d1",
                    "usageTotalUsd": 0.004,
                }
            },
        )
    )
    respx.get("https://api.apify.com/v2/datasets/d1/items").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "postId": "1",
                    "url": "https://facebook.com/p/1",
                    "text": "Looking for someone to build a website",
                }
            ],
        )
    )
    async with session_scope() as s:
        run = await ApifyClient("t").run_actor(
            s, "apify/facebook-search-scraper", {"categories": ["x"]},
            source="social_facebook", max_items=8, max_charge_usd=0.06,
        )  # fmt: skip
        credit = (
            await s.execute(select(Cost.credit_usd).where(Cost.provider == "apify"))
        ).scalar_one()
    assert run.items and credit == pytest.approx(0.004)
    params = dict(start.calls[0].request.url.params)
    assert params["maxItems"] == "8" and params["maxTotalChargeUsd"] == "0.0600"


def test_facebook_mapping_and_dates() -> None:
    lead = ApifyFacebookSource._map(
        {"postId": "9", "url": "https://www.facebook.com/x/posts/9", "text": "Need a website",
         "time": "2026-11-16T15:39:23.000Z", "user": {"name": "Mike"}}, "search")  # fmt: skip
    assert lead is not None and lead.author == "Mike"
    assert lead.evidence_data["platform"] == "facebook" and lead.posted_at is not None
    assert _parse_dt("Mon Nov 16 15:39:23 +0000 2026") is not None
    assert ApifyFacebookSource._map({"url": "u", "text": ""}, "search") is None


class _MX:
    def __init__(self, pref: int, host: str) -> None:
        self.preference, self.exchange = pref, host


class _Resolver:
    def __init__(self, result: Any) -> None:
        self.result = result

    async def resolve(self, domain: str, rtype: str, lifetime: float) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_local_verifier_syntax_and_mx() -> None:
    ok = await LocalVerifier(_Resolver([_MX(10, "mx.biz.com.")])).verify(
        None, "a@biz.com", agent="t"
    )  # type: ignore[arg-type]
    assert ok.status == VerificationStatus.VALID and ok.sub_status == "mx.biz.com"
    bad = await LocalVerifier(_Resolver([])).verify(None, "not-an-email", agent="t")  # type: ignore[arg-type]
    assert bad.status == VerificationStatus.INVALID
    nx = await LocalVerifier(_Resolver(dns.resolver.NXDOMAIN())).verify(
        None, "a@nope.zzz", agent="t"
    )  # type: ignore[arg-type]
    assert nx.status == VerificationStatus.INVALID
    null = await LocalVerifier(_Resolver([_MX(0, ".")])).verify(None, "a@null.com", agent="t")  # type: ignore[arg-type]
    assert null.status == VerificationStatus.INVALID


def test_smtp_mime_has_identity_unsubscribe_and_attachments() -> None:
    msg = build_mime(
        OutgoingEmail(
            from_address="Test Agency <outreach@test-agency.local>",
            to="owner@biz.com",
            subject="Hello",
            html="<p>Hi</p>",
            text="Hi",
            reply_to="replies@test-agency.local",
            headers={"List-Unsubscribe": "<http://localhost:8000/u/x>"},
            attachments=[
                EmailAttachment(
                    filename="audit.pdf", content=b"%PDF", content_type="application/pdf"
                )
            ],
            idempotency_key="k",
        ),
        "<id@test-agency.local>",
    )
    assert msg["Reply-To"] == "replies@test-agency.local"
    assert msg["List-Unsubscribe"].startswith("<http")
    assert [p.get_filename() for p in msg.iter_attachments()] == ["audit.pdf"]


@respx.mock
async def test_mailpit_mailbox_reads_only_our_address() -> None:
    raw = (b"From: Owner <owner@biz.com>\r\nTo: replies@test-agency.local\r\nSubject: Re: Hello\r\n"
           b"Message-ID: <r1@biz.com>\r\nIn-Reply-To: <id@test-agency.local>\r\n\r\nYes please, send a quote.")  # fmt: skip
    search = respx.get("http://mailpit:8025/api/v1/search").mock(
        return_value=httpx.Response(200, json={"messages": [{"ID": "abc"}]})
    )
    respx.get("http://mailpit:8025/api/v1/message/abc/raw").mock(
        return_value=httpx.Response(200, content=raw)
    )
    mark = respx.put("http://mailpit:8025/api/v1/messages").mock(return_value=httpx.Response(200))
    box = MailpitMailbox(
        "http://mailpit:8025", reply_address="replies@test-agency.local", alerts_address="alerts@x"
    )
    items = await box.fetch_unseen("INBOX")
    assert items[0].in_reply_to == "<id@test-agency.local>" and "quote" in items[0].text
    assert search.calls[0].request.url.params["query"] == "to:replies@test-agency.local is:unread"
    await box.mark_seen("INBOX", ["abc"])
    assert json.loads(mark.calls[0].request.content) == {"IDs": ["abc"], "Read": True}


@respx.mock
async def test_apify_refuses_to_run_when_account_usage_reached_cap() -> None:
    respx.get("https://api.apify.com/v2/users/me/limits").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"current": {"monthlyUsageUsd": 5.01}, "limits": {"maxMonthlyUsageUsd": 5}}
            },
        )
    )
    start = respx.post(url__regex=r".*/runs").mock(return_value=httpx.Response(201))
    async with session_scope() as s:
        with pytest.raises(FreeQuotaExceededError):
            await ApifyClient("t").run_actor(
                s, "apify/x", {}, source="social_x", max_items=1, max_charge_usd=0.01
            )
    assert not start.called
