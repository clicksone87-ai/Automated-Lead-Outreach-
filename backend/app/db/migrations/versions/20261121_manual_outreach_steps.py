"""manual outreach steps: contact confirmed, assets requested, sent

Revision ID: 2508611dc76b
Revises: a6d2861ea1b3
Create Date: 2026-11-21 09:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2508611dc76b"
down_revision: str | None = "a6d2861ea1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "manual_outreach",
        sa.Column("contact_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "manual_outreach", sa.Column("contact_confirmed_by", sa.String(100), nullable=True)
    )
    op.add_column(
        "manual_outreach",
        sa.Column("assets_requested", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("manual_outreach", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("manual_outreach", sa.Column("sent_by", sa.String(100), nullable=True))


def downgrade() -> None:
    for column in ("sent_by", "sent_at", "assets_requested", "contact_confirmed_by",
                   "contact_confirmed_at"):  # fmt: skip
        op.drop_column("manual_outreach", column)
