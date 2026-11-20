"""backfill Track B proposals into manual outreach

Revision ID: 9f3d4c1a6b20
Revises: 7c2d8e9f1a30
Create Date: 2026-11-20 16:20:00+00:00
"""
from collections.abc import Sequence

from alembic import op


revision: str = "9f3d4c1a6b20"
down_revision: str | None = "7c2d8e9f1a30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO manual_outreach (
            id, lead_id, channel, post_link, drafts, status,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            latest.lead_id,
            COALESCE(latest.draft->'track_b'->>'channel', 'manual'),
            latest.draft->'track_b'->>'route',
            jsonb_build_object(
                'comment', COALESCE(latest.draft->'track_b'->>'comment', ''),
                'dm', COALESCE(latest.draft->'track_b'->>'dm', ''),
                'contact_form', COALESCE(latest.draft->'track_b'->>'contact_form', '')
            ),
            'pending',
            now(),
            now()
        FROM (
            SELECT DISTINCT ON (lead_id) *
            FROM proposals
            WHERE draft->'track_b' IS NOT NULL
            ORDER BY lead_id, created_at DESC, version DESC
        ) AS latest
        ON CONFLICT (lead_id, channel) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM manual_outreach")
