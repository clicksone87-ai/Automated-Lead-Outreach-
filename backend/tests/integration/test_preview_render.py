"""Preview rendering with the compiled template: real Node, real Chromium, no network.

Skipped when Node or the template's node_modules are missing (the npm install itself needs the
network, so it is not part of the test).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from app.agents.website_builder import build
from app.agents.website_builder.build import build_site, run_preview_tests

TEMPLATE = Path(__file__).resolve().parents[3] / "site-templates" / "trades"
pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (TEMPLATE / "node_modules").is_dir(),
    reason="needs node and site-templates/trades/node_modules",
)

CONTENT = {
    "variant": "bold",
    "language": "de",
    "mode": "redesign",
    "business": {"name": "Müller Sanitär", "trade": "Sanitär", "phone": "+49 30 1234567",
                 "email": "info@mueller.example.com", "address": "Hauptstr. 1, Berlin", "city": "Berlin"},
    "brand": {"primary": "#0b4f6c", "accent": "#f2a541", "ink": "#111827", "surface": "#f5f7fa",
              "fontHeading": "system-ui, sans-serif", "fontBody": "system-ui, sans-serif", "logoSvg": ""},
    "seo": {"title": "Müller Sanitär Berlin", "description": "Sanitär & Heizung in Berlin"},
    "hero": {"headline": "Sanitär <schnell> & sauber", "subheadline": "Seit Jahren in Berlin.", "cta": "Jetzt anrufen"},
    "services": [{"title": "Rohrreinigung", "description": "Verstopfungen lösen wir sofort."}],
    "about": "Familienbetrieb.",
    "areas": ["Mitte", "Pankow"],
    "pages": [{"slug": "", "title": "Start", "sections": ["hero", "services", "about", "areas", "contact"]},
              {"slug": "leistungen", "title": "Leistungen", "sections": ["services", "contact"]}],
    "preview": {"agency": "Test Studio", "notice": "Vorschau — keine echte Website"},
}  # fmt: skip


async def test_render_once_then_seconds_per_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def local_deps(template_dir: Path, cache_root: Path) -> Path:
        return template_dir / "node_modules"

    monkeypatch.setattr(build, "ensure_deps", local_deps)
    first = await build_site(
        template_dir=TEMPLATE, data_dir=tmp_path, preview_id="p1", content=CONTENT
    )
    assert (first.site_dir / "index.html").exists()
    assert (first.site_dir / "leistungen" / "index.html").exists()
    assert (first.site_dir / "styles.css").stat().st_size > 1000
    html = (first.site_dir / "index.html").read_text(encoding="utf-8")
    assert '<html lang="de"' in html and "--brand:#0b4f6c" in html
    assert "Sanitär &lt;schnell&gt; &amp; sauber" in html  # content is escaped, never raw HTML
    assert 'name="robots" content="noindex' in html

    t0 = time.monotonic()
    second = await build_site(
        template_dir=TEMPLATE, data_dir=tmp_path, preview_id="p2", content=CONTENT
    )
    assert time.monotonic() - t0 < 15  # the compiled template is reused
    assert (second.work_dir / "content.json").exists()

    tests, files = await run_preview_tests(
        second.site_dir, expected_name="Müller Sanitär", run_lighthouse=False
    )
    failed = [c for c in tests.checks if not c["passed"]]
    assert tests.passed, failed
    assert files["desktop.png"][:4] == b"\x89PNG"


def test_template_change_gets_a_new_bundle(tmp_path: Path) -> None:
    copy = tmp_path / "trades"
    shutil.copytree(TEMPLATE, copy, ignore=build.COPY_IGNORE)
    before = build.source_digest(copy)
    (copy / "components" / "site.tsx").write_text("// changed\n", encoding="utf-8")
    assert build.source_digest(copy) != before


async def test_network_failure_during_install_is_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def flaky(
        cmd: list[str], cwd: Path, *, limit_s: float, env: dict[str, str] | None = None
    ) -> tuple[int, str]:
        return 1, "npm error code ECONNRESET\nnpm error network aborted"

    monkeypatch.setattr(build, "_run", flaky)
    with pytest.raises(build.BuildError) as exc:
        await build.ensure_deps(TEMPLATE, tmp_path)
    assert exc.value.timed_out  # retried later, not sent to human review
