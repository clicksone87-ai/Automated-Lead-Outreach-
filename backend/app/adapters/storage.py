"""Local file storage under DATA_DIR.

Paths in the database are relative to DATA_DIR, so moving storage is a config change.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import ClassVar, Protocol


class StorageAdapter(Protocol):
    name: ClassVar[str]

    def save_bytes(self, rel_path: str, data: bytes) -> str: ...
    def read_bytes(self, rel_path: str) -> bytes: ...
    def exists(self, rel_path: str) -> bool: ...
    def absolute(self, rel_path: str) -> Path: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalStorage:
    name: ClassVar[str] = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def absolute(self, rel_path: str) -> Path:
        rel = PurePosixPath(rel_path.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"unsafe storage path: {rel_path!r}")
        path = (self.root / Path(*rel.parts)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"path escapes storage root: {rel_path!r}")
        return path

    def save_bytes(self, rel_path: str, data: bytes) -> str:
        path = self.absolute(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return PurePosixPath(rel_path.replace("\\", "/")).as_posix()

    def read_bytes(self, rel_path: str) -> bytes:
        return self.absolute(rel_path).read_bytes()

    def exists(self, rel_path: str) -> bool:
        return self.absolute(rel_path).exists()
