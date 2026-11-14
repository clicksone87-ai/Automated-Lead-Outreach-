"""LLM adapter — every LLM call goes through here.

- Structured output only, validated by Pydantic (one repair retry on invalid JSON).
- Untrusted web content is wrapped by `policy.untrusted.wrap` with per-call random tokens.
- No tools/functions are ever exposed to the model.
- Budget is reserved via `budget.charge()` BEFORE the call and settled after.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError, SupportsMock
from app.orchestrator import budget
from app.policy.untrusted import wrap
from app.services import runtime_settings
from app.services.runtime_settings import ModelPrice

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Used only when no price is configured for a model: deliberately conservative so the
# budget cap errs on the safe side. Real prices are set in settings.model_prices.
FALLBACK_PRICE = ModelPrice(input_per_mtok=5.0, output_per_mtok=20.0, per_image=0.25)


class LLMOutputError(Exception):
    pass


@dataclass(slots=True)
class LLMRequest:
    agent: str
    task: str
    system: str = ""
    untrusted: dict[str, str] = field(default_factory=dict)
    images: list[bytes] = field(default_factory=list)  # PNG/JPEG bytes (vision)
    kind: Literal["text", "vision"] = "text"
    lead_id: uuid.UUID | None = None
    max_output_tokens: int = 1500
    mock_context: dict[str, Any] = field(default_factory=dict)


class LLMAdapter(Protocol):
    name: ClassVar[str]
    is_mock: ClassVar[bool]

    async def structured(
        self, session: AsyncSession, request: LLMRequest, schema: type[T]
    ) -> T: ...


def build_messages(request: LLMRequest, schema: type[BaseModel]) -> list[dict[str, Any]]:
    wrapped = wrap(request.untrusted) if request.untrusted else None
    system_parts = [request.system.strip()] if request.system else []
    if wrapped:
        system_parts.append(wrapped.rules)
    system_parts.append(
        "Return a single JSON object matching this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )
    user_text = request.task.strip()
    if wrapped:
        user_text += "\n\nDATA:\n" + wrapped.blocks
    content: list[dict[str, Any]] | str
    if request.images:
        content = [{"type": "text", "text": user_text}]
        for img in request.images:
            mime = "image/png" if img[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            data = base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    else:
        content = user_text
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": content},
    ]


def estimate_cost(messages: list[dict[str, Any]], price: ModelPrice, max_out: int,
                  n_images: int) -> float:
    chars = len(json.dumps(messages)) if n_images == 0 else sum(
        len(m["content"]) if isinstance(m["content"], str) else 2000 for m in messages
    )
    in_tokens = chars / 3.5 + n_images * 1100
    return (in_tokens * price.input_per_mtok + max_out * price.output_per_mtok) / 1_000_000


class OpenAILLM:
    name: ClassVar[str] = "openai"
    is_mock: ClassVar[bool] = False

    def __init__(self, api_key: str, text_model: str, vision_model: str) -> None:
        from openai import AsyncOpenAI  # local import: optional at test time

        self._client = AsyncOpenAI(api_key=api_key, max_retries=2, timeout=90)
        self._models = {"text": text_model, "vision": vision_model or text_model}

    async def structured(
        self, session: AsyncSession, request: LLMRequest, schema: type[T]
    ) -> T:
        model = self._models[request.kind]
        if not model:
            raise AdapterUnavailableError(f"openai {request.kind} model", ("OPENAI_*_MODEL",))
        rs = await runtime_settings.load(session)
        price = rs.model_prices.get(model, FALLBACK_PRICE)
        messages = build_messages(request, schema)
        estimate = estimate_cost(messages, price, request.max_output_tokens, len(request.images))
        reserved = await budget.charge(
            session, provider="openai", agent=request.agent, amount_usd=estimate,
            lead_id=request.lead_id, description=f"{model} {schema.__name__}",
        )
        total_in = total_out = 0
        last_error: Exception | None = None
        try:
            for _attempt in range(2):
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__[:64],
                            "schema": schema.model_json_schema(),
                            "strict": False,
                        },
                    },
                    max_completion_tokens=request.max_output_tokens,
                )
                if resp.usage:
                    total_in += resp.usage.prompt_tokens
                    total_out += resp.usage.completion_tokens
                raw = resp.choices[0].message.content or ""
                try:
                    return schema.model_validate_json(raw)
                except ValidationError as exc:
                    last_error = exc
                    messages = [
                        *messages,
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": f"Invalid output: {exc.errors()[:5]}. "
                                                    "Return corrected JSON only."},
                    ]
            raise LLMOutputError(f"invalid structured output for {schema.__name__}: {last_error}")
        finally:
            actual = (total_in * price.input_per_mtok + total_out * price.output_per_mtok) / 1e6
            await budget.settle(session, reserved, actual)


class MockLLM:
    """Deterministic fixture LLM for tests and DEMO_MODE. Costs nothing."""

    name: ClassVar[str] = "mock_llm"
    is_mock: ClassVar[bool] = True

    def __init__(self) -> None:
        self.calls: list[tuple[LLMRequest, type[BaseModel]]] = []

    async def structured(
        self, session: AsyncSession, request: LLMRequest, schema: type[T]
    ) -> T:
        self.calls.append((request, schema))
        build_messages(request, schema)  # exercise wrapping in tests
        if not isinstance(schema, SupportsMock):
            raise LLMOutputError(f"{schema.__name__} has no mock() fixture")
        context = {"untrusted": request.untrusted, "task": request.task, **request.mock_context}
        result = schema.mock(context)
        return schema.model_validate(result.model_dump())
