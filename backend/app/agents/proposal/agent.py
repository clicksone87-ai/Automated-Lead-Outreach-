"""Proposal agent: writes a personalised email from verified facts only.

Every factual claim must cite this lead's stored evidence or the draft is rejected (one retry,
then human review). At autonomy stage 1 every proposal waits for human approval.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar, Self

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.adapters.llm import LLMOutputError, LLMRequest
from app.db.models import (
    ApprovalStatus,
    Audit,
    Brief,
    Business,
    Contact,
    ContactType,
    Evidence,
    Lead,
    LeadStatus,
    LogoConcept,
    Preview,
    Proposal,
)
from app.orchestrator.agents import AgentContext, AgentOutcome, register
from app.policy.engine import PolicyEngine
from app.services import runtime_settings
from app.services.unsubscribe import make_token, unsubscribe_url

logger = logging.getLogger(__name__)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_FINDINGS = 4


class Claim(BaseModel):
    text: str = Field(
        max_length=220, description="One factual sentence about their website/business."
    )
    evidence_id: str = Field(description="The evidence_id of the fact this sentence is based on.")


class ProposalDraft(BaseModel):
    subject: str = Field(max_length=90)
    preview_text: str = Field(max_length=120)
    greeting: str = Field(max_length=80)
    intro: str = Field(max_length=700, description="Why we are writing; no factual claims here.")
    claims: list[Claim] = Field(max_length=MAX_FINDINGS)
    closing: str = Field(max_length=500)
    signoff: str = Field(max_length=40)

    @classmethod
    def mock(cls, context: dict[str, Any]) -> Self:
        facts = context.get("facts", [])
        return cls(
            subject=f"A few ideas for {context.get('name', 'your website')}",
            preview_text="We looked at your website and prepared some free drafts.",
            greeting="Hello,",
            intro="We help local businesses get more calls from their website.",
            claims=[Claim(text=f["title"], evidence_id=f["evidence_id"]) for f in facts[:2]],
            closing="Happy to walk you through the drafts.",
            signoff="Best regards,",
        )


SYSTEM = (
    "You write short, respectful cold emails for a small web-design agency. Plain language, "
    "no hype, no pressure, no fake urgency, no flattery. Every factual statement about the "
    "recipient's website must be one of the provided findings, returned as a claim with its "
    "evidence_id copied exactly. Never invent numbers, prices, results, reviews or promises. "
    "Write in the requested language."
)


ASSET_NAMES = {"logo": "logo concepts", "preview": "a website preview", "audit": "a website audit"}


def _assets_line(attached: list[str]) -> str:
    """What the email may say about drafts: only what is really attached."""
    names = [ASSET_NAMES[k] for k in ("logo", "preview", "audit") if k in attached]
    if names:
        return f"We already prepared free drafts for them, attached to this email: {', '.join(names)}.\n"
    return (
        "Nothing is attached. Offer to prepare free drafts (logo concepts or a website preview) "
        "if they are interested; never say anything is attached or already made.\n"
    )


class ProposalAgent:
    name: ClassVar[str] = "proposal"
    expensive: ClassVar[bool] = True

    async def can_resume(self, ctx: AgentContext, lead_id: uuid.UUID) -> bool:
        """After a human approves the (email) proposal, the pipeline continues to Outreach."""
        latest = (
            await ctx.session.execute(
                select(Proposal)
                .where(Proposal.lead_id == lead_id)
                .order_by(Proposal.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return bool(
            latest and latest.approval_status == ApprovalStatus.APPROVED and latest.to_email
        )

    async def run(self, ctx: AgentContext, lead_id: uuid.UUID) -> AgentOutcome:
        s = ctx.session
        lead = await s.get(Lead, lead_id)
        brief_row = await s.get(Brief, lead_id)
        if lead is None or brief_row is None or lead.business_id is None:
            return AgentOutcome(False, None, "missing lead/brief")
        brief = brief_row.data
        if "proposal" not in brief.get("agents_to_run", []):
            return AgentOutcome(True, None, "no proposal for this lead")
        contact_id = lead.pipeline_state.get("contact_id")
        if not contact_id:
            return AgentOutcome(False, LeadStatus.NO_CONTACT_ROUTE, "no contact route")
        verdict = await PolicyEngine(s).evaluate_drafting(lead)
        if not verdict.allowed:
            return AgentOutcome(False, None, f"policy: {verdict.failures[0].reason}")
        business = await s.get(Business, lead.business_id)
        contact = await s.get(Contact, uuid.UUID(contact_id))
        assert business is not None
        assert contact is not None
        language = lead.language or "en"

        facts = await self._facts(ctx, lead)
        allowed = {f["evidence_id"] for f in facts}
        attachments, preview_url = await self._attachments(ctx, lead)
        draft: ProposalDraft | None = None
        problem = ""
        for attempt in (1, 2):
            try:
                candidate = await ctx.adapters.llm.structured(
                    s,
                    LLMRequest(
                        agent=self.name,
                        system=SYSTEM,
                        task=self._task(
                            business=business,
                            brief=brief,
                            facts=facts,
                            language=language,
                            attempt=attempt,
                            problem=problem,
                            attached=sorted({a["kind"].split("_")[0] for a in attachments}),
                        ),
                        lead_id=lead.id,
                        lane="heavy" if lead.lane == "heavy" else "fast",
                        max_output_tokens=2000,
                        mock_context={"name": business.name, "facts": facts},
                    ),
                    ProposalDraft,
                )
            except LLMOutputError as exc:
                problem = str(exc)[:300]
                continue
            unmapped = [c.text for c in candidate.claims if c.evidence_id not in allowed]
            if not unmapped:
                draft = candidate
                break
            problem = f"claims without a provided evidence_id: {unmapped[:3]}"
            logger.info("proposal draft rejected (attempt %d): %s", attempt, problem)
        if draft is None:
            return AgentOutcome(False, LeadStatus.NEEDS_REVIEW, f"proposal rejected: {problem}")

        email = contact.value if contact.type == ContactType.EMAIL else None
        token = make_token(ctx.settings, business.id, email or f"lead:{lead.id}")
        sender = {
            "name": ctx.settings.sender_name,
            "businessName": ctx.settings.sender_business_name,
            "postalAddress": ctx.settings.sender_postal_address,
            "portfolioUrl": ctx.settings.portfolio_url,
        }
        assert ctx.adapters.renderer is not None
        rendered = await ctx.adapters.renderer.render(
            "proposal",
            {
                "language": language,
                "subject": draft.subject,
                "previewText": draft.preview_text,
                "greeting": draft.greeting,
                "intro": draft.intro,
                "findings": [c.text for c in draft.claims],
                "closing": draft.closing,
                "signoff": draft.signoff,
                "sender": sender,
                "unsubscribeUrl": unsubscribe_url(ctx.settings, token),
                "previewUrl": preview_url,
                "attachments": {
                    "logos": any(a["kind"] == "logo" for a in attachments),
                    "preview": any(a["kind"].startswith("preview") for a in attachments),
                    "audit": any(a["kind"] == "audit_pdf" for a in attachments),
                },
            },
        )
        rs = await runtime_settings.load(s)
        auto = rs.autonomy_stage >= 2 and email is not None
        version = (
            int(
                (
                    await s.execute(
                        select(func.coalesce(func.max(Proposal.version), 0)).where(
                            Proposal.lead_id == lead.id
                        )
                    )
                ).scalar_one()
            )
            + 1
        )
        proposal = Proposal(
            lead_id=lead.id,
            version=version,
            language=language,
            subject=draft.subject,
            body_html=rendered.html,
            body_text=rendered.text,
            attachments=attachments,
            claims=[
                c.model_dump()
                | {"source": next((f for f in facts if f["evidence_id"] == c.evidence_id), {})}
                for c in draft.claims
            ],
            template_variant="proposal_v1",
            approval_status=ApprovalStatus.APPROVED if auto else ApprovalStatus.PENDING,
            approved_by="autonomy_stage_" + str(rs.autonomy_stage) if auto else None,
            to_email=email,
            contact_id=contact.id,
            draft={
                "greeting": draft.greeting,
                "intro": draft.intro,
                "closing": draft.closing,
                "signoff": draft.signoff,
                "preview_text": draft.preview_text,
                "preview_url": preview_url,
                "unsubscribe_url": unsubscribe_url(ctx.settings, token),
            },
        )
        await s.execute(
            update(Proposal)
            .where(
                Proposal.lead_id == lead.id,
                Proposal.approval_status.in_([ApprovalStatus.PENDING, ApprovalStatus.DRAFT]),
            )
            .values(approval_status=ApprovalStatus.SUPERSEDED)
        )
        s.add(proposal)
        await s.flush()
        if email is None:
            return AgentOutcome(
                False, LeadStatus.AWAITING_APPROVAL,
                f"proposal v{version} drafted for a manual route ({contact.type}): a human sends it",
                {"proposal_id": str(proposal.id)},
            )  # fmt: skip
        if auto:
            return AgentOutcome(True, LeadStatus.APPROVED, f"proposal v{version} auto-approved (stage {rs.autonomy_stage})",
                                {"proposal_id": str(proposal.id)})  # fmt: skip
        return AgentOutcome(
            False, LeadStatus.AWAITING_APPROVAL,
            f"proposal v{version} waiting for your approval (Stage 1)", {"proposal_id": str(proposal.id)},
        )  # fmt: skip

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    async def _facts(ctx: AgentContext, lead: Lead) -> list[dict[str, Any]]:
        audit = (
            await ctx.session.execute(select(Audit).where(Audit.lead_id == lead.id))
        ).scalar_one_or_none()
        if audit is None:
            return []
        ids = {
            str(i)
            for i in (
                await ctx.session.execute(select(Evidence.id).where(Evidence.lead_id == lead.id))
            ).scalars()
        }
        findings = [f for f in audit.findings if f.get("evidence_id") in ids]
        findings.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity", "low"), 9))
        return [
            {"evidence_id": f["evidence_id"], "title": f.get("title", ""), "detail": f.get("detail", "")[:240],
             "severity": f.get("severity")}
            for f in findings[:MAX_FINDINGS]
        ]  # fmt: skip

    @staticmethod
    def _task(*, business: Business, brief: dict[str, Any], facts: list[dict[str, Any]], language: str,
              attempt: int, problem: str, attached: list[str] | None = None) -> str:  # fmt: skip
        lines = [
            f"- evidence_id={f['evidence_id']} [{f['severity']}] {f['title']}: {f['detail']}"
            for f in facts
        ]
        services = ", ".join(brief.get("services", [])) or "a website"
        retry = (
            f"\nYour previous draft was rejected: {problem}. Use only the listed evidence_ids.\n"
            if attempt > 1
            else ""
        )
        return (
            f"Write a first email to {business.name} ({brief.get('business_type') or 'local business'}"
            f"{', ' + str(brief.get('location')) if brief.get('location') else ''}) in language '{language}'.\n"
            f"We can help with: {services}. "
            + _assets_line(attached or [])
            + "Findings you may use as claims (copy the evidence_id exactly):\n"
            + ("\n".join(lines) if lines else "(none — make no factual claims)")
            + retry
            + "\nKeep it under 150 words. Subject: specific and calm, no clickbait."
        )

    @staticmethod
    async def _attachments(
        ctx: AgentContext, lead: Lead
    ) -> tuple[list[dict[str, Any]], str | None]:
        out: list[dict[str, Any]] = []
        logos = (
            (
                await ctx.session.execute(
                    select(LogoConcept)
                    .where(LogoConcept.lead_id == lead.id, LogoConcept.qa_status == "passed")
                    .order_by(LogoConcept.variant)
                )
            )
            .scalars()
            .all()
        )
        for lg in logos[:3]:
            small = (lg.typography or {}).get("png_512") or lg.png_path
            out.append({"kind": "logo", "path": small, "filename": f"logo-concept-{lg.variant}.png",
                        "content_type": "image/png"})  # fmt: skip
        preview = (
            await ctx.session.execute(select(Preview).where(Preview.lead_id == lead.id))
        ).scalar_one_or_none()
        preview_url = None
        if preview is not None and preview.qa_status == "passed":
            if ctx.settings.public_links_enabled and preview.preview_url:
                preview_url = preview.preview_url
            shots = preview.screenshots or {}
            for key in ("desktop", "mobile"):
                if shots.get(key):
                    out.append({"kind": f"preview_{key}", "path": shots[key], "filename": f"website-preview-{key}.png",
                                "content_type": "image/png"})  # fmt: skip
            if preview.pdf_path:
                out.append({"kind": "preview_pdf", "path": preview.pdf_path, "filename": "website-preview.pdf",
                            "content_type": "application/pdf"})  # fmt: skip
        audit = (
            await ctx.session.execute(select(Audit).where(Audit.lead_id == lead.id))
        ).scalar_one_or_none()
        if audit is not None and audit.report_pdf_path:
            out.append({"kind": "audit_pdf", "path": audit.report_pdf_path, "filename": "website-audit.pdf",
                        "content_type": "application/pdf"})  # fmt: skip
        return out, preview_url


register(ProposalAgent())
