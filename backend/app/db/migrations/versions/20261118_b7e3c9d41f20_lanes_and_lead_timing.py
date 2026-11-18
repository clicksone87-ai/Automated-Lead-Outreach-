"""lanes and lead timing

Revision ID: b7e3c9d41f20
Revises: 6f287366ae8c
Create Date: 2026-11-18 09:00:00+00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b7e3c9d41f20'
down_revision: str | None = '6f287366ae8c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FAMILIES = {
    'social': ('social_reddit', 'social_x', 'social_instagram', 'social_facebook', 'reddit', 'x', 'meta'),
    'google': ('search_api', 'local_sites', 'site_health'),
    'registry': ('companies_house', 'opencorporates'),
}


def upgrade() -> None:
    op.add_column('leads', sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('leads', sa.Column('source_family', sa.String(length=20), nullable=True))
    op.add_column('leads', sa.Column('lane', sa.String(length=10), nullable=True))
    op.add_column('leads', sa.Column('prefilter_reason', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('lane_timings', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.create_index(op.f('ix_leads_posted_at'), 'leads', ['posted_at'], unique=False)
    op.create_index(op.f('ix_leads_source_family'), 'leads', ['source_family'], unique=False)
    op.create_index(op.f('ix_leads_lane'), 'leads', ['lane'], unique=False)
    op.add_column('audits', sa.Column('depth', sa.String(length=10), server_default='full', nullable=False))

    # Backfill: family from the source key, the post's own timestamp from its evidence record
    # (only well-formed ISO values; anything else stays empty).
    cases = ' '.join(
        f"WHEN source IN ({', '.join(repr(s) for s in sources)}) THEN '{family}'"
        for family, sources in FAMILIES.items()
    )
    op.execute(f"UPDATE leads SET source_family = CASE {cases} ELSE 'other' END")
    op.execute(
        """
        UPDATE leads l SET posted_at = sub.posted
        FROM (
            SELECT DISTINCT ON (e.lead_id) e.lead_id, (e.data->>'posted_at')::timestamptz AS posted
            FROM evidence e
            WHERE e.kind = 'post'
              AND e.data->>'posted_at' ~ '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}'
            ORDER BY e.lead_id, e.captured_at
        ) sub
        WHERE sub.lead_id = l.id
        """
    )


def downgrade() -> None:
    op.drop_column('audits', 'depth')
    op.drop_index(op.f('ix_leads_lane'), table_name='leads')
    op.drop_index(op.f('ix_leads_source_family'), table_name='leads')
    op.drop_index(op.f('ix_leads_posted_at'), table_name='leads')
    op.drop_column('leads', 'lane_timings')
    op.drop_column('leads', 'prefilter_reason')
    op.drop_column('leads', 'lane')
    op.drop_column('leads', 'source_family')
    op.drop_column('leads', 'posted_at')
