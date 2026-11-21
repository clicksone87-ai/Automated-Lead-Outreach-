"""Dashboard walkthrough: open every page in Chromium and fail on console errors, failed API
calls or page exceptions. Pages come from the sidebar (dashboard/src/components/shell/nav.ts),
so new pages are checked automatically. Screenshots go to data/walkthrough/<date>/.

Usage: python scripts/walkthrough.py [--base http://localhost:3000] [--out DIR] [--pages / /leads]
Exit code 1 when any page has a problem.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
NAV = ROOT / "dashboard" / "src" / "components" / "shell" / "nav.ts"


def nav_pages() -> list[str]:
    return list(dict.fromkeys(re.findall(r'href:\s*"(/[^"]*)"', NAV.read_text(encoding="utf-8"))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3000")
    ap.add_argument("--out", default=str(ROOT / "data" / "walkthrough" / datetime.now(UTC).strftime("%Y-%m-%d")))
    ap.add_argument("--pages", nargs="*", help="paths to check (default: every sidebar page)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = args.pages or nav_pages()
    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for path in pages:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            console: list[str] = []
            api: list[str] = []
            errors: list[str] = []
            page.on("console", lambda m, c=console: c.append(m.text) if m.type == "error" else None)
            page.on("response", lambda r, a=api: a.append(f"{r.status} {r.url}")
                    if "/api/" in r.url and r.status >= 400 else None)  # fmt: skip
            page.on("pageerror", lambda e, x=errors: x.append(str(e)))
            t = time.monotonic()
            try:
                resp = page.goto(args.base + path, wait_until="networkidle", timeout=90_000)
                status = resp.status if resp else 0
            except Exception as exc:  # report and continue with the next page
                status, errors = 0, [*errors, f"{type(exc).__name__}: {exc}"]
            page.wait_for_timeout(1500)
            name = path.strip("/").replace("/", "_") or "overview"
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            bad = bool(console or api or errors) or status != 200
            failures += bad
            first = (console + api + errors)[:1]
            print(f"{'FAIL' if bad else 'ok  '} {path:20} HTTP {status} {time.monotonic() - t:5.1f}s "
                  f"console_errors={len(console)} failed_api={len(api)} exceptions={len(errors)}"
                  + (f" | {first[0][:160]}" if first else ""))  # fmt: skip
            page.close()
        browser.close()
    print(f"{len(pages)} pages, {failures} with problems; screenshots in {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
