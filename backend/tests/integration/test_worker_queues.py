"""Worker queues and Gemini budgets per lane."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import Cost, Lead
from app.db.session import session_scope
from app.orchestrator import lanes, quota, worker
from app.orchestrator.quota import FreeQuotaExceededError

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("db")]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=Path("./.test-data"), **kw)  # type: ignore[call-arg]


def test_lane_scope_by_agent() -> None:
    assert quota.gemini_scope("requirements") == "gemini:scoring"
    assert quota.gemini_scope("branding") == "gemini:heavy"
    assert quota.gemini_scope("proposal") == "gemini:fast"
    assert quota.gemini_scope("proposal", "heavy") == "gemini:heavy"
    assert quota.gemini_scope("replies") == "gemini:other"


async def test_heavy_share_can_run_out_while_scoring_continues() -> None:
    settings = _settings(gemini_daily_request_cap=20)  # heavy share = 5
    caps = quota.quotas(settings)
    assert caps["gemini:heavy"].cap == 5 and caps["gemini:scoring"].cap == 3
    async with session_scope() as s:
        for _ in range(5):
            await quota.consume(s, "gemini", agent="branding", scope=quota.gemini_scope("branding"),
                                settings=settings)  # fmt: skip
        with pytest.raises(FreeQuotaExceededError) as exc:
            await quota.consume(s, "gemini", agent="branding", scope=quota.gemini_scope("branding"),
                                settings=settings)  # fmt: skip
        assert "gemini:heavy" in str(exc.value.args)
        await quota.consume(s, "gemini", agent="requirements", scope=quota.gemini_scope("requirements"),
                            settings=settings)  # fmt: skip
    async with session_scope() as s:
        scopes = [c.quota_scope for c in (await s.execute(select(Cost))).scalars()]
    assert scopes.count("gemini:heavy") == 5 and scopes.count("gemini:scoring") == 1


def test_fast_and_heavy_workers_use_their_own_queues() -> None:
    fast = worker._worker(worker.WorkerSettings)
    heavy = worker._worker(worker.HeavyWorkerSettings)
    assert fast.queue_name == "arq:queue" and heavy.queue_name == "arq:heavy"
    assert heavy.max_jobs == 1 and fast.max_jobs > 1
    jobs = {name for name in heavy.functions if not name.startswith("cron:")}
    assert jobs == {"run_heavy_job", "run_heavy"}  # earlier job name kept
    assert "run_heavy_job" not in fast.functions


async def test_resume_puts_unfinished_heavy_work_back_on_its_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.orchestrator import queue  # noqa: PLC0415

    queued: list[uuid.UUID] = []

    async def fake(lead_id: uuid.UUID) -> bool:
        queued.append(lead_id)
        return True

    monkeypatch.setattr(queue, "enqueue_heavy", fake)
    async with session_scope() as s:
        lead = Lead(source="rss", source_url="https://x.test/1", source_item_id=uuid.uuid4().hex,
                    trigger_type="explicit_request", pipeline_state={}, lane="heavy",
                    status="rejected_low_intent",
                    lane_timings={"heavy": {"queued": "2026-11-18T10:00:00+00:00"}})  # fmt: skip
        s.add(lead)
        await s.flush()
        lead_id = lead.id
    await lanes.continue_lead(lead_id)
    assert queued == [lead_id]
    assert lanes.heavy_unfinished({"heavy": {"queued": "x", "finished": "y"}}) is False


def test_discovery_has_its_own_queue() -> None:
    discovery = worker._worker(worker.DiscoveryWorkerSettings)
    fast = worker._worker(worker.WorkerSettings)
    assert discovery.queue_name == "arq:discovery" and set(discovery.functions) == {
        "run_source_job"
    }
    assert "run_source_job" not in fast.functions


@pytest.mark.usefixtures("db")
async def test_stalled_gemini_call_falls_back_to_the_next_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio  # noqa: PLC0415

    import httpx  # noqa: PLC0415
    import respx  # noqa: PLC0415
    from pydantic import BaseModel  # noqa: PLC0415

    from app.adapters import gemini  # noqa: PLC0415
    from app.adapters.llm import LLMRequest  # noqa: PLC0415

    class Out(BaseModel):
        ok: bool

    async def stall(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(500)

    monkeypatch.setattr(gemini, "REQUEST_DEADLINE_S", 0.2)
    ok = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
    with respx.mock:
        respx.post(f"{gemini.BASE}/models/m-text:generateContent").mock(side_effect=stall)
        respx.post(f"{gemini.BASE}/models/m-fallback:generateContent").mock(
            return_value=httpx.Response(200, json=ok)
        )
        llm = gemini.GeminiLLM("k", text_model="m-text", vision_model="m-text", logo_model="m-text",
                               fallback_models=["m-fallback"], rpm=6000)  # fmt: skip
        async with session_scope() as s:
            out = await llm.structured(s, LLMRequest(agent="requirements", task="t"), Out)
    assert out.ok
