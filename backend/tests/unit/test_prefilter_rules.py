"""Prefilter rules: multilingual buyer/seller wording, staleness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.agents.prefilter.rules import check_text, stale_reason

BUYERS = [
    ("en", "Looking for a web designer to build a website for my plumbing business"),
    ("en", "Can anyone recommend someone to build a website? Budget around $1500"),
    ("en", "Need help with SEO for my bakery, we don't show up on Google"),
    ("en", "I make furniture and need a new website with a shop"),  # seller-ish words, real buyer
    ("de", "Suche jemanden, der mir eine Webseite für meinen Malerbetrieb erstellt"),
    ("fr", "Je cherche un webdesigner pour la refonte de notre site internet"),
    ("es", "Busco diseñador web para una página web de mi restaurante"),
    ("it", "Cerco qualcuno per il rifacimento del sito web del mio negozio"),
    ("nl", "Wij zoeken iemand voor een nieuwe website en logo"),
    ("pt", "Procuro alguém para criar o site da minha empresa"),
    ("pl", "Szukam kogoś do zrobienia strony internetowej dla firmy"),
    ("sv", "Vi söker någon som kan göra en ny hemsida åt vårt företag"),
    ("da", "Vi leder efter en webdesigner til vores hjemmeside"),
    ("fi", "Etsin tekijää yrityksen kotisivut uusiksi"),
    ("no", "Vi trenger ny nettside for bedriften, noen som kan anbefale?"),
    ("cs", "Hledám někoho na webové stránky pro naši firmu"),
    ("ro", "Caut pe cineva pentru o pagină web pentru firma mea"),
    ("el", "Ψάχνω κάποιον για ιστοσελίδα για την επιχείρησή μου"),  # noqa: RUF001
    ("hu", "Keresek valakit, aki honlapot készítene a cégemnek"),
]

SELLERS = [
    "I design websites for small businesses, DM me for a quote!",
    "[FOR HIRE] Logo designer, check out my portfolio",
    "We build websites starting at $299. Get your website today!",
    "I will design a modern logo for your brand in 24 hours, fiverr.com/someone",
    "Web designer here, taking on new clients this month",
    "Wir erstellen Webseiten für Handwerker — unsere Agentur hilft",
]


@pytest.mark.parametrize(("lang", "text"), BUYERS)
def test_buyers_pass_in_every_language(lang: str, text: str) -> None:
    verdict = check_text(text, lang)
    assert verdict.passed, (lang, verdict.reason)
    assert verdict.needs_llm


@pytest.mark.parametrize("text", SELLERS)
def test_sellers_are_rejected(text: str) -> None:
    verdict = check_text(text, "en")
    assert not verdict.passed
    assert verdict.reason.startswith("seller")


def test_posts_asking_for_nothing_are_rejected() -> None:
    assert check_text("Just launched our new website, so happy!", "en").reason == (
        "mentions our services but asks for nothing"
    )
    assert check_text("Need a plumber in Leeds asap", "en").reason.startswith("no logo")
    assert not check_text("   ", "en").passed


def test_job_listings_skip_seller_and_request_wording() -> None:
    text = "Website refresh for my contracting business; it should list my services and portfolio"
    assert not check_text(text, "en").passed
    assert check_text(text, "en", job_listing=True).passed


def test_unknown_language_is_left_to_the_llm() -> None:
    verdict = check_text("Kërkoj dikë për faqen time të internetit", "sq")
    assert verdict.passed and verdict.needs_llm


def test_stale_reason() -> None:
    now = datetime(2026, 11, 18, 12, tzinfo=UTC)
    limits = {"social": 24, "default": 168}
    fresh = now - timedelta(hours=3)
    old = now - timedelta(hours=30)
    assert stale_reason(fresh, family="social", now=now, max_age_hours=limits) is None
    assert "30 h old" in str(stale_reason(old, family="social", now=now, max_age_hours=limits))
    assert stale_reason(old, family="google", now=now, max_age_hours=limits) is None
    assert stale_reason(None, family="social", now=now, max_age_hours=limits) == (
        "social post without a timestamp"
    )
    assert stale_reason(None, family="google", now=now, max_age_hours=limits) is None
