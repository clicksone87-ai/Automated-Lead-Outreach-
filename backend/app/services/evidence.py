"""Evidence capture.

Idempotent: the same content for the same lead/kind returns the existing record.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import StorageAdapter, sha256_hex
from app.db.models import Evidence, EvidenceKind

EXCERPT_CHARS = 2000


async def capture(
    session: AsyncSession,
    storage: StorageAdapter,
    *,
    kind: EvidenceKind,
    lead_id: uuid.UUID | None = None,
    business_id: uuid.UUID | None = None,
    url: str | None = None,
    content: bytes | None = None,
    text: str | None = None,
    file_ext: str = "bin",
    data: dict[str, Any] | None = None,
) -> Evidence:
    """Store content (if any) under data/evidence and record it. Returns the Evidence row."""
    if content is None and text is not None:
        content = text.encode("utf-8")
    hash_source = content if content is not None else json.dumps(
        {"url": url, "data": data}, sort_keys=True, default=str
    ).encode()
    digest = sha256_hex(hash_source)

    file_path: str | None = None
    if content is not None:
        owner = str(lead_id or business_id or "shared")
        file_path = storage.save_bytes(f"evidence/{owner}/{kind}/{digest[:24]}.{file_ext}", content)

    values = {
        "id": uuid.uuid4(),
        "lead_id": lead_id,
        "business_id": business_id,
        "kind": str(kind),
        "url": url,
        "file_path": file_path,
        "content_hash": digest,
        "excerpt": (text or "")[:EXCERPT_CHARS] or None,
        "data": data or {},
    }
    stmt = insert(Evidence).values(**values).on_conflict_do_nothing(
        constraint="uq_evidence_lead_hash"
    )
    await session.execute(stmt)
    existing = await session.execute(
        select(Evidence).where(
            Evidence.lead_id.is_(None) if lead_id is None else Evidence.lead_id == lead_id,
            Evidence.kind == str(kind),
            Evidence.content_hash == digest,
        ).order_by(Evidence.captured_at).limit(1)
    )
    return existing.scalar_one()
