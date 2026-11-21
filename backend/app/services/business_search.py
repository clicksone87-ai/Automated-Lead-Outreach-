"""Business search jobs: find businesses with no or an outdated website in one city and
category, from OpenStreetMap and the free national registries (country routing in
`adapters.opendata.ROUTES`).

Excluded markets (EXCLUDED_COUNTRIES) are refused when a job starts, and every result is checked
again before anything is stored, because OpenStreetMap areas can cross borders.
"""

from __future__ import annotations

import logging
import uuid
import zlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.opendata import (
    CATEGORIES,
    CompaniesHouseSearch,
    FoundBusiness,
    FrRechercheEntreprises,
    NoBrreg,
    OpenDataError,
    Overpass,
    WebsiteStatus,
    osm_ref,
    route_for,
)
from app.config import Settings
from app.db.models import Business, Contact, DiscoveryJob, VerificationStatus
from app.db.session import session_scope
from app.services.dedup import (
    BusinessCandidate,
    create_business,
    fill_gaps,
    find_match,
    normalize_domain,
    normalize_name,
)

logger = logging.getLogger(__name__)


class ExcludedMarketError(ValueError):
    pass


# Weakest to strongest evidence about a business's website.
_STRENGTH: dict[str, int] = {
    WebsiteStatus.WEBSITE_UNCONFIRMED: 0,
    WebsiteStatus.OSM_WEBSITE_MISSING: 1,
    WebsiteStatus.WEBSITE_RECORDED: 2,
    WebsiteStatus.WEBSITE_DISCOVERED: 3,
    WebsiteStatus.WEBSITE_VERIFIED: 4,
}


def _secret(v: Any) -> str:
    return v.get_secret_value() if v is not None else ""


def available_sources(settings: Settings, country: str) -> list[str]:
    out = []
    for src in route_for(country):
        if src == "companies_house" and not _secret(settings.companies_house_api_key):
            continue
        out.append(src)
    return out


async def start_job(
    session: AsyncSession, settings: Settings, *, country: str, city: str, category: str,
    max_results: int = 50, sources: list[str] | None = None, requested_by: str | None = None,
) -> DiscoveryJob:  # fmt: skip
    code = country.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError("country must be a two-letter ISO code")
    if code in settings.excluded_country_set:
        raise ExcludedMarketError(f"{code} is an excluded market")
    if category not in CATEGORIES and "=" not in category:
        raise ValueError(
            f"unknown category {category!r}; use one of {sorted(CATEGORIES)} or key=value"
        )
    allowed = available_sources(settings, code)
    chosen = [s for s in (sources or allowed) if s in allowed]
    if not chosen:
        raise ValueError(f"no source available for {code}")
    job = DiscoveryJob(
        id=uuid.uuid4(), country=code, city=city.strip()[:200], category=category,
        sources=chosen, max_results=max(1, min(int(max_results), 200)), status="queued",
        requested_by=requested_by,
    )  # fmt: skip
    session.add(job)
    await session.flush()
    return job


async def _fetch(settings: Settings, src: str, job: DiscoveryJob) -> list[FoundBusiness]:
    if src == "osm":
        return await Overpass(settings.overpass_urls, per_minute=settings.overpass_max_requests_per_minute,
                              attribution=settings.osm_attribution,
                              contact=settings.overpass_contact).search(
            job.country, job.city, job.category, job.max_results)  # fmt: skip
    if src == "fr_recherche":
        return await FrRechercheEntreprises(
            settings.fr_recherche_entreprises_url, max_rps=settings.fr_recherche_max_rps
        ).search(job.city, job.category, job.max_results)
    if src == "no_brreg":
        return await NoBrreg(settings.no_brreg_api_url).search(
            job.city, job.category, job.max_results
        )
    if src == "companies_house":
        return await CompaniesHouseSearch(_secret(settings.companies_house_api_key)).search(
            job.city, job.category, job.max_results
        )
    raise OpenDataError(f"unknown source {src}")


async def store_one(
    session: AsyncSession, settings: Settings, f: FoundBusiness, *, industry: str | None,
    now: datetime | None = None,
) -> tuple[Business, bool] | None:  # fmt: skip
    """Upsert one result: an OpenStreetMap element by (osm_type, osm_id), anything else (and
    an element seen for the first time) through the normal duplicate matching, so OSM and
    registry records of one business end up as one record. None for excluded markets."""
    if f.country.upper() in settings.excluded_country_set:
        return None
    now = now or datetime.now(UTC)
    ref = osm_ref(f)
    domain = normalize_domain(f.website) if f.website else None
    cand = BusinessCandidate(
        name=f.name, domain=domain, registry_number=f.source_id, registry_source=f.source,
        country=f.country, industry=industry, address=f.address, phone=f.phone,
        has_website=True if f.website else None,
    )  # fmt: skip
    b: Business | None = None
    created = False
    if ref:
        b = (
            await session.execute(
                select(Business).where(Business.osm_type == ref[0], Business.osm_id == ref[1])
            )
        ).scalar_one_or_none()
        if b is not None:
            fill_gaps(b, cand)
    if b is None:
        # Decide before merging: a name-only hit on another OSM element is a different place
        # (two "Pizza Roma" in one country), and merging first would copy this element's
        # website onto it, so the new record and the merged one would share a domain.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"),
            {"k": zlib.crc32(normalize_name(cand.name).encode())},
        )
        match = await find_match(session, cand)
        if match is not None and not (ref and match[0].osm_id is not None and match[1] == "name"):
            b, created = match[0], False
            fill_gaps(b, cand)
        else:
            b, created = await create_business(session, cand), True
    if ref:
        if b.osm_id is None:
            b.osm_type, b.osm_id = ref
        b.first_seen = b.first_seen or now
        b.last_seen = now
    # Keep the strongest evidence: a registry's silence never overrides what OSM or a
    # website check said.
    if _STRENGTH[f.website_status] >= _STRENGTH.get(b.website_status or "", -1):
        b.website_status = f.website_status
    if f.website_status == WebsiteStatus.OSM_WEBSITE_MISSING and not b.domain:
        b.has_website = False
    email = (f.email or "").strip().lower()
    if email and "@" in email and f.url:
        exists = (await session.execute(select(Contact.id).where(
            Contact.business_id == b.id, Contact.value == email))).first()  # fmt: skip
        if not exists:
            session.add(Contact(business_id=b.id, type="email", value=email, source_url=f.url,
                                verification_status=VerificationStatus.UNVERIFIED))  # fmt: skip
    await session.flush()
    return b, created


async def store(
    session: AsyncSession, settings: Settings, found: list[FoundBusiness], *, industry: str | None
) -> tuple[list[uuid.UUID], dict[str, int]]:
    """Upsert the businesses (one record per business across sources); excluded markets never
    reach the database."""
    counts: dict[str, int] = {"found": len(found), "excluded": 0, "created": 0, "matched": 0}
    ids: list[uuid.UUID] = []
    for f in found:
        stored = await store_one(session, settings, f, industry=industry)
        if stored is None:
            counts["excluded"] += 1
            continue
        b, created = stored
        counts["created" if created else "matched"] += 1
        counts[f.website_status.value] = counts.get(f.website_status.value, 0) + 1
        ids.append(b.id)
    await session.flush()
    return ids, counts


async def run_job(job_id: uuid.UUID, settings: Settings, *, industry: str | None = None) -> None:
    """Run every source of the job; one failing source never stops the others."""
    async with session_scope() as s:
        job = await s.get(DiscoveryJob, job_id)
        if job is None or job.status not in ("queued", "failed"):
            return
        job.status, job.started_at = "running", datetime.now(UTC)
    found: list[FoundBusiness] = []
    errors: list[str] = []
    per_source: dict[str, int] = {}
    for src in list(job.sources):
        try:
            got = await _fetch(settings, src, job)
        except (OpenDataError, ValueError, OSError) as exc:
            errors.append(f"{src}: {exc}"[:300])
            continue
        except Exception as exc:
            logger.exception("business search source %s failed", src)
            errors.append(f"{src}: {type(exc).__name__}: {exc}"[:300])
            continue
        per_source[src] = len(got)
        found += got
    async with session_scope() as s:
        job = await s.get(DiscoveryJob, job_id)
        assert job is not None
        ids, counts = await store(s, settings, found, industry=industry)
        job.business_ids = [str(i) for i in dict.fromkeys(ids)]
        job.counts = {**counts, "by_source": per_source}
        job.errors = errors
        job.attribution = settings.osm_attribution if per_source.get("osm") else None
        job.status = "failed" if errors and not found else "done"
        job.finished_at = datetime.now(UTC)


def job_out(job: DiscoveryJob) -> dict[str, Any]:
    return {
        "id": str(job.id), "country": job.country, "city": job.city, "category": job.category,
        "sources": job.sources, "max_results": job.max_results, "status": job.status,
        "counts": job.counts, "errors": job.errors, "attribution": job.attribution,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }  # fmt: skip


async def job_results(session: AsyncSession, job: DiscoveryJob) -> list[dict[str, Any]]:
    ids = [uuid.UUID(i) for i in job.business_ids or []]
    if not ids:
        return []
    rows = (await session.execute(select(Business).where(Business.id.in_(ids)))).scalars().all()
    order = {i: n for n, i in enumerate(ids)}
    return [
        {
            "id": str(b.id),
            "name": b.name,
            "country": b.country,
            "address": b.address,
            "phone": b.phone,
            "domain": b.domain,
            "website_status": b.website_status,
            "registry_source": b.registry_source,
            "registry_number": b.registry_number,
            "is_osm": b.registry_source == "osm",
        }
        for b in sorted(rows, key=lambda b: order.get(b.id, 0))
    ]
