"""Contact extraction: only addresses present in content, never guessed."""

from __future__ import annotations

from app.agents.contact_enrichment.extract import (
    candidate_pages,
    decode_cfemail,
    extract_contact_forms,
    extract_emails,
    rank_email,
)


def _cf_encode(email: str, key: int = 0x42) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def test_extracts_mailto_text_obfuscated_and_cloudflare() -> None:
    html = f"""
    <a href="mailto:Info@Acme-Plumbing.com?subject=Hi">Email us</a>
    <p>Or write to bookings@acme-plumbing.com today.</p>
    <p>Owner: jane [at] acme-plumbing [dot] co [dot] uk</p>
    <span class="__cf_email__" data-cfemail="{_cf_encode("office@acme-plumbing.com")}">[email protected]</span>
    <img src="logo@2x.png"><p>noreply@acme-plumbing.com</p>
    <script>var x = "tracking@sentry.io";</script>
    """
    emails = {f.email for f in extract_emails(html)}
    assert emails == {
        "info@acme-plumbing.com",
        "bookings@acme-plumbing.com",
        "jane@acme-plumbing.co.uk",
        "office@acme-plumbing.com",
    }


def test_nothing_is_invented_from_names_or_domains() -> None:
    html = "<h1>Jane Smith — Acme Plumbing (acme-plumbing.com)</h1><p>Call 0113 496 0000</p>"
    assert extract_emails(html) == []


def test_cfemail_decoder_rejects_garbage() -> None:
    assert decode_cfemail(_cf_encode("a@b.co")) == "a@b.co"
    assert decode_cfemail("zz") is None
    assert decode_cfemail("") is None


def test_contact_form_detection_ignores_search_forms() -> None:
    html = """
    <form action="/search"><input name="q"></form>
    <form action="/send"><input name="email" type="email"><textarea name="message"></textarea></form>
    """
    forms = extract_contact_forms(html, "https://acme.com/contact")
    assert [f.action for f in forms] == ["https://acme.com/send"]


def test_candidate_pages_same_site_and_ranked() -> None:
    html = """
    <a href="/about-us">About</a><a href="/contact">Contact</a>
    <a href="https://other.com/contact">Partner</a><a href="/impressum">Impressum</a>
    <a href="mailto:x@acme.com">mail</a><a href="/blog">Blog</a>
    """
    pages = candidate_pages(html, "https://www.acme.com/")
    assert pages == [
        "https://www.acme.com/contact",
        "https://www.acme.com/impressum",
        "https://www.acme.com/about-us",
    ]


def test_email_ranking_prefers_own_domain_role_addresses() -> None:
    domain = "acme-plumbing.com"
    ranked = sorted(
        ["jane@gmail.com", "jane@acme-plumbing.com", "info@acme-plumbing.com", "x@other.com"],
        key=lambda e: -rank_email(e, domain),
    )
    assert ranked == [
        "info@acme-plumbing.com",
        "jane@acme-plumbing.com",
        "jane@gmail.com",
        "x@other.com",
    ]
