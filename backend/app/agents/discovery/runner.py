"""Runs one discovery source: fetch → dedup → locale → evidence → lead → enqueue pipeline."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterUnavailableError
from app.adapters.registry import Adapters
from app.agents.discovery.base import DiscoverySource, RawLead, SourceContext, detect_services
from app.agents.discovery.sources import SOURCES
from app.config import Settings, integration_status
from app.db.models import Lead, LeadStatus, RunStatus, Source, SourceRun
from app.orchestrator.budget import BudgetExceededError
from app.services import activity, evidence, runtime_settings
from app.services.dedup import BusinessCandidate, resolve_business
from app.services.locale import guess_locale

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceRunResult:
    source: str
    status: str
    items_seen: int = 0
    lead_ids: list[uuid.UUID] | None = None
    error: str | None = None


async def ensure_source_rows(session: AsyncSession, settings: Settings) -> None:
    """Create a `sources` row for every known source (idempotent)."""
    for src in SOURCES.values():
        enabled = src.default_enabled or (src.key == "mock_demo" and settings.demo_mode)
        stmt = (
            insert(Source)
            .values(
                key=src.key,
                enabled=enabled,
                schedule_minutes=src.default_schedule_minutes,
                config=dict(src.default_config),
            )
            .on_conflict_do_nothing(index_elements=[Source.key])
        )
        await session.execute(stmt)
    await session.flush()


async def run_source(  # noqa: PLR0915 - linear bookkeeping, clearer in one place
    session: AsyncSession,
    adapters: Adapters,
    settings: Settings,
    key: str,
    *,
    force: bool = False,
) -> SourceRunResult:
    source = SOURCES.get(key)
    if source is None:
        return SourceRunResult(key, "unknown_source")
    row = await session.get(Source, key, with_for_update=True)
    if row is None:
        await ensure_source_rows(session, settings)
        row = await session.get(Source, key, with_for_update=True)
    assert row is not None
    if not row.enabled and not force:
        return SourceRunResult(key, "disabled")
    if key == "mock_demo" and not settings.demo_mode:
        return SourceRunResult(key, "demo_mode_off")

    status = integration_status(settings).get(source.integration)
    uses_mock = adapters.uses_mock(source.integration)
    if status is not None and not status.configured and not uses_mock:
        row.last_status = "needs_api_key"
        row.last_error = f"set {', '.join(status.env_vars)}"
        row.last_run_at = datetime.now(UTC)
        return SourceRunResult(key, "needs_api_key")

    run = SourceRun(source_key=key, status=RunStatus.RUNNING)
    session.add(run)
    await session.flush()
    rs = await runtime_settings.load(session)
    cfg = dict(row.config or {})
    state = dict(cfg.pop("_state", {}))
    ctx = SourceContext(
        session=session, adapters=adapters, settings=settings, runtime=rs, config=cfg, state=state
    )
    lead_ids: list[uuid.UUID] = []
    items: list[RawLead] = []
    try:
        async with session.begin_nested():
            items = await source.fetch(ctx)
            for item in items:
                lead_id = await ingest(session, adapters, source, item, uses_mock=uses_mock)
                if lead_id:
                    lead_ids.append(lead_id)
    except (AdapterUnavailableError, BudgetExceededError) as exc:
        run.status, run.error = RunStatus.SKIPPED, str(exc)
        row.last_status = (
            "needs_api_key" if isinstance(exc, AdapterUnavailableError) else "paused_budget"
        )
        row.last_error = str(exc)
    except Exception as exc:
        logger.exception("source %s failed", key)
        run.status, run.error = RunStatus.FAILED, f"{type(exc).__name__}: {exc}"[:2000]
        row.last_status, row.last_error = "error", run.error
    else:
        run.status = RunStatus.SUCCEEDED
        row.last_status, row.last_error = "ok", None
        row.leads_found_total = (row.leads_found_total or 0) + len(lead_ids)
        row.config = {**cfg, "_state": ctx.state}

    now = datetime.now(UTC)
    run.finished_at = now
    run.items_seen = len(items)
    run.leads_created = len(lead_ids)
    row.last_run_at = now
    await activity.emit(
        session,
        "source_run",
        f"{source.label}: {len(lead_ids)} new lead(s) from {len(items)} item(s)"
        + (f" — {row.last_status}" if row.last_status != "ok" else ""),
        agent="discovery",
        data={"source": key, "status": row.last_status},
    )
    await session.flush()
    return SourceRunResult(key, str(run.status), len(items), lead_ids, run.error)


async def ingest(
    session: AsyncSession,
    adapters: Adapters,
    source: DiscoverySource,
    item: RawLead,
    *,
    uses_mock: bool,
) -> uuid.UUID | None:
    """Create a lead for a raw item. Idempotent on (source, source_item_id)."""
    exists = (
        await session.execute(
            select(Lead.id).where(
                Lead.source == source.key, Lead.source_item_id == item.source_item_id
            )
        )
    ).scalar_one_or_none()
    if exists:
        return None

    text = "\n\n".join(filter(None, [item.title, item.text]))
    biz = item.business
    locale = guess_locale(
        text=text,
        url=(biz.website if biz else None) or item.source_url,
        phone=biz.phone if biz else None,
        address=biz.address if biz else None,
        country_hint=biz.country_hint if biz else None,
    )
    business_id = None
    if biz and biz.name.strip():
        match = await resolve_business(
            session,
            BusinessCandidate(
                name=biz.name,
                domain=biz.website,
                registry_number=biz.registry_number,
                registry_source=biz.registry_source,
                country=locale.country,
                language=locale.language,
                timezone=locale.timezone,
                industry=biz.industry,
                address=biz.address,
                phone=biz.phone,
                email=biz.email,
                has_website=biz.has_website,
            ),
        )
        business_id = match.business.id

    pipeline_state: dict[str, Any] = {
        "locale_signal": locale.country_signal,
        "language_confidence": round(locale.language_confidence, 3),
    }
    if uses_mock or source.key.startswith("mock"):
        pipeline_state["mock_adapters_used"] = sorted({source.integration, "discovery"})

    lead = Lead(
        business_id=business_id,
        source=source.key,
        source_url=item.source_url,
        source_item_id=item.source_item_id,
        trigger_type=item.trigger_type,
        title=(item.title or "")[:1000] or None,
        raw_text=item.text,
        service_needed=detect_services(text),
        status=LeadStatus.DISCOVERED,
        country=locale.country,
        language=item.language_hint or locale.language,
        timezone=locale.timezone,
        pipeline_state=pipeline_state,
    )
    session.add(lead)
    await session.flush()
    await evidence.capture(
        session,
        adapters.storage,
        kind=item.evidence_kind,
        lead_id=lead.id,
        business_id=business_id,
        url=item.source_url,
        text=text or None,
        file_ext="txt",
        data={
            **item.evidence_data,
            "author": item.author,
            "posted_at": item.posted_at.isoformat() if item.posted_at else None,
        },
    )
    await activity.emit(
        session,
        "lead_discovered",
        f"New lead from {source.label}: {(item.title or text)[:120]}",
        lead_id=lead.id,
        agent="discovery",
        data={"trigger": str(item.trigger_type)},
    )
    return lead.id
