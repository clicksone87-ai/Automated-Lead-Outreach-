"""HTML helpers shared by discovery sources (input is untrusted; parse defensively)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import trafilatura
from bs4 import BeautifulSoup

from app.agents.discovery.base import RawBusiness

LOCAL_BUSINESS_TYPES = {
    "localbusiness",
    "homeandconstructionbusiness",
    "plumber",
    "electrician",
    "roofingcontractor",
    "hvacbusiness",
    "locksmith",
    "housepainter",
    "generalcontractor",
    "movingcompany",
    "professionalservice",
    "drycleaningorlaundry",
}


def main_text(html: str, url: str | None = None) -> str:
    extracted = trafilatura.extract(html, url=url, include_comments=True, favor_recall=True)
    if extracted:
        return extracted
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def page_title(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    return soup.title.get_text(strip=True)[:300] if soup.title else None


def _iter_jsonld(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict[str, Any]] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                out.append(node)
                for key in ("@graph", "itemListElement", "item"):
                    child = node.get(key)
                    if isinstance(child, list):
                        stack.extend(child)
                    elif isinstance(child, dict):
                        stack.append(child)
            elif isinstance(node, list):
                stack.extend(node)
    return out


def _types(node: dict[str, Any]) -> set[str]:
    t = node.get("@type", [])
    values = t if isinstance(t, list) else [t]
    return {str(v).lower() for v in values}


def _address(node: dict[str, Any]) -> str | None:
    addr = node.get("address")
    if isinstance(addr, str):
        return addr
    if isinstance(addr, dict):
        parts = [
            addr.get(k)
            for k in (
                "streetAddress",
                "addressLocality",
                "addressRegion",
                "postalCode",
                "addressCountry",
            )
        ]
        flat = [p.get("name") if isinstance(p, dict) else p for p in parts]
        return ", ".join(str(p) for p in flat if p) or None
    return None


def jsonld_businesses(html: str, base_url: str) -> list[RawBusiness]:
    found: list[RawBusiness] = []
    for node in _iter_jsonld(html):
        if not (_types(node) & LOCAL_BUSINESS_TYPES):
            continue
        name = node.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        url = node.get("url") if isinstance(node.get("url"), str) else None
        website = urljoin(base_url, url) if url else None
        if website and website.rstrip("/") == base_url.rstrip("/"):
            website = None  # the directory page itself, not the business site
        country = None
        addr = node.get("address")
        if isinstance(addr, dict):
            c = addr.get("addressCountry")
            country = c.get("name") if isinstance(c, dict) else c
        found.append(
            RawBusiness(
                name=name.strip()[:300],
                website=website,
                phone=node.get("telephone") if isinstance(node.get("telephone"), str) else None,
                email=None,  # contact details are found by Contact Enrichment with source URLs
                address=_address(node),
                country_hint=country if isinstance(country, str) and len(country) == 2 else None,
                has_website=bool(website),
            )
        )
    return found
