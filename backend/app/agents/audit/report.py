"""Audit report: findings bound to evidence, scores, before/after summary, HTML for the PDF."""

from __future__ import annotations

import base64
import hashlib
import html
from typing import Any

from app.agents.audit.checks import SEVERITY_ORDER, FindingDraft
from app.agents.audit.schemas import Competitor, DesignCritique, Finding


class MissingEvidenceError(ValueError):
    """A finding referenced evidence that was not captured. Findings without proof are illegal."""


def bind_evidence(drafts: list[FindingDraft], evidence_ids: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for d in drafts:
        evidence_id = evidence_ids.get(d.evidence_key)
        if not evidence_id:
            raise MissingEvidenceError(f"finding '{d.title}' needs evidence '{d.evidence_key}'")
        fid = hashlib.sha1(f"{d.check}|{d.title}".encode(), usedforsecurity=False).hexdigest()[:10]
        findings.append(
            Finding(
                id=fid,
                category=d.category,
                severity=d.severity,
                title=d.title,
                detail=d.detail,
                evidence_id=evidence_id,
                metric=d.metric,
                check=d.check,
            )
        )
    findings.sort(key=lambda f: (SEVERITY_ORDER[f.severity], f.category))
    return findings


def overall_scores(
    lighthouse: dict[str, float], critique: DesignCritique | None, findings: list[Finding]
) -> dict[str, float | None]:
    penalty = {"critical": 30, "high": 15, "medium": 7, "low": 3}
    security = 100.0
    for f in findings:
        if f.category == "security":
            security -= penalty[f.severity]
    seo = lighthouse.get("seo")
    if seo is None:
        seo_penalty = sum(penalty[f.severity] for f in findings if f.category == "seo")
        seo = max(0.0, 100.0 - seo_penalty)
    return {
        "performance": lighthouse.get("performance"),
        "accessibility": lighthouse.get("accessibility"),
        "best-practices": lighthouse.get("best-practices"),
        "seo": round(seo, 1),
        "security": max(0.0, security),
        "design": float(critique.design_score) if critique else None,
    }


FIX_COPY = {
    "https_missing": "move the site to HTTPS",
    "viewport_missing": "make every page mobile-first",
    "perf_low": "cut load time on phones",
    "lcp": "show the main content faster",
    "cta_missing": "add a clear 'get a quote' / 'call now' action on every page",
    "structured_data_missing": "add LocalBusiness structured data",
    "title_missing": "write search-friendly page titles",
    "meta_description_missing": "write meta descriptions for search snippets",
    "noindex": "unblock the homepage from Google",
    "places_missing": "set up a Google Business Profile",
    "broken_links": "fix broken links",
    "tel_mailto_missing": "add tap-to-call and email links",
}


def before_after(
    findings: list[Finding], critique: DesignCritique | None, services: list[str]
) -> str:
    top = [f for f in findings if f.severity in ("critical", "high")][:4] or findings[:3]
    before = "; ".join(f.title.lower() for f in top) or "no major issues detected"
    fixes = [FIX_COPY[f.check] for f in findings if f.check in FIX_COPY][:4]
    if critique and critique.looks_outdated and "redesign" in services:
        fixes.insert(0, "modernise the design while keeping your page structure")
    after = "; ".join(dict.fromkeys(fixes)) or "polish design and content"
    return f"Today: {before}. After the work: {after}."


SCORE_LABELS = {
    "performance": "Performance",
    "accessibility": "Accessibility",
    "best-practices": "Best practices",
    "seo": "SEO",
    "security": "Security",
    "design": "Design",
}


def _img(data: bytes | None, alt: str) -> str:
    if not data:
        return ""
    return (
        f'<img alt="{html.escape(alt)}" src="data:image/png;base64,'
        f'{base64.b64encode(data).decode()}">'
    )


def render_html(
    *,
    business_name: str,
    url: str,
    scores: dict[str, float | None],
    findings: list[Finding],
    summary: str,
    competitors: list[Competitor],
    desktop_png: bytes | None,
    mobile_png: bytes | None,
    sender: str,
    generated_on: str,
) -> str:
    esc = html.escape
    score_cells = "".join(
        f'<div class="score"><b>{"—" if v is None else round(v)}</b><span>{esc(SCORE_LABELS.get(k, k))}</span></div>'
        for k, v in scores.items()
    )
    rows = "".join(
        f'<tr><td><span class="sev {f.severity}">{f.severity}</span></td><td>{esc(f.category)}</td>'
        f"<td><b>{esc(f.title)}</b><br><small>{esc(f.detail)}</small></td></tr>"
        for f in findings
        if f.severity != "low"
    )
    minor = [f for f in findings if f.severity == "low"]
    minor_html = (
        "<h2>Minor technical items</h2><ul>"
        + "".join(f"<li>{esc(f.title)}</li>" for f in minor)
        + "</ul>"
        if minor
        else ""
    )
    comp_rows = "".join(
        f"<tr><td>{esc(c.domain)}</td><td>{'✓' if c.https else '✗'}</td>"
        f"<td>{'✓' if c.mobile_ready else '✗'}</td><td>{'✓' if c.has_cta else '✗'}</td>"
        f"<td>{'✓' if c.has_structured_data else '✗'}</td></tr>"
        for c in competitors
    )
    comp_table = (
        "<h2>Local competitors</h2><table><tr><th>Site</th><th>HTTPS</th><th>Mobile</th>"
        f"<th>Call to action</th><th>Structured data</th></tr>{comp_rows}</table>"
        if competitors
        else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{font-family:Helvetica,Arial,sans-serif;color:#1f2330;font-size:12px;line-height:1.5}}
h1{{font-size:22px;margin:0}} h2{{font-size:15px;margin:22px 0 8px}}
.muted{{color:#6b7080}} .scores{{display:flex;gap:10px;margin:14px 0}}
.score{{flex:1;border:1px solid #e3e5ec;border-radius:10px;padding:10px;text-align:center}}
.score b{{display:block;font-size:22px}} .score span{{color:#6b7080;font-size:11px}}
.summary{{background:#f3f2ff;border-radius:10px;padding:12px}}
table{{width:100%;border-collapse:collapse}} td,th{{border-bottom:1px solid #eceef3;padding:6px;text-align:left;vertical-align:top}}
.sev{{border-radius:99px;padding:1px 8px;font-size:10px;text-transform:uppercase;font-weight:bold}}
.critical{{background:#fde2e2;color:#9b1c1c}} .high{{background:#fdebd3;color:#8a4b08}}
.medium{{background:#fff6cc;color:#7a5d00}} .low{{background:#eef0f4;color:#4b5060}}
.shots{{display:flex;gap:12px;align-items:flex-start}} .shots img{{border:1px solid #e3e5ec;border-radius:8px}}
.shots img:first-child{{width:70%}} .shots img:last-child{{width:26%}}
</style></head><body>
<h1>Website review — {esc(business_name)}</h1>
<div class="muted">{esc(url)} · {esc(generated_on)} · prepared by {esc(sender)}</div>
<div class="scores">{score_cells}</div>
<div class="summary">{esc(summary)}</div>
<h2>How it looks today</h2><div class="shots">{_img(desktop_png, "Desktop")}{_img(mobile_png, "Mobile")}</div>
<h2>Findings</h2><table>{rows}</table>
{minor_html}
{comp_table}
<p class="muted">Every finding is based on measurements and screenshots taken on {esc(generated_on)}.</p>
</body></html>"""


def summarize_for_storage(findings: list[Finding]) -> list[dict[str, Any]]:
    return [f.model_dump() for f in findings]
