"""Platform access rules enforced in code.

Domains listed here are never fetched directly; their content only arrives through official
APIs, alert emails or feeds.
"""

from __future__ import annotations

from typing import Final

FORBIDDEN_FETCH_DOMAINS: Final[dict[str, str]] = {
    "linkedin.com": "LinkedIn scraping is not allowed (approved APIs only)",
    "google.com": "never scrape Google result pages (use the search API adapter)",
    "google.co.uk": "never scrape Google result pages (use the search API adapter)",
    "google.de": "never scrape Google result pages (use the search API adapter)",
    "google.fr": "never scrape Google result pages (use the search API adapter)",
    "google.es": "never scrape Google result pages (use the search API adapter)",
    "google.ie": "never scrape Google result pages (use the search API adapter)",
    "maps.app.goo.gl": "Google Maps only via the Places API",
    "facebook.com": "Meta content only via the Graph API",
    "instagram.com": "Meta content only via the Graph API",
    "x.com": "X content only via the official API",
    "twitter.com": "X content only via the official API",
    "reddit.com": "Reddit content only via the official Data API",
    "upwork.com": "job boards only via official APIs / alert emails",
    "fiverr.com": "job boards only via official APIs / alert emails",
    "craigslist.org": "classifieds prohibit scraping — alerts only",
    "gumtree.com": "classifieds prohibit scraping — alerts only",
    "facebook.net": "Meta content only via the Graph API",
}


def forbidden_reason(host: str) -> str | None:
    host = host.lower().rstrip(".")
    for domain, reason in FORBIDDEN_FETCH_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return reason
    return None
