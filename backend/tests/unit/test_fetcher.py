"""Safe fetcher: SSRF protection, redirects, robots.txt, limits."""

from __future__ import annotations

import httpx
import pytest

from app.adapters.fetcher import (
    BlockedURLError,
    ResponseTooLargeError,
    RobotsDisallowedError,
    SafeFetcher,
)
from app.policy.network import is_blocked_ip

UA = "TestBot/1.0"


@pytest.mark.parametrize(
    "ip",
    [
        "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1", "127.0.0.1", "169.254.169.254",
        "::1", "fc00::1", "fd12:3456::1", "0.0.0.0", "100.64.0.1", "::ffff:127.0.0.1",
        "::ffff:10.0.0.1", "fe80::1", "224.0.0.1", "not-an-ip",
    ],
)
def test_blocked_ips(ip: str) -> None:
    assert is_blocked_ip(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip: str) -> None:
    assert not is_blocked_ip(ip)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file", "file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/",
        "http://localhost/", "http://foo.localhost/", "http://example.com:8080/",
        "http://user:pass@example.com/", "http://10.0.0.5/admin", "http://printer.local/",
        "gopher://example.com/",
    ],
)
def test_validate_url_blocks(url: str) -> None:
    with pytest.raises(BlockedURLError):
        SafeFetcher.validate_url(url)


def test_validate_url_allows_public() -> None:
    SafeFetcher.validate_url("https://example.com/contact")
    SafeFetcher.validate_url("http://example.co.uk/")


async def test_dns_resolving_to_private_is_blocked_at_connect() -> None:
    async def evil_resolver(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    fetcher = SafeFetcher(UA, resolver=evil_resolver, min_interval_s=0)
    with pytest.raises(BlockedURLError):
        await fetcher.get("http://rebind.attacker.test/", respect_robots=False)
    await fetcher.aclose()


async def test_mixed_dns_records_blocked() -> None:
    async def mixed(host: str, port: int) -> list[str]:
        return ["93.184.216.34", "10.0.0.1"]

    fetcher = SafeFetcher(UA, resolver=mixed, min_interval_s=0)
    with pytest.raises(BlockedURLError):
        await fetcher.get("http://mixed.attacker.test/", respect_robots=False)
    await fetcher.aclose()


def _fetcher(handler: httpx.MockTransport, **kw: object) -> SafeFetcher:
    return SafeFetcher(UA, transport=handler, min_interval_s=0, **kw)  # type: ignore[arg-type]


async def test_redirect_to_internal_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    fetcher = _fetcher(httpx.MockTransport(handler))
    with pytest.raises(BlockedURLError):
        await fetcher.get("https://example.com/start")


async def test_follows_public_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="hello", headers={"content-type": "text/html"})

    result = await _fetcher(httpx.MockTransport(handler)).get("https://example.com/old")
    assert result.ok
    assert result.final_url == "https://example.com/new"
    assert result.text == "hello"
    assert result.redirects == ["https://example.com/new"]


async def test_robots_disallow_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="ok")

    fetcher = _fetcher(httpx.MockTransport(handler))
    assert (await fetcher.get("https://example.com/public")).ok
    with pytest.raises(RobotsDisallowedError):
        await fetcher.get("https://example.com/private/page")


async def test_robots_5xx_means_disallow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(503)
        return httpx.Response(200, text="ok")

    with pytest.raises(RobotsDisallowedError):
        await _fetcher(httpx.MockTransport(handler)).get("https://example.com/")


async def test_user_agent_sent_and_no_cookies() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="ok", headers={"set-cookie": "s=1"})

    fetcher = _fetcher(httpx.MockTransport(handler))
    await fetcher.get("https://example.com/a")
    await fetcher.get("https://example.com/b")
    assert all(r.headers["user-agent"] == UA for r in seen)
    assert "cookie" not in seen[-1].headers


async def test_response_size_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"x" * 5000)

    fetcher = _fetcher(httpx.MockTransport(handler), max_bytes=1000)
    with pytest.raises(ResponseTooLargeError):
        await fetcher.get("https://example.com/big")
