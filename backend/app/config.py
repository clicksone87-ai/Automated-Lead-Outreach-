"""Application configuration.

Environment values are initial values; runtime-editable settings live in the `settings` table.
A missing API key never crashes the system: `integration_status()` reports it, the adapter is
disabled and the dashboard shows "Needs API key".
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class AppMode(StrEnum):
    LOCAL = "local"
    SERVER = "server"
    TEST = "test"


class AppTier(StrEnum):
    """`free`: testing mode on free-tier providers only; paid integrations are forced off."""

    FREE = "free"
    PAID = "paid"


# The automated test suite (APP_MODE=test, set before import) never reads the real .env:
# tests are hermetic and can never pick up live keys or local limits.
_ENV_FILES: tuple[str, ...] | None = (
    None if os.environ.get("APP_MODE") == "test" else (".env", "../.env")
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    app_mode: AppMode = AppMode.LOCAL
    app_tier: AppTier = AppTier.FREE
    log_level: str = "INFO"
    # Fixture-backed mocks for integrations without keys. Honoured ONLY when APP_MODE=test
    # (the automated test suite); the running system never uses mock adapters or mock data.
    demo_mode: bool = False

    # Provider selection
    llm_provider: str = "gemini"  # gemini | openai
    search_provider: str = "serper"  # serper | exa | brave (fallback chain: serper → exa)
    email_provider: str = "smtp"  # smtp (Mailpit in testing) | resend

    # Gemini (free tier) — model names from config, never hard-coded in agents
    gemini_api_key: SecretStr | None = None
    gemini_text_model: str = "gemini-3.5-flash-lite"
    gemini_vision_model: str = "gemini-3.5-flash-lite"
    gemini_logo_model: str = "gemini-3.5-flash"
    gemini_fallback_models: str = "gemini-3.5-flash,gemini-3.8-flash"
    gemini_rpm: int = Field(default=10, ge=1)
    gemini_daily_request_cap: int = Field(default=500, ge=0)

    # Serper (Google results via API: web + local) and Exa (fallback search)
    serper_api_key: SecretStr | None = None
    serper_free_credits: int = Field(default=2500, ge=0)
    exa_api_key: SecretStr | None = None
    exa_free_credit_usd: float = Field(default=10.0, ge=0)

    # Apify (licensed third-party provider for public social posts)
    apify_api_token: SecretStr | None = None
    apify_monthly_cap_usd: float = Field(default=5.0, ge=0)

    # Scout profiles: official APIs (both optional; free)
    twitch_client_id: SecretStr | None = None
    twitch_client_secret: SecretStr | None = None
    github_token: SecretStr | None = None

    # PageSpeed free quota guard (Google's own limit is far higher)
    pagespeed_daily_cap: int = Field(default=400, ge=0)

    # SMTP (Mailpit in testing: every email lands in Mailpit, nothing is really sent)
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    mailpit_api_url: str = "http://mailpit:8025"

    database_url: str = "postgresql+psycopg://leads:leads@localhost:5432/leads"
    # Host port docker compose publishes Postgres on (read by compose; listed for 1:1 mapping)
    postgres_port: int = 5432
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("./data")
    # Website templates (mounted read-only at /site-templates in the containers)
    site_templates_dir: Path = Path("/site-templates")
    email_templates_dir: Path = Path("/email-templates")
    # Template dependencies and compiled bundles. Empty = DATA_DIR/site-builder. Compose puts
    # it on a Docker volume: thousands of small files are slow on a Windows bind mount.
    site_builder_dir: Path | None = None
    # Signs unsubscribe links (and other tokens). Set a long random value on a server.
    app_secret: SecretStr | None = None
    dashboard_origin: str = "http://localhost:3000"
    # Optional bearer token for the API (recommended when moving to a server). Empty = off.
    api_token: SecretStr | None = None

    # Markets: every country is a target except these (ISO 3166-1 alpha-2, comma separated)
    excluded_countries: str = "PK,IN,BD"
    all_countries_outreach_enabled: bool = True
    # Social posts older than this (or without a timestamp) are dropped
    social_max_post_age_hours: int = Field(default=8, ge=1, le=168)

    # LLM fallback when Gemini's daily cap is used up (scoring and drafting only)
    llm_fallback_provider: str = "groq"  # groq | "" (off)
    groq_api_key: SecretStr | None = None
    groq_model: str = ""  # empty = DEFAULT_GROQ_MODEL
    groq_daily_request_cap: int = Field(default=900, ge=0)

    # Search router: tried in this order, each within its own cap
    search_provider_order: str = "serper,serpapi,scrapingdog,searlo,tavily,exa"
    serper_api_key_fallback: SecretStr | None = None
    # The free credits are one-time, so a daily ceiling spreads them over weeks.
    serper_daily_cap: int = Field(default=60, ge=0)
    serpapi_api_key: SecretStr | None = None
    serpapi_monthly_cap: int = Field(default=90, ge=0)
    scrapingdog_api_key: SecretStr | None = None
    scrapingdog_monthly_cap: int = Field(default=900, ge=0)
    searlo_api_key: SecretStr | None = None
    searlo_monthly_cap: int = Field(default=100, ge=0)
    tavily_api_key: SecretStr | None = None
    tavily_monthly_cap: int = Field(default=900, ge=0)
    exa_monthly_cap: int = Field(default=500, ge=0)

    # Open data and registries (no keys)
    overpass_api_url: str = "https://overpass-api.de/api/interpreter"
    overpass_fallback_urls: str = ""
    overpass_max_requests_per_minute: int = Field(default=2, ge=1)
    geofabrik_base_url: str = "https://download.geofabrik.de"
    geofabrik_enabled: bool = False
    fr_recherche_entreprises_url: str = "https://recherche-entreprises.api.gouv.fr"
    fr_recherche_max_rps: int = Field(default=5, ge=1, le=7)
    no_brreg_api_url: str = "https://data.brreg.no/enhetsregisteret/api"
    osm_attribution: str = "© OpenStreetMap contributors (ODbL)"

    # Social providers (all on; each platform falls back to the next provider)
    social_sources_enabled: bool = True
    social_platforms: str = "instagram,tiktok,facebook,x,reddit,youtube,linkedin,pinterest,threads,twitch,github,linktree"
    apify_api_token_fallback: SecretStr | None = None
    ensembledata_api_token: SecretStr | None = None
    scrapecreators_api_key: SecretStr | None = None
    socialcrawl_api_key: SecretStr | None = None
    hikerapi_base_url: str = "https://api.instagrapi.com"
    hikerapi_access_key: SecretStr | None = None
    social_provider_daily_cap: int = Field(default=50, ge=0)

    # Outreach email from the owner's Gmail (compose link, IMAP draft, or SMTP send)
    outreach_email_mode: str = "gmail"  # gmail | provider (EMAIL_PROVIDER: Mailpit/Resend)
    gmail_sender: str = ""
    gmail_app_password: SecretStr | None = None
    gmail_delivery: str = "draft"  # draft | send
    gmail_imap_host: str = "imap.gmail.com"
    gmail_imap_port: int = 993
    gmail_smtp_host: str = "smtp.gmail.com"
    gmail_smtp_port: int = 587
    gmail_daily_send_cap: int = Field(default=400, ge=0)
    gmail_oauth_client_id: str = ""
    gmail_oauth_client_secret: SecretStr | None = None
    gmail_oauth_redirect_uri: str = "http://localhost:8000/api/gmail/oauth/callback"
    reply_to_email: str = ""
    zerobounce_monthly_cap: int = Field(default=90, ge=0)

    # OpenAI
    openai_api_key: SecretStr | None = None
    openai_text_model: str = ""
    openai_vision_model: str = ""
    openai_image_model: str = ""

    # Search / Google
    brave_search_api_key: SecretStr | None = None
    google_pagespeed_api_key: SecretStr | None = None
    google_places_api_key: SecretStr | None = None

    # Email sending + sender identity
    resend_api_key: SecretStr | None = None
    sending_domain: str = ""
    sender_name: str = ""
    sender_business_name: str = ""
    sender_postal_address: str = ""
    portfolio_url: str = ""

    # Reply mailbox
    reply_imap_host: str = ""
    reply_imap_user: str = ""
    reply_imap_password: SecretStr | None = None
    alerts_imap_folder: str = "Alerts"

    # Email verification: local (syntax + MX, free) | zerobounce
    email_verification_provider: str = "local"
    email_verification_api_key: SecretStr | None = None

    # Platform sources
    reddit_client_id: str = ""
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str = ""
    x_bearer_token: SecretStr | None = None
    meta_access_token: SecretStr | None = None
    freelancer_api_token: SecretStr | None = None

    # Registries
    opencorporates_api_key: SecretStr | None = None
    companies_house_api_key: SecretStr | None = None

    # Public URLs
    public_preview_base_url: str = ""
    public_api_base_url: str = ""

    # Initial limits (seeded into the `settings` table)
    monthly_budget_usd: float = Field(default=120.0, ge=0)
    daily_budget_usd: float = Field(default=4.0, ge=0)
    min_intent_score: int = Field(default=60, ge=0, le=100)
    cooldown_days: int = Field(default=90, ge=0)
    daily_send_limit: int = Field(default=10, ge=0)
    autonomy_stage: int = Field(default=1, ge=1, le=3)
    max_job_attempts: int = Field(default=4, ge=1, le=10)

    # Crawler identity
    crawler_user_agent: str = (
        "LeadsSystemBot/0.1 (+contact via sender website; respects robots.txt)"
    )

    @property
    def mocks_allowed(self) -> bool:
        """Mock adapters exist for the automated test suite only."""
        return self.demo_mode and self.app_mode == AppMode.TEST

    @property
    def testing_tier(self) -> bool:
        return self.app_tier == AppTier.FREE

    @property
    def sender_domain(self) -> str:
        return self.sending_domain or "test-agency.local"

    @property
    def from_address(self) -> str:
        name = self.sender_business_name or self.sender_name or "Outreach"
        return f"{name} <outreach@{self.sender_domain}>"

    @property
    def reply_address(self) -> str:
        return f"replies@{self.sender_domain}"

    @property
    def alerts_address(self) -> str:
        return f"alerts@{self.sender_domain}"

    @property
    def site_builder_root(self) -> Path:
        return self.site_builder_dir or self.data_dir / "site-builder"

    @property
    def excluded_country_set(self) -> frozenset[str]:
        return frozenset(c.strip().upper() for c in self.excluded_countries.split(",") if c.strip())

    @property
    def search_order(self) -> list[str]:
        return [p.strip().lower() for p in self.search_provider_order.split(",") if p.strip()]

    @property
    def social_platform_list(self) -> list[str]:
        return [p.strip().lower() for p in self.social_platforms.split(",") if p.strip()]

    @property
    def gemini_fallbacks(self) -> list[str]:
        return [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]

    @property
    def public_links_enabled(self) -> bool:
        """True when previews can be linked publicly instead of attached as screenshots/PDF."""
        return bool(self.public_preview_base_url)


# Paid integrations that are forced off while APP_TIER=free (testing mode), keys or not.
TESTING_OFF: frozenset[str] = frozenset(
    {
        "openai",
        "openai_vision",
        "openai_image",
        "brave",
        "places",
        "resend",
        "imap",
        "zerobounce",
        "reddit",
        "x",
        "meta",
        "opencorporates",
    }
)


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    key: str
    label: str
    env_vars: tuple[str, ...]
    has_keys: bool
    paid: bool
    signup_url: str
    #: "ready" | "needs_api_key" | "off_testing_mode" | "off"
    state: str = "ready"
    note: str = ""

    @property
    def configured(self) -> bool:
        """Usable right now: keys present, provider selected, not forced off."""
        return self.state == "ready"


def _has(value: SecretStr | str | None) -> bool:
    if value is None:
        return False
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    return bool(raw.strip())


def integration_status(s: Settings) -> dict[str, IntegrationStatus]:
    """Every external integration with its current state (Sources page, adapter registry)."""
    llm, search, email = s.llm_provider, s.search_provider, s.email_provider
    sender_ok = all(
        _has(v)
        for v in (s.sender_name, s.sender_business_name, s.sender_postal_address, s.portfolio_url)
    )
    reddit_ok = (
        _has(s.reddit_client_id) and _has(s.reddit_client_secret) and _has(s.reddit_user_agent)
    )
    imap_ok = _has(s.reply_imap_host) and _has(s.reply_imap_user) and _has(s.reply_imap_password)
    # key, label, env vars, keys present, paid, signup url, selected (active) provider
    specs: list[tuple[str, str, tuple[str, ...], bool, bool, str, bool]] = [
        (
            "gemini",
            "Gemini (free tier)",
            ("GEMINI_API_KEY",),
            _has(s.gemini_api_key),
            False,
            "https://aistudio.google.com/apikey",
            llm == "gemini",
        ),
        (
            "openai",
            "OpenAI",
            ("OPENAI_API_KEY", "OPENAI_TEXT_MODEL"),
            _has(s.openai_api_key) and _has(s.openai_text_model),
            True,
            "https://platform.openai.com/api-keys",
            llm == "openai",
        ),
        (
            "openai_vision",
            "OpenAI vision",
            ("OPENAI_API_KEY", "OPENAI_VISION_MODEL"),
            _has(s.openai_api_key) and _has(s.openai_vision_model),
            True,
            "https://platform.openai.com/api-keys",
            llm == "openai",
        ),
        (
            "openai_image",
            "OpenAI images",
            ("OPENAI_API_KEY", "OPENAI_IMAGE_MODEL"),
            _has(s.openai_api_key) and _has(s.openai_image_model),
            True,
            "https://platform.openai.com/api-keys",
            llm == "openai",
        ),
        (
            "serper",
            "Serper (Google results API)",
            ("SERPER_API_KEY",),
            _has(s.serper_api_key),
            False,
            "https://serper.dev",
            search in ("serper", "exa"),
        ),
        (
            "exa",
            "Exa search (fallback)",
            ("EXA_API_KEY",),
            _has(s.exa_api_key),
            False,
            "https://exa.ai",
            search in ("serper", "exa"),
        ),
        *[
            (key, label, (env,), _has(value), False, url, True)
            for key, label, env, value, url in (
                (
                    "serpapi",
                    "SerpAPI (search router)",
                    "SERPAPI_API_KEY",
                    s.serpapi_api_key,
                    "https://serpapi.com/manage-api-key",
                ),
                (
                    "scrapingdog",
                    "ScrapingDog (search router)",
                    "SCRAPINGDOG_API_KEY",
                    s.scrapingdog_api_key,
                    "https://www.scrapingdog.com",
                ),
                (
                    "searlo",
                    "Searlo (search router)",
                    "SEARLO_API_KEY",
                    s.searlo_api_key,
                    "https://searlo.tech",
                ),
                (
                    "tavily",
                    "Tavily (search router)",
                    "TAVILY_API_KEY",
                    s.tavily_api_key,
                    "https://app.tavily.com",
                ),
                (
                    "groq",
                    "Groq (LLM fallback)",
                    "GROQ_API_KEY",
                    s.groq_api_key,
                    "https://console.groq.com/keys",
                ),
                (
                    "ensembledata",
                    "EnsembleData (social)",
                    "ENSEMBLEDATA_API_TOKEN",
                    s.ensembledata_api_token,
                    "https://ensembledata.com",
                ),
                (
                    "scrapecreators",
                    "ScrapeCreators (social)",
                    "SCRAPECREATORS_API_KEY",
                    s.scrapecreators_api_key,
                    "https://scrapecreators.com",
                ),
                (
                    "socialcrawl",
                    "SocialCrawl (social)",
                    "SOCIALCRAWL_API_KEY",
                    s.socialcrawl_api_key,
                    "https://socialcrawl.dev",
                ),
                (
                    "hikerapi",
                    "HikerAPI (Instagram)",
                    "HIKERAPI_ACCESS_KEY",
                    s.hikerapi_access_key,
                    "https://hikerapi.com",
                ),
                (
                    "gmail_draft",
                    "Gmail drafts/sending (App Password)",
                    "GMAIL_APP_PASSWORD",
                    s.gmail_app_password,
                    "https://myaccount.google.com/apppasswords",
                ),
            )
        ],
        (
            "brave",
            "Brave Search",
            ("BRAVE_SEARCH_API_KEY",),
            _has(s.brave_search_api_key),
            True,
            "https://brave.com/search/api/",
            search == "brave",
        ),
        (
            "apify",
            "Apify (public social posts)",
            ("APIFY_API_TOKEN",),
            _has(s.apify_api_token),
            False,
            "https://console.apify.com/account/integrations",
            True,
        ),
        (
            "pagespeed",
            "PageSpeed Insights",
            ("GOOGLE_PAGESPEED_API_KEY",),
            _has(s.google_pagespeed_api_key),
            False,
            "https://developers.google.com/speed/docs/insights/v5/get-started",
            True,
        ),
        (
            "places",
            "Google Places",
            ("GOOGLE_PLACES_API_KEY",),
            _has(s.google_places_api_key),
            True,
            "https://console.cloud.google.com/google/maps-apis",
            True,
        ),
        (
            "smtp",
            "SMTP → Mailpit (nothing really sent)",
            ("SMTP_HOST", "SMTP_PORT"),
            _has(s.smtp_host),
            False,
            "",
            email == "smtp",
        ),
        (
            "mailpit",
            "Mailpit inbox (replies, alerts)",
            ("MAILPIT_API_URL",),
            _has(s.mailpit_api_url),
            False,
            "",
            email == "smtp",
        ),
        (
            "resend",
            "Resend",
            ("RESEND_API_KEY", "SENDING_DOMAIN"),
            _has(s.resend_api_key) and _has(s.sending_domain),
            True,
            "https://resend.com",
            email == "resend",
        ),
        (
            "sender_identity",
            "Sender identity",
            ("SENDER_NAME", "SENDER_BUSINESS_NAME", "SENDER_POSTAL_ADDRESS", "PORTFOLIO_URL"),
            sender_ok,
            False,
            "",
            True,
        ),
        (
            "imap",
            "Reply mailbox (IMAP)",
            ("REPLY_IMAP_HOST", "REPLY_IMAP_USER", "REPLY_IMAP_PASSWORD"),
            imap_ok,
            True,
            "",
            email == "resend",
        ),
        (
            "verification",
            "Email verification (syntax + MX, local)",
            ("EMAIL_VERIFICATION_PROVIDER",),
            True,
            False,
            "",
            s.email_verification_provider == "local",
        ),
        (
            "zerobounce",
            "Email verification (ZeroBounce)",
            ("EMAIL_VERIFICATION_API_KEY",),
            _has(s.email_verification_api_key),
            True,
            "https://www.zerobounce.net",
            s.email_verification_provider == "zerobounce",
        ),
        (
            "reddit",
            "Reddit Data API (official)",
            ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"),
            reddit_ok,
            True,
            "https://www.reddit.com/prefs/apps",
            True,
        ),
        (
            "x",
            "X API (official)",
            ("X_BEARER_TOKEN",),
            _has(s.x_bearer_token),
            True,
            "https://developer.x.com",
            True,
        ),
        (
            "meta",
            "Meta Graph API (official)",
            ("META_ACCESS_TOKEN",),
            _has(s.meta_access_token),
            True,
            "https://developers.facebook.com",
            True,
        ),
        # public project search works without a token
        (
            "freelancer",
            "Freelancer.com API",
            ("FREELANCER_API_TOKEN",),
            True,
            False,
            "https://developers.freelancer.com",
            True,
        ),
        (
            "opencorporates",
            "OpenCorporates",
            ("OPENCORPORATES_API_KEY",),
            _has(s.opencorporates_api_key),
            True,
            "https://api.opencorporates.com",
            True,
        ),
        (
            "companies_house",
            "Companies House",
            ("COMPANIES_HOUSE_API_KEY",),
            _has(s.companies_house_api_key),
            False,
            "https://developer.company-information.service.gov.uk",
            True,
        ),
        # Open data sources that need no key
        ("rss", "RSS / Atom feeds", (), True, False, "", True),
        ("web", "Public websites & directories", (), True, False, "", True),
    ]
    out: dict[str, IntegrationStatus] = {}
    for key, label, env, has_keys, paid, url, selected in specs:
        if s.testing_tier and key in TESTING_OFF:
            state, note = "off_testing_mode", "Off in testing mode"
        elif not selected:
            state, note = "off", "Inactive provider"
        elif not has_keys:
            state, note = "needs_api_key", f"Set {', '.join(env)}"
        else:
            state, note = "ready", ""
        out[key] = IntegrationStatus(key, label, env, has_keys, paid, url, state, note)
    # Derived: the inbox for replies and alert emails — Mailpit in testing, IMAP at go-live.
    inbox = out["mailpit"] if out["mailpit"].configured else out["imap"]
    out["mailbox"] = IntegrationStatus(
        "mailbox", f"Inbox ({inbox.label})", inbox.env_vars, inbox.has_keys, inbox.paid,
        inbox.signup_url, inbox.state, inbox.note,
    )  # fmt: skip
    return out


def log_missing_integrations(s: Settings) -> None:
    for status in integration_status(s).values():
        if status.state == "needs_api_key":
            logger.warning(
                "integration disabled: %s (set %s)", status.label, ", ".join(status.env_vars)
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
