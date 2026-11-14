"""Test configuration.

DB tests (marked `db`) run against TEST_DATABASE_URL (default: the compose Postgres's
`leads_test` database). They are skipped (reported as skips) when no database is reachable.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://leads:leads@postgres:5432/leads_test"
)
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("DATA_DIR", str(Path(tempfile.gettempdir()) / "leads-test-data"))
os.environ["APP_MODE"] = "test"
os.environ["DEMO_MODE"] = "true"
for key in list(os.environ):  # never let real keys leak into tests
    if key.endswith(("_API_KEY", "_TOKEN", "_SECRET")) or key in ("RESEND_API_KEY",):
        os.environ.pop(key)


def _db_available() -> bool:
    import psycopg  # noqa: PLC0415
    from sqlalchemy.engine import make_url  # noqa: PLC0415

    url = make_url(TEST_DB)
    try:
        with psycopg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            dbname=url.database,
            connect_timeout=3,
        ):
            return True
    except psycopg.OperationalError:
        return False


DB_AVAILABLE = _db_available()


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    # psycopg's async mode can't use Windows' default Proactor loop (containers are Linux).
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
    return asyncio.DefaultEventLoopPolicy()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if DB_AVAILABLE:
        return
    skip = pytest.mark.skip(reason=f"no test database at {TEST_DB}")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated() -> None:
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "app/db/migrations")
    )
    command.upgrade(cfg, "head")


@pytest.fixture
async def db(migrated: None) -> AsyncIterator[None]:
    """Clean tables before each DB test; dispose the engine after (one loop per test)."""
    from sqlalchemy import text  # noqa: PLC0415

    from app.adapters.registry import get_adapters  # noqa: PLC0415
    from app.db.models import Base  # noqa: PLC0415
    from app.db.session import get_engine, get_sessionmaker  # noqa: PLC0415
    from app.policy.country_rules import get_rules  # noqa: PLC0415

    engine = get_engine()
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    get_rules.cache_clear()
    yield
    await engine.dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_adapters.cache_clear()
