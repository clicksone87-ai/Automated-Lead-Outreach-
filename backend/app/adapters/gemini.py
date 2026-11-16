"""Gemini adapter (free tier) — default LLM for text, structured output and vision.

Same contract as the OpenAI adapter:
- structured output only, validated by Pydantic (one repair retry on invalid JSON)
- untrusted web content wrapped with per-call random tokens; no tools are ever exposed
- every request is counted against the free-tier quota (orchestrator.quota) BEFORE it is sent
- free-tier friendly: client-side rate limit (GEMINI_RPM), retry with backoff on 429 using
  the server's RetryInfo, daily-quota 429 → FreeQuotaExceededError (callers pause), and
  fallback to the next configured model on 503 / timeouts.
Model names come from config (GEMINI_*_MODEL), never from agent code.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import re
import time
from typing import Any, ClassVar, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMOutputError, LLMRequest
from app.orchestrator import quota
from app.orchestrator.quota import FreeQuotaExceededError
from app.policy.untrusted import wrap

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
BASE = "https://generativelanguage.googleapis.com/v1beta"
MAX_RETRY_WAIT_S = 65.0


class GeminiUnavailableError(Exception):
    """All configured models failed with transient errors (503 / timeouts)."""


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema → self-contained schema (no $ref/$defs) for responseJsonSchema."""
    defs = schema.get("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            return {}
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                merged = {
                    **copy.deepcopy(defs.get(name, {})),
                    **{k: v for k, v in node.items() if k != "$ref"},
                }
                return resolve(merged, depth + 1)
            return {k: resolve(v, depth + 1) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v, depth + 1) for v in node]
        return node

    out: dict[str, Any] = resolve(schema)
    return out


def _retry_delay(err: dict[str, Any]) -> float | None:
    for d in err.get("details", []) or []:
        if str(d.get("@type", "")).endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    return None


def _is_daily_quota(err: dict[str, Any]) -> bool:
    for d in err.get("details", []) or []:
        for v in d.get("violations", []) or []:
            qid = str(v.get("quotaId", "")) + str(v.get("quotaMetric", ""))
            if "PerDay" in qid or "per_day" in qid.lower():
                return True
    return False


class _RateLimiter:
    """Process-wide minimum spacing between requests (free tier RPM)."""

    def __init__(self, rpm: int) -> None:
        self._interval = 60.0 / max(1, rpm)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self._interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


class GeminiLLM:
    name: ClassVar[str] = "gemini"
    is_mock: ClassVar[bool] = False

    def __init__(
        self,
        api_key: str,
        *,
        text_model: str,
        vision_model: str,
        logo_model: str,
        fallback_models: list[str],
        rpm: int = 10,
        client: httpx.AsyncClient | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._key = api_key
        self._models = {
            "text": text_model,
            "vision": vision_model or text_model,
            "logo": logo_model or text_model,
        }
        self._fallbacks = fallback_models
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))
        self._limiter = _RateLimiter(rpm)
        self._sleep = sleep

    # ── request building ─────────────────────────────────────────────────

    @staticmethod
    def build_body(
        request: LLMRequest, schema: type[BaseModel], *, with_schema: bool = True
    ) -> dict[str, Any]:
        wrapped = wrap(request.untrusted) if request.untrusted else None
        system_parts = [request.system.strip()] if request.system else []
        if wrapped:
            system_parts.append(wrapped.rules)
        json_schema = schema.model_json_schema()
        if not with_schema:
            system_parts.append(
                "Return a single JSON object matching this JSON Schema:\n"
                + json.dumps(json_schema, separators=(",", ":"))
            )
        user_text = request.task.strip()
        if wrapped:
            user_text += "\n\nDATA:\n" + wrapped.blocks
        parts: list[dict[str, Any]] = [{"text": user_text}]
        for img in request.images:
            mime = "image/png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            parts.append(
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(img).decode()}}
            )
        gen: dict[str, Any] = {
            "responseMimeType": "application/json",
            "maxOutputTokens": max(request.max_output_tokens, 256),
            "temperature": 0.9 if request.kind == "logo" else 0.2,
        }
        if with_schema:
            gen["responseJsonSchema"] = inline_refs(json_schema)
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen,
        }
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return body

    @staticmethod
    def extract_text(payload: dict[str, Any]) -> str:
        cands = payload.get("candidates") or []
        if not cands:
            reason = (payload.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMOutputError(f"gemini returned no candidates ({reason})")
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMOutputError(f"gemini empty output (finishReason={cand.get('finishReason')})")
        return text

    # ── transport ────────────────────────────────────────────────────────

    async def _post(
        self, session: AsyncSession, model: str, body: dict[str, Any], request: LLMRequest
    ) -> dict[str, Any]:
        """One logical call to `model`, retrying 429s within the free tier. Raises on failure."""
        for attempt in range(4):
            await quota.consume(
                session,
                "gemini",
                agent=request.agent,
                lead_id=request.lead_id,
                description=f"{model} {request.kind}",
            )
            await self._limiter.wait()
            resp = await self._client.post(
                f"{BASE}/models/{model}:generateContent",
                headers={"x-goog-api-key": self._key},
                json=body,
            )
            if resp.status_code == 200:
                data: dict[str, Any] = resp.json()
                return data
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {"message": resp.text[:300]}
            if resp.status_code == 429:
                if _is_daily_quota(err):
                    raise FreeQuotaExceededError("gemini:daily(server)", 0, 0, 1)
                wait = min(_retry_delay(err) or 2.0 * (2**attempt), MAX_RETRY_WAIT_S)
                logger.info(
                    "gemini 429 on %s, waiting %.0fs (attempt %d)", model, wait, attempt + 1
                )
                await self._sleep(wait)
                continue
            if resp.status_code == 400 and "responseJsonSchema" in body.get("generationConfig", {}):
                raise _SchemaRejectedError(str(err.get("message", ""))[:300])
            if resp.status_code in (500, 502, 503, 504):
                raise GeminiUnavailableError(f"{model}: {resp.status_code} {err.get('status')}")
            raise LLMOutputError(
                f"gemini {model} HTTP {resp.status_code}: {str(err.get('message', ''))[:300]}"
            )
        raise GeminiUnavailableError(f"{model}: still rate limited after retries")

    async def _generate(
        self,
        session: AsyncSession,
        request: LLMRequest,
        schema: type[BaseModel],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        models = [self._models[request.kind], *self._fallbacks]
        seen: list[str] = []
        last: Exception | None = None
        for model in models:
            if model in seen:
                continue
            seen.append(model)
            try:
                return await self._post(session, model, body, request)
            except _SchemaRejectedError:
                # Older/other models may reject the JSON-Schema field: put the schema in the prompt.
                body = self.build_body(request, schema, with_schema=False)
                return await self._post(session, model, body, request)
            except (GeminiUnavailableError, httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning("gemini %s unavailable (%s); trying next model", model, exc)
                last = exc
        raise GeminiUnavailableError(f"all Gemini models failed: {last}")

    async def structured(self, session: AsyncSession, request: LLMRequest, schema: type[T]) -> T:
        body = self.build_body(request, schema)
        last_error: Exception | None = None
        for _attempt in range(2):
            payload = await self._generate(session, request, schema, body)
            raw = self.extract_text(payload)
            try:
                return schema.model_validate_json(_strip_fences(raw))
            except ValidationError as exc:
                last_error = exc
                body = copy.deepcopy(body)
                body["contents"] += [
                    {"role": "model", "parts": [{"text": raw[:20_000]}]},
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": f"Invalid output: {exc.errors()[:5]}. "
                                "Return corrected JSON only."
                            }
                        ],
                    },
                ]
        raise LLMOutputError(f"invalid structured output for {schema.__name__}: {last_error}")


class _SchemaRejectedError(Exception):
    pass


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    return s
