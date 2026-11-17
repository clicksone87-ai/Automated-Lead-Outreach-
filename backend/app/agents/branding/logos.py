"""Logo concepts designed by the LLM as SVG code.

The SVG is untrusted model output: it is sanitised against a strict allowlist and normalised
before it is rendered to PNG in a network-blocked browser.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal, Self

from lxml import etree
from pydantic import BaseModel, Field

MAX_SVG_BYTES = 60_000
SVG_NS = "http://www.w3.org/2000/svg"

ALLOWED_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text",
    "tspan", "defs", "linearGradient", "radialGradient", "stop", "title", "desc", "clipPath",
    "mask", "use", "symbol",
}  # fmt: skip
ALLOWED_ATTRS = {
    "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "d", "points", "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-opacity", "stroke-dasharray", "opacity",
    "transform", "font-family", "font-size", "font-weight", "font-style", "letter-spacing",
    "text-anchor", "dominant-baseline", "id", "offset", "stop-color", "stop-opacity",
    "gradientUnits", "gradientTransform", "clip-path", "mask", "href", "dx", "dy",
    "preserveAspectRatio", "xmlns", "rotate", "textLength", "lengthAdjust", "clip-rule",
    "paint-order",
}  # fmt: skip
_URL_REF = re.compile(r"url\(\s*['\"]?\s*#[\w-]+\s*['\"]?\s*\)")
_ANY_URL = re.compile(r"url\(", re.I)

STYLES: list[tuple[str, str]] = [
    ("wordmark", "a confident wordmark: the business name set in distinctive custom-feeling "
     "lettering, optionally with one small graphic accent"),
    ("icon_wordmark", "a simple geometric icon that hints at the trade (no clip-art, no "
     "stock symbols) placed beside or above the business name"),
    ("emblem", "a compact badge/emblem (circle, shield or rounded square) containing an "
     "initial or monogram, with the business name beneath"),
]  # fmt: skip


class SvgRejectedError(ValueError):
    pass


class LogoSpec(BaseModel):
    """Structured output of one logo concept."""

    concept_name: str = Field(max_length=60)
    svg: str = Field(description="A complete, self-contained <svg> document.")
    palette: list[str] = Field(description="Hex colours used, most prominent first.", max_length=6)
    font_family: str = Field(description="CSS font family used for the wording (web-safe stack).")
    font_weight: int = Field(ge=100, le=900, default=700)
    text_in_logo: str = Field(description="The exact wording drawn in the logo.")
    rationale: str = Field(max_length=400)

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self:
        name = str(context.get("text", "Acme"))
        colour = str(context.get("colour", "#1d4ed8"))
        svg = (
            f'<svg xmlns="{SVG_NS}" viewBox="0 0 400 160">'
            f'<rect x="10" y="30" width="100" height="100" rx="20" fill="{colour}"/>'
            f'<text x="130" y="95" font-family="Arial, sans-serif" font-size="34" '
            f'font-weight="700" fill="#111827">{name}</text></svg>'
        )
        return cls(
            concept_name=f"{context.get('style', 'wordmark').title()} concept",
            svg=svg,
            palette=[colour, "#111827"],
            font_family="Arial, sans-serif",
            text_in_logo=name,
            rationale="Fixture concept for tests.",
        )


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def sanitize_svg(svg: str) -> str:
    """Return a safe, normalised SVG string or raise SvgRejectedError."""
    raw = svg.strip()
    m = re.search(r"<svg[\s\S]*</svg>", raw, re.I)
    if not m:
        raise SvgRejectedError("no <svg> element")
    raw = m.group(0)
    if len(raw.encode()) > MAX_SVG_BYTES:
        raise SvgRejectedError("svg too large")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True,
                             remove_pis=True, huge_tree=False, load_dtd=False)  # fmt: skip
    try:
        root = etree.fromstring(raw.encode(), parser=parser)
    except etree.XMLSyntaxError as exc:
        raise SvgRejectedError(f"invalid xml: {exc}") from exc
    if _local(root.tag) != "svg":
        raise SvgRejectedError("root is not <svg>")
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            el.getparent().remove(el)
            continue
        tag = _local(el.tag)
        if tag not in ALLOWED_TAGS:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
            continue
        for attr in list(el.attrib):
            name = _local(attr)
            value = el.attrib[attr]
            if name.lower().startswith("on") or name not in ALLOWED_ATTRS:
                del el.attrib[attr]
                continue
            if name == "href" and not value.startswith("#"):
                del el.attrib[attr]
                continue
            if _ANY_URL.search(value) and not _URL_REF.fullmatch(value.strip()):
                del el.attrib[attr]
    if not root.get("viewBox"):
        w = re.sub(r"[^\d.]", "", root.get("width") or "") or "400"
        h = re.sub(r"[^\d.]", "", root.get("height") or "") or "160"
        root.set("viewBox", f"0 0 {w} {h}")
    for attr in ("width", "height"):
        root.attrib.pop(attr, None)
    if not any(_local(e.tag) in {"path", "rect", "circle", "ellipse", "polygon", "text",
                                 "line", "polyline"} for e in root.iter() if isinstance(e.tag, str)):  # fmt: skip
        raise SvgRejectedError("svg draws nothing")
    out: str = etree.tostring(root, encoding="unicode")
    return out


def svg_texts(svg: str) -> list[str]:
    """Wording drawn with <text>/<tspan> (whitespace-normalised)."""
    try:
        root = etree.fromstring(svg.encode())
    except etree.XMLSyntaxError:
        return []
    out = []
    for el in root.iter():
        if isinstance(el.tag, str) and _local(el.tag) == "text":
            t = " ".join("".join(el.itertext()).split())
            if t:
                out.append(t)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def text_matches(svg: str, expected: str) -> Literal["exact", "missing", "mismatch", "paths"]:
    """Does the logo spell `expected` correctly? 'paths' = wording drawn as outlines."""
    texts = svg_texts(svg)
    if not texts:
        return "paths"
    joined = _norm(" ".join(texts))
    target = _norm(expected)
    if target in joined or (joined in target and len(joined) >= len(target) * 0.9):
        return "exact"
    words = set(target.split())
    return "mismatch" if words & set(joined.split()) else "missing"


def render_page(svg: str, size: int) -> str:
    """Trusted wrapper page that centres the (already sanitised) SVG on a transparent canvas."""
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "html,body{margin:0;background:transparent}"
        f"body{{width:{size}px;height:{size // 2}px;display:flex;align-items:center;"
        "justify-content:center}"
        f"svg{{width:{int(size * 0.92)}px;height:{int(size * 0.46)}px}}"
        "</style></head><body>" + svg + "</body></html>"
    )


class LogoPrompt:
    """Builds the design brief sent to the model (trusted text only; site text is not sent)."""

    SYSTEM: ClassVar[str] = (
        "You are a senior brand designer. You design original logos as clean, valid, "
        "self-contained SVG code: no <script>, no <image>, no external fonts or links, no "
        "foreignObject, no CSS @import. Use only basic shapes, paths, gradients and <text> "
        "with a web-safe font stack. Never imitate an existing trademark or famous logo."
    )

    @staticmethod
    def task(
        *,
        text: str,
        language: str,
        industry: str,
        style: str,
        style_desc: str,
        colours: list[str],
        fonts: list[str],
        avoid_colours: list[str],
    ) -> str:
        palette = ", ".join(colours) if colours else "choose a fitting, accessible palette"
        avoid = ", ".join(avoid_colours) if avoid_colours else "none"
        return (
            f"Design logo concept '{style}' for a {industry.replace('_', ' ')} business.\n"
            f"Style: {style_desc}.\n"
            f"The logo must contain exactly this wording, spelled exactly, as SVG <text>: "
            f'"{text}". Any extra tagline must be in language "{language}".\n'
            f"Existing brand colours to respect: {palette}. "
            f"Existing fonts on their website: {', '.join(fonts) or 'unknown'}.\n"
            f"Avoid looking like competitors whose main colours are: {avoid}.\n"
            "Canvas: viewBox 0 0 400 160 (wide). Keep it simple enough to work at 64px wide, "
            "with strong contrast on a white background. Return the SVG and the metadata."
        )
