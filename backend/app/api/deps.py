"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import Adapters, get_adapters
from app.config import Settings, get_settings
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
AdaptersDep = Annotated[Adapters, Depends(get_adapters)]


async def require_token(
    authorization: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> None:
    """Optional bearer auth (API_TOKEN). Off when unset, which is fine on 127.0.0.1."""
    if settings.api_token is None or not settings.api_token.get_secret_value():
        return
    expected = f"Bearer {settings.api_token.get_secret_value()}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token")
