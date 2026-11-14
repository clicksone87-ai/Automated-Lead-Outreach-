"""Registry of discovery sources (each its own module, enable/disable in settings)."""

from __future__ import annotations

from app.agents.discovery.base import DiscoverySource
from app.agents.discovery.sources.mock_demo import MockDemoSource
from app.agents.discovery.sources.reddit import RedditSource
from app.agents.discovery.sources.registries import (
    BrregSource,
    CompaniesHouseSource,
    OpenCorporatesSource,
)
from app.agents.discovery.sources.rss import RssSource
from app.agents.discovery.sources.search_api import SearchApiSource
from app.agents.discovery.sources.social import MetaSource, XSource
from app.agents.discovery.sources.web_directory import WebDirectorySource

_ALL: list[DiscoverySource] = [
    SearchApiSource(),
    WebDirectorySource(),
    RssSource(),
    CompaniesHouseSource(),
    OpenCorporatesSource(),
    BrregSource(),
    RedditSource(),
    XSource(),
    MetaSource(),
    MockDemoSource(),
]

SOURCES: dict[str, DiscoverySource] = {s.key: s for s in _ALL}

__all__ = ["SOURCES"]
