"""osm sweep: businesses.osm_type/osm_id/first_seen/last_seen, geonames_cities, osm_sweep_cities

Revision ID: 05d3363cc15c
Revises: 8d1c4e7a2b90
Create Date: 2026-11-21 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "05d3363cc15c"
down_revision: str | None = "8d1c4e7a2b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("osm_type", sa.String(10), nullable=True))
    op.add_column("businesses", sa.Column("osm_id", sa.BigInteger(), nullable=True))
    op.add_column("businesses", sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True))
    # Businesses stored by the business search carry their OSM element as the registry id.
    op.execute(
        "UPDATE businesses SET osm_type = split_part(registry_number, '/', 1), "
        "osm_id = split_part(registry_number, '/', 2)::bigint, "
        "first_seen = created_at, last_seen = updated_at "
        "WHERE registry_source = 'osm' AND registry_number ~ '^(node|way|relation)/[0-9]+$'"
    )
    op.create_unique_constraint("uq_business_osm", "businesses", ["osm_type", "osm_id"])
    op.create_index("ix_businesses_last_seen", "businesses", ["last_seen"])

    op.create_table(
        "geonames_cities",
        sa.Column("geonameid", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("ascii_name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("population", sa.BigInteger(), nullable=False),
        sa.Column("feature_code", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_geonames_cities_country_pop", "geonames_cities", ["country", "population"]
    )

    op.create_table(
        "osm_sweep_cities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("country", sa.String(2), nullable=False, index=True),
        sa.Column("geonameid", sa.Integer(), nullable=False),
        sa.Column("city", sa.String(200), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("population", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("counts", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mirror", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("day", "geonameid", name="uq_osm_sweep_day_city"),
    )
    op.create_index("ix_osm_sweep_cities_day_status", "osm_sweep_cities", ["day", "status"])


def downgrade() -> None:
    op.drop_table("osm_sweep_cities")
    op.drop_index("ix_geonames_cities_country_pop", table_name="geonames_cities")
    op.drop_table("geonames_cities")
    op.drop_index("ix_businesses_last_seen", table_name="businesses")
    op.drop_constraint("uq_business_osm", "businesses", type_="unique")
    op.drop_column("businesses", "last_seen")
    op.drop_column("businesses", "first_seen")
    op.drop_column("businesses", "osm_id")
    op.drop_column("businesses", "osm_type")
