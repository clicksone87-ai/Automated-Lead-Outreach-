"""Registry of discovery sources (each its own module, enable/disable in settings)."""

from __future__ import annotations

from app.agents.discovery.base import DiscoverySource
from app.agents.discovery.proactive_triggers import (
    LocalSitesSource,
    NoWebsiteSource,
    SiteHealthSource,
)
from app.agents.discovery.sources.boards import AlertEmailSource, FreelancerSource
from app.agents.discovery.sources.mock_demo import MockDemoSource
from app.agents.discovery.sources.reddit import RedditSource
from app.agents.discovery.sources.registries import CompaniesHouseSource, OpenCorporatesSource
from app.agents.discovery.sources.rss import RssSource
from app.agents.discovery.sources.scout import ScoutProfilesSource
from app.agents.discovery.sources.search_api import SearchApiSource
from app.agents.discovery.sources.social import MetaSource, XSource
from app.agents.discovery.sources.social_apify import (
    ApifyFacebookSource,
    ApifyInstagramSource,
    ApifyRedditSource,
    ApifyXSource,
)
from app.agents.discovery.sources.social_providers import SocialProvidersSource
from app.agents.discovery.sources.web_directory import WebDirectorySource
from app.config import get_settings

_ALL: list[DiscoverySource] = [
    SearchApiSource(),
    WebDirectorySource(),
    RssSource(),
    CompaniesHouseSource(),
    OpenCorporatesSource(),
    ApifyRedditSource(),
    ApifyXSource(),
    ApifyInstagramSource(),
    ApifyFacebookSource(),
    SocialProvidersSource(),
    # Official platform APIs: kept, but disabled (off in testing mode).
    RedditSource(),
    XSource(),
    MetaSource(),
    FreelancerSource(),
    AlertEmailSource(),
    SiteHealthSource(),
    LocalSitesSource(),
    NoWebsiteSource(),
    ScoutProfilesSource(),
]
# Fixture source for the automated test suite only — never registered in the running system.
if get_settings().mocks_allowed:
    _ALL.append(MockDemoSource())

SOURCES: dict[str, DiscoverySource] = {s.key: s for s in _ALL}

__all__ = ["SOURCES"]
