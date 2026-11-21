"""Command line: python -m app.cli {seed|run-cycle|run-source KEY|run-lead ID|backup|check-providers|account-status|purge-demo|clean-leads [--dry-run]|map-not-useful|geonames-refresh|osm-sweep [N]}."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select

from app.adapters.registry import get_adapters
from app.agents.discovery.runner import run_source
from app.config import get_settings, log_missing_integrations
from app.db.models import Lead, LeadStatus, Source
from app.db.session import session_scope
from app.logging_setup import configure_logging
from app.orchestrator import pipeline
from app.services import backup, lead_cleanup, not_useful_leads, provider_check, seed


async def _seed() -> dict[str, object]:
    async with session_scope() as session:
        return dict(await seed.seed(session, get_settings()))


IN_PROGRESS = (
    LeadStatus.DISCOVERED,
    LeadStatus.QUALIFIED,
    LeadStatus.CONTACT_FOUND,
    LeadStatus.AUDITED,
    LeadStatus.BRANDED,
    LeadStatus.BUILT,
    LeadStatus.QA_PASSED,
)


async def _run_pending() -> dict[str, object]:
    """Run/resume the pipeline for every lead that is mid-pipeline (no discovery)."""
    adapters = get_adapters()
    async with session_scope() as session:
        waiting = (
            (
                await session.execute(
                    select(Lead.id)
                    .where(Lead.status.in_([str(s) for s in IN_PROGRESS]))
                    .order_by(Lead.created_at)
                )
            )
            .scalars()
            .all()
        )
    out = []
    for lead_id in waiting:
        out.append(await _safe_run_lead(lead_id, adapters))
    await adapters.fetcher.aclose()
    return {"pipelines": out}


async def _safe_run_lead(lead_id: Any, adapters: Any) -> dict[str, object]:
    """One lead's pipeline; a failure is recorded (agent_runs) and the batch continues."""
    try:
        state = await pipeline.run_lead(lead_id, adapters)
    except Exception as exc:
        return {"lead_id": str(lead_id), "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {
        "lead_id": str(lead_id),
        "completed": state.get("completed"),
        "stopped_at": state.get("stopped_at"),
        "reason": state.get("reason"),
    }


async def _run_cycle() -> dict[str, object]:
    """One full cycle, synchronously: every enabled source once, then each new lead's pipeline."""
    settings, adapters = get_settings(), get_adapters()
    async with session_scope() as session:
        keys = [
            k
            for (k,) in (
                await session.execute(select(Source.key).where(Source.enabled.is_(True)))
            ).all()
        ]
    sources: dict[str, dict[str, object]] = {}
    pipelines: list[dict[str, object]] = []
    new_leads = []
    for key in keys:
        async with session_scope() as session:
            result = await run_source(session, adapters, settings, key, force=True)
        sources[key] = {
            "status": result.status,
            "items": result.items_seen,
            "leads": len(result.lead_ids or []),
        }
        new_leads.extend(result.lead_ids or [])
    # Resume every lead that is mid-pipeline (e.g. a step crashed or a key was missing);
    # completed steps are skipped by the orchestrator, so this never repeats work.
    async with session_scope() as session:
        waiting = (
            (
                await session.execute(
                    select(Lead.id)
                    .where(Lead.status.in_([str(s) for s in IN_PROGRESS]))
                    .order_by(Lead.created_at)
                )
            )
            .scalars()
            .all()
        )
    new_leads = list(dict.fromkeys([*new_leads, *waiting]))
    for lead_id in new_leads:
        pipelines.append(await _safe_run_lead(lead_id, adapters))
    await adapters.fetcher.aclose()
    return {"sources": sources, "pipelines": pipelines}


async def _backup() -> dict[str, object]:
    return {"backup": str(await backup.run_backup())}


async def _check_providers() -> dict[str, object]:
    settings, adapters = get_settings(), get_adapters()
    rows = await provider_check.check_all(settings, adapters)
    await adapters.fetcher.aclose()
    sys.stderr.write(provider_check.as_table(rows) + chr(10))
    return {"providers": provider_check.as_dicts(rows)}


async def _account_status() -> dict[str, object]:
    """One live call per provider, concurrently; what each provider says is left."""
    from app.services import account_status  # noqa: PLC0415

    rows = await account_status.check_all(get_settings())
    sys.stderr.write(account_status.render(rows) + chr(10))
    return {
        "providers": [
            r.__dict__
            if hasattr(r, "__dict__")
            else {
                "provider": r.provider,
                "key_set": r.key_set,
                "result": r.result,
                "left": r.left,
                "notes": r.notes,
            }
            for r in rows
        ]
    }


async def _run_one_source(key: str) -> dict[str, object]:
    """Run a single discovery source once (forced) and report the leads it created."""
    settings, adapters = get_settings(), get_adapters()
    async with session_scope() as session:
        result = await run_source(session, adapters, settings, key, force=True)
    await adapters.fetcher.aclose()
    return {
        "source": key,
        "status": result.status,
        "items": result.items_seen,
        "lead_ids": [str(x) for x in result.lead_ids or []],
        "error": result.error,
    }


async def _run_one_lead(lead_id: str) -> dict[str, object]:
    """Run (or resume) the per-lead pipeline for one lead."""
    import uuid  # noqa: PLC0415

    adapters = get_adapters()
    state = await pipeline.run_lead(uuid.UUID(lead_id), adapters)
    await adapters.fetcher.aclose()
    return {"lead_id": lead_id, **dict(state)}


async def _purge_demo() -> dict[str, object]:
    async with session_scope() as session:
        return dict(await seed.purge_demo_data(session))


async def _geonames_refresh() -> dict[str, object]:
    """Download and load the GeoNames city list now, whatever its age."""
    from app.services import geonames  # noqa: PLC0415

    return dict(await geonames.refresh(get_settings(), force=True))


async def _osm_sweep(max_cities: int | None) -> dict[str, object]:
    """Sweep up to `max_cities` of today's pending cities now (all due ones when omitted)."""
    from app.services import osm_sweep  # noqa: PLC0415

    adapters = get_adapters()
    try:
        return dict(await osm_sweep.run_job(get_settings(), adapters, max_cities=max_cities))
    finally:
        await adapters.fetcher.aclose()


async def _clean_leads(dry_run: bool) -> dict[str, object]:
    """Move old or undated social posts and freelance-platform leads to "Not useful"."""
    async with session_scope() as session:
        result = await lead_cleanup.clean_leads(session, get_settings(), dry_run=dry_run)
    return {"dry_run": dry_run, **result.as_dict()}


async def _map_not_useful() -> dict[str, object]:
    """Move leads stopped under the old never-useful statuses into "Not useful" (idempotent)."""
    async with session_scope() as session:
        moved = await not_useful_leads.map_existing(session, get_settings().excluded_country_set)
    return {"moved": moved, "total": sum(moved.values())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    parser.add_argument(
        "command",
        choices=[
            "seed",
            "run-cycle",
            "run-source",
            "run-lead",
            "run-pending",
            "backup",
            "check-providers",
            "account-status",
            "purge-demo",
            "geonames-refresh",
            "osm-sweep",
            "clean-leads",
            "map-not-useful",
        ],
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="source key (run-source), lead id (run-lead) or number of cities (osm-sweep)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="clean-leads: count the leads, change nothing"
    )
    args = parser.parse_args(argv)
    if args.command in ("run-source", "run-lead") and not args.target:
        parser.error(f"{args.command} needs a target")
    configure_logging()
    log_missing_integrations(get_settings())
    if args.command == "run-source":
        runner: Any = lambda: _run_one_source(args.target)  # noqa: E731
    elif args.command == "run-lead":
        runner = lambda: _run_one_lead(args.target)  # noqa: E731
    elif args.command == "osm-sweep":
        runner = lambda: _osm_sweep(int(args.target) if args.target else None)  # noqa: E731
    elif args.command == "clean-leads":
        runner = lambda: _clean_leads(args.dry_run)  # noqa: E731
    else:
        runner = {
            "seed": _seed,
            "run-cycle": _run_cycle,
            "run-pending": _run_pending,
            "backup": _backup,
            "check-providers": _check_providers,
            "account-status": _account_status,
            "purge-demo": _purge_demo,
            "geonames-refresh": _geonames_refresh,
            "map-not-useful": _map_not_useful,
        }[args.command]
    if sys.platform == "win32":
        # psycopg async needs the selector loop on Windows (containers are Linux); the
        # audit browser runs Playwright on its own Proactor loop in a thread when needed.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined,unused-ignore]
    result = asyncio.run(runner())
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
