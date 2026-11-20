"""Every variable in .env.example maps 1:1 to a config field."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

# Repo root on the host; compose mounts the same file at /app/.env.example in the containers.
CANDIDATES = (
    Path(__file__).resolve().parents[3] / ".env.example",
    Path(__file__).resolve().parents[2] / ".env.example",
)
EXAMPLE = next((p for p in CANDIDATES if p.is_file()), CANDIDATES[0])


def test_env_example_matches_settings_fields() -> None:
    keys = set(re.findall(r"^([A-Z0-9_]+)=", EXAMPLE.read_text(encoding="utf-8"), re.M))
    fields = {name.upper() for name in Settings.model_fields}
    assert keys == fields, {"missing": sorted(fields - keys), "extra": sorted(keys - fields)}


def test_env_example_has_no_real_values_for_secrets() -> None:
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m and any(w in m.group(1) for w in ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            assert m.group(2) == "", f"{m.group(1)} must be empty in .env.example"
