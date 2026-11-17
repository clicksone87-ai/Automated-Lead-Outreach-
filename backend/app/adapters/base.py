"""Shared adapter types."""

from __future__ import annotations

from typing import Any, Protocol, Self, runtime_checkable


class AdapterUnavailableError(Exception):
    """Raised when an integration is not configured (missing key) and mocks are off.

    Callers catch this and park the work as `needs_api_key` — never crash.
    """

    def __init__(self, integration: str, env_vars: tuple[str, ...] = ()) -> None:
        hint = f" (set {', '.join(env_vars)})" if env_vars else ""
        super().__init__(f"{integration} is not configured{hint}")
        self.integration = integration
        self.env_vars = env_vars


@runtime_checkable
class SupportsMock(Protocol):
    """Pydantic output schemas implement this to give the mock LLM a deterministic answer."""

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self: ...
