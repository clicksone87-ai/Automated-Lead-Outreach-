"""Database backups: pg_dump → ./backups. Keeps the newest N dumps."""

from __future__ import annotations

import asyncio
import gzip
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import get_settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
KEEP = 14


async def run_backup(backup_dir: Path = BACKUP_DIR, keep: int = KEEP) -> Path:
    url = make_url(get_settings().database_url)
    await asyncio.to_thread(backup_dir.mkdir, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"leads-{stamp}.sql.gz"
    env = {**os.environ, "PGPASSWORD": url.password or ""}
    proc = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--no-owner",
        "--format=plain",
        "-h",
        url.host or "localhost",
        "-p",
        str(url.port or 5432),
        "-U",
        url.username or "postgres",
        url.database or "postgres",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {err.decode(errors='replace')[:500]}")
    size = await asyncio.to_thread(_write_and_rotate, target, out, backup_dir, keep)
    logger.info("backup written: %s (%d bytes)", target, size)
    return target


def _write_and_rotate(target: Path, dump: bytes, backup_dir: Path, keep: int) -> int:
    target.write_bytes(gzip.compress(dump))
    for old in sorted(backup_dir.glob("leads-*.sql.gz"))[:-keep]:
        old.unlink(missing_ok=True)
    return target.stat().st_size
