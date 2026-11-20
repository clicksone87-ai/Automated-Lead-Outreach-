"""manual outreach queue

Revision ID: 7c2d8e9f1a30
Revises: 4dbe07b2e107
Create Date: 2026-11-20 15:20:00+00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c2d8e9f1a30"
down_revision: str | None = "4dbe07b2e107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manual_outreach",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("post_link", sa.Text(), nullable=True),
        sa.Column(
            "drafts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("done_by", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lead_id", "channel", name="uq_manual_outreach_lead_channel"),
    )
    op.create_index(
        op.f("ix_manual_outreach_channel"),
        "manual_outreach",
        ["channel"],
        unique=False,
    )
    op.create_index(
        op.f("ix_manual_outreach_lead_id"),
        "manual_outreach",
        ["lead_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_manual_outreach_status"),
        "manual_outreach",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_manual_outreach_status"), table_name="manual_outreach")
    op.drop_index(op.f("ix_manual_outreach_lead_id"), table_name="manual_outreach")
    op.drop_index(op.f("ix_manual_outreach_channel"), table_name="manual_outreach")
    op.drop_table("manual_outreach")
