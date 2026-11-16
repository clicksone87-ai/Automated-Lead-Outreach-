"""Agent 3 — Contact Enrichment.

Finds a usable contact route BEFORE any drafting money is spent:
  1. an email the author published in the lead's own post,
  2. emails / contact forms on the business's own website (homepage + contact/about/imprint
     pages found via links), fetched through the safe fetcher,
  3. for platform posts: the platform's public reply (handled manually, never automated).
Emails are verified through the verification adapter; invalid/unknown ones are never used.
Every contact stores the URL it was found on. Addresses are never guessed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.fetcher import FetchError
from app.agents.contact_enrichment.extract import (
    candidate_pages,
    extract_contact_forms,
    extract_emails,
    rank_email,
)
from app.db.models import (
    Business,
    Contact,
    ContactType,
    EvidenceKind,
    Lead,
    LeadStatus,
    TriggerType,
    VerificationStatus,
)
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.services import evidence

logger = logging.getLogger(__name__)

PLATFORM_SOURCES = {"reddit", "x", "meta", "rss", "search_api", "mock_demo"}
MAX_PAGES = 6
MAX_VERIFICATIONS = 3
USABLE = {VerificationStatus.VALID, VerificationStatus.CATCH_ALL}


@dataclass(slots=True)
class Candidate:
    email: str
    source_url: str
    context: str


class ContactEnrichmentAgent:
    name: ClassVar[str] = "contact_enrichment"
    expensive: ClassVar[bool] = False  # verification is cheap and gated by budget.charge()

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        if lead is None or lead.business_id is None:
            return AgentOutcome(False, LeadStatus.NO_CONTACT_ROUTE, "lead has no business record")
        business = await s.get(Business, lead.business_id)
        assert business is not None

        candidates = self._from_post(lead)
        forms: list[tuple[str, str]] = []
        if business.domain:
            site_candidates, forms = await self._crawl_site(ctx, lead, business)
            candidates += site_candidates

        chosen = await self._verify_best(ctx, lead, business, candidates)
        for form_url, page_url in forms[:1]:
            await self._upsert(
                s,
                business.id,
                kind=ContactType.CONTACT_FORM,
                value=form_url,
                source_url=page_url,
                status=VerificationStatus.NOT_APPLICABLE,
            )
        manual_route = None
        if (
            chosen is None
            and lead.trigger_type == TriggerType.EXPLICIT_REQUEST
            and lead.source in PLATFORM_SOURCES
        ):
            manual_route = await self._upsert(
                s,
                business.id,
                kind=ContactType.PLATFORM_REPLY,
                value=lead.source_url,
                source_url=lead.source_url,
                status=VerificationStatus.NOT_APPLICABLE,
            )
        if chosen is None and forms and manual_route is None:
            manual_route = (
                (
                    await s.execute(
                        select(Contact).where(
                            Contact.business_id == business.id,
                            Contact.type == ContactType.CONTACT_FORM,
                        )
                    )
                )
                .scalars()
                .first()
            )

        output = {"emails_found": len(candidates), "forms_found": len(forms)}
        if chosen is not None:
            lead.pipeline_state = {
                **lead.pipeline_state,
                "contact_id": str(chosen.id),
                "contact_channel": "email",
            }
            return AgentOutcome(
                True,
                LeadStatus.CONTACT_FOUND,
                f"verified email ({chosen.verification_status}) from {chosen.source_url}",
                {**output, "contact": chosen.value},
            )
        if manual_route is not None:
            lead.pipeline_state = {
                **lead.pipeline_state,
                "contact_id": str(manual_route.id),
                "contact_channel": str(manual_route.type),
            }
            return AgentOutcome(
                True,
                LeadStatus.CONTACT_FOUND,
                f"manual route only ({manual_route.type}): a human sends the "
                "message on that channel",
                output,
            )
        return AgentOutcome(
            False,
            LeadStatus.NO_CONTACT_ROUTE,
            "no verified email, contact form or platform route found",
            output,
        )

    # ── sources of candidates ────────────────────────────────────────────

    @staticmethod
    def _from_post(lead: Lead) -> list[Candidate]:
        text = "\n".join(filter(None, [lead.title, lead.raw_text]))
        return [Candidate(f.email, lead.source_url, f.context) for f in extract_emails(text)]

    async def _crawl_site(
        self, ctx: AgentContext, lead: Lead, business: Business
    ) -> tuple[list[Candidate], list[tuple[str, str]]]:
        fetcher = ctx.adapters.fetcher
        home = f"https://{business.domain}/"
        queue: list[str] = [home]
        seen: set[str] = set()
        candidates: list[Candidate] = []
        forms: list[tuple[str, str]] = []
        while queue and len(seen) < MAX_PAGES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                page = await fetcher.get(url)
            except FetchError as exc:
                logger.info("contact crawl skipped %s: %s", url, exc)
                if url == home:
                    business.site_status = "unreachable"
                    business.site_checked_at = datetime.now(UTC)
                continue
            if not page.ok or "html" not in page.headers.get("content-type", "text/html"):
                continue
            html = page.text
            found = extract_emails(html)
            for f in found:
                candidates.append(Candidate(f.email, page.final_url, f.context))
            if found:
                await evidence.capture(
                    ctx.session,
                    ctx.adapters.storage,
                    kind=EvidenceKind.PAGE,
                    lead_id=lead.id,
                    business_id=business.id,
                    url=page.final_url,
                    text="\n".join(f"{f.email}: {f.context}" for f in found),
                    file_ext="txt",
                    data={"purpose": "contact_enrichment"},
                )
            forms += [
                (f.action, page.final_url) for f in extract_contact_forms(html, page.final_url)
            ]
            if url == home:
                queue += candidate_pages(html, page.final_url)
        return candidates, forms

    # ── verification and storage ─────────────────────────────────────────

    async def _verify_best(
        self, ctx: AgentContext, lead: Lead, business: Business, candidates: list[Candidate]
    ) -> Contact | None:
        unique: dict[str, Candidate] = {}
        for c in candidates:
            unique.setdefault(c.email, c)
        ranked = sorted(unique.values(), key=lambda c: -rank_email(c.email, business.domain))
        best: Contact | None = None
        for cand in ranked[:MAX_VERIFICATIONS]:
            existing = (
                await ctx.session.execute(
                    select(Contact).where(
                        Contact.business_id == business.id,
                        Contact.type == ContactType.EMAIL,
                        Contact.value == cand.email,
                    )
                )
            ).scalar_one_or_none()
            if (
                existing is not None
                and existing.verification_status != VerificationStatus.UNVERIFIED
            ):
                status = VerificationStatus(existing.verification_status)
            else:
                result = await ctx.adapters.verification.verify(
                    ctx.session, cand.email, agent=self.name
                )
                status = result.status
                if ctx.adapters.verification.is_mock:
                    used = set(lead.pipeline_state.get("mock_adapters_used", []))
                    lead.pipeline_state = {
                        **lead.pipeline_state,
                        "mock_adapters_used": sorted(used | {"verification"}),
                    }
            contact = await self._upsert(
                ctx.session,
                business.id,
                kind=ContactType.EMAIL,
                value=cand.email,
                source_url=cand.source_url,
                status=status,
            )
            if status == VerificationStatus.VALID:
                return contact
            if status in USABLE and best is None:
                best = contact
        return best

    @staticmethod
    async def _upsert(
        session: AsyncSession,
        business_id: uuid.UUID,
        *,
        kind: ContactType,
        value: str,
        source_url: str,
        status: VerificationStatus,
    ) -> Contact:
        verified_at = datetime.now(UTC) if status in USABLE else None
        stmt = (
            insert(Contact)
            .values(
                id=uuid.uuid4(),
                business_id=business_id,
                type=str(kind),
                value=value,
                source_url=source_url,
                verification_status=str(status),
                verified_at=verified_at,
            )
            .on_conflict_do_update(
                constraint="uq_contact",
                set_={"verification_status": str(status), "verified_at": verified_at},
            )
        )
        await session.execute(stmt)
        return (
            await session.execute(
                select(Contact).where(
                    Contact.business_id == business_id,
                    Contact.type == str(kind),
                    Contact.value == value,
                )
            )
        ).scalar_one()


register(ContactEnrichmentAgent())
