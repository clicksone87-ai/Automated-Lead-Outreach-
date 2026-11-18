"""Source families: where a lead was found, grouped for the dashboard and reports."""

from __future__ import annotations

SOCIAL = "social"
GOOGLE = "google"
REGISTRY = "registry"
OTHER = "other"

SOURCE_FAMILY: dict[str, str] = {
    "social_reddit": SOCIAL,
    "social_x": SOCIAL,
    "social_instagram": SOCIAL,
    "social_facebook": SOCIAL,
    "reddit": SOCIAL,
    "x": SOCIAL,
    "meta": SOCIAL,
    "search_api": GOOGLE,
    "local_sites": GOOGLE,
    "site_health": GOOGLE,
    "companies_house": REGISTRY,
    "opencorporates": REGISTRY,
}


def family_for(source: str) -> str:
    return SOURCE_FAMILY.get(source, OTHER)
