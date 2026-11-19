"""merge lanes and logo assets

Revision ID: 4dbe07b2e107
Revises: b7e3c9d41f20, b7d41c2e9a10
Create Date: 2026-11-19 08:45:12.479498+00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '4dbe07b2e107'
down_revision: str | Sequence[str] | None = ('b7e3c9d41f20', 'b7d41c2e9a10')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
