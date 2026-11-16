"""Every variable in .env.example maps 1:1 to a config field."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def test_env_example_matches_settings_fields() -> None:
    keys = set(re.findall(r"^([A-Z0-9_]+)=", EXAMPLE.read_text(encoding="utf-8"), re.M))
    fields = {name.upper() for name in Settings.model_fields}
    assert keys == fields, {"missing": sorted(fields - keys), "extra": sorted(keys - fields)}


def test_env_example_has_no_real_values_for_secrets() -> None:
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m and m.group(1).endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            assert m.group(2) == "", f"{m.group(1)} must be empty in .env.example"
