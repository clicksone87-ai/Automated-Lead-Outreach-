"""Adapter registry — picks the real or disabled implementation for each integration.

Rules:
- Integration ready (keys present, provider selected, not off in testing mode) -> real adapter
- Otherwise                                   -> disabled adapter (raises AdapterUnavailableError,
                                                 which agents/sources turn into a visible state)
- Mock adapters are used ONLY by the automated test suite (APP_MODE=test + DEMO_MODE=true).
  The running system never uses mock adapters or produces mock data.

Testing-mode defaults (APP_TIER=free): Gemini (LLM), Serper → Exa (search), Serper local
results (single-business lookups), PageSpeed (lab data), SMTP → Mailpit (email + replies),
local syntax+MX verifier, Apify (public social posts).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from pydantic import SecretStr

from app.adapters.apify import ApifyClient
from app.adapters.base import AdapterUnavailableError
from app.adapters.email import DisabledEmail, EmailAdapter, MockEmail, ResendEmail
from app.adapters.email_free import LocalVerifier, MailpitMailbox, SmtpEmail
from app.adapters.fetcher import SafeFetcher
from app.adapters.gemini import GeminiLLM
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
from app.adapters.mailbox import DisabledMailbox, ImapMailbox, MailboxAdapter, MockMailbox
from app.adapters.search import BraveSearch, DisabledSearch, MockSearch, SearchAdapter
from app.adapters.search_free import ExaSearch, FallbackSearch, SerperPlaces, SerperSearch
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

    def __init__(self, env: tuple[str, ...] = ("GEMINI_API_KEY",)) -> None:
        self._env = env

    async def structured(self, session: Any, request: Any, schema: Any) -> Any:
        raise AdapterUnavailableError("LLM", self._env)


@dataclass
class Adapters:
    llm: LLMAdapter
    search: SearchAdapter
    verification: VerificationAdapter
    pagespeed: PageSpeedAdapter
    places: PlacesAdapter
    email: EmailAdapter
    mailbox: MailboxAdapter
    storage: StorageAdapter
    fetcher: SafeFetcher
    apify: ApifyClient | None = None
    serper: SerperSearch | None = None
    mocks_in_use: set[str] = field(default_factory=set)

    def uses_mock(self, *names: str) -> bool:
        return any(n in self.mocks_in_use for n in names)


def _key(v: SecretStr | None) -> str:
    return v.get_secret_value() if v else ""


def build_adapters(s: Settings) -> Adapters:  # noqa: PLR0915 - one flat decision per integration
    st = integration_status(s)
    mocks: set[str] = set()

    def ready(name: str) -> bool:
        return st[name].configured

    def fallback(name: str, mock: Any, disabled: Any) -> Any:
        if s.mocks_allowed:
            mocks.add(name)
            return mock
        return disabled

    # LLM
    llm: Any
    if ready("gemini"):
        llm = GeminiLLM(
            _key(s.gemini_api_key),
            text_model=s.gemini_text_model,
            vision_model=s.gemini_vision_model,
            logo_model=s.gemini_logo_model,
            fallback_models=s.gemini_fallbacks,
            rpm=s.gemini_rpm,
        )
    elif ready("openai"):
        llm = OpenAILLM(_key(s.openai_api_key), s.openai_text_model, s.openai_vision_model)
    else:
        env = ("GEMINI_API_KEY",) if s.llm_provider == "gemini" else ("OPENAI_API_KEY",)
        llm = fallback(
            "gemini" if s.llm_provider == "gemini" else "openai", MockLLM(), _DisabledLLM(env)
        )

    # Search: Serper → Exa fallback chain (Brave only when selected and not in testing mode)
    serper = SerperSearch(_key(s.serper_api_key)) if ready("serper") else None
    chain: list[SearchAdapter] = []
    if s.search_provider == "brave" and ready("brave"):
        chain.append(BraveSearch(_key(s.brave_search_api_key)))
    if serper is not None:
        chain.append(serper)
    if ready("exa"):
        exa = ExaSearch(_key(s.exa_api_key))
        if s.search_provider == "exa":
            chain.insert(0, exa)
        else:
            chain.append(exa)
    search: Any = (
        FallbackSearch(chain) if chain else fallback("serper", MockSearch(), DisabledSearch())
    )

    # Verification
    verification: Any
    if ready("verification"):
        verification = LocalVerifier()
    elif ready("zerobounce"):
        verification = ZeroBounceVerifier(_key(s.email_verification_api_key))
    else:
        verification = fallback("verification", MockVerifier(), DisabledVerifier())

    pagespeed: Any = (
        GooglePageSpeed(_key(s.google_pagespeed_api_key))
        if ready("pagespeed")
        else fallback("pagespeed", MockPageSpeed(), DisabledPageSpeed())
    )

    places: Any
    if ready("places"):
        places = GooglePlaces(_key(s.google_places_api_key))
    elif serper is not None:
        places = SerperPlaces(serper)
    else:
        places = fallback("places", MockPlaces(), DisabledPlaces())

    email: Any
    if ready("smtp"):
        email = SmtpEmail(
            s.smtp_host,
            s.smtp_port,
            domain=s.sender_domain,
            user=s.smtp_user,
            password=_key(s.smtp_password),
            starttls=s.smtp_starttls,
            mailpit_api_url=s.mailpit_api_url if ready("mailpit") else "",
        )
    elif ready("resend"):
        email = ResendEmail(_key(s.resend_api_key))
    else:
        email = fallback("resend", MockEmail(), DisabledEmail())

    mailbox: Any
    if ready("mailpit"):
        mailbox = MailpitMailbox(
            s.mailpit_api_url, reply_address=s.reply_address, alerts_address=s.alerts_address
        )
    elif ready("imap"):
        mailbox = ImapMailbox(s.reply_imap_host, s.reply_imap_user, _key(s.reply_imap_password))
    else:
        mailbox = fallback("imap", MockMailbox(), DisabledMailbox())

    adapters = Adapters(
        llm=llm,
        search=search,
        verification=verification,
        pagespeed=pagespeed,
        places=places,
        email=email,
        mailbox=mailbox,
        storage=LocalStorage(s.data_dir),
        fetcher=SafeFetcher(s.crawler_user_agent),
        apify=ApifyClient(_key(s.apify_api_token)) if ready("apify") else None,
        serper=serper,
        mocks_in_use=mocks,
    )
    if mocks:
        logger.warning("TEST SUITE: using mock adapters for %s", sorted(mocks))
    return adapters


@lru_cache(maxsize=1)
def get_adapters() -> Adapters:
    return build_adapters(get_settings())
