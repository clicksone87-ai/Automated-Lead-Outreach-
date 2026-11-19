"""Headless Chromium for audits: screenshots, axe-core and PDF rendering.

Every request the page makes goes through a route guard with the same URL rules and IP
blocklist as the safe fetcher.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar
from urllib.parse import urlsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Playwright,
    Route,
    ViewportSize,
    async_playwright,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.adapters.fetcher import BlockedURLError, Resolver, SafeFetcher, system_resolver
from app.policy.network import is_blocked_ip

logger = logging.getLogger(__name__)
_R = TypeVar("_R")

DESKTOP: ViewportSize = {"width": 1366, "height": 900}
MOBILE: ViewportSize = {"width": 390, "height": 844}
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
AXE_CANDIDATES = (
    os.environ.get("AXE_JS_PATH", ""),
    "/usr/local/lib/node_modules/axe-core/axe.min.js",
    str(Path(__file__).resolve().parents[4] / "dashboard/node_modules/axe-core/axe.min.js"),
)

# url -> (status, content_type, body)
Fixtures = dict[str, tuple[int, str, str]]


@dataclass(slots=True)
class Capture:
    requested_url: str
    final_url: str
    status: int
    html: str
    headers: dict[str, str]
    desktop_png: bytes
    mobile_png: bytes
    axe_violations: list[dict[str, Any]] | None
    blocked_requests: list[str] = field(default_factory=list)


async def _settle(page: Any, load_ms: int = 12_000) -> None:
    try:
        await page.wait_for_load_state("load", timeout=load_ms)
    except PlaywrightTimeoutError:
        logger.info("page did not finish loading in %d ms; capturing what is rendered", load_ms)
    await page.wait_for_timeout(800)


def axe_script() -> str | None:
    for candidate in AXE_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_text(encoding="utf-8")
    return None


class GuardedBrowser:
    def __init__(
        self,
        *,
        fulfill: Fixtures | None = None,
        resolver: Resolver = system_resolver,
        user_agent: str | None = None,
        reduced_motion: bool = False,
    ) -> None:
        self._fulfill = fulfill
        #: our own previews: capture the finished layout, not an entrance animation mid-way
        self._reduced_motion = reduced_motion
        self._resolver = resolver
        self._ua = user_agent
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._threaded = False

    # Windows dev only: async psycopg needs the selector event loop, but Playwright needs the
    # Proactor loop to spawn the browser. In that case each browser call runs on its own
    # Proactor loop in a worker thread. Linux (containers/servers) never takes this path.
    @staticmethod
    def _needs_thread() -> bool:
        if sys.platform != "win32":
            return False
        proactor = getattr(asyncio, "ProactorEventLoop", None)
        return proactor is not None and not isinstance(asyncio.get_running_loop(), proactor)

    async def _isolated(self, fn: Callable[..., Awaitable[_R]], *args: Any) -> _R:
        def runner() -> _R:
            loop = asyncio.ProactorEventLoop()  # type: ignore[attr-defined,unused-ignore]
            try:
                return loop.run_until_complete(self._with_browser(fn, *args))  # type: ignore[no-any-return,unused-ignore]
            finally:
                loop.close()

        return await asyncio.to_thread(runner)

    async def _with_browser(self, fn: Callable[..., Awaitable[_R]], *args: Any) -> _R:
        await self._start()
        try:
            return await fn(*args)
        finally:
            await self._stop()

    async def _start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )

    async def _stop(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> GuardedBrowser:
        self._threaded = self._needs_thread()
        if not self._threaded:
            await self._start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._threaded:
            await self._stop()

    async def capture(self, url: str, *, timeout_ms: int = 30_000) -> Capture:
        if self._threaded:
            return await self._isolated(self._capture, url, timeout_ms)
        return await self._capture(url, timeout_ms)

    async def screenshot(self, url: str, *, timeout_ms: int = 30_000) -> bytes:
        """Desktop viewport screenshot only (no axe, no mobile pass)."""
        if self._threaded:
            return await self._isolated(self._screenshot, url, timeout_ms)
        return await self._screenshot(url, timeout_ms)

    async def _screenshot(self, url: str, timeout_ms: int) -> bytes:
        desktop, _ = await self._context(mobile=False)
        try:
            page = await desktop.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await _settle(page)
            return await page.screenshot(full_page=False, type="png")
        finally:
            await desktop.close()

    async def pdf(self, html: str) -> bytes:
        if self._threaded:
            return await self._isolated(self._pdf, html)
        return await self._pdf(html)

    async def render_png(
        self, html: str, *, width: int, height: int, transparent: bool = True
    ) -> bytes:
        """Screenshot trusted, self-contained HTML/SVG with ALL network blocked."""
        if self._threaded:
            return await self._isolated(self._render_png, html, width, height, transparent)
        return await self._render_png(html, width, height, transparent)

    async def _render_png(self, html: str, width: int, height: int, transparent: bool) -> bytes:
        assert self._browser is not None
        ctx = await self._browser.new_context(
            viewport={"width": width, "height": height}, java_script_enabled=False
        )
        try:
            await ctx.route("**/*", lambda route: route.abort())
            page = await ctx.new_page()
            await page.set_content(html, wait_until="load")
            return await page.screenshot(type="png", omit_background=transparent)
        finally:
            await ctx.close()

    async def _allowed(self, url: str) -> bool:
        if url.startswith(("data:", "blob:", "about:")):
            return True
        try:
            SafeFetcher.validate_url(url)
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            addresses = await self._resolver(parts.hostname or "", port)
        except (BlockedURLError, OSError):
            return False
        return bool(addresses) and not any(is_blocked_ip(a) for a in addresses)

    async def _context(self, *, mobile: bool) -> tuple[BrowserContext, list[str]]:
        assert self._browser is not None
        ctx = await self._browser.new_context(
            viewport=MOBILE if mobile else DESKTOP,
            user_agent=MOBILE_UA if mobile else self._ua,
            is_mobile=mobile,
            has_touch=mobile,
            device_scale_factor=1,
            java_script_enabled=True,
            accept_downloads=False,
            service_workers="block",
            reduced_motion="reduce" if self._reduced_motion else "no-preference",
        )
        blocked: list[str] = []

        async def guard(route: Route) -> None:
            url = route.request.url
            if self._fulfill is not None:
                hit = self._fulfill.get(url) or self._fulfill.get(url.rstrip("/"))
                if hit is None:
                    blocked.append(url)
                    await route.abort()
                    return
                status, ctype, body = hit
                await route.fulfill(status=status, content_type=ctype, body=body)
                return
            if await self._allowed(url):
                await route.continue_()
            else:
                blocked.append(url)
                await route.abort()

        await ctx.route("**/*", guard)
        return ctx, blocked

    async def _capture(self, url: str, timeout_ms: int = 30_000) -> Capture:
        desktop, blocked_requests = await self._context(mobile=False)
        try:
            page = await desktop.new_page()
            # Many small-business sites never fire "load" (trackers, chat widgets): wait for the
            # DOM, then give the full load a bounded chance before capturing what is there.
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await _settle(page)
            html = await page.content()
            headers = await response.all_headers() if response else {}
            status = response.status if response else 0
            final_url = page.url
            desktop_png = await page.screenshot(full_page=False, type="png")
            violations = None
            script = axe_script()
            if script:
                try:
                    await page.add_script_tag(content=script)
                    violations = await page.evaluate(
                        "async () => (await axe.run(document, {resultTypes: ['violations']}))"
                        ".violations.map(v => ({id: v.id, impact: v.impact, help: v.help, "
                        "nodes: v.nodes.slice(0, 5).map(n => ({target: n.target}))}))"
                    )
                except Exception as exc:
                    logger.info("axe failed on %s: %s", url, exc)
            blocked = list(blocked_requests)
        finally:
            await desktop.close()
        mobile, _ = await self._context(mobile=True)
        try:
            mpage = await mobile.new_page()
            await mpage.goto(final_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await _settle(mpage)
            mobile_png = await mpage.screenshot(full_page=False, type="png")
        finally:
            await mobile.close()
        return Capture(
            url,
            final_url,
            status,
            html,
            {k.lower(): v for k, v in headers.items()},
            desktop_png,
            mobile_png,
            violations,
            blocked,
        )

    async def _pdf(self, html: str) -> bytes:
        """Render trusted report HTML (our own template) to PDF with all network blocked."""
        assert self._browser is not None
        ctx = await self._browser.new_context()
        try:
            await ctx.route("**/*", lambda route: route.abort())
            page = await ctx.new_page()
            await page.set_content(html, wait_until="load")
            return await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "16mm", "bottom": "16mm", "left": "14mm", "right": "14mm"},
            )
        finally:
            await ctx.close()
