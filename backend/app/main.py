"""FastAPI application: dashboard API, orchestrator endpoints, webhooks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import discovery_routes, ops_routes, outreach_routes
from app.api.deps import require_token
from app.api.routes import public, router
from app.config import get_settings, log_missing_integrations
from app.logging_setup import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log_missing_integrations(get_settings())
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Leads System API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.dashboard_origin],
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(public)
    app.include_router(router, dependencies=[Depends(require_token)])
    app.include_router(outreach_routes.public)
    app.include_router(outreach_routes.router, dependencies=[Depends(require_token)])
    app.include_router(ops_routes.router, dependencies=[Depends(require_token)])
    app.include_router(discovery_routes.router, dependencies=[Depends(require_token)])
    return app


app = create_app()
