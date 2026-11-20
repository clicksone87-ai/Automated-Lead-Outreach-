"""messages.body_text: the text of a Gmail draft or compose link, kept for the outbox

Revision ID: 3a8e51c07d42
Revises: 9f3d4c1a6b20
Create Date: 2026-11-21 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3a8e51c07d42"
down_revision: str | None = "9f3d4c1a6b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("body_text", sa.Text(), nullable=True))
    op.create_index("ix_messages_delivery_status", "messages", ["delivery_status"])


def downgrade() -> None:
    op.drop_index("ix_messages_delivery_status", table_name="messages")
    op.drop_column("messages", "body_text")
