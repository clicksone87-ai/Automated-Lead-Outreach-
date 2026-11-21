"""move leads stopped as never useful into the "Not useful" bucket

rejected_low_intent -> older_than_8h (stale or undated post stopped by the prefilter) or
low_intent; no_contact_route -> no_contact; parked_country for an excluded market ->
excluded_market. Other parked countries, parked_language, cooldown, duplicate and suppressed
stay as they are. Idempotent: a mapped lead no longer has its old status. The same rules run
from `python -m app.cli map-not-useful`.

Revision ID: a6d2861ea1b3
Revises: 8d1c4e7a2b90
Create Date: 2026-11-21 09:00:00+00:00
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6d2861ea1b3"
down_revision: str | None = "8d1c4e7a2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the rules (a migration must not change when the app code does).
_REASON = "status_reason = '{label}' || COALESCE(' (' || status_reason || ')', '')"
STATEMENTS: tuple[str, ...] = (
    "UPDATE leads SET status = 'not_useful', not_useful_reason = 'older_than_8h', "
    + _REASON.format(label="not useful: older than 8 h")
    + " WHERE status = 'rejected_low_intent' AND (prefilter_reason LIKE 'post is % h old (limit % h)'"
    " OR prefilter_reason = 'social post without a timestamp')",
    "UPDATE leads SET status = 'not_useful', not_useful_reason = 'low_intent', "
    + _REASON.format(label="not useful: low intent")
    + " WHERE status = 'rejected_low_intent'",
    "UPDATE leads SET status = 'not_useful', not_useful_reason = 'no_contact', "
    + _REASON.format(label="not useful: no contact route")
    + " WHERE status = 'no_contact_route'",
    "UPDATE leads SET status = 'not_useful', not_useful_reason = 'excluded_market', "
    + _REASON.format(label="not useful: excluded market")
    + " WHERE status = 'parked_country' AND (status_reason LIKE '%is an excluded market%'"
    " OR upper(country) = ANY(:excluded))",
)

DOWN: tuple[str, ...] = (
    "UPDATE leads SET status = 'rejected_low_intent', not_useful_reason = NULL"
    " WHERE status = 'not_useful' AND not_useful_reason IN ('low_intent', 'older_than_8h')",
    "UPDATE leads SET status = 'no_contact_route', not_useful_reason = NULL"
    " WHERE status = 'not_useful' AND not_useful_reason = 'no_contact'",
    "UPDATE leads SET status = 'parked_country', not_useful_reason = NULL"
    " WHERE status = 'not_useful' AND not_useful_reason = 'excluded_market'",
)


def excluded_countries() -> list[str]:
    raw = os.environ.get("EXCLUDED_COUNTRIES", "PK,IN,BD")
    return sorted({c.strip().upper() for c in raw.split(",") if c.strip()})


def upgrade() -> None:
    for sql in STATEMENTS:
        stmt = sa.text(sql)
        if ":excluded" in sql:
            stmt = stmt.bindparams(sa.bindparam("excluded", excluded_countries()))
        op.execute(stmt)


def downgrade() -> None:
    for sql in DOWN:
        op.execute(sa.text(sql))
