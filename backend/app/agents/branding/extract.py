"""Brand extraction from a site's HTML and CSS.

Pure functions over already-fetched text, so they are testable with fixtures and never touch
the network themselves. Every value carries where it was found (`source`) so the brand
profile can point back at evidence.
"""

from __future__ import annotations

import colorsys
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

MAX_BRAND_COLORS = 5
MAX_FONTS = 3
MAX_STYLESHEETS = 5

_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})(?![0-9a-fA-F])")
_RGB = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:[\s,/]+([\d.]+%?))?\s*\)")
_DECL = re.compile(r"(--[\w-]+|[a-z-]+)\s*:\s*([^;{}]+)")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_FONT_FACE = re.compile(r"@font-face\s*\{[^}]*font-family\s*:\s*([^;}]+)", re.I)
_BRAND_VAR = re.compile(r"--[\w-]*(primary|brand|accent|secondary|main|theme)[\w-]*", re.I)
_PROMINENT_SELECTOR = re.compile(
    r"(header|nav|navbar|btn|button|cta|hero|banner|primary|brand|accent|logo|\ba\b)", re.I
)
_COLOR_PROPS = {"color", "background", "background-color", "border-color", "fill", "stroke"}

GENERIC_FONTS = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-sans-serif",
    "ui-serif",
    "ui-monospace",
    "-apple-system",
    "blinkmacsystemfont",
    "segoe ui",
    "inherit",
    "initial",
    "unset",
    "emoji",
    "math",
    "apple color emoji",
    "segoe ui emoji",
    "segoe ui symbol",
    "noto color emoji",
}
ICON_FONTS = re.compile(r"(awesome|icon|glyph|material symbols|dashicons|eicons)", re.I)

SOCIAL_HOSTS = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "linkedin.com": "linkedin",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "pinterest.com": "pinterest",
    "yelp.com": "yelp",
}
_SOCIAL_SKIP_PATHS = re.compile(r"/(sharer|share|intent|dialog|plugins)(/|\.php|$)", re.I)


@dataclass(slots=True)
class ColorHit:
    hex: str
    weight: float
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"hex": self.hex, "weight": round(self.weight, 2), "sources": self.sources[:3]}


@dataclass(slots=True)
class BrandExtraction:
    brand_name: str | None
    colors: list[dict[str, Any]]
    neutrals: list[str]
    fonts: list[dict[str, Any]]
    logo_candidates: list[dict[str, Any]]
    social_profiles: list[dict[str, str]]
    stylesheets: list[str]


# ─── colours ────────────────────────────────────────────────────────────────


def normalize_hex(value: str) -> str:
    v = value.lower().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    return f"#{v}"


def _rgb_hex(r: int, g: int, b: int) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in (r, g, b)))


def colors_in(value: str) -> list[str]:
    out = [normalize_hex(m) for m in _HEX.findall(value)]
    for m in _RGB.finditer(value):
        alpha = m.group(4)
        if alpha is not None:
            a = float(alpha.rstrip("%")) / (100 if alpha.endswith("%") else 1)
            if a < 0.5:  # mostly transparent overlays say nothing about the brand
                continue
        out.append(_rgb_hex(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return out


def is_neutral(hex_color: str) -> bool:
    """White, black and greys (low saturation or extreme lightness) are not brand colours."""
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    _, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    return saturation < 0.15 or lightness > 0.94 or lightness < 0.08


def _distance(a: str, b: str) -> float:
    ra, ga, ba = (int(a[i : i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i : i + 2], 16) for i in (1, 3, 5))
    return float(((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5)


def _iter_declarations(css: str) -> list[tuple[str, str, str]]:
    """(selector, property, value) for every declaration; selector is '' for inline styles."""
    out: list[tuple[str, str, str]] = []
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for rule in _RULE.finditer(css):
        selector = rule.group(1).strip().split("@")[-1]
        for decl in _DECL.finditer(rule.group(2)):
            out.append((selector, decl.group(1).strip().lower(), decl.group(2).strip()))
    return out


def extract_colors(
    soup: BeautifulSoup, css_blocks: list[tuple[str, str]]
) -> tuple[list[ColorHit], list[str]]:
    """Rank brand colours. `css_blocks` is [(source, css_text)]. Returns (brand, neutrals)."""
    hits: dict[str, ColorHit] = {}
    neutrals: Counter[str] = Counter()

    def add(hex_color: str, weight: float, source: str) -> None:
        if is_neutral(hex_color):
            neutrals[hex_color] += 1
            return
        hit = hits.setdefault(hex_color, ColorHit(hex_color, 0.0))
        hit.weight += weight
        if source not in hit.sources:
            hit.sources.append(source)

    theme = soup.find("meta", attrs={"name": "theme-color"})
    if isinstance(theme, Tag) and isinstance(theme.get("content"), str):
        for c in colors_in(str(theme["content"])):
            add(c, 8.0, "meta:theme-color")

    blocks = list(css_blocks)
    for el in soup.find_all(style=True):
        if isinstance(el, Tag):
            blocks.append((f"inline:{el.name}", f"{el.name}{{{el['style']}}}"))

    for source, css in blocks:
        for selector, prop, value in _iter_declarations(css):
            if prop.startswith("--"):
                weight = 4.0 if _BRAND_VAR.fullmatch(prop) else 0.5
            elif prop in _COLOR_PROPS or prop.endswith("-color"):
                weight = 2.0 if _PROMINENT_SELECTOR.search(selector) else 1.0
                if prop.startswith("background"):
                    weight *= 1.25
            else:
                continue
            for c in colors_in(value):
                add(c, weight, source)

    # Merge near-duplicates (e.g. a button colour and its hover shade).
    ranked = sorted(hits.values(), key=lambda h: h.weight, reverse=True)
    merged: list[ColorHit] = []
    for hit in ranked:
        twin = next((m for m in merged if _distance(m.hex, hit.hex) < 28), None)
        if twin:
            twin.weight += hit.weight
            twin.sources.extend(s for s in hit.sources if s not in twin.sources)
        else:
            merged.append(hit)
    merged.sort(key=lambda h: h.weight, reverse=True)
    return merged[:MAX_BRAND_COLORS], [c for c, _ in neutrals.most_common(3)]


# ─── fonts ──────────────────────────────────────────────────────────────────


def _first_family(value: str) -> str | None:
    for part in value.split(","):
        name = part.strip().strip("'\"").strip()
        if name.startswith("var("):
            return None
        if name and name.lower() not in GENERIC_FONTS and not ICON_FONTS.search(name):
            return name
    return None


_FONT_SIZE = re.compile(
    r"(?:^|\s)(?:\d*\.?\d+(?:px|pt|em|rem|%|vw|vh|ex|ch)|xx-small|x-small|small|medium|large|"
    r"x-large|xx-large|smaller|larger)(?:\s*/\s*[\w.%-]+)?\s+(.+)$",
    re.I,
)


def _shorthand_family(value: str) -> str:
    """`font: italic 700 16px/1.4 "Helvetica Neue", Arial` → the family list after the size."""
    m = _FONT_SIZE.search(value)
    return m.group(1) if m else ""


def extract_fonts(
    soup: BeautifulSoup, css_blocks: list[tuple[str, str]], base_url: str
) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    sources: dict[str, str] = {}
    display = {"heading": Counter[str](), "body": Counter[str]()}

    def add(name: str, weight: int, source: str, role: str | None = None) -> None:
        key = name.strip()
        if not key or ICON_FONTS.search(key):
            return
        counts[key] += weight
        sources.setdefault(key, source)
        if role:
            display[role][key] += weight

    for link in soup.find_all("link", href=True):
        if not isinstance(link, Tag):
            continue
        href = urljoin(base_url, str(link["href"]))
        parts = urlsplit(href)
        if parts.hostname in ("fonts.googleapis.com", "fonts.bunny.net"):
            for families in parse_qs(parts.query).get("family", []):
                for name in families.split("|"):
                    add(name.split(":")[0].replace("+", " "), 3, "google-fonts")

    for source, css in css_blocks:
        for m in _FONT_FACE.finditer(css):
            fam = _first_family(m.group(1))
            if fam:
                add(fam, 2, source)
        for selector, prop, value in _iter_declarations(css):
            if prop not in ("font-family", "font"):
                continue
            fam = _first_family(value if prop == "font-family" else _shorthand_family(value))
            if not fam:
                continue
            role = (
                "heading"
                if re.search(r"\bh[1-3]\b|title|heading|display", selector, re.I)
                else ("body" if re.search(r"\b(body|html|p)\b", selector, re.I) else None)
            )
            add(fam, 1, source, role)

    out: list[dict[str, Any]] = []
    for name, _ in counts.most_common(MAX_FONTS):
        role = (
            "heading"
            if display["heading"][name] > display["body"][name]
            else ("body" if display["body"][name] else "unknown")
        )
        out.append({"family": name, "role": role, "source": sources[name]})
    return out


# ─── logo, social, name ─────────────────────────────────────────────────────


def _attrs_text(el: Tag) -> str:
    bits = [
        str(el.get("alt", "")),
        str(el.get("src", "")),
        " ".join(el.get("class") or []),
        str(el.get("id", "")),
    ]
    return " ".join(bits).lower()


def find_logo_candidates(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Ranked logo image URLs. Score: explicit 'logo' markers > header home link > icons."""
    found: dict[str, dict[str, Any]] = {}

    def add(src: str | None, score: int, how: str) -> None:
        if not src or src.startswith("data:"):
            return
        url = urljoin(base_url, src.strip())
        if urlsplit(url).scheme not in ("http", "https"):
            return
        prev = found.get(url)
        if prev is None or prev["score"] < score:
            found[url] = {"url": url, "score": score, "how": how}

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text() or "null")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict):
                logo = item.get("logo")
                if isinstance(logo, dict):
                    logo = logo.get("url")
                if isinstance(logo, str):
                    add(logo, 95, "json-ld")

    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = img.get("src") or img.get("data-src")
        if not isinstance(src, str):
            continue
        text = _attrs_text(img)
        parent_link = img.find_parent("a")
        in_header = img.find_parent(["header", "nav"]) is not None
        home_link = isinstance(parent_link, Tag) and str(parent_link.get("href", "")).rstrip(
            "/"
        ) in ("", urlsplit(base_url).path.rstrip("/"), base_url.rstrip("/"))
        if "logo" in text:
            add(src, 90 if in_header else 80, "img[logo]")
        elif in_header and home_link:
            add(src, 70, "header home link")

    for svg in soup.select("header svg, a[class*=logo] svg, [class*=logo] svg"):
        # Inline SVG logos have no URL; noted so the agent can store the markup instead.
        if isinstance(svg, Tag) and "inline-svg" not in found:
            found["inline-svg"] = {"url": None, "score": 60, "how": "inline svg", "svg": str(svg)}

    for link in soup.find_all("link", rel=True, href=True):
        if not isinstance(link, Tag):
            continue
        rel = " ".join(link.get("rel") or []).lower()
        if "apple-touch-icon" in rel:
            add(str(link["href"]), 40, "apple-touch-icon")
        elif "icon" in rel:
            add(str(link["href"]), 20, "favicon")

    og = soup.find("meta", property="og:image")
    if isinstance(og, Tag) and isinstance(og.get("content"), str):
        add(str(og["content"]), 15, "og:image")

    return sorted(found.values(), key=lambda c: c["score"], reverse=True)[:5]


def find_social_profiles(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    """Links to the business's own social profiles (recorded only — never scraped here)."""
    out: dict[str, dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        url = urljoin(base_url, str(a["href"]).strip())
        parts = urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
        network = SOCIAL_HOSTS.get(host)
        if not network or _SOCIAL_SKIP_PATHS.search(parts.path) or parts.path in ("", "/"):
            continue
        out.setdefault(network, {"network": network, "url": url})
    return list(out.values())


def _domain_overlap(candidate: str, label: str) -> float:
    """Longest common substring of the compacted candidate and the domain label, as a share
    of the label (hubcityplumbing vs "Hub City Plumbing and Mechanical" → 1.0)."""
    a = re.sub(r"[^a-z0-9]", "", candidate.lower())
    if not a or not label:
        return 0.0
    best = 0
    for i in range(len(label)):
        for j in range(i + best + 1, len(label) + 1):
            if label[i:j] in a:
                best = j - i
            else:
                break
    return best / len(label)


def find_brand_name(soup: BeautifulSoup, domain: str | None = None) -> str | None:
    site_name = soup.find("meta", property="og:site_name")
    if isinstance(site_name, Tag) and str(site_name.get("content", "")).strip():
        return str(site_name["content"]).strip()
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text() or "null")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if (
                isinstance(item, dict)
                and str(item.get("@type", "")).endswith(("Organization", "Business"))
                and isinstance(item.get("name"), str)
            ):
                return str(item["name"]).strip()
    title = soup.title.get_text(strip=True) if soup.title else ""
    if title:
        # "Home | Smith Plumbing" / "Smith Plumbing – Emergency plumbers"
        parts = [p.strip() for p in re.split(r"\s[|–—:-]\s", title) if p.strip()]
        parts = [p for p in parts if p.lower() not in ("home", "homepage", "welcome")]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        label = (domain or "").split(".")[0].replace("-", "")
        if label:
            scored = sorted(parts, key=lambda p: (-_domain_overlap(p, label), len(p)))
            if _domain_overlap(scored[0], label) >= 0.5:
                return scored[0]
        return min(parts, key=len)
    return None


def stylesheet_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Same-site stylesheets first (brand CSS), then third-party, capped."""
    site = (urlsplit(base_url).hostname or "").removeprefix("www.")
    own: list[str] = []
    other: list[str] = []
    for link in soup.find_all("link", href=True):
        if not isinstance(link, Tag):
            continue
        rel = " ".join(link.get("rel") or []).lower()
        if "stylesheet" not in rel:
            continue
        url = urljoin(base_url, str(link["href"]))
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        if host in ("fonts.googleapis.com", "fonts.bunny.net"):
            continue  # parsed as font hints, not fetched
        (own if host == site or host.endswith("." + site) else other).append(url)
    return (own + other)[:MAX_STYLESHEETS]


def extract(html: str, base_url: str, external_css: list[tuple[str, str]]) -> BrandExtraction:
    soup = BeautifulSoup(html, "lxml")
    css_blocks = [
        ("inline:<style>", tag.get_text()) for tag in soup.find_all("style") if isinstance(tag, Tag)
    ] + external_css
    colors, neutrals = extract_colors(soup, css_blocks)
    return BrandExtraction(
        brand_name=find_brand_name(
            soup, (urlsplit(base_url).hostname or "").removeprefix("www.") or None
        ),
        colors=[c.as_dict() for c in colors],
        neutrals=neutrals,
        fonts=extract_fonts(soup, css_blocks, base_url),
        logo_candidates=find_logo_candidates(soup, base_url),
        social_profiles=find_social_profiles(soup, base_url),
        stylesheets=stylesheet_urls(soup, base_url),
    )
