"""Command line: python -m app.cli {seed|run-cycle|backup}."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from app.adapters.registry import get_adapters
from app.agents.discovery.runner import run_source
from app.config import get_settings, log_missing_integrations
from app.db.models import Lead, LeadStatus, Source
from app.db.session import session_scope
from app.logging_setup import configure_logging
from app.orchestrator import pipeline
from app.services import backup, seed


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
        state = await pipeline.run_lead(lead_id, adapters)
        pipelines.append(
            {
                "lead_id": str(lead_id),
                "completed": state.get("completed"),
                "stopped_at": state.get("stopped_at"),
                "reason": state.get("reason"),
            }
        )
    await adapters.fetcher.aclose()
    return {"sources": sources, "pipelines": pipelines}


async def _backup() -> dict[str, object]:
    return {"backup": str(await backup.run_backup())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    parser.add_argument("command", choices=["seed", "run-cycle", "backup"])
    args = parser.parse_args(argv)
    configure_logging()
    log_missing_integrations(get_settings())
    runner = {"seed": _seed, "run-cycle": _run_cycle, "backup": _backup}[args.command]
    result = asyncio.run(runner())
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
