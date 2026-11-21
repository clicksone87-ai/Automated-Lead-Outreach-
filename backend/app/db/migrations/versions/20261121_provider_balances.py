"""provider_balances: what each provider itself reports as left (live caps)

Revision ID: 4f7b2c9d1e35
Revises: 8d1c4e7a2b90
Create Date: 2026-11-21 06:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4f7b2c9d1e35"
down_revision: str | None = "8d1c4e7a2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_balances",
        sa.Column("provider", sa.String(80), primary_key=True),
        sa.Column("remaining", sa.Float(), nullable=True),
        sa.Column("limit", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=False, server_default="units"),
        sa.Column("period", sa.String(20), nullable=False, server_default="lifetime"),
        sa.Column("cost_per_call", sa.Float(), nullable=False, server_default="1"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("provider_balances")
