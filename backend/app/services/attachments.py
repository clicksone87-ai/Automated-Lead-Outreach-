"""Attachment size policy: every outgoing email fits Gmail's 25 MB message limit.

Gmail counts the message after base64 encoding (about 4/3 of the raw size plus line breaks), so
the budget is checked against the encoded size. When attachments would not fit, the same steps
always run in the same order and each one is recorded:

1. compress large images (fit inside 1600x6000 px, high-quality JPEG or optimised PNG);
2. merge the remaining secondary images (logos, mobile screenshot) into one PDF;
3. drop the least important items, in DROP_ORDER;
4. as a last resort shrink the main preview screenshot further.

At least one preview screenshot is always kept when the proposal has one.
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

from PIL import Image

from app.adapters.storage import StorageAdapter

logger = logging.getLogger(__name__)

GMAIL_MESSAGE_LIMIT = 25_000_000
# Leaves 2 MB for the text and HTML bodies, headers and MIME boundaries. 23 MB encoded is
# about 16.8 MB of raw attachment bytes.
ENCODED_BUDGET = 23_000_000
PART_OVERHEAD = 400  # MIME headers of one attachment part
COMPRESS_MIN_BYTES = 300_000  # smaller images are left as they are
IMAGE_BOX = (1600, 6000)  # full-page screenshots are tall: keep them readable
JPEG_QUALITY = 85
MERGE_QUALITY = 80
MERGED_FILENAME = "drafts.pdf"
# Most expendable first. The preview PDF repeats the screenshots; the main screenshot is never
# in this list.
DROP_ORDER = ("preview_pdf", "audit_pdf", "merged", "preview_mobile", "logo")
PRIMARY_KINDS = ("preview_desktop", "preview_mobile")
IMAGE_TYPES = {"image/png", "image/jpeg"}


@dataclass(slots=True, eq=False)  # compared by identity: two files may hold equal bytes
class Item:
    kind: str
    filename: str
    content_type: str
    data: bytes
    path: str | None = None  # storage path; None once the bytes were changed
    note: str = ""
    contains: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Fitted:
    items: list[Item]
    actions: list[str]
    original_bytes: int

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def encoded_size(raw: int) -> int:
    """Bytes one attachment of `raw` bytes takes in the message (base64, 76-char lines)."""
    b64 = 4 * math.ceil(raw / 3)
    return b64 + 2 * math.ceil(b64 / 76) + PART_OVERHEAD


def encoded_total(items: list[Item]) -> int:
    return sum(encoded_size(len(i.data)) for i in items)


def mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return img.convert("RGB")


def _has_alpha(img: Image.Image) -> bool:
    if img.mode not in ("RGBA", "LA", "P"):
        return False
    extrema = img.convert("RGBA").getchannel("A").getextrema()
    lowest = extrema[0]  # one band: (min, max)
    return isinstance(lowest, int | float) and lowest < 255


def compress_image(
    data: bytes, *, box: tuple[int, int] = IMAGE_BOX, quality: int = JPEG_QUALITY
) -> tuple[bytes, str]:
    """Downscale into `box` and re-encode; returns (bytes, content type), whichever is smaller
    of optimised PNG and JPEG (PNG only when the image is transparent)."""
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        img = src.copy()
    img.thumbnail(box, Image.Resampling.LANCZOS)
    png = io.BytesIO()
    img.save(png, format="PNG", optimize=True)
    best = (png.getvalue(), "image/png")
    if not _has_alpha(img):
        jpg = io.BytesIO()
        _rgb(img).save(jpg, format="JPEG", quality=quality, optimize=True, progressive=True)
        if len(jpg.getvalue()) < len(best[0]):
            best = (jpg.getvalue(), "image/jpeg")
    return best


def merge_images_pdf(images: list[bytes], *, quality: int = MERGE_QUALITY) -> bytes:
    """One page per image (Pillow stores the pages JPEG-compressed)."""
    pages = []
    for data in images:
        with Image.open(io.BytesIO(data)) as src:
            src.load()
            page = _rgb(src)
        page.thumbnail(IMAGE_BOX, Image.Resampling.LANCZOS)
        pages.append(page)
    buf = io.BytesIO()
    pages[0].save(
        buf, format="PDF", save_all=True, append_images=pages[1:], resolution=96, quality=quality
    )
    return buf.getvalue()


def _with_suffix(filename: str, content_type: str) -> str:
    suffix = ".jpg" if content_type == "image/jpeg" else ".png"
    return str(PurePosixPath(filename).with_suffix(suffix))


def _primary(items: list[Item]) -> Item | None:
    for kind in PRIMARY_KINDS:
        for item in items:
            if item.kind == kind:
                return item
    return None


class _Plan:
    """Mutable working state of one `fit` run."""

    def __init__(self, items: list[Item], budget: int) -> None:
        self.items = list(items)
        self.budget = budget
        self.primary = _primary(self.items)
        self.actions: list[str] = []

    def fits(self) -> bool:
        return encoded_total(self.items) <= self.budget

    def _swap(self, old: Item, new: Item) -> None:
        self.items[self.items.index(old)] = new
        if old is self.primary:
            self.primary = new

    def compress(self) -> None:
        for item in list(self.items):
            if item.content_type not in IMAGE_TYPES or len(item.data) < COMPRESS_MIN_BYTES:
                continue
            try:
                data, ctype = compress_image(item.data)
            except (OSError, ValueError) as exc:
                logger.warning("could not compress %s: %s", item.filename, exc)
                continue
            if len(data) >= len(item.data):
                continue
            note = f"compressed from {mb(len(item.data))} to {mb(len(data))}"
            self.actions.append(f"{item.filename}: {note}")
            self._swap(item, replace(item, data=data, content_type=ctype, path=None, note=note,
                                     filename=_with_suffix(item.filename, ctype)))  # fmt: skip

    def merge(self) -> None:
        secondary = [
            i for i in self.items if i.content_type in IMAGE_TYPES and i is not self.primary
        ]
        if len(secondary) < 2:
            return
        try:
            merged = merge_images_pdf([i.data for i in secondary])
        except (OSError, ValueError) as exc:
            logger.warning("could not merge images: %s", exc)
            return
        if len(merged) >= sum(len(i.data) for i in secondary):
            return
        names = ", ".join(i.filename for i in secondary)
        self.actions.append(f"{names}: merged into {MERGED_FILENAME} ({mb(len(merged))})")
        first = self.items.index(secondary[0])
        self.items = [i for i in self.items if i not in secondary]
        self.items.insert(first, Item(kind="merged", filename=MERGED_FILENAME, data=merged,
                                      content_type="application/pdf",
                                      note=f"{len(secondary)} images merged into one PDF",
                                      contains=sorted({i.kind for i in secondary})))  # fmt: skip

    def _drop(self, item: Item) -> None:
        self.items.remove(item)
        self.actions.append(f"{item.filename}: left out ({mb(len(item.data))}) to fit 25 MB")

    def drop(self) -> None:
        for kind in DROP_ORDER:
            for item in [i for i in self.items if i.kind == kind and i is not self.primary]:
                if self.fits():
                    return
                self._drop(item)
        others = [i for i in self.items if i is not self.primary]
        for item in sorted(others, key=lambda i: len(i.data), reverse=True):  # other kinds
            if self.fits():
                return
            self._drop(item)

    def shrink_primary(self) -> None:
        box, quality = IMAGE_BOX, JPEG_QUALITY
        while self.primary is not None and not self.fits() and box[0] > 200:
            box, quality = (int(box[0] * 0.7), int(box[1] * 0.7)), max(60, quality - 5)
            old = self.primary
            data, ctype = compress_image(old.data, box=box, quality=quality)
            note = f"shrunk to {box[0]}x{box[1]} px max ({mb(len(data))})"
            self._swap(old, replace(old, data=data, content_type=ctype, path=None, note=note,
                                    filename=_with_suffix(old.filename, ctype)))  # fmt: skip
            self.actions.append(f"{old.filename}: {note}")


def fit(items: list[Item], budget: int = ENCODED_BUDGET) -> Fitted:
    """Apply the size policy (steps in a fixed order, stopping once it fits); `items` is not
    modified."""
    plan = _Plan(items, budget)
    original = sum(len(i.data) for i in items)
    for step in (plan.compress, plan.merge, plan.drop, plan.shrink_primary):
        if plan.fits():
            break
        step()
    return Fitted(plan.items, plan.actions, original)


# ── stored attachments (proposal.attachments) ───────────────────────────────


def load(storage: StorageAdapter, attachments: list[dict[str, Any]]) -> list[Item]:
    items = []
    for a in attachments:
        try:
            data = storage.read_bytes(a["path"])
        except (OSError, ValueError, KeyError):
            logger.warning("attachment missing: %s", a.get("path"))
            continue
        items.append(Item(kind=str(a.get("kind", "file")), filename=a["filename"], data=data,
                          content_type=a.get("content_type", "application/octet-stream"),
                          path=a["path"], note=str(a.get("note", "")),
                          contains=list(a.get("contains", []))))  # fmt: skip
    return items


def prepare(
    storage: StorageAdapter, attachments: list[dict[str, Any]], *, folder: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit a proposal's attachments to the budget; changed files are stored under `folder`.

    Returns the attachment records (each with its size in `bytes`) and a report of what was done.
    """
    fitted = fit(load(storage, attachments))
    out: list[dict[str, Any]] = []
    for item in fitted.items:
        path = item.path or storage.save_bytes(f"{folder}/{item.filename}", item.data)
        record: dict[str, Any] = {"kind": item.kind, "path": path, "filename": item.filename,
                                  "content_type": item.content_type, "bytes": len(item.data)}  # fmt: skip
        if item.note:
            record["note"] = item.note
        if item.contains:
            record["contains"] = item.contains
        out.append(record)
    report = report_for(out) | {"original_bytes": fitted.original_bytes, "actions": fitted.actions}
    return out, report


def attached_kinds(attachments: list[dict[str, Any]]) -> set[str]:
    """Kinds that really travel with the email, looking inside a merged PDF."""
    kinds: set[str] = set()
    for a in attachments:
        kinds.update(a.get("contains") or [a.get("kind", "file")])
    return kinds


def template_flags(attachments: list[dict[str, Any]]) -> dict[str, bool]:
    """Which drafts the email template may say are attached."""
    kinds = attached_kinds(attachments)
    return {
        "logos": "logo" in kinds,
        "preview": any(k.startswith("preview") for k in kinds),
        "audit": "audit_pdf" in kinds,
    }


def report_for(
    attachments: list[dict[str, Any]], storage: StorageAdapter | None = None
) -> dict[str, Any]:
    """Attachment count and size for the dashboard; older records are measured on disk."""
    sizes = []
    for a in attachments:
        size = a.get("bytes")
        if size is None and storage is not None and a.get("path"):
            try:
                size = storage.absolute(a["path"]).stat().st_size
            except (OSError, ValueError):
                size = None
        sizes.append(int(size or 0))
    total = sum(sizes)
    encoded = sum(encoded_size(s) for s in sizes)
    return {
        "count": len(attachments),
        "bytes": total,
        "encoded_bytes": encoded,
        "mb": round(total / 1_000_000, 2),
        "within_gmail_limit": encoded <= ENCODED_BUDGET,
    }
