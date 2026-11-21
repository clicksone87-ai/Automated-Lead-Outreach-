"""World cities from GeoNames (cities15000: every place with 15,000+ inhabitants), the city list
of the weekly OpenStreetMap sweep. Data: GeoNames (geonames.org), CC BY 4.0, so the attribution
is shown wherever city data appears.

The zip (about 3.4 MB) is downloaded to DATA_DIR/geonames, read line by line straight from the
archive and loaded into `geonames_cities`; it is refreshed every GEONAMES_REFRESH_DAYS.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.opendata import bot_user_agent
from app.config import Settings
from app.db.models import GeoCity
from app.db.session import session_scope

logger = logging.getLogger(__name__)

# Sections of a city (PPLX: boroughs, districts) and places that no longer exist are not
# cities of their own.
SKIP_FEATURES = frozenset({"PPLX", "PPLH", "PPLQ", "PPLW", "PPLCH"})
MAX_DOWNLOAD_BYTES = 50_000_000  # the file is ~3.4 MB; anything far bigger is not it
_CHUNK = 2000


class GeoNamesError(Exception):
    pass


@dataclass(slots=True)
class CityRow:
    geonameid: int
    name: str
    ascii_name: str
    country: str
    lat: float
    lon: float
    population: int
    feature_code: str
    timezone: str | None


def parse_lines(lines: Iterable[str]) -> Iterator[CityRow]:
    """Rows of the GeoNames dump format (19 tab-separated columns); malformed lines and
    non-city features are skipped."""
    for line in lines:
        cols = line.rstrip("\r\n").split("\t")
        if len(cols) < 18 or cols[6] != "P" or cols[7] in SKIP_FEATURES:
            continue
        country = cols[8].strip().upper()
        if len(country) != 2 or not country.isalpha():
            continue
        try:
            row = CityRow(
                geonameid=int(cols[0]),
                name=cols[1].strip()[:200],
                ascii_name=(cols[2].strip() or cols[1].strip())[:200],
                country=country,
                lat=float(cols[4]),
                lon=float(cols[5]),
                population=int(cols[14] or 0),
                feature_code=cols[7][:10],
                timezone=cols[17].strip()[:64] or None,
            )
        except ValueError:
            continue
        if row.name and row.population > 0:
            yield row


def iter_zip(path: Path) -> Iterator[CityRow]:
    """Stream the rows of the .txt member without unpacking the archive."""
    with zipfile.ZipFile(path) as zf:
        member = next((n for n in zf.namelist() if n.endswith(".txt")), None)
        if member is None:
            raise GeoNamesError(f"{path.name} holds no .txt file")
        with zf.open(member) as raw:
            yield from parse_lines(io.TextIOWrapper(raw, encoding="utf-8", newline=""))


def zip_path(settings: Settings) -> Path:
    name = settings.geonames_cities_url.rstrip("/").rsplit("/", 1)[-1] or "cities15000.zip"
    return settings.data_dir / "geonames" / name


async def download(settings: Settings, client: httpx.AsyncClient | None = None) -> Path:
    """Download the zip next to the other data files (written to a temp name, then renamed, so
    a broken download never replaces a good file)."""
    target = zip_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    own = client is None
    c = client or httpx.AsyncClient(
        timeout=httpx.Timeout(120, connect=15),
        headers={"User-Agent": bot_user_agent(settings.overpass_contact)},
        follow_redirects=True,
    )
    try:
        async with c.stream("GET", settings.geonames_cities_url) as resp:
            if resp.status_code != 200:
                raise GeoNamesError(f"GeoNames download HTTP {resp.status_code}")
            size = 0
            with part.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise GeoNamesError("GeoNames download larger than expected")
                    fh.write(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        if own:
            await c.aclose()
    part.replace(target)
    return target


async def load(session: AsyncSession, path: Path, *, now: datetime | None = None) -> int:
    """Replace the table with the rows of the zip, in one transaction."""
    now = now or datetime.now(UTC)
    rows = iter_zip(path)
    first = next(rows, None)
    if first is None:  # never wipe a good table with an empty file
        raise GeoNamesError(f"{path.name} has no city rows")
    await session.execute(delete(GeoCity))
    total, batch = 0, [first]
    for row in rows:
        batch.append(row)
        if len(batch) >= _CHUNK:
            total += await _insert(session, batch, now)
            batch = []
    if batch:
        total += await _insert(session, batch, now)
    return total


async def _insert(session: AsyncSession, batch: list[CityRow], now: datetime) -> int:
    await session.execute(insert(GeoCity), [{**asdict(r), "refreshed_at": now} for r in batch])
    return len(batch)


async def last_refresh(session: AsyncSession) -> datetime | None:
    return (await session.execute(select(func.max(GeoCity.refreshed_at)))).scalar_one_or_none()


async def refresh(
    settings: Settings,
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Download and load the cities when the table is empty or older than the refresh
    interval."""
    now = now or datetime.now(UTC)
    async with session_scope() as s:
        last = await last_refresh(s)
    if (
        not force
        and last is not None
        and now - last < timedelta(days=settings.geonames_refresh_days)
    ):
        return {"status": "fresh", "refreshed_at": last.isoformat()}
    path = await download(settings, client)
    async with session_scope() as s:
        count = await load(s, path, now=now)
    logger.info("geonames: %d cities loaded from %s", count, path.name)
    return {"status": "refreshed", "cities": count, "bytes": path.stat().st_size}


# ── rotation ───────────────────────────────────────────────────────────────


async def rotation_countries(session: AsyncSession, settings: Settings) -> list[str]:
    """Every country with at least one city, minus the excluded markets, by ISO code."""
    rows = (await session.execute(select(GeoCity.country).distinct())).scalars()
    return sorted(c for c in rows if c not in settings.excluded_country_set)


def countries_for_day(countries: Iterable[str], day: date) -> list[str]:
    """The countries swept on `day`: position in the ISO-sorted list modulo 7 equals the
    weekday (Monday 0), so every country comes up once a week."""
    wd = day.weekday()
    return [c for i, c in enumerate(sorted(countries)) if i % 7 == wd]


def weekday_of(countries: Iterable[str], country: str) -> int | None:
    ordered = sorted(countries)
    return ordered.index(country) % 7 if country in ordered else None


async def top_cities(
    session: AsyncSession, countries: list[str], per_country: int
) -> list[GeoCity]:
    """The `per_country` most populous cities of each country."""
    if not countries:
        return []
    rank = (
        func.row_number()
        .over(
            partition_by=GeoCity.country,
            order_by=(GeoCity.population.desc(), GeoCity.geonameid.asc()),
        )
        .label("rank")
    )
    ranked = select(GeoCity.geonameid, rank).where(GeoCity.country.in_(countries)).subquery()
    stmt = (
        select(GeoCity)
        .join(ranked, ranked.c.geonameid == GeoCity.geonameid)
        .where(ranked.c.rank <= per_country)
        .order_by(GeoCity.country, GeoCity.population.desc())
    )
    return list((await session.execute(stmt)).scalars())
