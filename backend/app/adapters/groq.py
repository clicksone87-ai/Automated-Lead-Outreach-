"""Groq (OpenAI-compatible chat API) as the LLM fallback for text work.

Used for scoring and drafting when Gemini's daily cap is used up or Gemini is unavailable.
Logos and vision stay on Gemini. Same contract as the other LLM adapters: untrusted content
wrapped, JSON output validated by Pydantic (one repair attempt), usage counted before each call.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMOutputError, LLMRequest, build_messages
from app.orchestrator import quota
from app.orchestrator.errors import TransientAgentError
from app.orchestrator.quota import FreeQuotaExceededError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
# Used when GROQ_MODEL is empty: a production model on the free plan with JSON output support.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


class GroqUnavailableError(TransientAgentError):
    pass


class GroqLLM:
    name: ClassVar[str] = "groq"
    is_mock: ClassVar[bool] = False

    def __init__(self, api_key: str, model: str = "", client: httpx.AsyncClient | None = None):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model or DEFAULT_GROQ_MODEL
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15))

    async def structured(self, session: AsyncSession, request: LLMRequest, schema: type[T]) -> T:
        if request.images:
            raise LLMOutputError("groq fallback handles text requests only")
        messages = build_messages(request, schema)
        last: Exception | None = None
        for _attempt in range(2):
            await quota.consume(session, "groq", agent=request.agent, lead_id=request.lead_id,
                                description=f"{self.model} {schema.__name__}")  # fmt: skip
            body: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.2,
                # reasoning models spend part of this budget before the answer
                "max_completion_tokens": max(request.max_output_tokens, 2048),
            }
            resp = await self._client.post(ENDPOINT, json=body, headers=self._headers)
            if resp.status_code == 429:
                raise GroqUnavailableError("groq rate limited")
            if resp.status_code >= 500:
                raise GroqUnavailableError(f"groq HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise LLMOutputError(f"groq HTTP {resp.status_code}: {resp.text[:200]}")
            raw = str(resp.json()["choices"][0]["message"].get("content") or "")
            try:
                return schema.model_validate_json(raw)
            except ValidationError as exc:
                last = exc
                messages = [*messages, {"role": "assistant", "content": raw},
                            {"role": "user", "content": f"Invalid output: {exc.errors()[:5]}. "
                             "Return corrected JSON only."}]  # fmt: skip
        raise LLMOutputError(f"invalid structured output for {schema.__name__}: {last}")


class FallbackLLM:
    """Primary LLM (Gemini) with Groq taking over text requests when the primary's daily cap is
    used up or it is unavailable. Logo and vision requests never leave the primary."""

    is_mock: ClassVar[bool] = False

    def __init__(self, primary: Any, fallback: GroqLLM) -> None:
        self.primary, self.fallback = primary, fallback
        self.name = f"{primary.name}+{fallback.name}"

    async def structured(self, session: AsyncSession, request: LLMRequest, schema: type[T]) -> T:
        try:
            result: T = await self.primary.structured(session, request, schema)
            return result
        except (FreeQuotaExceededError, TransientAgentError) as exc:
            if request.kind != "text" or request.images:
                raise
            logger.info("LLM fallback to %s for %s: %s", self.fallback.name, request.agent, exc)
            await quota.record_provider_error(self.primary.name, f"{type(exc).__name__}: {exc}")
            return await self.fallback.structured(session, request, schema)
