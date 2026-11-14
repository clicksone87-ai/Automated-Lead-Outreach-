"""Safe fetcher — the ONLY way agents fetch external web content.

Guarantees:
- http/https on ports 80/443 only.
- Every TCP connection is made to an address that was resolved *and validated at connect
  time* (DNS pinning inside the network backend), so DNS rebinding and redirects to internal
  hosts are blocked. All resolved addresses must be public, not just the first.
- Redirects are followed manually; scheme/port/robots are re-checked on every hop.
- robots.txt is fetched, cached and honoured (5xx/timeout => disallow, 4xx => allow).
- Per-domain rate limit, identifying User-Agent, response size and time limits.
- Never sends cookies or credentials (no login walls).
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import logging
import socket
import ssl
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import certifi
import httpcore
import httpx

from app.policy.network import ALLOWED_PORTS, ALLOWED_SCHEMES, is_blocked_ip
from app.policy.platforms import forbidden_reason

logger = logging.getLogger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """Base class for fetch failures."""


class BlockedURLError(FetchError):
    """URL/address rejected by the SSRF policy."""


class RobotsDisallowedError(FetchError):
    """robots.txt disallows this URL for our user agent."""


class ResponseTooLargeError(FetchError):
    pass


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves + validates the host, then connects to the validated IP.

    TLS SNI and the Host header still use the original hostname because httpcore passes
    `server_hostname` from the request URL to `start_tls`, not from this connect call.
    """

    def __init__(self, resolver: Resolver) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._resolver = resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await self._resolver(host, port)
        if not addresses:
            raise BlockedURLError(f"could not resolve {host}")
        blocked = [a for a in addresses if is_blocked_ip(a)]
        if blocked:
            raise BlockedURLError(f"{host} resolves to blocked address(es): {blocked}")
        return await self._inner.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore interface
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedURLError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, resolver: Resolver, max_connections: int = 20) -> None:
        super().__init__(retries=0)
        ctx = ssl.create_default_context(cafile=certifi.where())
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ctx,
            max_connections=max_connections,
            network_backend=_GuardedBackend(resolver),
        )


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    elapsed_ms: int
    redirects: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass(slots=True)
class _RobotsEntry:
    parser: urllib.robotparser.RobotFileParser | None
    allow_all: bool
    disallow_all: bool
    fetched_at: float


class SafeFetcher:
    def __init__(
        self,
        user_agent: str,
        *,
        resolver: Resolver = system_resolver,
        min_interval_s: float = 2.0,
        timeout_s: float = 15.0,
        max_bytes: int = 5 * 1024 * 1024,
        max_redirects: int = 5,
        robots_ttl_s: float = 24 * 3600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.robots_token = user_agent.split("/", 1)[0]
        self._min_interval = min_interval_s
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._robots_ttl = robots_ttl_s
        self._robots: dict[str, _RobotsEntry] = {}
        self._last_hit: dict[str, float] = {}
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._client = httpx.AsyncClient(
            transport=transport or _GuardedTransport(resolver),
            timeout=httpx.Timeout(timeout_s),
            follow_redirects=False,
            headers={"User-Agent": user_agent, "Accept-Language": "en;q=0.9, *;q=0.5"},
            # A jar whose policy accepts no domain: cookies are never stored or sent.
            cookies=http.cookiejar.CookieJar(
                policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[])
            ),
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── public API ────────────────────────────────────────────────────────

    async def get(
        self, url: str, *, respect_robots: bool = True, headers: dict[str, str] | None = None
    ) -> FetchResult:
        """GET a public URL safely. Raises FetchError subclasses on policy violations."""
        started = time.monotonic()
        redirects: list[str] = []
        current = url
        for _ in range(self._max_redirects + 1):
            self.validate_url(current)
            if respect_robots and not await self.allowed_by_robots(current):
                raise RobotsDisallowedError(current)
            await self._throttle(current)
            status, resp_headers, content = await self._raw_get(current, headers)
            if status in (301, 302, 303, 307, 308) and "location" in resp_headers:
                nxt = urljoin(current, resp_headers["location"])
                redirects.append(nxt)
                current = nxt
                continue
            return FetchResult(
                url=url,
                final_url=current,
                status_code=status,
                headers=resp_headers,
                content=content,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                redirects=redirects,
            )
        raise FetchError(f"too many redirects from {url}")

    async def head_status(self, url: str) -> int | None:
        """Lightweight status check for link checking (still fully guarded)."""
        try:
            self.validate_url(url)
            await self._throttle(url)
            resp = await self._client.head(url)
            if resp.status_code in (405, 501):
                return (await self.get(url, respect_robots=False)).status_code
            return resp.status_code
        except (httpx.HTTPError, FetchError):
            return None

    @staticmethod
    def validate_url(url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ALLOWED_SCHEMES:
            raise BlockedURLError(f"scheme not allowed: {parts.scheme!r}")
        if not parts.hostname:
            raise BlockedURLError("missing host")
        if parts.username or parts.password:
            raise BlockedURLError("credentials in URL are not allowed")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise BlockedURLError(f"port not allowed: {port}")
        host = parts.hostname.strip("[]")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise BlockedURLError(f"internal hostname: {host}")
        if _looks_like_ip(host) and is_blocked_ip(host):
            raise BlockedURLError(f"blocked address: {host}")
        reason = forbidden_reason(host)
        if reason:
            raise BlockedURLError(f"{host}: {reason}")

    async def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        entry = self._robots.get(origin)
        if entry is None or time.monotonic() - entry.fetched_at > self._robots_ttl:
            entry = await self._load_robots(origin)
            self._robots[origin] = entry
        if entry.disallow_all:
            return False
        if entry.allow_all or entry.parser is None:
            return True
        return entry.parser.can_fetch(self.robots_token, url)

    def crawl_delay(self, url: str) -> float | None:
        parts = urlsplit(url)
        entry = self._robots.get(f"{parts.scheme}://{parts.netloc}")
        if entry and entry.parser:
            delay = entry.parser.crawl_delay(self.robots_token)
            return float(delay) if delay is not None else None
        return None

    # ── internals ─────────────────────────────────────────────────────────

    async def _load_robots(self, origin: str) -> _RobotsEntry:
        now = time.monotonic()
        try:
            await self._throttle(origin)
            status, _, content = await self._raw_get(f"{origin}/robots.txt", None)
        except (httpx.HTTPError, FetchError) as exc:
            logger.info("robots.txt unavailable for %s (%s): disallowing", origin, exc)
            return _RobotsEntry(None, allow_all=False, disallow_all=True, fetched_at=now)
        if 400 <= status < 500:
            return _RobotsEntry(None, allow_all=True, disallow_all=False, fetched_at=now)
        if status >= 500:
            return _RobotsEntry(None, allow_all=False, disallow_all=True, fetched_at=now)
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(content.decode("utf-8", errors="replace").splitlines())
        return _RobotsEntry(parser, allow_all=False, disallow_all=False, fetched_at=now)

    async def _raw_get(
        self, url: str, headers: dict[str, str] | None
    ) -> tuple[int, dict[str, str], bytes]:
        async with self._client.stream("GET", url, headers=headers) as resp:
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                raise ResponseTooLargeError(f"{url}: {declared} bytes")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > self._max_bytes:
                    raise ResponseTooLargeError(f"{url}: exceeds {self._max_bytes} bytes")
                chunks.append(chunk)
            return (
                resp.status_code,
                {k.lower(): v for k, v in resp.headers.items()},
                b"".join(chunks),
            )

    async def _throttle(self, url: str) -> None:
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        lock = self._domain_locks.setdefault(host, asyncio.Lock())
        async with lock:
            interval = max(self._min_interval, self.crawl_delay(url) or 0.0)
            wait = self._last_hit.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_hit[host] = time.monotonic()


def _looks_like_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
    except OSError:
        return False
    return True
