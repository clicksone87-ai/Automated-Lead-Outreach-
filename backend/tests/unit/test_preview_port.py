"""The preview-only Caddy site (port 8090, for a Cloudflare Tunnel) serves static preview files
and nothing else: no proxy to the API or the dashboard, no listings, loopback-only in compose."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CADDYFILE = REPO / "caddy" / "Caddyfile"
COMPOSE = REPO / "docker-compose.yml"
PORT = 8090
pytestmark = pytest.mark.skipif(
    not (CADDYFILE.exists() and COMPOSE.exists()), reason="repo root not mounted here"
)


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
                     for line in text.splitlines())  # fmt: skip


def site_block(text: str, address: str) -> str:
    """The body of the top-level site block that starts with `address {`."""
    text = _strip_comments(text)
    m = re.search(rf"^{re.escape(address)}\s*\{{", text, re.MULTILINE)
    assert m, f"no site block {address}"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[m.end() : i - 1]


@pytest.fixture(scope="module")
def block() -> str:
    return site_block(CADDYFILE.read_text(encoding="utf-8"), f":{PORT}")


def directives(block: str) -> list[str]:
    return [line.split()[0] for line in block.splitlines() if line.strip()]


def test_no_proxying_of_any_kind(block: str) -> None:
    used = set(directives(block))
    for forbidden in ("reverse_proxy", "php_fastcgi", "forward_auth", "import", "invoke", "map"):
        assert forbidden not in used, forbidden
    for upstream in ("api:", "dashboard:", ":8000", ":3000", "adminer", "mailpit"):
        assert upstream not in block, upstream


def test_only_a_read_only_file_server_rooted_at_the_previews(block: str) -> None:
    roots = re.findall(r"^\s*root\s+\*\s+(\S+)", block, re.MULTILINE)
    assert roots == ["/srv/previews/{re.preview.1}/site"]
    assert "file_server" in directives(block)
    assert "browse" not in block  # no directory listings
    assert re.search(r"hide\s+\.\*", block)  # dotfiles stay hidden
    # Only GET/HEAD reach the files; the preview id must be a 32-hex lead id.
    assert re.search(r"@write\s+not\s+method\s+GET\s+HEAD", block)
    assert "^/p/([a-f0-9]{32})(/.*)$" in block


def test_redirects_stay_inside_a_preview(block: str) -> None:
    targets = re.findall(r"^\s*redir\s+\*\s+(\S+)", block, re.MULTILINE)
    assert targets and all(t.startswith("/p/{re.") for t in targets)


def test_everything_else_is_404(block: str) -> None:
    last = block.rstrip().rsplit("handle {", 1)[1]
    assert 'respond "Not found" 404' in last


def test_noindex_and_security_headers(block: str) -> None:
    for header in (
        'X-Robots-Tag "noindex, nofollow, noarchive"',
        'X-Frame-Options "DENY"',
        'X-Content-Type-Options "nosniff"',
        "Content-Security-Policy",
        "-Server",
    ):
        assert header in block, header


def test_compose_binds_the_port_to_loopback_and_mounts_previews_read_only() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    after = compose.split("\n  caddy:\n", 1)[1]
    caddy = re.split(r"\n(?:  [a-z]|[a-z])", after, maxsplit=1)[0]  # up to the next service
    assert f'"127.0.0.1:{PORT}:{PORT}"' in caddy
    assert f'"{PORT}:{PORT}"' not in caddy.replace(f'"127.0.0.1:{PORT}:{PORT}"', "")
    assert "./data/previews:/srv/previews:ro" in caddy
