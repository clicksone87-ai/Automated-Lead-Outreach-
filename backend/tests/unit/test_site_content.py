"""Website Builder content (3.06–3.08): structure, facts, variants."""

from __future__ import annotations

import io

from PIL import Image

from app.agents.website_builder.build import internal_links, screenshots_pdf, site_fixtures
from app.agents.website_builder.content import (
    SiteCopy,
    SiteFacts,
    build_content,
    find_phone,
    nav_pages,
    pick_variant,
    sections_for,
)

NAV = """<html><body><header><nav>
<a href="/">Home</a><a href="/our-services/">Our services</a><a href="/about-us">About us</a>
<a href="https://other.example/x">Partner</a><a href="/contact">Contact</a><a href="/our-services/#x">Dup</a>
</nav></header></body></html>"""


def test_nav_pages_keep_original_structure() -> None:
    pages = nav_pages(NAV, "https://smith.example/")
    assert pages == [
        ("our-services", "Our services"),
        ("about-us", "About us"),
        ("contact", "Contact"),
    ]
    assert sections_for("our-services") == ["services"]
    assert sections_for("about-us") == ["about"]
    assert sections_for("contact") == ["contact"]
    assert sections_for("gallery") == ["about"]


def test_find_phone() -> None:
    assert find_phone("Call us on 0113 496 0000 today", "GB") == "0113 496 0000"
    assert find_phone("Phone: (512) 555-0134", "US") == "(512) 555-0134"
    assert find_phone("no number here", "US") == ""


def _facts(**kw: object) -> SiteFacts:
    base: dict[str, object] = {
        "name": "Hub City Plumbing", "trade": "plumber", "city": "Lubbock", "country": "US",
        "language": "en", "services_hint": ["redesign"], "phone": "(806) 555-0100",
        "nav": [("services", "Services"), ("about", "About")],
    }  # fmt: skip
    base.update(kw)
    return SiteFacts(**base)  # type: ignore[arg-type]


def test_build_content_redesign_keeps_pages_and_identity() -> None:
    copy = SiteCopy.mock({"name": "Hub City Plumbing", "trade": "plumber", "city": "Lubbock"})
    c = build_content(
        lead_id="x", mode="redesign", facts=_facts(), copy=copy, colours=["#041e51", "#0073ea"],
        fonts=["Poppins"], logo_svg="<svg/>", agency="Test Agency",
    )  # fmt: skip
    assert c["variant"] == "classic" and c["mode"] == "redesign"
    assert [p["slug"] for p in c["pages"]] == ["", "services", "about"]
    assert c["brand"]["primary"] == "#041e51" and c["brand"]["fontHeading"].startswith('"Poppins"')
    assert c["business"]["phone"] == "(806) 555-0100"
    assert c["preview"] == {"agency": "Test Agency", "notice": "Preview — not a live website"}


def test_new_site_variant_is_deterministic_and_localised_notice() -> None:
    assert pick_variant("lead-a", "new", []) == pick_variant("lead-a", "new", [])
    assert {pick_variant(f"lead-{i}", "new", []) for i in range(30)} == {
        "classic",
        "bold",
        "minimal",
    }
    copy = SiteCopy.mock({"name": "Maler Müller", "trade": "Maler"})
    c = build_content(lead_id="y", mode="new", facts=_facts(language="de", nav=[]), copy=copy,
                      colours=[], fonts=[], logo_svg="", agency="")  # fmt: skip
    assert [p["slug"] for p in c["pages"]] == [""]
    assert c["preview"]["notice"].startswith("Vorschau")


def test_internal_link_check_and_fixtures(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "services").mkdir()
    (tmp_path / "index.html").write_text(
        '<a href="/services/">s</a><a href="/missing/">m</a><a href="tel:1">t</a>'
    )
    (tmp_path / "services" / "index.html").write_text("<h1>S</h1>")
    assert internal_links(tmp_path) == [("index.html", "/missing/")]
    fx = site_fixtures(tmp_path)
    assert "http://preview.local/" in fx and "http://preview.local/services/" in fx


def test_screenshots_pdf() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (255, 0, 0)).save(buf, format="PNG")
    pdf = screenshots_pdf([buf.getvalue(), buf.getvalue()])
    assert pdf.startswith(b"%PDF")
