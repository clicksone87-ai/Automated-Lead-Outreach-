"""The clean-leads command is registered and passes --dry-run through."""

from __future__ import annotations

import pytest

from app import cli


def test_clean_leads_is_a_cli_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    async def fake(dry_run: bool) -> dict[str, object]:
        seen.append(dry_run)
        return {"dry_run": dry_run}

    monkeypatch.setattr(cli, "_clean_leads", fake)
    monkeypatch.setattr(cli, "log_missing_integrations", lambda s: None)
    assert cli.main(["clean-leads", "--dry-run"]) == 0
    assert cli.main(["clean-leads"]) == 0
    assert seen == [True, False]
