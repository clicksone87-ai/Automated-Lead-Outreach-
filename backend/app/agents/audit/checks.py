"""Deterministic audit checks on fetched HTML + headers (pure functions, no network).

Each check returns Finding drafts; the agent attaches an evidence id before they are stored.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

Severity = Literal["critical", "high", "medium", "low"]
SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(slots=True)
class FindingDraft:
    category: str  # seo | security | performance | accessibility | design | content | links | local
    severity: Severity
    title: str
    detail: str
    evidence_key: (
        str  # which captured evidence proves it: "html", "headers", "screenshot_mobile", …
    )
    metric: dict[str, Any] = field(default_factory=dict)
    check: str = ""


@dataclass(slots=True)
class PageFacts:
    """What the SEO checks need, extracted once from the HTML."""

    title: str | None
    meta_description: str | None
    h1: list[str]
    canonical: str | None
    lang: str | None
    viewport: bool
    robots_meta: str | None
    jsonld_types: list[str]
    og_title: bool
    images_total: int
    images_missing_alt: int
    links: list[str]
    http_resources: list[str]
    word_count: int
    has_tel_or_mailto: bool
    cta_candidates: list[str]


def _attr(el: Any, name: str) -> str:
    """BeautifulSoup attribute as a plain string (multi-valued attributes are joined)."""
    value = el.get(name) if el is not None else None
    if value is None:
        return ""
    return " ".join(value) if isinstance(value, list) else str(value)


CTA_RE = re.compile(
    r"\b(call|contact|get (a )?quote|book|request|schedule|free estimate|enquire|inquire|"
    r"get in touch|order|buy|sign up)\b",
    re.I,
)


def page_facts(html: str, base_url: str) -> PageFacts:
    soup = BeautifulSoup(html, "lxml")
    meta = {
        (_attr(m, "name") or _attr(m, "property")).lower(): _attr(m, "content")
        for m in soup.find_all("meta")
    }
    jsonld_types: list[str] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict):
                t = node.get("@type")
                jsonld_types += [str(x) for x in (t if isinstance(t, list) else [t]) if x]
    canonical = soup.find("link", rel=lambda v: bool(v) and "canonical" in v)
    images = soup.find_all("img")
    links = [urljoin(base_url, _attr(a, "href")) for a in soup.find_all("a", href=True)]
    http_resources: list[str] = []
    if base_url.startswith("https://"):
        for tag_name, attr in (
            ("img", "src"),
            ("script", "src"),
            ("link", "href"),
            ("iframe", "src"),
        ):
            for el in soup.find_all(tag_name):
                src = _attr(el, attr)
                if src.startswith("http://"):
                    http_resources.append(src)
    buttons = [b.get_text(" ", strip=True) for b in soup.find_all(["a", "button"])]
    text = soup.get_text(" ", strip=True)
    return PageFacts(
        title=soup.title.get_text(strip=True) if soup.title else None,
        meta_description=meta.get("description") or None,
        h1=[h.get_text(" ", strip=True) for h in soup.find_all("h1")],
        canonical=_attr(canonical, "href") or None,
        lang=_attr(soup.html, "lang") or None,
        viewport="viewport" in meta,
        robots_meta=meta.get("robots") or None,
        jsonld_types=jsonld_types,
        og_title="og:title" in meta,
        images_total=len(images),
        images_missing_alt=sum(1 for i in images if not _attr(i, "alt").strip()),
        links=links,
        http_resources=http_resources[:20],
        word_count=len(text.split()),
        has_tel_or_mailto=any(link.startswith(("tel:", "mailto:")) for link in links),
        cta_candidates=[b for b in buttons if b and CTA_RE.search(b)][:10],
    )


def seo_findings(
    f: PageFacts, *, x_robots: str | None, robots_ok: bool | None, sitemap_ok: bool | None
) -> list[FindingDraft]:
    out: list[FindingDraft] = []

    def add(sev: Severity, title: str, detail: str, check: str, **metric: Any) -> None:
        out.append(FindingDraft("seo", sev, title, detail, "html", metric, check))

    if not f.title:
        add(
            "high",
            "Missing page title",
            "The homepage has no <title>, which search results use as the headline.",
            "title_missing",
        )
    elif not 10 <= len(f.title) <= 65:
        add(
            "medium",
            "Page title length is off",
            f"Title is {len(f.title)} characters; 10–65 displays best in search results.",
            "title_length",
            length=len(f.title),
            page_title=f.title,
        )
    if not f.meta_description:
        add(
            "medium",
            "Missing meta description",
            "Search engines will pick arbitrary text for the result snippet.",
            "meta_description_missing",
        )
    elif not 50 <= len(f.meta_description) <= 160:
        add(
            "low",
            "Meta description length is off",
            f"Description is {len(f.meta_description)} characters; 50–160 is recommended.",
            "meta_description_length",
            length=len(f.meta_description),
        )
    if len(f.h1) == 0:
        add(
            "medium",
            "No H1 heading",
            "The page has no main heading for search engines and screen readers.",
            "h1_missing",
        )
    elif len(f.h1) > 1:
        add(
            "low",
            "Multiple H1 headings",
            f"{len(f.h1)} H1 headings found; one clear H1 is recommended.",
            "h1_multiple",
            count=len(f.h1),
        )
    if not f.canonical:
        add(
            "low",
            "No canonical URL",
            "Without rel=canonical, duplicate URLs can split ranking signals.",
            "canonical_missing",
        )
    if not f.lang:
        add("low", "No language declared", "The <html> tag has no lang attribute.", "lang_missing")
    if not f.viewport:
        add(
            "high",
            "Not set up for mobile",
            "No viewport meta tag: phones render the desktop layout zoomed out.",
            "viewport_missing",
        )
    if not f.jsonld_types:
        add(
            "medium",
            "No structured data",
            "No schema.org data (e.g. LocalBusiness), so rich results and local signals are weaker.",
            "structured_data_missing",
        )
    noindex = "noindex" in (f.robots_meta or "").lower() or "noindex" in (x_robots or "").lower()
    if noindex:
        add(
            "critical",
            "Homepage blocked from Google",
            "A noindex directive tells search engines not to list this page.",
            "noindex",
        )
    if robots_ok is False:
        add(
            "high",
            "robots.txt blocks crawling",
            "robots.txt disallows the whole site for search engines.",
            "robots_block",
        )
    if sitemap_ok is False:
        add("low", "No sitemap.xml", "No sitemap was found at /sitemap.xml.", "sitemap_missing")
    if f.images_total and f.images_missing_alt / f.images_total > 0.3:
        add(
            "low",
            "Images missing alt text",
            f"{f.images_missing_alt} of {f.images_total} images have no alt text.",
            "img_alt",
            missing=f.images_missing_alt,
            total=f.images_total,
        )
    return out


SECURITY_HEADERS: dict[str, tuple[Severity, str, str]] = {
    "strict-transport-security": (
        "medium",
        "No HSTS header",
        "Browsers aren't told to always use HTTPS.",
    ),
    "content-security-policy": (
        "low",
        "No Content-Security-Policy",
        "No CSP header to limit injected scripts.",
    ),
    "x-content-type-options": ("low", "No X-Content-Type-Options", "Missing nosniff protection."),
    "referrer-policy": (
        "low",
        "No Referrer-Policy",
        "Visitors' full URLs may leak to other sites.",
    ),
}


def security_findings(
    *,
    requested_url: str,
    final_url: str,
    headers: dict[str, str],
    http_resources: list[str],
    http_redirects_to_https: bool | None,
) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    if not final_url.startswith("https://"):
        out.append(
            FindingDraft(
                "security",
                "critical",
                "Site doesn't use HTTPS",
                "Browsers mark the site 'Not secure' and forms send data unencrypted.",
                "headers",
                {"final_url": final_url},
                "https_missing",
            )
        )
    elif http_redirects_to_https is False:
        out.append(
            FindingDraft(
                "security",
                "medium",
                "HTTP doesn't redirect to HTTPS",
                "The http:// address still serves the site instead of redirecting.",
                "headers",
                {"requested_url": requested_url},
                "https_redirect",
            )
        )
    if final_url.startswith("https://"):
        for name, (sev, title, detail) in SECURITY_HEADERS.items():
            if name not in headers:
                out.append(
                    FindingDraft(
                        "security",
                        sev,
                        title,
                        detail,
                        "headers",
                        {"header": name},
                        f"header_{name}",
                    )
                )
    has_frame_protection = "x-frame-options" in headers or "frame-ancestors" in headers.get(
        "content-security-policy", ""
    )
    if final_url.startswith("https://") and not has_frame_protection:
        out.append(
            FindingDraft(
                "security",
                "low",
                "No clickjacking protection",
                "Neither X-Frame-Options nor CSP frame-ancestors is set.",
                "headers",
                {},
                "frame_protection",
            )
        )
    if http_resources:
        out.append(
            FindingDraft(
                "security",
                "medium",
                "Mixed content",
                f"{len(http_resources)} resource(s) load over insecure http:// on an https page.",
                "html",
                {"examples": http_resources[:5]},
                "mixed_content",
            )
        )
    return out


def content_findings(f: PageFacts) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    if not f.cta_candidates:
        out.append(
            FindingDraft(
                "content",
                "high",
                "No clear call to action",
                "No visible 'call', 'get a quote' or 'book' button/link on the homepage.",
                "html",
                {},
                "cta_missing",
            )
        )
    if not f.has_tel_or_mailto:
        out.append(
            FindingDraft(
                "content",
                "medium",
                "No tap-to-call or email link",
                "Mobile visitors can't call or email with one tap.",
                "html",
                {},
                "tel_mailto_missing",
            )
        )
    if f.word_count < 120:
        out.append(
            FindingDraft(
                "content",
                "medium",
                "Very little text on the homepage",
                f"Only ~{f.word_count} words; search engines and visitors get little to go on.",
                "html",
                {"words": f.word_count},
                "thin_content",
            )
        )
    return out


def link_targets(f: PageFacts, base_url: str, limit: int = 40) -> list[str]:
    host = urlsplit(base_url).hostname
    seen: list[str] = []
    for link in f.links:
        parts = urlsplit(link)
        if parts.scheme not in ("http", "https") or link.split("#")[0] in seen:
            continue
        seen.append(link.split("#")[0])
    internal = [u for u in seen if urlsplit(u).hostname == host]
    external = [u for u in seen if urlsplit(u).hostname != host]
    return (internal + external)[:limit]


def performance_findings(scores: dict[str, float], metrics: dict[str, float]) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    perf = scores.get("performance")
    if perf is not None and perf < 50:
        out.append(
            FindingDraft(
                "performance",
                "high",
                "Slow on mobile",
                f"Mobile performance score {perf:.0f}/100.",
                "metric_pagespeed",
                {"performance": perf},
                "perf_low",
            )
        )
    elif perf is not None and perf < 80:
        out.append(
            FindingDraft(
                "performance",
                "medium",
                "Could load faster",
                f"Mobile performance score {perf:.0f}/100.",
                "metric_pagespeed",
                {"performance": perf},
                "perf_medium",
            )
        )
    lcp = metrics.get("lcp_ms")
    if lcp and lcp > 4000:
        out.append(
            FindingDraft(
                "performance",
                "high",
                "Main content appears late",
                f"Largest Contentful Paint {lcp / 1000:.1f}s (good is under 2.5s).",
                "metric_pagespeed",
                {"lcp_ms": lcp},
                "lcp",
            )
        )
    cls = metrics.get("cls")
    if cls and cls > 0.25:
        out.append(
            FindingDraft(
                "performance",
                "medium",
                "Layout jumps while loading",
                f"Cumulative Layout Shift {cls:.2f} (good is under 0.1).",
                "metric_pagespeed",
                {"cls": cls},
                "cls",
            )
        )
    a11y = scores.get("accessibility")
    if a11y is not None and a11y < 70:
        out.append(
            FindingDraft(
                "accessibility",
                "medium",
                "Accessibility problems",
                f"Lighthouse accessibility score {a11y:.0f}/100.",
                "metric_pagespeed",
                {"accessibility": a11y},
                "a11y_score",
            )
        )
    return out


def axe_findings(violations: list[dict[str, Any]]) -> list[FindingDraft]:
    impact_map: dict[str, Severity] = {
        "critical": "high",
        "serious": "medium",
        "moderate": "low",
        "minor": "low",
    }
    out = []
    for v in violations[:8]:
        out.append(
            FindingDraft(
                "accessibility",
                impact_map.get(str(v.get("impact")), "low"),
                str(v.get("help") or v.get("id")),
                f"{len(v.get('nodes', []))} element(s) affected ({v.get('id')}).",
                "axe",
                {"rule": v.get("id"), "nodes": len(v.get("nodes", []))},
                f"axe_{v.get('id')}",
            )
        )
    return out
