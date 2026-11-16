"""Logo concepts (3.03–3.05): SVG sanitising, wording check, legal suffixes, similarity."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.agents.branding.agent import logo_text
from app.agents.branding.logos import SvgRejectedError, sanitize_svg, svg_texts, text_matches
from app.quality.images import compare

EVIL = """<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
 width="400" height="160" onload="alert(1)">
  <script>alert(1)</script>
  <defs><linearGradient id="g"><stop offset="0" stop-color="#123456"/></linearGradient></defs>
  <foreignObject><div>x</div></foreignObject>
  <image href="https://evil.example/x.png" width="10" height="10"/>
  <use href="https://evil.example/s.svg#a"/>
  <use href="#g"/>
  <rect width="10" height="10" fill="url(#g)" style="background:url(https://evil.example)"/>
  <circle r="5" fill="url(https://evil.example/p)"/>
  <text x="10" y="50" font-family="Arial">Smith Plumbing</text>
</svg>"""


def test_sanitize_strips_active_and_external_content() -> None:
    out = sanitize_svg("Here is your logo:\n```svg\n" + EVIL + "\n```")
    for bad in ("script", "onload", "foreignObject", "<image", "evil.example", "style="):
        assert bad not in out, bad
    assert 'fill="url(#g)"' in out and 'href="#g"' in out  # internal refs survive
    assert 'viewBox="0 0 400 160"' in out and "width=" not in out.split(">")[0]
    assert svg_texts(out) == ["Smith Plumbing"]


@pytest.mark.parametrize(
    "bad",
    [
        "no svg here",
        "<svg xmlns='http://www.w3.org/2000/svg'><g/></svg>",  # draws nothing
        "<svg xmlns='http://www.w3.org/2000/svg'><rect></svg>",  # invalid xml
        "<svg xmlns='http://www.w3.org/2000/svg'>" + "<rect/>" * 20000 + "</svg>",  # too big
    ],
)
def test_sanitize_rejects(bad: str) -> None:
    with pytest.raises(SvgRejectedError):
        sanitize_svg(bad)


def test_xxe_entities_are_not_resolved() -> None:
    xxe = (
        '<?xml version="1.0"?><!DOCTYPE s [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text><rect width="1" height="1"/></svg>'
    )
    try:
        out = sanitize_svg(xxe)
    except SvgRejectedError:
        return
    assert "root:" not in out


def test_text_check() -> None:
    svg = sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Smith  <tspan>Plumbing</tspan></text></svg>'
    )
    assert text_matches(svg, "Smith Plumbing") == "exact"
    assert text_matches(svg, "Smith Plumbers") == "mismatch"
    assert text_matches(svg, "Jones Roofing") == "missing"
    paths = sanitize_svg('<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0L1 1"/></svg>')
    assert text_matches(paths, "Smith Plumbing") == "paths"


def test_logo_text_drops_legal_suffixes() -> None:
    assert logo_text("Smith Plumbing Ltd") == "Smith Plumbing"
    assert logo_text("Acme Roofing, LLC") == "Acme Roofing"
    assert logo_text("Bright Sparks Electrical Limited") == "Bright Sparks Electrical"
    assert logo_text("Co-op Builders") == "Co-op Builders"


def _png(draw: str) -> bytes:
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if draw == "circle":
        d.ellipse((20, 10, 100, 90), fill=(20, 60, 200, 255))
    elif draw == "bars":
        for x in range(10, 190, 30):
            d.rectangle((x, 10, x + 12, 90), fill=(200, 30, 30, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_similarity_flags_near_copies_only() -> None:
    circle = _png("circle")
    sims = compare(circle, {"same": circle, "different": _png("bars")})
    assert sims[0].against == "same" and sims[0].too_similar
    assert not next(s for s in sims if s.against == "different").too_similar
