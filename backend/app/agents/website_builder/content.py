"""Website Builder — content for the template.

The site copy is written by the LLM from VERIFIED FACTS ONLY (the business's own public site
text, the brief, the brand profile). It must not invent years in business, reviews, awards,
licences, prices or guarantees. For redesigns the page structure mirrors the original site's
navigation (fair before/after), and only the client's own public text is reused — every
reused item is recorded with its source URL. No images are copied from anywhere.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Self
from urllib.parse import urljoin, urlsplit

import phonenumbers
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

Variant = Literal["classic", "bold", "minimal"]
VARIANTS: tuple[Variant, ...] = ("classic", "bold", "minimal")
SECTION_FOR = {
    "service": "services", "what-we-do": "services", "repairs": "services",
    "about": "about", "who-we-are": "about", "our-story": "about", "team": "about",
    "area": "areas", "location": "areas", "where": "areas", "coverage": "areas",
    "contact": "contact", "quote": "contact", "book": "contact", "estimate": "contact",
}  # fmt: skip


class SiteService(BaseModel):
    title: str = Field(max_length=60)
    description: str = Field(max_length=220)


class SiteCopy(BaseModel):
    """Structured output: website copy grounded in the supplied facts."""

    headline: str = Field(max_length=90)
    subheadline: str = Field(max_length=220)
    cta: str = Field(max_length=30)
    services: list[SiteService] = Field(max_length=8)
    about: str = Field(max_length=900)
    areas: list[str] = Field(default_factory=list, max_length=12)
    seo_title: str = Field(max_length=65)
    seo_description: str = Field(max_length=160)
    facts_used: list[str] = Field(
        default_factory=list, description="Short quotes from the facts that the copy relies on."
    )

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self:
        name = str(context.get("name", "Business"))
        trade = str(context.get("trade", "plumbing"))
        city = str(context.get("city", ""))
        return cls(
            headline=f"{trade.title()} you can count on{f' in {city}' if city else ''}",
            subheadline=f"{name} — clear quotes and tidy work.",
            cta="Call now",
            services=[
                SiteService(
                    title=f"{trade.title()} repairs", description="Fixes for everyday problems."
                )
            ],
            about=f"{name} is a local {trade} business.",
            areas=[city] if city else [],
            seo_title=f"{name} | {trade.title()}",
            seo_description=f"{name}: local {trade}.",
        )


COPY_SYSTEM = (
    "You write clear, friendly small-business website copy. Use ONLY the facts provided. Do "
    "NOT invent years in business, numbers of customers, reviews, ratings, awards, licences, "
    "certifications, guarantees, prices or response times. If a fact is not provided, write "
    "around it generically. Write in the requested language."
)


@dataclass(slots=True)
class SiteFacts:
    name: str
    trade: str
    city: str
    country: str
    language: str
    services_hint: list[str]
    phone: str = ""
    email: str = ""
    address: str = ""
    site_text: str = ""
    site_url: str | None = None
    nav: list[tuple[str, str]] = field(default_factory=list)  # (slug, title)


def copy_task(f: SiteFacts) -> str:
    return (
        f"Write the website copy for this business, in language '{f.language}'.\n"
        f"Business name: {f.name}\nTrade: {f.trade}\nTown/city: {f.city or 'unknown'}\n"
        f"Country: {f.country or 'unknown'}\n"
        f"Services the business needs us for: {', '.join(f.services_hint) or 'a website'}\n"
        "The text of their current website (if any) is in the DATA block — take services, "
        "areas and facts about the business only from there. Keep services to what the "
        "business actually offers; 3–6 services. The CTA invites a phone call or a quote."
    )


def find_phone(text: str, country: str | None) -> str:
    region = (country or "US").upper().replace("UK", "GB")
    for match in phonenumbers.PhoneNumberMatcher(text or "", region):
        if phonenumbers.is_valid_number(match.number):
            return phonenumbers.format_number(match.number, phonenumbers.PhoneNumberFormat.NATIONAL)
    return ""


def nav_pages(html: str, base_url: str, limit: int = 5) -> list[tuple[str, str]]:
    """The original site's main internal pages as (slug, link text) — redesign structure."""
    soup = BeautifulSoup(html or "", "lxml")
    root = soup.find("nav") or soup.find("header") or soup
    host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    if not isinstance(root, Tag):
        return out
    for a in root.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        url = urljoin(base_url, str(a["href"]))
        parts = urlsplit(url)
        if (parts.hostname or "").removeprefix("www.") != host:
            continue
        slug = re.sub(r"[^a-z0-9-]", "", parts.path.strip("/").split("/")[0].lower())[:40]
        text = " ".join(a.get_text(" ").split())[:40]
        if not slug or slug in seen or not text or slug in ("index", "home", "wp-admin"):
            continue
        seen.add(slug)
        out.append((slug, text))
        if len(out) >= limit:
            break
    return out


def sections_for(slug: str) -> list[str]:
    for key, section in SECTION_FOR.items():
        if key in slug:
            return [section] if section != "contact" else ["contact"]
    return ["about"]


def pick_variant(lead_id: str, mode: str, colours: list[str]) -> Variant:
    """Redesigns keep a calm 'classic' look; new sites vary deterministically per lead."""
    if mode == "redesign":
        return "classic"
    return VARIANTS[int(hashlib.sha1(lead_id.encode(), usedforsecurity=False).hexdigest(), 16) % 3]


def build_content(
    *,
    lead_id: str,
    mode: Literal["new", "redesign"],
    facts: SiteFacts,
    copy: SiteCopy,
    colours: list[str],
    fonts: list[str],
    logo_svg: str,
    agency: str,
) -> dict[str, Any]:
    primary = colours[0] if colours else "#1d4ed8"
    accent = colours[1] if len(colours) > 1 else "#f59e0b"
    heading = f'"{fonts[0]}", system-ui, sans-serif' if fonts else "system-ui, sans-serif"
    body = f'"{fonts[1]}", system-ui, sans-serif' if len(fonts) > 1 else heading
    pages: list[dict[str, Any]] = [
        {"slug": "", "title": "Home", "sections": ["hero", "services", "about", "areas", "contact"]}
    ]
    if mode == "redesign":
        for slug, title in facts.nav:
            pages.append({"slug": slug, "title": title, "sections": sections_for(slug)})
    notice = {
        "en": "Preview — not a live website",
        "de": "Vorschau — keine Live-Website",
        "fr": "Aperçu — site non publié",
        "es": "Vista previa — sitio no publicado",
    }.get(facts.language, "Preview — not a live website")
    return {
        "variant": pick_variant(lead_id, mode, colours),
        "language": facts.language,
        "mode": mode,
        "business": {
            "name": facts.name,
            "trade": facts.trade,
            "phone": facts.phone,
            "email": facts.email,
            "address": facts.address,
            "city": facts.city,
        },
        "brand": {
            "primary": primary,
            "accent": accent,
            "ink": "#0f172a",
            "surface": "#f8fafc",
            "fontHeading": heading,
            "fontBody": body,
            "logoSvg": logo_svg,
        },
        "seo": {"title": copy.seo_title, "description": copy.seo_description},
        "hero": {"headline": copy.headline, "subheadline": copy.subheadline, "cta": copy.cta},
        "services": [s.model_dump() for s in copy.services],
        "about": copy.about,
        "areas": copy.areas,
        "pages": pages,
        "preview": {"agency": agency, "notice": notice},
    }
