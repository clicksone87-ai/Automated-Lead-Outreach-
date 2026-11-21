"""The "Not useful" tab: leads that will never be worked, each with its reason."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.services import not_useful_leads
from app.services.not_useful_leads import NotUsefulCounts, NotUsefulPage

router = APIRouter(prefix="/api")


@router.get("/not-useful", response_model=NotUsefulPage)
async def not_useful(
    session: SessionDep,
    reason: Annotated[str | None, Query(max_length=30, pattern="^[a-z0-9_]+$")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotUsefulPage:
    return await not_useful_leads.list_leads(
        session, reason=reason, q=q, page=page, page_size=page_size
    )


@router.get("/not-useful/counts", response_model=NotUsefulCounts)
async def not_useful_counts(session: SessionDep) -> NotUsefulCounts:
    return await not_useful_leads.counts(session)
