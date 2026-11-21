"""Contact enrichment agent: finds a usable contact route before any drafting money is spent.

Checked in order: an email in the lead's own post, the post author's public Instagram bio,
link-in-bio pages (Scout), the business website, then the platform's public reply or profile
DM (manual). Emails must pass verification and are never guessed.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.apify import ApifyRunError
from app.adapters.fetcher import FetchError
from app.agents.contact_enrichment.extract import (
    candidate_pages,
    extract_contact_forms,
    extract_emails,
    rank_email,
)
from app.agents.scout.client import ScoutClient
from app.agents.scout.parsers import LINK_HUBS, is_link_hub
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
from app.orchestrator.budget import BudgetExceededError
from app.services import evidence
from app.services.dedup import PLATFORM_DOMAINS, normalize_domain
from app.services.not_useful import NotUsefulReason
from app.services.not_useful_leads import stop

logger = logging.getLogger(__name__)

PLATFORM_SOURCES = {
    "reddit",
    "x",
    "meta",
    "rss",
    "search_api",
    "mock_demo",
    "social_x",
    "social_instagram",
    "social_facebook",
    "social_reddit",
    "scout_profiles",
}
#: Profile leads (no post to reply to): the manual route is a DM on the profile.
PROFILE_SOURCES = {"scout_profiles"}
INSTAGRAM_PROFILE_ACTOR = "apify/instagram-profile-scraper"
MAX_LINK_HUBS = 2
_URL = re.compile(r"https?://[^\s)\"'<>,]+")
MAX_PAGES = 6
MAX_VERIFICATIONS = 3
USABLE = {VerificationStatus.VALID, VerificationStatus.CATCH_ALL}

DISPOSABLE_DOMAINS = {
    "10minutemail.com",
    "mailinator.com",
    "guerrillamail.com",
    "temp-mail.org",
}


def track_for_contact(contact: Contact | None) -> str:
    """Return Track A only for a published, valid, MX-usable email."""
    if contact is None or contact.type != ContactType.EMAIL:
        return "track_b"
    if contact.verification_status not in (VerificationStatus.VALID, VerificationStatus.CATCH_ALL):
        return "track_b"
    value = contact.value.strip().casefold()
    if "@" not in value:
        return "track_b"
    domain = value.rsplit("@", 1)[1]
    if domain in DISPOSABLE_DOMAINS:
        return "track_b"
    return "track_a"


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
        if lead is None:
            return AgentOutcome(False, None, "lead not found")
        if lead.business_id is None:
            return stop(lead, NotUsefulReason.NO_CONTACT, "lead has no business record")
        business = await s.get(Business, lead.business_id)
        assert business is not None

        candidates = self._from_post(lead)
        if not candidates and lead.source == "social_instagram":
            candidates += await self._instagram_bio(ctx, lead, business)
        if not candidates:
            candidates += await self._link_hubs(ctx, lead, business)
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
        if chosen is None and (
            (lead.trigger_type == TriggerType.EXPLICIT_REQUEST and lead.source in PLATFORM_SOURCES)
            or lead.source in PROFILE_SOURCES
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
        return stop(
            lead,
            NotUsefulReason.NO_CONTACT,
            "no verified email, contact form or platform route found",
            output,
        )

    # ── sources of candidates ────────────────────────────────────────────

    @staticmethod
    def _from_post(lead: Lead) -> list[Candidate]:
        text = "\n".join(filter(None, [lead.title, lead.raw_text]))
        return [Candidate(f.email, lead.source_url, f.context) for f in extract_emails(text)]

    async def _instagram_bio(
        self, ctx: AgentContext, lead: Lead, business: Business
    ) -> list[Candidate]:
        """Social-bio route: the post author's public Instagram profile (one profile, capped).
        Emails published there become candidates (source = the profile URL); a linked website
        becomes the business domain so the normal site crawl can run. Never guesses."""
        apify = ctx.adapters.apify
        handle = await self._author_handle(ctx, lead)
        if apify is None or not handle:
            return []
        try:
            run = await apify.run_actor(
                ctx.session,
                INSTAGRAM_PROFILE_ACTOR,
                {"usernames": [handle]},
                source="social_instagram",
                max_items=1,
                max_charge_usd=0.01,
            )
        except (ApifyRunError, BudgetExceededError) as exc:
            logger.info("instagram bio lookup skipped for %s: %s", handle, exc)
            return []
        if not run.items:
            return []
        p = run.items[0]
        profile_url = f"https://www.instagram.com/{handle}/"
        text_parts = [str(p.get("biography") or "")]
        for key in ("businessEmail", "publicEmail", "email", "contactEmail"):
            if p.get(key):
                text_parts.append(str(p[key]))
        await evidence.capture(
            ctx.session,
            ctx.adapters.storage,
            kind=EvidenceKind.PAGE,
            lead_id=lead.id,
            business_id=business.id,
            url=profile_url,
            text="\n".join(text_parts),
            file_ext="txt",
            data={"purpose": "social_bio", "platform": "instagram", "provider": "apify",
                  "external_url": p.get("externalUrl"), "full_name": p.get("fullName")},
        )  # fmt: skip
        external = (
            normalize_domain(str(p.get("externalUrl") or "")) if p.get("externalUrl") else None
        )
        if (
            external
            and not business.domain
            and external not in PLATFORM_DOMAINS
            and external not in LINK_HUBS
        ):
            business.domain, business.has_website = external, True
        return [
            Candidate(f.email, profile_url, f.context)
            for f in extract_emails("\n".join(text_parts))
        ]

    async def _link_hubs(
        self, ctx: AgentContext, lead: Lead, business: Business
    ) -> list[Candidate]:
        """Scout bio extraction: link-in-bio pages named in the lead or its evidence.

        Only hosts whose robots.txt allows crawling are read (the safe fetcher enforces it);
        a website found there becomes the business domain so the site crawl can run.
        """
        from app.db.models import Evidence  # noqa: PLC0415

        urls = _URL.findall("\n".join(filter(None, [lead.title, lead.raw_text])))
        for data in (
            await ctx.session.execute(select(Evidence.data).where(Evidence.lead_id == lead.id))
        ).scalars():
            urls += [str(u) for u in (data or {}).get("links") or []]
        if business.domain in LINK_HUBS:
            urls.append(f"https://{business.domain}/")
        hubs = list(dict.fromkeys(u for u in urls if is_link_hub(u)))[:MAX_LINK_HUBS]
        if not hubs:
            return []
        client = ScoutClient(ctx.session, ctx.adapters, ctx.settings)
        out: list[Candidate] = []
        for url in hubs:
            page = await client.link_hub(url)
            if page is None:
                continue
            await evidence.capture(
                ctx.session,
                ctx.adapters.storage,
                kind=EvidenceKind.PAGE,
                lead_id=lead.id,
                business_id=business.id,
                url=page.profile_url,
                text="\n".join(filter(None, [page.full_name, page.bio, page.email, *page.links])),
                file_ext="txt",
                data={"purpose": "link_in_bio", "provider": "scout", "website": page.website,
                      "links": page.links},
            )  # fmt: skip
            if page.email:
                out.append(
                    Candidate(page.email, page.profile_url, f"link in bio: {page.bio[:160]}")
                )
            site = normalize_domain(page.website) if page.website else None
            if site and (not business.domain or business.domain in LINK_HUBS):
                business.domain, business.has_website = site, True
        return out

    @staticmethod
    async def _author_handle(ctx: AgentContext, lead: Lead) -> str | None:
        from app.db.models import Evidence  # noqa: PLC0415

        rows = (
            await ctx.session.execute(
                select(Evidence.data).where(
                    Evidence.lead_id == lead.id, Evidence.kind == EvidenceKind.POST
                )
            )
        ).scalars()
        for data in rows:
            handle = (data or {}).get("handle") or (data or {}).get("author")
            if handle:
                return str(handle).lstrip("@")
        return None

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
