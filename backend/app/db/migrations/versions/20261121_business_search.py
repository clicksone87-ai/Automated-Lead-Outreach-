"""business search: businesses.website_status and the discovery_jobs table

Revision ID: 6b2f0d9e4c11
Revises: 3a8e51c07d42
Create Date: 2026-11-21 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b2f0d9e4c11"
down_revision: str | None = "3a8e51c07d42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("website_status", sa.String(30), nullable=True))
    op.create_index("ix_businesses_website_status", "businesses", ["website_status"])
    op.create_table(
        "discovery_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("country", sa.String(2), nullable=False, index=True),
        sa.Column("city", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued", index=True),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("business_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("errors", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("discovery_jobs")
    op.drop_index("ix_businesses_website_status", table_name="businesses")
    op.drop_column("businesses", "website_status")
