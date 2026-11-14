"""Normalization for duplicate matching and locale resolution."""

from __future__ import annotations

import pytest

from app.services.dedup import normalize_domain, normalize_name, normalize_phone
from app.services.locale import (
    country_from_domain,
    country_from_phone,
    country_from_text,
    detect_language,
    guess_locale,
    timezone_for,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Smith & Sons Plumbing Ltd.", "smith and sons plumbing"),
        ("SMITH AND SONS PLUMBING LIMITED", "smith and sons plumbing"),
        ("Müller Sanitär GmbH", "muller sanitar"),
        ("The Handy Co", "handy"),
        ("Électricité Dupont SARL", "electricite dupont"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.smithplumbing.co.uk/contact", "smithplumbing.co.uk"),
        ("SmithPlumbing.com", "smithplumbing.com"),
        ("http://shop.example.de/", "example.de"),
        ("https://www.facebook.com/smithplumbing", None),
        ("https://smith.wixsite.com/site", None),
        ("", None),
        (None, None),
        ("not a url", None),
    ],
)
def test_normalize_domain(raw: str | None, expected: str | None) -> None:
    assert normalize_domain(raw) == expected


def test_normalize_phone() -> None:
    assert normalize_phone("+44 (0)20 7946 0018") == "+4402079460018"
    assert normalize_phone("123") is None


def test_country_signals() -> None:
    assert country_from_domain("https://plumber.co.uk") == "GB"
    assert country_from_domain("https://elektro.de") == "DE"
    assert country_from_domain("https://plumber.com") is None
    assert country_from_phone("+1 415 555 0100") == "US"
    assert country_from_phone("+353 1 234 5678") == "IE"
    assert country_from_text("Unit 4, Leeds LS1 4AP")[0] == "GB"
    assert country_from_text("123 Main St, Austin TX 78701")[0] == "US"


def test_timezones() -> None:
    assert timezone_for("GB") == "Europe/London"
    assert timezone_for("US", "Austin, TX 78701") == "America/Chicago"
    assert timezone_for("US", "Portland, Oregon") == "America/Los_Angeles"
    assert timezone_for("US", "somewhere") is None


def test_language_detection() -> None:
    assert detect_language("Wir suchen einen Webdesigner für unsere neue Firmenwebseite.")[0] == "de"
    assert detect_language("Nous cherchons quelqu'un pour créer le site de notre entreprise.")[0] \
        == "fr"
    assert detect_language("Necesitamos una página web nueva para nuestro negocio.")[0] == "es"
    assert detect_language("short")[0] is None


def test_guess_locale_prefers_strong_signals() -> None:
    guess = guess_locale(
        text="Need a website for my plumbing company in Leeds",
        url="https://example.com", address="Leeds LS1 4AP",
    )
    assert guess.country == "GB"
    assert guess.language == "en"
    assert guess.timezone == "Europe/London"
    assert guess.country_signal == "uk_postcode"
