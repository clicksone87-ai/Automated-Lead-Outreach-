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
