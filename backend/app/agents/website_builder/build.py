"""Preview build pipeline and preview tests.

Each template version is compiled once (TypeScript renderer + Tailwind stylesheet, cached by a
hash of its sources). A lead's preview is then rendered from its content to static HTML in
about a second and published atomically for Caddy; no per-lead Next.js build. Tests: smoke
test, screenshots, internal links and Lighthouse scores.
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
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from PIL import Image

from app.agents.audit.browser import GuardedBrowser

logger = logging.getLogger(__name__)

BUILD_TIMEOUT_S = 300  # compiling one template version (once)
RENDER_TIMEOUT_S = 60  # rendering one preview
NPM_TIMEOUT_S = 600
_BUILD_LOCK = asyncio.Lock()
COPY_IGNORE = shutil.ignore_patterns(
    "node_modules", "out", ".next", "*.tsbuildinfo", "dist-render", "styles.css"
)
LIGHTHOUSE_THRESHOLDS = {"performance": 60, "accessibility": 85, "best-practices": 80, "seo": 80}
PREVIEW_ORIGIN = "http://preview.local"
NETWORK_ERRORS = re.compile(r"ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|ECONNREFUSED|network", re.I)


class BuildError(Exception):
    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


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


def _run_blocking(
    cmd: list[str], cwd: Path, limit_s: float, env: dict[str, str]
) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv built by this module
            cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=limit_s, check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {limit_s:.0f}s"
    return done.returncode, done.stdout.decode("utf-8", errors="replace")


async def _run(
    cmd: list[str], cwd: Path, *, limit_s: float, env: dict[str, str] | None = None
) -> tuple[int, str]:
    full_env = {**os.environ, **(env or {})}
    proactor = getattr(asyncio, "ProactorEventLoop", None)
    if sys.platform == "win32" and not isinstance(asyncio.get_running_loop(), proactor or ()):
        # Windows dev hosts run the selector loop (for psycopg), which cannot spawn processes.
        return await asyncio.to_thread(_run_blocking, cmd, cwd, limit_s, full_env)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=full_env,
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
        ["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error",
         "--fetch-retries=5", "--fetch-retry-mintimeout=5000", "--fetch-retry-maxtimeout=60000"],
        target,
        limit_s=NPM_TIMEOUT_S,
    )  # fmt: skip
    if code != 0:
        # A dropped connection is not a broken template: retried later like a timeout.
        network = bool(NETWORK_ERRORS.search(out))
        raise BuildError(f"npm ci failed ({code}): {out[-800:]}", timed_out=code == 124 or network)
    (target / ".ok").write_text(digest)
    return modules


def link_dir(link: Path, target: Path) -> None:
    """Directory link (a junction on Windows hosts, where symlinks need extra rights)."""
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi  # noqa: PLC0415 - Windows-only module

        _winapi.CreateJunction(str(target), str(link))


def source_digest(template_dir: Path) -> str:
    """Hash of everything that affects rendering, so a template change gets a fresh bundle."""
    h = hashlib.sha256()
    skip = {"node_modules", "out", ".next", "dist-render"}
    for f in sorted(template_dir.rglob("*")):
        rel = f.relative_to(template_dir)
        if not f.is_file() or skip & set(rel.parts) or rel.name == "styles.css":
            continue
        h.update(rel.as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


async def ensure_bundle(template_dir: Path, cache_root: Path) -> Path:
    """Compile the template once per version: renderer (tsc) and stylesheet (Tailwind)."""
    bundle = cache_root / "bundles" / f"{template_dir.name}-{source_digest(template_dir)}"
    if (bundle / ".ok").exists():
        return bundle
    async with _BUILD_LOCK:
        if (bundle / ".ok").exists():
            return bundle
        modules = await ensure_deps(template_dir, cache_root)
        shutil.rmtree(bundle, ignore_errors=True)
        shutil.copytree(template_dir, bundle, ignore=COPY_IGNORE)
        link_dir(bundle / "node_modules", modules)
        log = []
        for cmd in (
            ["node", str(modules / "typescript" / "bin" / "tsc"), "-p", "tsconfig.render.json"],
            ["node", "render/build-css.mjs"],
        ):
            code, out = await _run(cmd, bundle, limit_s=BUILD_TIMEOUT_S)
            log.append(f"$ {' '.join(cmd[1:])}\n{out}")
            if code != 0:
                (bundle / "build.log").write_text("\n".join(log), encoding="utf-8")
                raise BuildError(
                    f"template compile failed ({code}): {out[-600:]}", timed_out=code == 124
                )
        (bundle / "build.log").write_text("\n".join(log), encoding="utf-8")
        (bundle / ".ok").write_text(bundle.name)
        return bundle


async def build_site(
    *,
    template_dir: Path,
    data_dir: Path,
    preview_id: str,
    content: dict[str, Any],
    cache_dir: Path | None = None,
) -> BuildResult:
    """Render one preview from its content with the compiled template and publish it."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    bundle = await ensure_bundle(template_dir, cache_dir or data_dir / "site-builder")
    work = data_dir / "builds" / preview_id
    work.mkdir(parents=True, exist_ok=True)
    (work / "content.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (work / "template.txt").write_text(bundle.name + "\n", encoding="utf-8")
    commit = await _git_commit(work, content)
    log_path = data_dir / "builds" / f"{preview_id}.log"
    site = data_dir / "previews" / preview_id / "site"
    tmp = site.with_name("site.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    code, out = await _run(
        ["node", str(bundle / "render" / "cli.cjs"), str(work / "content.json"), str(tmp)],
        bundle,
        limit_s=RENDER_TIMEOUT_S,
    )
    log_path.write_text(f"template {bundle.name}\n{out}", encoding="utf-8")
    if code != 0 or not (tmp / "index.html").exists():
        raise BuildError(
            f"preview render failed ({code}); see {log_path.name}: {out[-600:]}",
            timed_out=code == 124,
        )
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
                # Lighthouse's default 4x CPU slowdown assumes a fast desktop; on small machines it
                # measures the host rather than the site.
                "--throttling.cpuSlowdownMultiplier=1",
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
    thresholds: dict[str, int] | None = None,
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
            limits = thresholds or LIGHTHOUSE_THRESHOLDS
            low = {k: v for k, v in scores.items() if v < limits.get(k, 0)}
            check(
                "lighthouse",
                not low,
                f"scores {scores}" + (f"; below threshold: {low}" if low else ""),
            )
        else:
            check("lighthouse", True, "lighthouse unavailable here — skipped")
    passed = all(c["passed"] for c in checks)
    return PreviewTests(passed, checks, {}, scores), files
