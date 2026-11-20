"""Guards against encoding damage in source files (BOMs, cp1252 mojibake)."""

from __future__ import annotations

import re
from pathlib import Path

from app.services.locale import country_from_text

ROOT = Path(__file__).resolve().parents[2]
# Lead bytes of UTF-8 sequences decoded as cp1252 (Â Ã â) followed by a continuation-ish char.
# Built with chr() so the pattern itself stays plain ASCII.
_LEADS = "".join(map(chr, (0xC2, 0xC3, 0xE2)))
_TRAIL = f"{chr(0x80)}-{chr(0xBF)}{chr(0x2010)}-{chr(0x203A)}{chr(0x20AC)}{chr(0x2122)}"
MOJIBAKE = re.compile(f"[{_LEADS}][{_TRAIL}]")


def _sources() -> list[Path]:
    files = [*ROOT.joinpath("app").rglob("*.py"), *ROOT.joinpath("app").rglob("*.yaml")]
    dashboard = ROOT.parent / "dashboard" / "src"
    if dashboard.is_dir():
        files += [*dashboard.rglob("*.ts"), *dashboard.rglob("*.tsx"), *dashboard.rglob("*.css")]
    return files


def test_no_bom_or_mojibake_in_sources() -> None:
    bad = []
    for path in _sources():
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            bad.append(f"{path}: UTF-8 BOM")
        text = raw.decode("utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if MOJIBAKE.search(line):
                bad.append(f"{path}:{n}: {line.strip()[:60]}")
    assert bad == []


def test_spanish_country_word_detected() -> None:
    assert country_from_text("Calle Mayor 1, Madrid, España")[0] == "ES"


def test_social_posts_old_fresh_and_undated_are_classified() -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from app.agents.discovery.sources.social_apify import MAX_POST_AGE, _is_recent  # noqa: PLC0415

    now = datetime.now(UTC)
    assert timedelta(hours=8) == MAX_POST_AGE
    assert _is_recent(now - timedelta(hours=7, minutes=59))
    assert not _is_recent(now - timedelta(hours=8, minutes=1))
    assert not _is_recent(None)


def test_saved_social_fixture_freshness_for_four_platforms() -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from app.agents.discovery.sources.social_apify import _is_recent, _parse_dt  # noqa: PLC0415

    now = datetime.now(UTC)
    fixtures = {
        "x": now.isoformat(),
        "instagram": (now - timedelta(hours=9)).isoformat(),
        "facebook": None,
        "reddit": int((now - timedelta(hours=7)).timestamp()),
    }
    parsed = {platform: _parse_dt(value) for platform, value in fixtures.items()}
    assert _is_recent(parsed["x"])
    assert not _is_recent(parsed["instagram"])
    assert not _is_recent(parsed["facebook"])
    assert _is_recent(parsed["reddit"])
