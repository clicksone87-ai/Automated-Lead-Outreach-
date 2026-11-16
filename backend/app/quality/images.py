"""Image checks shared by Branding (trademark similarity) and the Quality check.

Perceptual difference hash (dHash) on the trimmed, square-padded grayscale image plus a
coarse colour-histogram overlap. Two logos are "too similar" when their shapes nearly match
(small Hamming distance) — colour alone never flags a logo (many trades share blue).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageOps

HASH_SIZE = 16  # 16x16 = 256-bit hash
SIMILAR_MAX_DISTANCE = 0.18  # normalized Hamming distance at or below this = too similar


def _prepared(img: Image.Image) -> Image.Image:
    """Flatten transparency onto white, trim uniform borders, pad to a square."""
    rgba = img.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(bg, rgba).convert("RGB")
    diff = ImageChops.difference(flat, Image.new("RGB", flat.size, (255, 255, 255)))
    box = diff.getbbox()
    if box:
        flat = flat.crop(box)
    side = max(flat.size)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(flat, ((side - flat.width) // 2, (side - flat.height) // 2))
    return square


def dhash(data: bytes, size: int = HASH_SIZE) -> int:
    with Image.open(io.BytesIO(data)) as img:
        g = ImageOps.grayscale(_prepared(img)).resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(g.tobytes())
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int, size: int = HASH_SIZE) -> float:
    """Normalized Hamming distance in [0, 1]."""
    return bin(a ^ b).count("1") / (size * size)


def colour_overlap(a: bytes, b: bytes) -> float:
    """Histogram intersection of coarse RGB histograms, in [0, 1]."""

    def hist(data: bytes) -> list[float]:
        with Image.open(io.BytesIO(data)) as img:
            small = _prepared(img).resize((64, 64)).quantize(colors=64).convert("RGB")
        counts = [0] * 64
        raw = small.tobytes()
        for i in range(0, len(raw), 3):
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            counts[(r // 64) * 16 + (g // 64) * 4 + (b // 64)] += 1
        total = float(sum(counts)) or 1.0
        return [c / total for c in counts]

    ha, hb = hist(a), hist(b)
    return sum(min(x, y) for x, y in zip(ha, hb, strict=True))


@dataclass(slots=True)
class Similarity:
    against: str
    distance: float  # 0 = identical shape
    colour_overlap: float

    @property
    def too_similar(self) -> bool:
        return self.distance <= SIMILAR_MAX_DISTANCE


def compare(candidate: bytes, others: dict[str, bytes]) -> list[Similarity]:
    """Similarity of `candidate` to each reference image (unreadable references skipped)."""
    try:
        h = dhash(candidate)
    except OSError:
        return []
    out = []
    for name, data in others.items():
        try:
            out.append(
                Similarity(
                    name,
                    round(hamming(h, dhash(data)), 4),
                    round(colour_overlap(candidate, data), 4),
                )
            )
        except OSError:
            continue
    return sorted(out, key=lambda s: s.distance)
