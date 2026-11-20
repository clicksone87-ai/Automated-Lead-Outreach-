"""Lead family and tier count invariants."""

from app.services.queries import summarize_lead_family_tiers


def test_family_tier_counts_keep_low_and_unscored_separate() -> None:
    rows = [
        ("social", "social_x", 75),
        ("social", "social_x", 60),
        ("social", "social_x", 59),
        ("social", "social_x", None),
        ("google", "search_api", 74),
        (None, "companies_house", 80),
    ]
    counts = {row.family: row for row in summarize_lead_family_tiers(rows)}

    assert counts["social"].high == 1
    assert counts["social"].medium == 1
    assert counts["social"].low == 1
    assert counts["social"].unscored == 1
    assert counts["google"].medium == 1
    assert counts["registry"].high == 1
