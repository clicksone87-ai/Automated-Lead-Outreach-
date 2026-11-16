"""Duplicate matching — one business = one record across all sources.

Match order (first hit wins):
  1. website domain (registrable domain, exact)
  2. registry source + number (exact)
  3. verified contact detail (email / phone, exact)
  4. normalized name similarity (pg_trgm) within the same country
Resolution is serialized per normalized name with an advisory lock so concurrent discovery
jobs cannot create the same business twice.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass
from urllib.parse import urlsplit

import tldextract
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Business, Contact

NAME_SIMILARITY_THRESHOLD = 0.62

tld_extract = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)

# Shared site builders / reserved test domains: each subdomain is a different business, so the
# full host (not the registrable domain) is the identity. Private PSL suffixes (github.io,
# netlify.app, …) are already handled by include_psl_private_domains.
SHARED_HOST_DOMAINS = frozenset(
    {
        "wixsite.com",
        "weebly.com",
        "wordpress.com",
        "blogspot.com",
        "business.site",
        "godaddysites.com",
        "square.site",
        "webflow.io",
        "carrd.co",
        "jimdosite.com",
        "site123.me",
        "example.com",
        "example.org",
        "example.net",
    }
)

# Hosts that are platforms, not a business's own website.
PLATFORM_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
        "youtube.com",
        "google.com",
        "goo.gl",
        "yelp.com",
        "yell.com",
        "reddit.com",
        "upwork.com",
        "fiverr.com",
        "freelancer.com",
        "peopleperhour.com",
        "etsy.com",
        "amazon.com",
        "linktr.ee",
        "nextdoor.com",
        "checkatrade.com",
        "trustpilot.com",
        "bbb.org",
        "thumbtack.com",
        "angi.com",
        "houzz.com",
        "producthunt.com",
        "quora.com",
        "indiehackers.com",
        "medium.com",
        "craigslist.org",
        "gumtree.com",
    }
)

LEGAL_SUFFIXES = (
    "limited",
    "ltd",
    "llc",
    "l l c",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "plc",
    "llp",
    "lp",
    "gmbh",
    "ug",
    "ag",
    "kg",
    "ohg",
    "e k",
    "ek",
    "sarl",
    "sas",
    "sa",
    "sl",
    "slu",
    "srl",
    "spa",
    "bv",
    "nv",
    "as",
    "asa",
    "ab",
    "oy",
    "aps",
    "teo",
    "dac",
    "clg",
    "the",
)
_suffix_re = re.compile(r"\b(" + "|".join(re.escape(s) for s in LEGAL_SUFFIXES) + r")\b")


def normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_only.casefold().replace("&", " and ")
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    without_suffix = _suffix_re.sub(" ", cleaned)
    return re.sub(r"\s+", " ", without_suffix).strip() or cleaned.strip()


def normalize_domain(url_or_host: str | None) -> str | None:
    """Registrable domain of a business website, or None for platforms/invalid input."""
    if not url_or_host:
        return None
    raw = url_or_host.strip()
    host = urlsplit(raw if "://" in raw else f"http://{raw}").hostname
    if not host:
        return None
    ext = tld_extract(host)
    if not ext.domain or not ext.suffix:
        return None
    registrable = f"{ext.domain}.{ext.suffix}".lower()
    if registrable in PLATFORM_DOMAINS or ext.suffix in ("local", "localhost"):
        return None
    if registrable in SHARED_HOST_DOMAINS:
        sub = ext.subdomain.lower().removeprefix("www.").removeprefix("www")
        return f"{sub}.{registrable}" if sub else None
    return registrable


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    return digits if len(digits.lstrip("+")) >= 7 else None


@dataclass(slots=True)
class BusinessCandidate:
    name: str
    domain: str | None = None
    registry_number: str | None = None
    registry_source: str | None = None
    country: str | None = None
    language: str | None = None
    timezone: str | None = None
    industry: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    has_website: bool | None = None
    #: False for identities that must match exactly (e.g. platform handles)
    allow_fuzzy: bool = True


@dataclass(slots=True)
class MatchResult:
    business: Business
    created: bool
    matched_on: str


async def find_match(session: AsyncSession, c: BusinessCandidate) -> tuple[Business, str] | None:
    domain = normalize_domain(c.domain)
    if domain:
        hit = (
            await session.execute(select(Business).where(Business.domain == domain))
        ).scalar_one_or_none()
        if hit:
            return hit, "domain"
    if c.registry_number and c.registry_source:
        hit = (
            await session.execute(
                select(Business).where(
                    Business.registry_source == c.registry_source,
                    Business.registry_number == c.registry_number,
                )
            )
        ).scalar_one_or_none()
        if hit:
            return hit, "registry"
    contact_values = [v for v in (c.email and c.email.lower(), normalize_phone(c.phone)) if v]
    if contact_values:
        hit = (
            await session.execute(
                select(Business)
                .join(Contact)
                .where(func.lower(Contact.value).in_(contact_values))
                .limit(1)
            )
        ).scalar_one_or_none()
        if hit:
            return hit, "contact"
        phone = normalize_phone(c.phone)
        if phone:
            hit = (
                await session.execute(select(Business).where(Business.phone == phone).limit(1))
            ).scalar_one_or_none()
            if hit:
                return hit, "phone"
    norm = normalize_name(c.name)
    if norm and c.allow_fuzzy:
        sim = func.similarity(Business.normalized_name, norm)
        stmt = (
            select(Business, sim.label("sim"))
            .where(Business.normalized_name.op("%")(norm), sim >= NAME_SIMILARITY_THRESHOLD)
            .order_by(sim.desc())
            .limit(1)
        )
        if c.country:
            stmt = stmt.where((Business.country == c.country) | Business.country.is_(None))
        row = (await session.execute(stmt)).first()
        if row:
            return row[0], "name"
    return None


async def resolve_business(session: AsyncSession, c: BusinessCandidate) -> MatchResult:
    """Find the existing business for this candidate or create it; fill missing fields."""
    norm = normalize_name(c.name)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": zlib.crc32(norm.encode())}
    )
    match = await find_match(session, c)
    if match:
        business, matched_on = match
        _merge(business, c)
        await session.flush()
        return MatchResult(business, created=False, matched_on=matched_on)
    business = Business(
        name=c.name.strip()[:300],
        normalized_name=norm[:300],
        domain=normalize_domain(c.domain),
        registry_number=c.registry_number,
        registry_source=c.registry_source,
        country=c.country,
        language=c.language,
        timezone=c.timezone,
        industry=c.industry,
        address=c.address,
        phone=normalize_phone(c.phone),
        has_website=c.has_website
        if c.has_website is not None
        else (True if normalize_domain(c.domain) else None),
    )
    session.add(business)
    await session.flush()
    return MatchResult(business, created=True, matched_on="new")


def _merge(b: Business, c: BusinessCandidate) -> None:
    """Fill gaps only; never overwrite existing values with candidate data."""
    domain = normalize_domain(c.domain)
    if not b.domain and domain:
        b.domain = domain
        b.has_website = True
    for attr in (
        "registry_number",
        "registry_source",
        "country",
        "language",
        "timezone",
        "industry",
        "address",
    ):
        if getattr(b, attr) is None and getattr(c, attr) is not None:
            setattr(b, attr, getattr(c, attr))
    if not b.phone and c.phone:
        b.phone = normalize_phone(c.phone)
    if b.has_website is None and c.has_website is not None:
        b.has_website = c.has_website
