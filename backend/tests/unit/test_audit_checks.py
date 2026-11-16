"""Audit checks, evidence binding and the browser SSRF guard (no network)."""

from __future__ import annotations

import pytest

from app.agents.audit import checks
from app.agents.audit.browser import GuardedBrowser
from app.agents.audit.report import (
    MissingEvidenceError,
    before_after,
    bind_evidence,
    overall_scores,
)
from app.agents.audit.schemas import DesignCritique

OUTDATED = """<html><head><title>Acme</title></head><body>
<h1>Acme Plumbing</h1><h1>Welcome!!!</h1>
<img src="http://acme-plumbing.com/banner.gif"><img src="/a.png" alt="">
<p>Best plumbers.</p><a href="/old-page">Old page</a></body></html>"""

MODERN = (
    """<html lang="en"><head><title>Acme Plumbing — 24/7 Emergency Plumbers in Austin</title>
<meta name="description" content="Licensed Austin plumbers. Same-day repairs, upfront prices and a 2-year guarantee on all work.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="canonical" href="https://acme-plumbing.com/">
<script type="application/ld+json">{"@type": "Plumber", "name": "Acme"}</script></head>
<body><h1>Austin's trusted plumbers</h1><a href="tel:+15125550100">Call now</a>
<a href="/quote">Get a quote</a><p>"""
    + "We fix leaks, drains and water heaters. " * 30
    + "</p></body></html>"
)


def _checks(draft_list: list[checks.FindingDraft]) -> set[str]:
    return {d.check for d in draft_list}


def test_outdated_page_produces_expected_seo_and_content_findings() -> None:
    facts = checks.page_facts(OUTDATED, "https://acme-plumbing.com/")
    seo = _checks(checks.seo_findings(facts, x_robots=None, robots_ok=True, sitemap_ok=False))
    assert {
        "title_length",
        "meta_description_missing",
        "h1_multiple",
        "canonical_missing",
        "lang_missing",
        "viewport_missing",
        "structured_data_missing",
        "sitemap_missing",
        "img_alt",
    } <= seo
    content = _checks(checks.content_findings(facts))
    assert content == {"cta_missing", "tel_mailto_missing", "thin_content"}
    assert facts.http_resources == ["http://acme-plumbing.com/banner.gif"]


def test_modern_page_is_clean() -> None:
    facts = checks.page_facts(MODERN, "https://acme-plumbing.com/")
    assert checks.seo_findings(facts, x_robots=None, robots_ok=True, sitemap_ok=True) == []
    assert checks.content_findings(facts) == []
    assert facts.jsonld_types == ["Plumber"]


def test_noindex_is_critical() -> None:
    facts = checks.page_facts(MODERN, "https://acme-plumbing.com/")
    found = checks.seo_findings(facts, x_robots="noindex", robots_ok=True, sitemap_ok=True)
    assert [(d.check, d.severity) for d in found] == [("noindex", "critical")]


def test_security_findings() -> None:
    plain = checks.security_findings(
        requested_url="http://a.com",
        final_url="http://a.com/",
        headers={},
        http_resources=[],
        http_redirects_to_https=None,
    )
    assert [(d.check, d.severity) for d in plain] == [("https_missing", "critical")]
    https = checks.security_findings(
        requested_url="https://a.com",
        final_url="https://a.com/",
        headers={"x-frame-options": "DENY"},
        http_resources=["http://a.com/x.js"],
        http_redirects_to_https=False,
    )
    got = _checks(https)
    assert {
        "https_redirect",
        "header_strict-transport-security",
        "header_content-security-policy",
        "mixed_content",
    } <= got
    assert "frame_protection" not in got


def test_performance_and_axe_mapping() -> None:
    perf = checks.performance_findings(
        {"performance": 38, "accessibility": 61}, {"lcp_ms": 5200, "cls": 0.3}
    )
    assert _checks(perf) == {"perf_low", "lcp", "cls", "a11y_score"}
    axe = checks.axe_findings(
        [{"id": "color-contrast", "impact": "serious", "help": "Contrast", "nodes": [{}, {}]}]
    )
    assert (axe[0].severity, axe[0].metric["nodes"]) == ("medium", 2)


def test_findings_without_evidence_are_rejected() -> None:
    draft = checks.FindingDraft("seo", "high", "Missing title", "x", "html", {}, "title_missing")
    with pytest.raises(MissingEvidenceError):
        bind_evidence([draft], {})
    bound = bind_evidence([draft], {"html": "ev-1"})
    assert bound[0].evidence_id == "ev-1"


def test_scores_and_before_after() -> None:
    drafts = [
        checks.FindingDraft(
            "security", "critical", "Site doesn't use HTTPS", "x", "h", {}, "https_missing"
        ),
        checks.FindingDraft(
            "content", "high", "No clear call to action", "x", "h", {}, "cta_missing"
        ),
    ]
    findings = bind_evidence(drafts, {"h": "e"})
    critique = DesignCritique.mock({})
    scores = overall_scores({"performance": 41.0}, critique, findings)
    assert scores["security"] == 70.0
    assert scores["design"] == 48.0
    text = before_after(findings, critique, ["redesign"])
    assert text.startswith("Today: site doesn't use https; no clear call to action.")
    assert "modernise the design" in text and "move the site to HTTPS" in text


@pytest.mark.parametrize(
    ("url", "ips", "allowed"),
    [
        ("https://acme.com/", ["93.184.216.34"], True),
        ("https://acme.com/", ["10.0.0.7"], False),
        ("http://169.254.169.254/latest", ["169.254.169.254"], False),
        ("https://acme.com:8443/", ["93.184.216.34"], False),
        ("https://www.linkedin.com/in/x", ["93.184.216.34"], False),
        ("data:image/png;base64,AAAA", [], True),
    ],
)
async def test_browser_route_guard(url: str, ips: list[str], allowed: bool) -> None:
    async def resolver(host: str, port: int) -> list[str]:
        return ips

    assert await GuardedBrowser(resolver=resolver)._allowed(url) is allowed
