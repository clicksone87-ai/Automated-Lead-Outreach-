"""logo assets: wordmark/monogram/icon sets and animated version

Revision ID: b7d41c2e9a10
Revises: 6f287366ae8c
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d41c2e9a10"
down_revision: str | None = "6f287366ae8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "logo_concepts",
        sa.Column(
            "assets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("logo_concepts", "assets")
