"""Dashboard API routes (thin: validation + delegation to services)."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from app.agents.discovery.sources import SOURCES
from app.api.deps import AdaptersDep, SessionDep, SettingsDep
from app.config import integration_status
from app.db.session import session_scope
from app.orchestrator.queue import enqueue_pipeline, enqueue_source
from app.schemas import (
    ActivityOut,
    AuditDetail,
    AuditPage,
    CostsOut,
    FreeTierOut,
    GalleryItem,
    HealthOut,
    IntegrationOut,
    LeadDetail,
    LeadPage,
    OverviewOut,
    SearchHit,
    SettingsOut,
    SettingsPatch,
    SourceOut,
    SourcePatch,
    TimingsOut,
)
from app.services import admin, manual_runs, queries, runtime_settings, timings

router = APIRouter(prefix="/api")
public = APIRouter(prefix="/api")


@public.get("/health", response_model=HealthOut)
async def health(session: SessionDep, settings: SettingsDep) -> HealthOut:
    db_ok = redis_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    try:
        client = Redis.from_url(settings.redis_url, socket_timeout=2)
        redis_ok = bool(await client.ping())
        await client.aclose()
    except Exception:
        redis_ok = False
    missing = sum(1 for s in integration_status(settings).values() if s.state == "needs_api_key")
    return HealthOut(
        status="ok" if db_ok and redis_ok else "degraded",
        database=db_ok,
        redis=redis_ok,
        app_mode=str(settings.app_mode),
        app_tier=str(settings.app_tier),
        testing_mode=settings.testing_tier,
        demo_mode=settings.mocks_allowed,
        integrations_missing=missing,
    )


@router.get("/overview", response_model=OverviewOut)
async def overview(session: SessionDep) -> OverviewOut:
    return await queries.overview(session)


@router.get("/timings", response_model=TimingsOut)
async def lead_timings(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> TimingsOut:
    return await timings.timings(session, days=days)


@router.get("/leads", response_model=LeadPage)
async def list_leads(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    sort: str = "-created_at",
    status_: Annotated[list[str] | None, Query(alias="status")] = None,
    stage: str | None = None,
    source: Annotated[list[str] | None, Query()] = None,
    country: Annotated[list[str] | None, Query()] = None,
    language: Annotated[list[str] | None, Query()] = None,
    service: Annotated[list[str] | None, Query()] = None,
    trigger: Annotated[list[str] | None, Query()] = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    max_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    unscored: Annotated[str | None, Query(pattern="^(include|only)$")] = None,
    q: str | None = None,
) -> LeadPage:
    return await queries.list_leads(
        session,
        page=page,
        page_size=page_size,
        sort=sort,
        status=status_,
        stage=stage,
        source=source,
        country=country,
        language=language,
        service=service,
        trigger=trigger,
        min_score=min_score,
        max_score=max_score,
        unscored=unscored,
        q=q,
    )


@router.get("/leads/{lead_id}", response_model=LeadDetail)
async def lead_detail(lead_id: uuid.UUID, session: SessionDep) -> LeadDetail:
    detail = await queries.lead_detail(session, lead_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found")
    return detail


@router.post("/leads/{lead_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
async def rerun_lead(lead_id: uuid.UUID, session: SessionDep) -> dict[str, bool]:
    try:
        await admin.mark_lead_for_rerun(session, lead_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead not found") from exc
    return {"queued": await enqueue_pipeline(lead_id)}


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    session: SessionDep, settings: SettingsDep, adapters: AdaptersDep
) -> list[SourceOut]:
    return await queries.sources(session, settings, adapters)


@router.patch("/sources/{key}", response_model=list[SourceOut])
async def patch_source(
    key: str, patch: SourcePatch, session: SessionDep, settings: SettingsDep, adapters: AdaptersDep
) -> list[SourceOut]:
    try:
        await admin.patch_source(session, settings, key, patch)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown source") from exc
    except admin.InvalidInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await queries.sources(session, settings, adapters)


@router.post("/sources/{key}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_source_now(
    key: str, session: SessionDep, settings: SettingsDep, adapters: AdaptersDep
) -> dict[str, bool]:
    from datetime import UTC, datetime  # noqa: PLC0415

    if key not in SOURCES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown source")
    verdict = await manual_runs.authorize(session, settings, adapters, key)
    if not verdict.allowed:
        await session.commit()  # keep the refusal in the decision log
        raise HTTPException(verdict.status_code, verdict.reason)
    return {
        "queued": await enqueue_source(key, datetime.now(UTC).strftime("%Y%m%d%H%M%S"), force=True)
    }


@router.get("/settings", response_model=SettingsOut)
async def get_settings_view(
    session: SessionDep, settings: SettingsDep, adapters: AdaptersDep
) -> SettingsOut:
    return await queries.settings_view(session, settings, adapters)


@router.patch("/settings", response_model=SettingsOut)
async def patch_settings(
    body: SettingsPatch, session: SessionDep, settings: SettingsDep, adapters: AdaptersDep
) -> SettingsOut:
    try:
        await runtime_settings.update(
            session, body.changes, changed_by="dashboard", reason=body.reason
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return await queries.settings_view(session, settings, adapters)


@router.get("/integrations", response_model=list[IntegrationOut])
async def integrations(settings: SettingsDep, adapters: AdaptersDep) -> list[IntegrationOut]:
    return queries.integrations_out(settings, adapters)


@router.get("/gallery", response_model=list[GalleryItem])
async def gallery(session: SessionDep) -> list[GalleryItem]:
    return await queries.gallery(session)


@router.get("/free-tier", response_model=FreeTierOut)
async def free_tier(
    session: SessionDep, settings: SettingsDep, adapters: AdaptersDep
) -> FreeTierOut:
    return await queries.free_tier(session, settings, adapters)


@router.get("/costs", response_model=CostsOut)
async def costs(session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 30) -> CostsOut:
    return await queries.costs(session, days)


@router.get("/activity", response_model=list[ActivityOut])
async def activity(
    session: SessionDep, after_id: int = 0, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> list[ActivityOut]:
    return await queries.activity_since(session, after_id, limit)


@router.get("/events")
async def events(request: Request, after_id: int = 0) -> EventSourceResponse:
    """Server-sent events: new activity rows, polled from the DB every 2 seconds."""

    async def stream() -> AsyncIterator[dict[str, str]]:
        last = after_id
        if not last:
            async with session_scope() as session:
                recent = await queries.activity_since(session, 0, 1)
                last = recent[-1].id if recent else 0
        while not await request.is_disconnected():
            async with session_scope() as session:
                rows = await queries.activity_since(session, last, 200)
            for row in rows:
                last = row.id
                yield {
                    "event": "activity",
                    "id": str(row.id),
                    "data": json.dumps(row.model_dump(mode="json")),
                }
            await asyncio.sleep(2)

    return EventSourceResponse(stream(), ping=15)


@router.get("/audits", response_model=AuditPage)
async def audits(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 24,
) -> AuditPage:
    return await queries.list_audits(session, page=page, page_size=page_size)


@router.get("/audits/{lead_id}", response_model=AuditDetail)
async def audit_detail(lead_id: uuid.UUID, session: SessionDep) -> AuditDetail:
    detail = await queries.audit_view(session, lead_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audit not found")
    return detail


@router.get("/search", response_model=list[SearchHit])
async def search(session: SessionDep, q: str = "") -> list[SearchHit]:
    return await queries.search(session, q)


@router.get("/files/{path:path}")
async def files(path: str, adapters: AdaptersDep) -> FileResponse:
    """Serve stored artefacts (screenshots, PDFs, logos) to the dashboard."""
    try:
        absolute = adapters.storage.absolute(path)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid path") from exc
    if not absolute.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    media_type = mimetypes.guess_type(absolute.name)[0] or "application/octet-stream"
    # Stored web content is untrusted: never let the browser execute it as a page.
    headers = {
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
        "X-Content-Type-Options": "nosniff",
    }
    if media_type in ("text/html", "image/svg+xml", "application/xhtml+xml"):
        headers["Content-Disposition"] = "attachment"
    return FileResponse(absolute, media_type=media_type, headers=headers)
