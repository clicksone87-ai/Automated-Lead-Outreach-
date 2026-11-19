"""The proposal prompt only mentions drafts that are really attached."""

from app.agents.proposal.agent import _assets_line


def test_nothing_attached_offers_drafts_instead() -> None:
    line = _assets_line([])
    assert "Nothing is attached" in line and "never say anything is attached" in line


def test_lists_only_attached_assets() -> None:
    line = _assets_line(["audit", "logo"])
    assert "logo concepts" in line and "a website audit" in line
    assert "website preview" not in line
