"""Logo sets derived in code: reversed, mono, animated (no model calls)."""

from __future__ import annotations

from lxml import etree

from app.agents.branding import logo_sets
from app.agents.branding.logos import sanitize_svg

SVG = sanitize_svg(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 160">'
    '<defs><linearGradient id="g"><stop offset="0" stop-color="#0b5fa5"/></linearGradient></defs>'
    '<rect x="10" y="30" width="100" height="100" fill="url(#g)"/>'
    '<circle cx="60" cy="80" r="20" fill="#ffffff"/>'
    '<text x="130" y="95" fill="#111827" font-size="34">Smith Plumbing</text></svg>'
)


def _fills(svg: str) -> list[str]:
    root = etree.fromstring(svg.encode())
    return [el.get("fill") for el in root.iter() if isinstance(el.tag, str) and el.get("fill")]


def test_saturation() -> None:
    assert logo_sets.saturation("#111827") < 0.4 and logo_sets.saturation("#808080") == 0
    assert logo_sets.saturation("#0b5fa5") > 0.8


def test_luminance() -> None:
    assert logo_sets.luminance("#000") == 0
    assert round(logo_sets.luminance("#ffffff"), 3) == 1
    assert logo_sets.luminance("url(#x)") == 0.5


def test_reversed_turns_dark_ink_white_and_keeps_brand() -> None:
    out = logo_sets.reversed_(SVG)
    fills = _fills(out)
    assert "#111827" not in fills and fills.count("#ffffff") >= 2  # text + root default
    assert "url(#g)" in fills and "#0b5fa5" in out  # gradient kept


def test_mono_uses_one_ink_but_keeps_white_knockouts() -> None:
    out = logo_sets.mono(SVG)
    assert set(_fills(out)) == {"#111827", "#ffffff"}
    assert "#0b5fa5" not in out


def test_animated_adds_scoped_css_and_honours_reduced_motion() -> None:
    out = logo_sets.animated(SVG, "Ab12-1")
    assert "<script" not in out and "@keyframes lg-ab12-1-pop" in out
    assert "prefers-reduced-motion" in out
    root = etree.fromstring(out.encode())
    classes = [el.get("class") for el in root.iter() if isinstance(el.tag, str) and el.get("class")]
    assert classes.count("lg-ab12-1-s") == 2 and classes.count("lg-ab12-1-t") == 1
    # shapes inside <defs> are never animated
    stop = next(el for el in root.iter() if isinstance(el.tag, str) and el.tag.endswith("stop"))
    assert stop.get("class") is None


def test_pages_are_sized() -> None:
    assert "width:512px;height:512px" in logo_sets.square_page(SVG, 512)
    assert "width:1024px;height:512px" in logo_sets.wide_page(SVG, 1024)
