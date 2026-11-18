"""Pure signal detection for proactive triggers (no network)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from app.services.dedup import normalize_name

_COPYRIGHT = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)
_OLD_JQUERY = re.compile(r"jquery[-.]?(1\.[0-8](?:\.\d+)?)(?:\.min)?\.js", re.I)
_OLD_GENERATORS = re.compile(
    r"(frontpage|dreamweaver|iweb|wordpress [1-4]\.|joomla! 1\.|"
    r"microsoft word|publisher)",
    re.I,
)

# Plausible domain endings per target country (used only to CHECK whether a site exists).
# A country missing here falls back to (".com",), which misses local domains.
TLDS_BY_COUNTRY = {
    "GB": ("co.uk", "uk", "com"),
    "IE": ("ie", "com"),
    "US": ("com", "net", "us"),
    "DE": ("de", "com"),
    "FR": ("fr", "com"),
    "ES": ("es", "com"),
    "AT": ("at", "com"),
    "BE": ("be", "com"),
    "IT": ("it", "com"),
    "NL": ("nl", "com"),
    "PT": ("pt", "com"),
    "SE": ("se", "com"),
    "DK": ("dk", "com"),
    "NO": ("no", "com"),
    "FI": ("fi", "com"),
    "PL": ("pl", "com"),
    "LU": ("lu", "com"),
    "CH": ("ch", "com"),
    "CZ": ("cz", "com"),
    "RO": ("ro", "com"),
    "GR": ("gr", "com"),
    "HU": ("hu", "com"),
    "BG": ("bg", "com"),
    "HR": ("hr", "com"),
    "SK": ("sk", "com"),
    "SI": ("si", "com"),
    "EE": ("ee", "com"),
    "LV": ("lv", "com"),
    "LT": ("lt", "com"),
    "MT": ("mt", "com"),
    "CY": ("cy", "com"),
    "IS": ("is", "com"),
}


_CHALLENGE = re.compile(
    r"(just a moment\.\.\.|attention required! \| cloudflare|cf-chl|cf_chl_|challenge-platform|"
    r"captcha|are you a robot|access denied|ddos protection)",
    re.I,
)
# On a normal 200 page only unambiguous challenge markers count (contact forms use reCAPTCHA).
_CHALLENGE_200 = re.compile(
    r"(<title>\s*just a moment\.\.\.|attention required! \| cloudflare|cf_chl_|"
    r"/cdn-cgi/challenge-platform)",
    re.I,
)


@dataclass(slots=True)
class SiteHealth:
    # ok | broken | outdated | unreachable | protected (bot check / access denied: unknown,
    # never actionable — we do not get past challenges)
    status: str
    signals: list[str] = field(default_factory=list)


def outdated_signals(html: str, *, final_url: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    soup = BeautifulSoup(html, "lxml")
    signals: list[str] = []
    if not final_url.startswith("https://"):
        signals.append("no_https")
    if not soup.find("meta", attrs={"name": "viewport"}):
        signals.append("no_mobile_viewport")
    years = [int(y) for y in _COPYRIGHT.findall(soup.get_text(" "))]
    if years and max(years) <= now.year - 4:
        signals.append(f"copyright_{max(years)}")
    layout_tables = [t for t in soup.find_all("table") if t.find("table") or t.get("width")]
    if len(layout_tables) >= 2:
        signals.append("table_layout")
    if soup.find(["marquee", "blink", "frameset"]) or soup.find(
        "embed", src=re.compile(r"\.swf", re.I)
    ):
        signals.append("obsolete_tags")
    if _OLD_JQUERY.search(html):
        signals.append("old_jquery")
    generator = soup.find("meta", attrs={"name": "generator"})
    if generator and _OLD_GENERATORS.search(str(generator.get("content") or "")):
        signals.append("old_generator")
    return signals


def classify_site(
    status_code: int | None, html: str | None, final_url: str | None, now: datetime | None = None
) -> SiteHealth:
    if status_code is None:
        return SiteHealth("unreachable", ["unreachable"])
    head = (html or "")[:20_000]
    if status_code in (401, 403, 429) or (status_code in (503, 202) and _CHALLENGE.search(head)):
        return SiteHealth("protected", [f"http_{status_code}"])
    if status_code >= 400:
        return SiteHealth("broken", [f"http_{status_code}"])
    if _CHALLENGE_200.search(head):
        return SiteHealth("protected", ["bot_challenge"])
    signals = outdated_signals(html or "", final_url=final_url or "", now=now)
    # Two or more independent signals (or no HTTPS + no mobile) = outdated.
    strong = {"no_https", "no_mobile_viewport"}
    if len(signals) >= 2 or strong <= set(signals):
        return SiteHealth("outdated", signals)
    return SiteHealth("ok", signals)


def domain_candidates(name: str, country: str | None, limit: int = 6) -> list[str]:
    """Plausible domains for a business name, used only to CHECK whether a site exists."""
    words = normalize_name(name).split()
    if not words:
        return []
    joined, hyphen = "".join(words), "-".join(words)
    stems = list(dict.fromkeys([joined, hyphen] if len(words) > 1 else [joined]))
    tlds = TLDS_BY_COUNTRY.get((country or "").upper(), ("com",))
    out = [f"{stem}.{tld}" for tld in tlds for stem in stems if 3 <= len(stem) <= 63]
    return out[:limit]


def page_mentions_business(html: str, name: str) -> bool:
    words = [w for w in normalize_name(name).split() if len(w) > 2]
    if not words:
        return False
    text = normalize_name(BeautifulSoup(html, "lxml").get_text(" ")[:20_000])
    hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", text))
    return hits >= max(1, round(len(words) * 0.6))