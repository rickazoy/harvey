"""Grounded, approval-only lead nurturing.

Never fabricates property values, case studies, MLS comps or appointments.
Generates reviewable drafts, not provider sends. Existing Harvey mail
autopilot must NOT be used for consumer leads.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Mapping
from .qualification import Qualification

@dataclass(frozen=True)
class NurtureDraft:
    stage: str
    subject: str
    body: str
    status: str
    channel: str
    requires_human_approval: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def draft_nurture(
    prospect: Mapping[str, Any] | object,
    qualification: Qualification,
    *,
    sender_company: str = "ProRealtorAI",
) -> NurtureDraft:
    name = (prospect.get("first_name") if isinstance(prospect, Mapping)
        else getattr(prospect, "first_name", "")) or "there"
    first = str(name).strip().split()[0][:50]
    company = (prospect.get("company") if isinstance(prospect, Mapping)
        else getattr(prospect, "company", "")) or ""
    company = str(company).strip()[:100]
    if not qualification.ready_for_review:
        return NurtureDraft("research","", "", "blocked", "email",True,
            "Incomplete eligibility or documented sourcing/consent")
    if qualification.segment == "brokerage_b2b":
        subject = "Question about your brokerage's lead response process"
        body = (
            f"Hi {first},\n\n"
            f"At {sender_company}, we help brokerages review their lead response "
            "process and connect inquiries to the right agent. "
            + (f"I noticed your company is {company}. " if company else "")
            + "Are you the right person to speak with about lead routing and "
            "follow-up at your brokerage?\n\n"
            "If this isn't relevant, reply 'no thanks' and I'll close the loop."
        )
        reason = "Drafted for a verified brokerage prospect; business email laws still apply"
    else:
        subject = "Following up on your real estate inquiry"
        body = (
            f"Hi {first},\n\n"
            "Thank you for contacting us about your real estate search. "
            "What type of property are you interested in, and what timeline "
            "are you considering? Your agent can help you review options.\n\n"
            "If you'd rather not receive follow-ups, just let us know."
        )
        reason = "Requires original lead consent and channel-specific permission before send"
    return NurtureDraft("intro",subject,body,"pending_approval","email",True,reason)
