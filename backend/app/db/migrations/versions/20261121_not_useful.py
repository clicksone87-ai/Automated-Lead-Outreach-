"""leads.not_useful_reason for the "Not useful" bucket

Revision ID: 8d1c4e7a2b90
Revises: 6b2f0d9e4c11
Create Date: 2026-11-21 05:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d1c4e7a2b90"
down_revision: str | None = "6b2f0d9e4c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("not_useful_reason", sa.String(30), nullable=True))
    op.create_index("ix_leads_not_useful_reason", "leads", ["not_useful_reason"])


def downgrade() -> None:
    op.drop_index("ix_leads_not_useful_reason", table_name="leads")
    op.drop_column("leads", "not_useful_reason")
