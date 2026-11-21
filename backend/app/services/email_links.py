"""Links that may appear in outgoing email, and the check that keeps local ones out.

Recipients cannot open localhost addresses, so a link is only written when it points at a
public host (PUBLIC_API_BASE_URL, PUBLIC_PREVIEW_BASE_URL, PORTFOLIO_URL). Every outgoing email
is checked once more before it leaves (`assert_no_local_links`).
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from app.adapters.email import OutgoingEmail
from app.config import Settings

LOCAL_PREVIEW_PORT = 8080
# Substrings that mean "only reachable on this machine". `.localhost` also covers
# `<id>.preview.localhost`; `0.0.0.0` and `[::1]` are what a misconfigured base URL looks like.
_LOCAL_PATTERN = re.compile(
    r"localhost|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|0\.0\.0\.0|\[::1\]", re.IGNORECASE
)


class LocalLinkError(ValueError):
    """An outgoing email would carry a link only this machine can open."""


def is_local_url(url: str | None) -> bool:
    """True for empty values and for URLs whose host is this machine."""
    if not url or not url.strip():
        return True
    host = (urlsplit(url.strip()).hostname or "").lower()
    if not host:
        return True
    return bool(
        host == "localhost"
        or host.endswith(".localhost")
        or host.startswith("127.")
        or host in {"0.0.0.0", "::1"}  # noqa: S104 - compared, never bound
    )


def public_base(url: str | None) -> str | None:
    """The base URL without a trailing slash, or None when it is unset or local."""
    if is_local_url(url):
        return None
    assert url is not None
    return url.strip().rstrip("/")


def preview_id_for(lead_id: uuid.UUID) -> str:
    """Previews are stored and served under the lead id (data/previews/<id>/site)."""
    return lead_id.hex


def preview_local_url(preview_id: str) -> str:
    """The owner's own view of a preview (Caddy on this machine)."""
    return f"http://{preview_id}.preview.localhost:{LOCAL_PREVIEW_PORT}/"


def preview_public_url(settings: Settings, preview_id: str) -> str | None:
    """`<PUBLIC_PREVIEW_BASE_URL>/p/<id>/` (the preview-only Caddy port behind a tunnel)."""
    base = public_base(settings.public_preview_base_url)
    return f"{base}/p/{preview_id}/" if base else None


def preview_url(settings: Settings, preview_id: str) -> str:
    """The stored preview link: public when a tunnel is configured, else the local one."""
    return preview_public_url(settings, preview_id) or preview_local_url(preview_id)


def sender_props(settings: Settings) -> dict[str, str]:
    """Sender identity for the email footer; a local portfolio URL is left out."""
    portfolio = settings.portfolio_url.strip()
    return {
        "name": settings.sender_name,
        "businessName": settings.sender_business_name,
        "postalAddress": settings.sender_postal_address,
        "portfolioUrl": "" if is_local_url(portfolio) else portfolio,
    }


def find_local_links(text: str) -> list[str]:
    """Every local-looking fragment in `text` (with a little context for the error message)."""
    return [text[max(0, m.start() - 30) : m.end() + 30] for m in _LOCAL_PATTERN.finditer(text)]


def _email_texts(email: OutgoingEmail) -> list[tuple[str, str]]:
    parts: list[tuple[str, Any]] = [
        ("subject", email.subject),
        ("html", email.html),
        ("text", email.text),
        ("from", email.from_address),
        ("reply-to", email.reply_to or ""),
        *((f"header {k}", f"{k}: {v}") for k, v in email.headers.items()),
        *((f"tag {k}", f"{k}: {v}") for k, v in email.tags.items()),
        *((f"attachment {a.filename}", a.filename) for a in email.attachments),
    ]
    return [(where, str(value)) for where, value in parts]


def assert_no_local_links(email: OutgoingEmail) -> None:
    """Raise LocalLinkError when any part of the email mentions a local address."""
    problems = [
        f"{where}: …{hit}…"
        for where, value in _email_texts(email)
        for hit in find_local_links(value)
    ]
    if problems:
        raise LocalLinkError(
            "outgoing email contains a local address recipients cannot open: "
            + "; ".join(problems[:3])
        )
