"""Application configuration.

All environment variables live here. Values from the environment are the
*initial* values; runtime-editable settings (autonomy stage, limits, budgets, languages,
industry) are persisted in the `settings` table and edited from the dashboard.

A missing API key never crashes the system: `integration_status()` reports which
integrations are unavailable so adapters fall back to disabled/mock behaviour and the
dashboard can show "Needs API key".
"""

from __future__ import annotations

import logging
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    app_mode: AppMode = AppMode.LOCAL
    log_level: str = "INFO"
    # When true, integrations without keys fall back to fixture-backed mocks so the whole
    # pipeline can run for development/demo. Anything produced with a mock is tagged and
    # can never be sent to a real recipient (policy: `demo_data` check).
    demo_mode: bool = False
    database_url: str = "postgresql+psycopg://leads:leads@localhost:5432/leads"
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("./data")
    dashboard_origin: str = "http://localhost:3000"

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

    # Email verification
    email_verification_provider: str = "zerobounce"
    email_verification_api_key: SecretStr | None = None

    # Platform sources
    reddit_client_id: str = ""
    reddit_client_secret: SecretStr | None = None
    reddit_user_agent: str = ""
    x_bearer_token: SecretStr | None = None
    meta_access_token: SecretStr | None = None
    freelancer_api_token: SecretStr | None = None
    producthunt_api_token: SecretStr | None = None

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
    def is_local(self) -> bool:
        return self.app_mode == AppMode.LOCAL

    @property
    def previews_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def public_links_enabled(self) -> bool:
        """True when previews can be linked publicly instead of attached as screenshots/PDF."""
        return bool(self.public_preview_base_url)


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    key: str
    label: str
    env_vars: tuple[str, ...]
    configured: bool
    paid: bool
    signup_url: str

    @property
    def state(self) -> str:
        return "ready" if self.configured else "needs_api_key"


def _has(value: SecretStr | str | None) -> bool:
    if value is None:
        return False
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    return bool(raw.strip())


def integration_status(s: Settings) -> dict[str, IntegrationStatus]:
    """Which external integrations are usable with the current configuration."""
    specs: list[tuple[str, str, tuple[str, ...], bool, bool, str]] = [
        (
            "openai", "OpenAI",
            ("OPENAI_API_KEY", "OPENAI_TEXT_MODEL"),
            _has(s.openai_api_key) and _has(s.openai_text_model), True,
            "https://platform.openai.com/api-keys",
        ),
        (
            "openai_vision", "OpenAI vision",
            ("OPENAI_API_KEY", "OPENAI_VISION_MODEL"),
            _has(s.openai_api_key) and _has(s.openai_vision_model), True,
            "https://platform.openai.com/api-keys",
        ),
        (
            "openai_image", "OpenAI images",
            ("OPENAI_API_KEY", "OPENAI_IMAGE_MODEL"),
            _has(s.openai_api_key) and _has(s.openai_image_model), True,
            "https://platform.openai.com/api-keys",
        ),
        ("brave", "Brave Search", ("BRAVE_SEARCH_API_KEY",), _has(s.brave_search_api_key), True,
         "https://brave.com/search/api/"),
        ("pagespeed", "PageSpeed Insights", ("GOOGLE_PAGESPEED_API_KEY",),
         _has(s.google_pagespeed_api_key), False,
         "https://developers.google.com/speed/docs/insights/v5/get-started"),
        ("places", "Google Places", ("GOOGLE_PLACES_API_KEY",), _has(s.google_places_api_key),
         True, "https://console.cloud.google.com/google/maps-apis"),
        (
            "resend", "Resend",
            ("RESEND_API_KEY", "SENDING_DOMAIN"),
            _has(s.resend_api_key) and _has(s.sending_domain), True,
            "https://resend.com",
        ),
        (
            "sender_identity", "Sender identity",
            ("SENDER_NAME", "SENDER_BUSINESS_NAME", "SENDER_POSTAL_ADDRESS", "PORTFOLIO_URL"),
            all(_has(v) for v in (s.sender_name, s.sender_business_name,
                                   s.sender_postal_address, s.portfolio_url)),
            False, "",
        ),
        (
            "imap", "Reply mailbox (IMAP)",
            ("REPLY_IMAP_HOST", "REPLY_IMAP_USER", "REPLY_IMAP_PASSWORD"),
            _has(s.reply_imap_host) and _has(s.reply_imap_user) and _has(s.reply_imap_password),
            True, "",
        ),
        ("verification", "Email verification (ZeroBounce)", ("EMAIL_VERIFICATION_API_KEY",),
         _has(s.email_verification_api_key), True, "https://www.zerobounce.net"),
        (
            "reddit", "Reddit Data API",
            ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"),
            _has(s.reddit_client_id) and _has(s.reddit_client_secret)
            and _has(s.reddit_user_agent),
            False, "https://www.reddit.com/prefs/apps",
        ),
        ("x", "X API", ("X_BEARER_TOKEN",), _has(s.x_bearer_token), True,
         "https://developer.x.com"),
        ("meta", "Meta Graph API", ("META_ACCESS_TOKEN",), _has(s.meta_access_token), False,
         "https://developers.facebook.com"),
        ("freelancer", "Freelancer.com API", ("FREELANCER_API_TOKEN",),
         _has(s.freelancer_api_token), False, "https://developers.freelancer.com"),
        ("producthunt", "Product Hunt API", ("PRODUCTHUNT_API_TOKEN",),
         _has(s.producthunt_api_token), False, "https://api.producthunt.com/v2/docs"),
        ("opencorporates", "OpenCorporates", ("OPENCORPORATES_API_KEY",),
         _has(s.opencorporates_api_key), True, "https://api.opencorporates.com"),
        ("companies_house", "Companies House", ("COMPANIES_HOUSE_API_KEY",),
         _has(s.companies_house_api_key), False,
         "https://developer.company-information.service.gov.uk"),
        # Open data sources that need no key
        ("brreg", "Brønnøysund Register", (), True, False, "https://data.brreg.no"),
        ("rss", "RSS / Atom feeds", (), True, False, ""),
        ("web", "Public websites & directories", (), True, False, ""),
    ]
    return {
        key: IntegrationStatus(key, label, env, configured, paid, url)
        for key, label, env, configured, paid, url in specs
    }


def log_missing_integrations(s: Settings) -> None:
    for status in integration_status(s).values():
        if not status.configured:
            logger.warning(
                "integration disabled: %s (set %s)", status.label, ", ".join(status.env_vars)
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
