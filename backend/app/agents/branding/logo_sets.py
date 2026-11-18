"""Logo sets: every concept becomes a usable package, derived in code from the sanitised SVG.

For each concept (wordmark, monogram, icon):
- full     — the colour logo as designed
- reversed — for dark backgrounds: dark ink turned white, brand colours kept
- mono     — one ink colour (fax, stamps, embroidery, single-colour print)
- mark     — the square symbol (initials badge / icon) in the same three versions; PNG sizes for
             app icons and favicons
- animated — an SVG that draws itself in once (CSS keyframes, honours reduced motion)

Inputs are already sanitised by `sanitize_svg`; these functions only recolour attributes and add
a trusted <style> block, so the outputs stay script-free.
"""

from __future__ import annotations

import re

from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
MONO_INK = "#111827"
WHITE = "#ffffff"
DARK_LUMINANCE = 0.35
NEUTRAL_SATURATION = 0.4
COLOUR_ATTRS = ("fill", "stroke", "stop-color")
SHAPES = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}
_HEX = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.I)
_NAMED = {"black": "#000000", "white": "#ffffff", "currentcolor": "#000000"}


def _local(tag: object) -> str:
    return tag.split("}", 1)[-1] if isinstance(tag, str) and "}" in tag else str(tag)


def _hex(value: str) -> str | None:
    v = _NAMED.get(value.strip().lower(), value.strip())
    m = _HEX.match(v)
    if not m:
        return None
    h = m.group(1)
    return "#" + ("".join(c * 2 for c in h) if len(h) == 3 else h).lower()


def luminance(colour: str) -> float:
    """Relative luminance 0..1 (WCAG); unknown colours count as mid-grey."""
    h = _hex(colour)
    if h is None:
        return 0.5

    def channel(c: int) -> float:
        x = c / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    r, g, b = (int(h[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def saturation(colour: str) -> float:
    """HSL saturation 0..1; unknown colours count as saturated (kept)."""
    h = _hex(colour)
    if h is None:
        return 1.0
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
    hi, lo = max(r, g, b), min(r, g, b)
    if hi == lo:
        return 0.0
    light = (hi + lo) / 2
    return (hi - lo) / (2 - hi - lo) if light > 0.5 else (hi - lo) / (hi + lo)


def _parse(svg: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    return etree.fromstring(svg.encode(), parser=parser)


def _dump(root: etree._Element) -> str:
    return str(etree.tostring(root, encoding="unicode"))


def _recolour(svg: str, mapping: object, default: str | None) -> str:
    """Apply `mapping(colour) -> colour` to every paint; `default` fills unpainted shapes."""
    root = _parse(svg)
    if default is not None:
        root.set("fill", default if root.get("fill") in (None, "") else mapping(root.get("fill")))  # type: ignore[operator]
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in COLOUR_ATTRS:
            value = el.get(attr)
            if value is None or value.strip().lower() in ("none", "transparent"):
                continue
            el.set(attr, mapping(value))  # type: ignore[operator]
    return _dump(root)


def mono(svg: str, ink: str = MONO_INK) -> str:
    """Everything one ink; white stays white so knocked-out letters remain readable."""

    def to_ink(value: str) -> str:
        h = _hex(value)
        return WHITE if h is not None and luminance(h) > 0.92 else ink

    return _recolour(svg, to_ink, ink)


def reversed_(svg: str) -> str:
    """For dark backgrounds: dark neutral ink turns white; brand colours and gradients stay."""

    def flip(value: str) -> str:
        h = _hex(value)
        if h is None:
            return value  # url(#gradient) and friends: keep
        dark, lum = luminance(h) < DARK_LUMINANCE, luminance(h)
        # Neutral dark ink (black, charcoal, grey) turns white; saturated brand colours stay
        # unless they are nearly black and would vanish on a dark background.
        return WHITE if dark and (saturation(h) < NEUTRAL_SATURATION or lum < 0.03) else h

    return _recolour(svg, flip, WHITE)


def animated(svg: str, uid: str) -> str:
    """Shapes pop in one after another, then the wording rises in. Plays once.

    `uid` scopes the CSS so several animated logos can share one page.
    """
    root = _parse(svg)
    cls = re.sub(r"[^a-z0-9-]", "", f"lg-{uid}".lower())
    shapes = [
        el
        for el in root.iter()
        if isinstance(el.tag, str)
        and _local(el.tag) in SHAPES
        and not any(
            _local(a.tag) in ("defs", "clipPath", "mask", "symbol") for a in el.iterancestors()
        )
    ]
    texts = [el for el in root.iter() if isinstance(el.tag, str) and _local(el.tag) == "text"]
    step = 0.9 / max(1, len(shapes))
    for i, el in enumerate(shapes[:40]):
        el.set("class", f"{cls}-s")
        el.set("style", f"animation-delay:{i * step:.2f}s")
    for i, el in enumerate(texts[:6]):
        el.set("class", f"{cls}-t")
        el.set("style", f"animation-delay:{0.6 + i * 0.15:.2f}s")
    css = (
        f".{cls}-s{{transform-box:fill-box;transform-origin:center;"
        f"animation:{cls}-pop .7s cubic-bezier(.22,1,.36,1) both}}"
        f".{cls}-t{{animation:{cls}-rise .8s cubic-bezier(.22,1,.36,1) both}}"
        f"@keyframes {cls}-pop{{from{{opacity:0;transform:scale(.6)}}to{{opacity:1;transform:none}}}}"
        f"@keyframes {cls}-rise{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:none}}}}"
        f"@media (prefers-reduced-motion:reduce){{.{cls}-s,.{cls}-t{{animation:none}}}}"
    )
    style = etree.Element(f"{{{SVG_NS}}}style")
    style.text = css
    root.insert(0, style)
    return _dump(root)


def square_page(svg: str, size: int, background: str = "transparent") -> str:
    """Trusted wrapper that centres a (sanitised) square mark for PNG rendering."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        f"html,body{{margin:0;background:{background}}}"
        f"body{{width:{size}px;height:{size}px;display:flex;align-items:center;"
        "justify-content:center}"
        f"svg{{width:{int(size * 0.9)}px;height:{int(size * 0.9)}px}}"
        "</style></head><body>" + svg + "</body></html>"
    )


def wide_page(svg: str, size: int, background: str = "transparent") -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        f"html,body{{margin:0;background:{background}}}"
        f"body{{width:{size}px;height:{size // 2}px;display:flex;align-items:center;"
        "justify-content:center}"
        f"svg{{width:{int(size * 0.92)}px;height:{int(size * 0.46)}px}}"
        "</style></head><body>" + svg + "</body></html>"
    )
