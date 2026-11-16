"""Apify adapter — licensed third-party provider for PUBLIC social posts (X, Instagram,
Facebook public pages/groups, Reddit).

Every run is capped twice: Apify enforces `maxItems` + `maxTotalChargeUsd` server-side, and
the estimated cost is reserved against the per-source share of APIFY_MONTHLY_CAP_USD before
the run starts (orchestrator.quota), then settled with the real charge afterwards.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator import quota

logger = logging.getLogger(__name__)

API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT", "TIMED_OUT"}


class ApifyRunError(Exception):
    """The actor run failed, timed out or returned nothing usable."""


@dataclass(slots=True)
class ApifyRun:
    run_id: str
    status: str
    items: list[dict[str, Any]]
    cost_usd: float


def _run_cost(run: dict[str, Any]) -> float:
    """Actual charge: pay-per-event charges (FREE tier prices) or platform usage, whichever
    is reported (the larger of the two when both are)."""
    usage = float(run.get("usageTotalUsd") or 0.0)
    ppe = 0.0
    counts = run.get("chargedEventCounts") or {}
    events = ((run.get("pricingInfo") or {}).get("pricingPerEvent") or {}).get(
        "actorChargeEvents"
    ) or {}
    for event, n in counts.items():
        spec = events.get(event) or {}
        price = spec.get("eventPriceUsd")
        if price is None:
            price = ((spec.get("eventTieredPricingUsd") or {}).get("FREE") or {}).get(
                "tieredEventPriceUsd", 0.0
            )
        ppe += float(n or 0) * float(price or 0.0)
    return round(max(usage, ppe), 5)


class ApifyClient:
    name: ClassVar[str] = "apify"
    is_mock: ClassVar[bool] = False

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient | None = None,
        *,
        poll_s: float = 5.0,
        max_wait_s: float = 300.0,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15))
        self._poll = poll_s
        self._max_wait = max_wait_s
        self._live: tuple[float, float | None] | None = None

    async def run_actor(
        self,
        session: AsyncSession,
        actor: str,
        actor_input: dict[str, Any],
        *,
        source: str,
        max_items: int,
        max_charge_usd: float,
    ) -> ApifyRun:
        """Run `actor` (e.g. 'apidojo/tweet-scraper') and return its dataset items."""
        await self._check_live_cap()
        reserved = await quota.consume(
            session,
            "apify",
            agent="discovery",
            units=1,
            credit_usd=max_charge_usd,
            scope=f"apify:{source}",
            description=f"{actor} (reserved)",
        )
        actor_id = actor.replace("/", "~")
        resp = await self._client.post(
            f"{API}/acts/{actor_id}/runs",
            params={
                "maxItems": max_items,
                "maxTotalChargeUsd": f"{max_charge_usd:.4f}",
                "waitForFinish": 60,
            },
            json=actor_input,
            headers=self._headers,
        )
        if resp.status_code >= 400:
            await quota.settle(session, reserved, units=1, credit_usd=0.0)
            raise ApifyRunError(f"{actor}: start failed HTTP {resp.status_code}: {resp.text[:200]}")
        run: dict[str, Any] = resp.json()["data"]
        started = time.monotonic()
        while run.get("status") not in TERMINAL and time.monotonic() - started < self._max_wait:
            await asyncio.sleep(self._poll)
            r = await self._client.get(
                f"{API}/actor-runs/{run['id']}", params={"waitForFinish": 60}, headers=self._headers
            )
            r.raise_for_status()
            run = r.json()["data"]
        if run.get("status") not in TERMINAL:
            await self._client.post(f"{API}/actor-runs/{run['id']}/abort", headers=self._headers)
            r = await self._client.get(f"{API}/actor-runs/{run['id']}", headers=self._headers)
            run = r.json()["data"] if r.status_code == 200 else run
        cost = _run_cost(run)
        await quota.settle(session, reserved, units=1, credit_usd=cost)
        reserved.description = f"{actor} run {run.get('id')} {run.get('status')}"
        items: list[dict[str, Any]] = []
        dataset = run.get("defaultDatasetId")
        if dataset:
            r = await self._client.get(
                f"{API}/datasets/{dataset}/items",
                params={"clean": "true", "limit": max_items, "format": "json"},
                headers=self._headers,
            )
            if r.status_code == 200 and isinstance(r.json(), list):
                items = r.json()
        status = str(run.get("status"))
        if status != "SUCCEEDED" and not items:
            raise ApifyRunError(f"{actor}: run {run.get('id')} ended {status}")
        logger.info("apify %s: %s, %d items, $%.4f", actor, status, len(items), cost)
        return ApifyRun(str(run.get("id")), status, items, cost)

    async def _check_live_cap(self) -> None:
        """Belt and braces: per-run cost reports can lag, so the account's own monthly usage
        (cached 5 min) must also be below APIFY_MONTHLY_CAP_USD before any run starts."""
        from app.config import get_settings  # noqa: PLC0415
        from app.orchestrator.quota import FreeQuotaExceededError  # noqa: PLC0415

        now = time.monotonic()
        if self._live is None or now - self._live[0] > 300:
            usage = await self.monthly_usage()
            self._live = (now, usage["used_usd"] if usage else None)
        used, cap = self._live[1], get_settings().apify_monthly_cap_usd
        if used is not None and used >= cap:
            raise FreeQuotaExceededError("apify:account(live)", used, cap, 0.0)

    async def monthly_usage(self) -> dict[str, Any] | None:
        """Account usage this billing cycle (free; for the Sources page)."""
        try:
            r = await self._client.get(f"{API}/users/me/limits", headers=self._headers)
            r.raise_for_status()
            d = r.json()["data"]
            return {
                "used_usd": float(d.get("current", {}).get("monthlyUsageUsd") or 0),
                "limit_usd": float(d.get("limits", {}).get("maxMonthlyUsageUsd") or 0),
                "cycle_end": (d.get("monthlyUsageCycle") or {}).get("endAt"),
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None
