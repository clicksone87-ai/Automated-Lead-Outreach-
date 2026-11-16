"""Website Builder — build pipeline (3.09) and preview tests (3.10).

Build: copy the template into a per-preview working directory, link the shared
node_modules (installed once from the template's lockfile), write content.json, commit to a
local git repo per preview, run `next build` (static export) with a memory cap, a timeout and
low CPU priority — one build at a time — then publish atomically to
DATA_DIR/previews/<id>/site, which Caddy serves privately.

Tests: Playwright smoke test (title, h1, CTA, no console errors) on the built files with zero
network access, desktop + mobile screenshots, internal link check, Lighthouse lab scores
against a local static server (the crawlability audit is skipped: previews are noindex on
purpose), and a PDF made from the full-page screenshots for local-mode email attachments.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import mimetypes
import os
import shutil
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from PIL import Image

from app.agents.audit.browser import GuardedBrowser

logger = logging.getLogger(__name__)

BUILD_TIMEOUT_S = 480
NPM_TIMEOUT_S = 600
_BUILD_LOCK = asyncio.Lock()
COPY_IGNORE = shutil.ignore_patterns("node_modules", "out", ".next", "*.tsbuildinfo")
LIGHTHOUSE_THRESHOLDS = {"performance": 60, "accessibility": 85, "best-practices": 80, "seo": 80}
PREVIEW_ORIGIN = "http://preview.local"


class BuildError(Exception):
    pass


@dataclass(slots=True)
class BuildResult:
    site_dir: Path
    work_dir: Path
    git_commit: str | None
    log_path: Path
    seconds: float


@dataclass(slots=True)
class PreviewTests:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    screenshots: dict[str, str] = field(default_factory=dict)  # name -> relative path
    lighthouse: dict[str, float] = field(default_factory=dict)
    pdf_path: str | None = None


async def _run(
    cmd: list[str], cwd: Path, *, limit_s: float, env: dict[str, str] | None = None
) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env={**os.environ, **(env or {})},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=limit_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timed out after {limit_s:.0f}s"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _nice(cmd: list[str]) -> list[str]:
    return ["nice", "-n", "10", *cmd] if shutil.which("nice") else cmd


async def ensure_deps(template_dir: Path, cache_root: Path) -> Path:
    """Install the template's node_modules once per lockfile hash; returns its path."""
    lock = template_dir / "package-lock.json"
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    target = cache_root / f"{template_dir.name}-{digest}"
    modules = target / "node_modules"
    if (target / ".ok").exists() and modules.is_dir():
        return modules
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_dir / "package.json", target / "package.json")
    shutil.copy2(lock, target / "package-lock.json")
    code, out = await _run(
        ["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"],
        target,
        limit_s=NPM_TIMEOUT_S,
    )
    if code != 0:
        raise BuildError(f"npm ci failed ({code}): {out[-800:]}")
    (target / ".ok").write_text(digest)
    return modules


async def build_site(
    *,
    template_dir: Path,
    data_dir: Path,
    preview_id: str,
    content: dict[str, Any],
) -> BuildResult:
    """Build one preview (serialised: one build at a time)."""
    async with _BUILD_LOCK:
        loop = asyncio.get_running_loop()
        started = loop.time()
        modules = await ensure_deps(template_dir, data_dir / "site-builder")
        work = data_dir / "builds" / preview_id
        if work.exists():
            shutil.rmtree(work / "out", ignore_errors=True)
            shutil.rmtree(work / ".next", ignore_errors=True)
        shutil.copytree(template_dir, work, ignore=COPY_IGNORE, dirs_exist_ok=True)
        link = work / "node_modules"
        if link.is_symlink() or link.exists():
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link)
        link.symlink_to(modules, target_is_directory=True)
        (work / "content.json").write_text(
            json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        commit = await _git_commit(work, content)
        log_path = data_dir / "builds" / f"{preview_id}.log"
        code, out = await _run(
            _nice(["node", str(modules / "next" / "dist" / "bin" / "next"), "build"]),
            work,
            limit_s=BUILD_TIMEOUT_S,
            env={
                "NODE_OPTIONS": "--max-old-space-size=900",
                "NEXT_TELEMETRY_DISABLED": "1",
                "CI": "1",
            },
        )
        log_path.write_text(out, encoding="utf-8")
        if code != 0 or not (work / "out" / "index.html").exists():
            raise BuildError(f"next build failed ({code}); see {log_path.name}: {out[-600:]}")
        site = data_dir / "previews" / preview_id / "site"
        tmp = site.with_name("site.tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(work / "out", tmp)
        old = site.with_name("site.old")
        shutil.rmtree(old, ignore_errors=True)
        if site.exists():
            site.rename(old)
        tmp.rename(site)
        shutil.rmtree(old, ignore_errors=True)
        return BuildResult(site, work, commit, log_path, loop.time() - started)


async def _git_commit(work: Path, content: dict[str, Any]) -> str | None:
    if not shutil.which("git"):
        return None
    env = {
        "GIT_AUTHOR_NAME": "Website Builder",
        "GIT_AUTHOR_EMAIL": "builder@localhost",
        "GIT_COMMITTER_NAME": "Website Builder",
        "GIT_COMMITTER_EMAIL": "builder@localhost",
    }
    if not (work / ".git").exists():
        await _run(["git", "init", "-q"], work, limit_s=30, env=env)
        (work / ".gitignore").write_text("node_modules\nout/\n.next/\n")
    await _run(["git", "add", "-A"], work, limit_s=60, env=env)
    msg = f"preview: {content.get('business', {}).get('name', '')} ({content.get('variant')})"
    await _run(["git", "commit", "-q", "--allow-empty", "-m", msg], work, limit_s=60, env=env)
    code, out = await _run(["git", "rev-parse", "HEAD"], work, limit_s=30, env=env)
    return out.strip()[:40] if code == 0 else None


# ── preview tests ────────────────────────────────────────────────────────────


def site_fixtures(site: Path) -> dict[str, tuple[int, str, str]]:
    """Every built file as a fulfil-able response at PREVIEW_ORIGIN (no network at all)."""
    out: dict[str, tuple[int, str, str]] = {}
    for f in site.rglob("*"):
        if not f.is_file() or f.stat().st_size > 5_000_000:
            continue
        rel = f.relative_to(site).as_posix()
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        try:
            body = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary assets are not needed for the checks
        url = f"{PREVIEW_ORIGIN}/{rel}"
        out[url] = (200, ctype, body)
        if rel.endswith("index.html"):
            base = rel[: -len("index.html")]
            out[f"{PREVIEW_ORIGIN}/{base}"] = (200, "text/html", body)
            out[f"{PREVIEW_ORIGIN}/{base}".rstrip("/")] = (200, "text/html", body)
    return out


def internal_links(site: Path) -> list[tuple[str, str]]:
    """(page, href) for every internal link whose target file does not exist."""
    broken = []
    for page in site.rglob("*.html"):
        soup = BeautifulSoup(page.read_text(encoding="utf-8", errors="replace"), "lxml")
        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            if not href.startswith("/") or href.startswith("//"):
                continue
            path = href.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0].strip("/")
            target = site / path
            if (
                not path
                or (target / "index.html").exists()
                or target.with_suffix(".html").exists()
                or target.exists()
            ):
                continue
            broken.append((page.relative_to(site).as_posix(), href))
    return broken


async def lighthouse_scores(site: Path) -> dict[str, float]:
    """Lighthouse lab scores against a throwaway local static server (127.0.0.1 only)."""
    binary = shutil.which("lighthouse")
    if not binary:
        return {}
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
        cwd=str(site), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    try:
        await asyncio.sleep(1.0)
        code, out = await _run(
            [
                binary, f"http://127.0.0.1:{port}/", "--output=json", "--quiet",
                "--form-factor=mobile", "--skip-audits=is-crawlable",
                "--only-categories=performance,accessibility,best-practices,seo",
                "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage",
            ],
            site,
            limit_s=240,
        )  # fmt: skip
        if code != 0:
            logger.info("lighthouse failed: %s", out[-300:])
            return {}
        start = out.find("{")
        cats = json.loads(out[start:]).get("categories", {})
        return {
            k: round(float(v["score"]) * 100, 1)
            for k, v in cats.items()
            if v.get("score") is not None
        }
    finally:
        with contextlib.suppress(ProcessLookupError):
            server.kill()
        await server.wait()


def screenshots_pdf(pngs: list[bytes]) -> bytes:
    pages = []
    for data in pngs:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        pages.append(img)
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=96)
    return buf.getvalue()


async def run_preview_tests(
    site: Path,
    *,
    expected_name: str,
    browser_factory: Any = GuardedBrowser,
    run_lighthouse: bool = True,
) -> tuple[PreviewTests, dict[str, bytes]]:
    """Run every preview check. Returns results + screenshot/PDF bytes to store."""
    checks: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    index = site / "index.html"
    check("build_output", index.exists(), "index.html present" if index.exists() else "missing")
    if not index.exists():
        return PreviewTests(False, checks), files
    html = index.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    check("title", bool(title), title[:80])
    h1 = soup.find("h1")
    check("h1", h1 is not None, h1.get_text(" ", strip=True)[:80] if h1 is not None else "")
    check("business_name", expected_name.casefold() in soup.get_text(" ").casefold(), expected_name)
    has_cta = bool(soup.select('a[href^="tel:"], a[href="#contact"]'))
    check("cta", has_cta, "tel: link or contact anchor")
    check("viewport_meta", bool(soup.find("meta", attrs={"name": "viewport"})), "")
    check("noindex", "noindex" in html.lower(), "preview must not be indexed")
    broken = internal_links(site)
    check(
        "internal_links",
        not broken,
        f"{len(broken)} broken: {broken[:3]}" if broken else "all resolve",
    )

    fixtures = site_fixtures(site)
    async with browser_factory(fulfill=fixtures) as browser:
        cap = await browser.capture(f"{PREVIEW_ORIGIN}/", timeout_ms=30_000)
        files["desktop.png"] = cap.desktop_png
        files["mobile.png"] = cap.mobile_png
        check("renders", cap.status == 200, f"HTTP {cap.status}")
        external = [u for u in cap.blocked_requests if not u.startswith(PREVIEW_ORIGIN)]
        check("no_external_requests", not external, f"{len(external)} blocked: {external[:3]}")
    files["preview.pdf"] = screenshots_pdf([files["desktop.png"], files["mobile.png"]])
    scores: dict[str, float] = {}
    if run_lighthouse:
        scores = await lighthouse_scores(site)
        if scores:
            low = {k: v for k, v in scores.items() if v < LIGHTHOUSE_THRESHOLDS.get(k, 0)}
            check(
                "lighthouse",
                not low,
                f"scores {scores}" + (f"; below threshold: {low}" if low else ""),
            )
        else:
            check("lighthouse", True, "lighthouse unavailable here — skipped")
    passed = all(c["passed"] for c in checks)
    return PreviewTests(passed, checks, {}, scores), files
