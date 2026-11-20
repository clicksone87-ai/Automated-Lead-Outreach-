"""Template digest: cheap on a cache hit (never walks node_modules) and sensitive to sources."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agents.website_builder import build
from app.agents.website_builder.build import source_digest


def _template(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "page.tsx").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "package.json").write_text("{}", encoding="utf-8")
    deps = root / "node_modules" / "pkg"
    deps.mkdir(parents=True)
    (deps / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    (root / "out").mkdir()
    (root / "out" / "index.html").write_text("<html></html>", encoding="utf-8")
    (root / "styles.css").write_text("/* generated */", encoding="utf-8")
    return root


def test_digest_ignores_generated_and_dependency_files(tmp_path: Path) -> None:
    tpl = _template(tmp_path / "trades")
    first = source_digest(tpl)
    (tpl / "node_modules" / "pkg" / "index.js").write_text("changed", encoding="utf-8")
    (tpl / "out" / "index.html").write_text("changed", encoding="utf-8")
    (tpl / "styles.css").write_text("changed", encoding="utf-8")
    assert source_digest(tpl) == first
    (tpl / "src" / "page.tsx").write_text("export const x = 2;\n", encoding="utf-8")
    assert source_digest(tpl) != first


def test_digest_never_walks_into_node_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tpl = _template(tmp_path / "trades")
    visited: list[str] = []
    real_walk = os.walk

    def spy(top: Path, *a: object, **kw: object):  # type: ignore[no-untyped-def]
        for root, dirs, names in real_walk(top, *a, **kw):  # type: ignore[arg-type]
            visited.append(Path(root).name)
            yield root, dirs, names

    monkeypatch.setattr(build.os, "walk", spy)
    source_digest(tpl)
    assert "node_modules" not in visited and "pkg" not in visited and "out" not in visited
    assert "src" in visited
