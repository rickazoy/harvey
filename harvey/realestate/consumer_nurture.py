"""Consent-aware real-estate nurture plans for buyers and sellers.

No delivery calls, no subscription emulation, no promises of listings,
property values, financing or showings. Every step is review-only.
Provider-specific consents and suppressions are rechecked at send time
by the actual brokerage messaging dispatcher, outside Harvey.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .consumer import ConsumerLead, LeadAssessment

class NurtureStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: int = Field(ge=1, le=10)
    offset_days: int = Field(ge=0, le=180)
    channel: Literal["email", "sms", "voice"]
    subject: str = Field(max_length=200)
    body: str = Field(min_length=3, max_length=1200)
    state: Literal["pending_approval"] = "pending_approval"
    requires_human_approval: bool = True
    send_allowed: bool = False

class NurturePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["buyer", "seller"]
    status: Literal["pending_review", "blocked_consent", "suppressed"]
    policy_reason: str
    steps: list[NurtureStep] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=lambda: [
        "contact_opted_out", "consent_expired_or_revoked",
        "agent_handled_conversation", "appointment_confirmed",
        "lead_closed_or_disqualified", "brokerage_policy_changed",
    ])
    marketing_contact_authorized: bool = False

def create_nurture_plan(
    lead: ConsumerLead, assessment: LeadAssessment, *,
    brokerage_name: str,
) -> NurturePlan:
    """Create grounded, never-sent review drafts from submitted inquiry data."""
    if assessment.kind != lead.kind:
        raise ValueError("Assessment must belong to the same buyer/seller kind")
    if assessment.tier == "suppressed" or lead.status == "opted_out":
        return NurturePlan(kind=lead.kind,status="suppressed",
            policy_reason="Consumer opt-out; no further nurturing")
    if not assessment.can_prepare_nurture:
        return NurturePlan(kind=lead.kind,status="blocked_consent",
            policy_reason="No documented, active marketing consent on a contactable channel")

    name = (lead.first_name or "there").split()[0][:40]
    sender = brokerage_name.strip()[:100]
    if not sender:
        raise ValueError("Brokerage display name required for every draft")
    # Email-only in the initial release. SMS and outbound calls have separate
    # higher-risk consent/disclosure and recipient-timezone requirements.
    if "email" not in assessment.marketing_channels or not lead.email:
        return NurturePlan(kind=lead.kind,status="blocked_consent",
            policy_reason="Pilot nurturing is email-only and needs explicit email marketing consent")
    if lead.kind == "buyer":
        opener = (
            f"Hi {name},\n\n"
            f"Thanks for contacting {sender} about your home search. "
            "Would you like an agent to help narrow down properties or arrange "
            "a showing for a home you selected? We will confirm availability "
            "with the appropriate listing source before proposing any times.\n\n"
            "You can ask us to stop follow-up emails at any time."
        )
        followup = (
            f"Hi {name},\n\n"
            "Checking in on your home search. Has your preferred property type "
            "or moving timeline changed? An agent can review options with you "
            "based on the current listings you are authorized to access.\n\n"
            "Reply STOP EMAIL to end these updates."
        )
        third = (
            f"Hi {name},\n\n"
            "If you are still considering a move, let us know what would be most "
            "helpful next: property comparisons, neighborhood questions, or a "
            "conversation with an agent. No obligation to continue.\n\n"
            "Reply STOP EMAIL to opt out."
        )
    else:
        opener = (
            f"Hi {name},\n\n"
            f"Thanks for your property inquiry with {sender}. "
            "Would you like an agent to discuss your goals and next steps "
            "for a potential home sale? We can arrange a conversation before "
            "preparing a market analysis from permitted, current property data.\n\n"
            "You can ask us to stop follow-up emails at any time."
        )
        followup = (
            f"Hi {name},\n\n"
            "Following up on your property plans. Has your intended timing "
            "changed, or would you like a conversation about preparing your "
            "home for sale? Any market analysis would be reviewed by your agent.\n\n"
            "Reply STOP EMAIL to end these updates."
        )
        third = (
            f"Hi {name},\n\n"
            "If you are still exploring a sale, you can ask an agent about "
            "next steps or an evidence-based market analysis. There is no "
            "need to respond if your plans have changed.\n\n"
            "Reply STOP EMAIL to opt out."
        )

    if lead.requested_showing or lead.requested_valuation or lead.requested_consultation:
        offsets = (0, 3, 10)
    else:
        offsets = (0, 5, 14)
    steps = [
        NurtureStep(step=i + 1,offset_days=offset,channel="email",
            subject=("Your real estate inquiry" if i == 0
                else "Checking in on your real estate plans"),
            body=message)
        for i, (offset,message) in enumerate(zip(
            offsets, (opener,followup,third)))
    ]
    return NurturePlan(kind=lead.kind,status="pending_review",
        policy_reason="First-party inquiry and explicitly recorded email marketing consent; broker review required",
        steps=steps)
