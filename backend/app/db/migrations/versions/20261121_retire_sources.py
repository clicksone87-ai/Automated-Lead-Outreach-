"""retire Apify X/Reddit and freelance sources; social family for provider and Scout leads

Revision ID: dd5dd9cbf259
Revises: 8d1c4e7a2b90
Create Date: 2026-11-21 12:00:00+00:00

Data only. Apify now serves Facebook and Instagram only, and freelance platforms are handled
outside this system, so those source rows are switched off (kept for their run history; the
code no longer registers them). Provider and Scout leads were stored with the "other" family
because the mapping lacked them. Scout's untouched default config gains Twitch and GitHub so
every platform in SOCIAL_PLATFORMS is covered.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "dd5dd9cbf259"
down_revision: str | None = "8d1c4e7a2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETIRED = ("social_x", "social_reddit", "freelancer", "alert_emails")
OLD_SCOUT_PLATFORMS = '["tiktok", "youtube", "instagram", "linktree", "pinterest"]'
NEW_SCOUT = (
    '{"platforms": ["tiktok", "youtube", "instagram", "linktree", "pinterest", "twitch", '
    '"github"], "queries_per_run": 2}'
)


def upgrade() -> None:
    keys = ", ".join(f"'{k}'" for k in RETIRED)
    op.execute(f"UPDATE sources SET enabled = false WHERE key IN ({keys})")
    op.execute(
        "UPDATE leads SET source_family = 'social' "
        "WHERE source IN ('social_providers', 'scout_profiles') "
        "AND (source_family IS NULL OR source_family = 'other')"
    )
    op.execute(
        f"UPDATE sources SET config = config || '{NEW_SCOUT}'::jsonb "
        f"WHERE key = 'scout_profiles' AND config -> 'platforms' = '{OLD_SCOUT_PLATFORMS}'::jsonb "
        "AND COALESCE(config ->> 'queries_per_run', '1') = '1'"
    )


def downgrade() -> None:
    # The previous enabled flags and families are not known any more; nothing to undo safely.
    pass
