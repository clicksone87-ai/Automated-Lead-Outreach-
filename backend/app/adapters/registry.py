"""Adapter registry — picks real, mock or disabled implementations from configuration.

Rules:
- Integration configured            -> real adapter
- Not configured and DEMO_MODE=true -> mock adapter (output tagged as mock; never sendable)
- Not configured and DEMO_MODE=false-> disabled adapter (raises AdapterUnavailableError,
                                       which agents turn into a `needs_api_key` pause)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic import SecretStr

from app.adapters.base import AdapterUnavailableError
from app.adapters.email import DisabledEmail, EmailAdapter, MockEmail, ResendEmail
from app.adapters.fetcher import SafeFetcher
from app.adapters.google import (
    DisabledPageSpeed,
    DisabledPlaces,
    GooglePageSpeed,
    GooglePlaces,
    MockPageSpeed,
    MockPlaces,
    PageSpeedAdapter,
    PlacesAdapter,
)
from app.adapters.llm import LLMAdapter, MockLLM, OpenAILLM
from app.adapters.search import BraveSearch, DisabledSearch, MockSearch, SearchAdapter
from app.adapters.storage import LocalStorage, StorageAdapter
from app.adapters.verification import (
    DisabledVerifier,
    MockVerifier,
    VerificationAdapter,
    ZeroBounceVerifier,
)
from app.config import Settings, get_settings, integration_status

logger = logging.getLogger(__name__)


class _DisabledLLM:
    name = "llm_disabled"
    is_mock = False

    async def structured(self, session: Any, request: Any, schema: Any) -> Any:
        raise AdapterUnavailableError("OpenAI", ("OPENAI_API_KEY", "OPENAI_TEXT_MODEL"))


@dataclass
class Adapters:
    llm: LLMAdapter
    search: SearchAdapter
    verification: VerificationAdapter
    pagespeed: PageSpeedAdapter
    places: PlacesAdapter
    email: EmailAdapter
    storage: StorageAdapter
    fetcher: SafeFetcher
    mocks_in_use: set[str] = field(default_factory=set)

    def uses_mock(self, *names: str) -> bool:
        return any(n in self.mocks_in_use for n in names)


def build_adapters(s: Settings) -> Adapters:
    st = integration_status(s)
    mocks: set[str] = set()

    def pick(key: str, real: Any, mock: Any, disabled: Any) -> Any:
        if st[key].configured and real is not None:
            return real
        if s.demo_mode:
            mocks.add(key)
            return mock
        return disabled

    def key(v: SecretStr | None) -> str:
        return v.get_secret_value() if v else ""

    llm = pick(
        "openai",
        OpenAILLM(key(s.openai_api_key), s.openai_text_model, s.openai_vision_model)
        if st["openai"].configured
        else None,
        MockLLM(),
        _DisabledLLM(),
    )
    adapters = Adapters(
        llm=llm,
        search=pick(
            "brave",
            BraveSearch(key(s.brave_search_api_key)) if st["brave"].configured else None,
            MockSearch(),
            DisabledSearch(),
        ),
        verification=pick(
            "verification",
            ZeroBounceVerifier(key(s.email_verification_api_key))
            if st["verification"].configured
            else None,
            MockVerifier(),
            DisabledVerifier(),
        ),
        pagespeed=pick(
            "pagespeed",
            GooglePageSpeed(key(s.google_pagespeed_api_key))
            if st["pagespeed"].configured
            else None,
            MockPageSpeed(),
            DisabledPageSpeed(),
        ),
        places=pick(
            "places",
            GooglePlaces(key(s.google_places_api_key)) if st["places"].configured else None,
            MockPlaces(),
            DisabledPlaces(),
        ),
        email=pick(
            "resend",
            ResendEmail(key(s.resend_api_key)) if st["resend"].configured else None,
            MockEmail(),
            DisabledEmail(),
        ),
        storage=LocalStorage(s.data_dir),
        fetcher=SafeFetcher(s.crawler_user_agent),
        mocks_in_use=mocks,
    )
    if mocks:
        logger.warning("DEMO_MODE: using mock adapters for %s", sorted(mocks))
    return adapters


@lru_cache(maxsize=1)
def get_adapters() -> Adapters:
    return build_adapters(get_settings())
