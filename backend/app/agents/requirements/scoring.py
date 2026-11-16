"""Rule-based buying-intent signals (plain code; weights adjustable by the learning loop).

Weights live in `settings.scoring_weights`; any key missing there uses DEFAULT_WEIGHTS.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.db.models import TriggerType

DEFAULT_WEIGHTS: dict[str, float] = {
    # base score per trigger
    "base:explicit_request": 50,
    "base:new_business": 62,
    "base:no_website": 66,
    "base:broken_or_outdated_site": 62,
    "base:profile_without_site": 66,
    # explicit-request text signals
    "budget_mentioned": 12,
    "timeline_mentioned": 6,
    "request_phrase": 6,
    "recent_7d": 5,
    "stale_30d": -20,
    "seller_not_buyer": -45,
    "diy_question": -15,
    "business_identified": 4,
    # weight of the LLM score when combining (0..1)
    "llm_weight": 0.7,
}

_REQUEST = re.compile(r"\b(hiring|looking for|need(ed)?|seeking|quote|recommend(ations)?)\b", re.I)
_BUDGET = re.compile(r"(budget|[$£€]\s?\d|\d+\s?(usd|eur|gbp|k\b))", re.I)
_TIMELINE = re.compile(
    r"\b(asap|urgent|this week|next week|by (monday|friday|end of)|within \d+)", re.I
)
_SELLER = re.compile(
    r"(\[for hire\]|\bi (design|build|make|create|offer)\b|\bmy (portfolio|services|agency)\b|"
    r"\bhire me\b|\bdm me for\b)",
    re.I,
)
_DIY = re.compile(
    r"\b(how (do|can) i|which (builder|platform|theme)|wix or squarespace|diy)\b", re.I
)


def rule_signals(
    trigger: str,
    text: str,
    *,
    posted_at: datetime | None,
    business_known: bool,
    weights: dict[str, float],
) -> dict[str, int]:
    w = {**DEFAULT_WEIGHTS, **weights}
    signals: dict[str, int] = {f"base:{trigger}": int(w.get(f"base:{trigger}", 50))}
    if trigger != TriggerType.EXPLICIT_REQUEST:
        return signals
    checks = {
        "budget_mentioned": bool(_BUDGET.search(text)),
        "timeline_mentioned": bool(_TIMELINE.search(text)),
        "request_phrase": bool(_REQUEST.search(text)),
        "seller_not_buyer": bool(_SELLER.search(text)),
        "diy_question": bool(_DIY.search(text)),
        "business_identified": business_known,
    }
    if posted_at is not None:
        age_days = (datetime.now(UTC) - posted_at).days
        checks["recent_7d"] = age_days <= 7
        checks["stale_30d"] = age_days > 30
    for name, hit in checks.items():
        if hit:
            signals[name] = int(w[name])
    return signals


def combine(llm_score: int | None, signals: dict[str, int], weights: dict[str, float]) -> int:
    rule = max(0, min(100, sum(signals.values())))
    if llm_score is None:
        return rule
    lw = float({**DEFAULT_WEIGHTS, **weights}["llm_weight"])
    return max(0, min(100, round(lw * llm_score + (1 - lw) * rule)))
