"""Loader for country_rules.yaml (validated; missing/invalid file => everything blocked).

Excluded markets (EXCLUDED_COUNTRIES) never get a rule; unlisted countries use the file's
`default` entry when ALL_COUNTRIES_OUTREACH_ENABLED is on."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

RULES_PATH = Path(__file__).with_name("country_rules.yaml")

RecipientType = Literal["corporate_entity", "sole_trader", "individual", "unknown"]


class CountryRule(BaseModel):
    name: str
    enabled: bool = False
    sending_enabled: bool = True
    legal_reviewed: bool = False  # informational: a lawyer confirmed this entry
    regime_note: str | None = None
    allowed_recipient_types: list[RecipientType] = Field(default_factory=list)
    requires_prior_consent: bool = True
    requires_unsubscribe: bool = True
    requires_postal_address: bool = True
    requires_sender_identity: bool = True
    max_follow_ups: int = Field(default=0, ge=0, le=5)
    reviewed_by: str | None = None
    reviewed_on: str | None = None

    @property
    def can_draft(self) -> bool:
        return self.enabled

    @property
    def can_send(self) -> bool:
        return self.enabled and self.sending_enabled and not self.requires_prior_consent


class CountryRules(BaseModel):
    version: int
    countries: dict[str, CountryRule]
    default: CountryRule | None = None
    excluded: frozenset[str] = frozenset()

    def get(self, country: str | None) -> CountryRule | None:
        if not country:
            return None
        code = country.upper()
        if code in self.excluded:
            return None
        return self.countries.get(code) or self.default

    def is_excluded(self, country: str | None) -> bool:
        return bool(country) and str(country).upper() in self.excluded


def load_rules(
    path: Path = RULES_PATH,
    *,
    excluded: frozenset[str] = frozenset(),
    all_countries: bool = True,
) -> CountryRules:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules = CountryRules.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        logger.error("country rules unavailable (%s): ALL outreach blocked", exc)
        return CountryRules(version=0, countries={}, excluded=excluded)
    rules.countries = {
        k.upper(): v for k, v in rules.countries.items() if k.upper() not in excluded
    }
    rules.excluded = excluded
    if not all_countries:
        rules.default = None
    return rules


@lru_cache(maxsize=1)
def get_rules() -> CountryRules:
    from app.config import get_settings  # noqa: PLC0415 - config imports nothing from policy

    s = get_settings()
    return load_rules(
        excluded=s.excluded_country_set, all_countries=s.all_countries_outreach_enabled
    )
