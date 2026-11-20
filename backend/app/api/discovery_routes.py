"""Business search API: start a no-website / outdated-website search and read its results."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.opendata import CATEGORIES, ROUTES, WebsiteStatus
from app.api.deps import SessionDep, SettingsDep
from app.db.models import DiscoveryJob
from app.services import business_search, runtime_settings

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class SearchIn(BaseModel):
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    city: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    max_results: int = Field(default=50, ge=1, le=200)
    sources: list[str] | None = None


@router.get("/options")
async def options(settings: SettingsDep) -> dict[str, Any]:
    return {
        "categories": sorted(CATEGORIES),
        "routes": {k: list(v) for k, v in ROUTES.items()},
        "default_route": ["osm"],
        "excluded_countries": sorted(settings.excluded_country_set),
        "website_statuses": [s.value for s in WebsiteStatus],
        "osm_attribution": settings.osm_attribution,
    }


@router.post("/search", status_code=status.HTTP_202_ACCEPTED)
async def search(
    body: SearchIn, session: SessionDep, settings: SettingsDep, background: BackgroundTasks
) -> dict[str, Any]:
    try:
        job = await business_search.start_job(
            session, settings, country=body.country, city=body.city, category=body.category,
            max_results=body.max_results, sources=body.sources, requested_by="dashboard",
        )  # fmt: skip
    except ValueError as exc:  # includes ExcludedMarketError
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    rs = await runtime_settings.load(session)
    await session.commit()
    background.add_task(business_search.run_job, job.id, settings, industry=rs.active_industry)
    return business_search.job_out(job)


@router.get("/jobs")
async def jobs(session: SessionDep, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(DiscoveryJob).order_by(DiscoveryJob.created_at.desc()).limit(min(limit, 200))
        )
    ).scalars()
    return [business_search.job_out(j) for j in rows]


@router.get("/jobs/{job_id}")
async def job(job_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    j = await session.get(DiscoveryJob, job_id)
    if j is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job not found")
    return {**business_search.job_out(j), "results": await business_search.job_results(session, j)}
