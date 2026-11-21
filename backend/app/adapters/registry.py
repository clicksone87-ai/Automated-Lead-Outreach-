"""Builds the adapter set: the real adapter when an integration is ready, otherwise a disabled one
that raises AdapterUnavailableError.

Mock adapters are only used by the test suite (APP_MODE=test and DEMO_MODE=true).
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
from app.adapters.email_render import EmailRenderer, FixtureRenderer, ReactEmailRenderer
from app.adapters.fetcher import SafeFetcher
from app.adapters.gemini import GeminiLLM
from app.adapters.gmail import GmailComposeLink, GmailEmail
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
from app.adapters.groq import FallbackLLM, GroqLLM
from app.adapters.llm import LLMAdapter, MockLLM, OpenAILLM
from app.adapters.mailbox import DisabledMailbox, ImapMailbox, MailboxAdapter, MockMailbox
from app.adapters.search import BraveSearch, DisabledSearch, MockSearch, SearchAdapter
from app.adapters.search_free import ExaSearch, FallbackSearch, SerperPlaces, SerperSearch
from app.adapters.search_router import (
    ScrapingDogSearch,
    SearloSearch,
    SerpApiSearch,
    TavilySearch,
)
from app.adapters.storage import LocalStorage, StorageAdapter
from app.adapters.verification import (
    DisabledVerifier,
    FallbackVerifier,
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
    renderer: EmailRenderer | None = None
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

    if s.llm_fallback_provider == "groq" and ready("groq") and not getattr(llm, "is_mock", False):
        llm = FallbackLLM(llm, GroqLLM(_key(s.groq_api_key), s.groq_model))

    # Search router: SEARCH_PROVIDER_ORDER, each provider within its own cap (Brave only when
    # selected and not in testing mode)
    serper = (
        SerperSearch(_key(s.serper_api_key), fallback_key=_key(s.serper_api_key_fallback))
        if ready("serper")
        else None
    )
    builders: dict[str, Any] = {
        "serper": lambda: serper,
        "serpapi": lambda: SerpApiSearch(_key(s.serpapi_api_key)) if ready("serpapi") else None,
        "scrapingdog": lambda: (
            ScrapingDogSearch(_key(s.scrapingdog_api_key)) if ready("scrapingdog") else None
        ),
        "searlo": lambda: SearloSearch(_key(s.searlo_api_key)) if ready("searlo") else None,
        "tavily": lambda: TavilySearch(_key(s.tavily_api_key)) if ready("tavily") else None,
        "exa": lambda: ExaSearch(_key(s.exa_api_key)) if ready("exa") else None,
    }
    chain: list[SearchAdapter] = []
    if s.search_provider == "brave" and ready("brave"):
        chain.append(BraveSearch(_key(s.brave_search_api_key)))
    for name in s.search_order:
        adapter = builders.get(name, lambda: None)()
        if adapter is not None and adapter not in chain:
            chain.append(adapter)
    search: Any = (
        FallbackSearch(chain) if chain else fallback("serper", MockSearch(), DisabledSearch())
    )

    # Verification
    verification: Any
    if ready("verification"):
        verification = LocalVerifier()
    elif ready("zerobounce"):
        verification = FallbackVerifier(
            ZeroBounceVerifier(_key(s.email_verification_api_key), free_tier=s.testing_tier),
            LocalVerifier(),
        )
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
    if s.gmail_mode in ("draft", "send"):
        email = GmailEmail(
            s.gmail_sender.strip(),
            _key(s.gmail_app_password),
            delivery=s.gmail_mode,
            imap_host=s.gmail_imap_host,
            imap_port=s.gmail_imap_port,
            smtp_host=s.gmail_smtp_host,
            smtp_port=s.gmail_smtp_port,
            smtp_ssl_port=s.gmail_smtp_ssl_port,
        )
    elif s.gmail_mode == "compose":
        email = GmailComposeLink(s.gmail_sender.strip())
    elif ready("smtp"):
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
        renderer=FixtureRenderer()
        if s.mocks_allowed
        else ReactEmailRenderer(s.email_templates_dir, s.data_dir / "email-renderer"),
        mocks_in_use=mocks,
    )
    if mocks:
        logger.warning("TEST SUITE: using mock adapters for %s", sorted(mocks))
    return adapters


@lru_cache(maxsize=1)
def get_adapters() -> Adapters:
    return build_adapters(get_settings())
