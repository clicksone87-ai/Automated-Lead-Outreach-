"""Country, language and time-zone resolution.

Deterministic signals (TLD, phone prefix, postcode patterns, country names) come first, then
lingua language detection; each result records the signal used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import phonenumbers
from lingua import Language, LanguageDetectorBuilder

from app.services.dedup import tld_extract

# Languages we can detect (the 16 V2 draft languages). Leads in anything other than enabled
# languages get parked.
_DETECTABLE = [
    Language.ENGLISH,
    Language.GERMAN,
    Language.FRENCH,
    Language.SPANISH,
    Language.ITALIAN,
    Language.DUTCH,
    Language.PORTUGUESE,
    Language.POLISH,
    Language.SWEDISH,
    Language.DANISH,
    Language.BOKMAL,
    Language.FINNISH,
    Language.CZECH,
    Language.ROMANIAN,
    Language.GREEK,
    Language.HUNGARIAN,
]
_ISO = {
    Language.ENGLISH: "en",
    Language.GERMAN: "de",
    Language.FRENCH: "fr",
    Language.SPANISH: "es",
    Language.ITALIAN: "it",
    Language.DUTCH: "nl",
    Language.PORTUGUESE: "pt",
    Language.POLISH: "pl",
    Language.SWEDISH: "sv",
    Language.DANISH: "da",
    Language.BOKMAL: "nb",
    Language.FINNISH: "fi",
    Language.CZECH: "cs",
    Language.ROMANIAN: "ro",
    Language.GREEK: "el",
    Language.HUNGARIAN: "hu",
}

TLD_COUNTRY = {
    "uk": "GB",
    "co.uk": "GB",
    "org.uk": "GB",
    "ie": "IE",
    "de": "DE",
    "fr": "FR",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "be": "BE",
    "at": "AT",
    "ch": "CH",
    "pt": "PT",
    "se": "SE",
    "dk": "DK",
    "no": "NO",
    "fi": "FI",
    "pl": "PL",
    "lu": "LU",
    "gr": "GR",
    "cz": "CZ",
    "ro": "RO",
    "hu": "HU",
    "bg": "BG",
    "hr": "HR",
    "si": "SI",
    "sk": "SK",
    "ee": "EE",
    "lv": "LV",
    "lt": "LT",
    "mt": "MT",
    "cy": "CY",
    "is": "IS",
    "us": "US",
}

COUNTRY_DEFAULT_LANGUAGE = {
    "US": "en",
    "GB": "en",
    "IE": "en",
    "DE": "de",
    "AT": "de",
    "CH": "de",
    "FR": "fr",
    "BE": "fr",
    "LU": "fr",
    "ES": "es",
    "IT": "it",
    "NL": "nl",
    "PT": "pt",
    "SE": "sv",
    "DK": "da",
    "NO": "nb",
    "FI": "fi",
    "PL": "pl",
    "CZ": "cs",
    "RO": "ro",
    "GR": "el",
    "CY": "el",
    "HU": "hu",
    # Languages outside our 16 fall back to English (handoff, Item 1).
    "BG": "en",
    "HR": "en",
    "SK": "en",
    "SI": "en",
    "EE": "en",
    "LV": "en",
    "LT": "en",
    "MT": "en",
    "IS": "en",
}

COUNTRY_TIMEZONE = {
    "GB": "Europe/London",
    "IE": "Europe/Dublin",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "NL": "Europe/Amsterdam",
    "BE": "Europe/Brussels",
    "AT": "Europe/Vienna",
    "CH": "Europe/Zurich",
    "PT": "Europe/Lisbon",
    "SE": "Europe/Stockholm",
    "DK": "Europe/Copenhagen",
    "NO": "Europe/Oslo",
    "FI": "Europe/Helsinki",
    "PL": "Europe/Warsaw",
    "LU": "Europe/Luxembourg",
    "CZ": "Europe/Prague",
    "RO": "Europe/Bucharest",
    "GR": "Europe/Athens",
    "HU": "Europe/Budapest",
    "BG": "Europe/Sofia",
    "HR": "Europe/Zagreb",
    "SK": "Europe/Bratislava",
    "SI": "Europe/Ljubljana",
    "EE": "Europe/Tallinn",
    "LV": "Europe/Riga",
    "LT": "Europe/Vilnius",
    "MT": "Europe/Malta",
    "CY": "Asia/Nicosia",
    "IS": "Atlantic/Reykjavik",
}

_US_TZ_GROUPS = {
    "America/New_York": "CT DE DC FL GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC VT VA WV",
    "America/Chicago": "AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI",
    "America/Denver": "CO MT NM UT WY ID",
    "America/Phoenix": "AZ",
    "America/Los_Angeles": "CA NV OR WA",
    "America/Anchorage": "AK",
    "Pacific/Honolulu": "HI",
}
US_STATE_TZ = {st: tz for tz, states in _US_TZ_GROUPS.items() for st in states.split()}
US_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

_UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")
_US_STATE_ZIP = re.compile(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b")
_COUNTRY_WORDS = {
    "united kingdom": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "uk": "GB",
    "ireland": "IE",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "spain": "ES",
    "españa": "ES",
    "united states": "US",
    "usa": "US",
    "u.s.": "US",
}


@dataclass(frozen=True, slots=True)
class LocaleGuess:
    country: str | None
    language: str | None
    timezone: str | None
    country_signal: str | None
    language_confidence: float


@lru_cache(maxsize=1)
def _detector():  # type: ignore[no-untyped-def]
    return (
        LanguageDetectorBuilder.from_languages(*_DETECTABLE)
        .with_preloaded_language_models()
        .build()
    )


def detect_language(text: str, min_confidence: float = 0.5) -> tuple[str | None, float]:
    sample = text.strip()[:3000]
    if len(sample) < 20:
        return None, 0.0
    values = _detector().compute_language_confidence_values(sample)
    if not values:
        return None, 0.0
    top = values[0]
    if top.value < min_confidence:
        return None, float(top.value)
    return _ISO.get(top.language), float(top.value)


def country_from_domain(url_or_host: str | None) -> str | None:
    if not url_or_host:
        return None
    host = re.sub(r"^[a-z]+://", "", url_or_host.lower()).split("/")[0]
    suffix = tld_extract(host).suffix
    return TLD_COUNTRY.get(suffix) or TLD_COUNTRY.get(suffix.rsplit(".", 1)[-1])


def country_from_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    try:
        parsed = phonenumbers.parse(phone, None)
    except phonenumbers.NumberParseException:
        return None
    return phonenumbers.region_code_for_number(parsed)


def us_state_from_text(text: str) -> str | None:
    m = _US_STATE_ZIP.search(text)
    if m and m.group(1) in US_STATE_TZ:
        return m.group(1)
    lowered = text.lower()
    for name, code in US_STATE_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", lowered):
            return code
    return None


def country_from_text(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    if _UK_POSTCODE.search(text.upper()):
        return "GB", "uk_postcode"
    if _US_STATE_ZIP.search(text):
        return "US", "us_state_zip"
    lowered = text.lower()
    for word, code in _COUNTRY_WORDS.items():
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            return code, f"mentions:{word}"
    return None, None


def timezone_for(country: str | None, address_or_text: str | None = None) -> str | None:
    if not country:
        return None
    if country == "US":
        state = us_state_from_text(address_or_text or "")
        return US_STATE_TZ.get(state) if state else None  # unknown state => resolve later
    return COUNTRY_TIMEZONE.get(country)


def guess_locale(
    *,
    text: str | None = None,
    url: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    country_hint: str | None = None,
) -> LocaleGuess:
    country, signal = None, None
    if country_hint:
        country, signal = country_hint.upper().replace("UK", "GB"), "source"
    if not country:
        country = country_from_phone(phone)
        signal = "phone" if country else None
    if not country:
        country, signal = country_from_text(address)
    if not country:
        country = country_from_domain(url)
        signal = "tld" if country else None
    if not country:
        country, signal = country_from_text(text)
    language, confidence = detect_language(text or "")
    if language is None and country:
        language, confidence = COUNTRY_DEFAULT_LANGUAGE.get(country), 0.3
    return LocaleGuess(
        country=country,
        language=language,
        timezone=timezone_for(country, " ".join(filter(None, [address, text]))),
        country_signal=signal,
        language_confidence=confidence,
    )
