"""Local addresses never reach an email; preview links use the tunnel's /p/<id>/ path."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.adapters.email import EmailAttachment, OutgoingEmail
from app.config import Settings
from app.services.email_links import (
    LocalLinkError,
    assert_no_local_links,
    find_local_links,
    is_local_url,
    preview_id_for,
    preview_public_url,
    preview_url,
    public_base,
    sender_props,
)
from app.services.unsubscribe import unsubscribe_link, unsubscribe_mailto, unsubscribe_url


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://localhost:8000",
        "http://LOCALHOST/u/x",
        "http://127.0.0.1:8090",
        "http://127.1.2.3/",
        "http://abc.preview.localhost:8080/",
        "http://0.0.0.0:8000",
        "http://[::1]:8000/",
        "not a url",
    ],
)
def test_local_or_empty_urls_are_not_public(url: str) -> None:
    assert is_local_url(url) and public_base(url) is None


@pytest.mark.parametrize(
    "url", ["https://previews.agency.example", "https://abc.trycloudflare.com/", "https://x.io"]
)
def test_public_urls(url: str) -> None:
    assert not is_local_url(url) and public_base(url) == url.rstrip("/")


def test_preview_urls_use_the_preview_only_port_path() -> None:
    pid = preview_id_for(uuid.UUID("0123456789abcdef0123456789abcdef"))
    assert pid == "0123456789abcdef0123456789abcdef"
    tunnel = _settings(public_preview_base_url="https://previews.agency.example/")
    assert preview_public_url(tunnel, pid) == f"https://previews.agency.example/p/{pid}/"
    assert preview_url(tunnel, pid) == f"https://previews.agency.example/p/{pid}/"
    assert tunnel.public_links_enabled
    local = _settings()
    assert preview_public_url(local, pid) is None and not local.public_links_enabled
    assert preview_url(local, pid) == f"http://{pid}.preview.localhost:8080/"  # owner's view
    misconfigured = _settings(public_preview_base_url="http://localhost:8090")
    assert preview_public_url(misconfigured, pid) is None
    assert not misconfigured.public_links_enabled


def test_unsubscribe_link_is_mailto_until_the_api_is_public() -> None:
    local = _settings(sending_domain="agency.example")
    assert unsubscribe_url(local, "tok") is None
    assert unsubscribe_link(local, "tok") == "mailto:replies@agency.example?subject=unsubscribe"
    assert unsubscribe_mailto(local) == unsubscribe_link(local, "tok")
    still_local = _settings(public_api_base_url="http://localhost:8000")
    assert unsubscribe_url(still_local, "tok") is None
    public = _settings(public_api_base_url="https://api.agency.example/")
    assert unsubscribe_link(public, "tok") == "https://api.agency.example/u/tok"


def test_a_local_portfolio_url_is_left_out_of_the_footer() -> None:
    assert sender_props(_settings(portfolio_url="http://localhost:3000"))["portfolioUrl"] == ""
    assert (
        sender_props(_settings(portfolio_url="https://agency.example"))["portfolioUrl"]
        == "https://agency.example"
    )


def _email(**kw: Any) -> OutgoingEmail:
    base: dict[str, Any] = {
        "from_address": "Agency <a@agency.example>", "to": "owner@client.example",
        "subject": "Ideas", "html": "<p>Hi</p>", "text": "Hi", "idempotency_key": "p:0",
        "headers": {"List-Unsubscribe": "<mailto:replies@agency.example?subject=unsubscribe>"},
    }  # fmt: skip
    return OutgoingEmail(**{**base, **kw})


def test_clean_email_passes() -> None:
    assert_no_local_links(_email())


@pytest.mark.parametrize(
    "field",
    [
        {"subject": "See http://localhost:8080"},
        {"html": '<a href="http://abc.preview.localhost:8080/">preview</a>'},
        {"text": "Unsubscribe: http://127.0.0.1:8000/u/x"},
        {"headers": {"List-Unsubscribe": "<http://localhost:8000/u/x>"}},
        {"reply_to": "x@localhost"},
        {"attachments": [EmailAttachment(filename="localhost.png", content=b"")]},
    ],
)
def test_any_local_address_blocks_the_email(field: dict[str, Any]) -> None:
    with pytest.raises(LocalLinkError):
        assert_no_local_links(_email(**field))


def test_find_local_links_gives_context() -> None:
    hits = find_local_links("visit http://localhost:8000/u/abc now")
    assert len(hits) == 1 and "localhost:8000" in hits[0]
