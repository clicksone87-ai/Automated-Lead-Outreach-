"""Renders outgoing email with the React Email templates in `email-templates/`.

The backend never builds email HTML itself, so every message carries the same identity footer
and unsubscribe link.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class RenderError(Exception):
    pass


@dataclass(slots=True)
class Rendered:
    html: str
    text: str


class EmailRenderer(Protocol):
    async def render(self, template: str, props: dict[str, Any]) -> Rendered: ...


class ReactEmailRenderer:
    def __init__(self, templates_dir: Path, cache_root: Path) -> None:
        self._src = templates_dir
        self._cache_root = cache_root
        self._lock = asyncio.Lock()

    async def _workdir(self) -> Path:
        lock = self._src / "package-lock.json"
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
        work = self._cache_root / f"email-{digest}"
        async with self._lock:
            if not (work / ".ok").exists():
                work.mkdir(parents=True, exist_ok=True)
                for name in ("package.json", "package-lock.json"):
                    shutil.copy2(self._src / name, work / name)
                proc = await asyncio.create_subprocess_exec(
                    "npm", "ci", "--no-audit", "--no-fund", "--loglevel=error",
                    cwd=str(work), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )  # fmt: skip
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
                if proc.returncode != 0:
                    raise RenderError(f"npm ci failed: {out.decode(errors='replace')[-600:]}")
                (work / ".ok").write_text(digest)
            # template sources are copied on every call so edits take effect immediately
            for name in ("render.mjs", "templates.mjs", "i18n.mjs"):
                shutil.copy2(self._src / name, work / name)
        return work

    async def render(self, template: str, props: dict[str, Any]) -> Rendered:
        work = await self._workdir()
        proc = await asyncio.create_subprocess_exec(
            "node", "render.mjs",
            cwd=str(work), env={**os.environ, "NODE_ENV": "production"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps({"template": template, "props": props}).encode()),
            timeout=60,
        )
        if proc.returncode != 0:
            raise RenderError(f"render failed: {err.decode(errors='replace')[-600:]}")
        data = json.loads(out)
        return Rendered(html=data["html"], text=data["text"])


class FixtureRenderer:
    """Test-suite renderer: deterministic plain HTML (no Node needed)."""

    async def render(self, template: str, props: dict[str, Any]) -> Rendered:
        parts = [props.get("greeting", ""), props.get("intro", "") or props.get("body", "")]
        parts += [f"- {f}" for f in props.get("findings", [])]
        parts += [props.get("closing", ""), props.get("signoff", "")]
        sender = props.get("sender", {})
        footer = f"{sender.get('businessName', '')} · {sender.get('postalAddress', '')}"
        text = (
            "\n\n".join(p for p in parts if p)
            + f"\n\n{footer}\nUnsubscribe: {props.get('unsubscribeUrl')}"
        )
        html = (
            "<html><body>" + "".join(f"<p>{p}</p>" for p in text.split("\n\n")) + "</body></html>"
        )
        return Rendered(html=html, text=text)
