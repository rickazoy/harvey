"""Optional, consent-backed engagement signals for buyer follow-up priorities.

Zillow *does not* provide a verified public visitor/calculator activity API.
This code accepts ONLY events from a brokerage-owned website or licensed
first-party analytics system. Never call Zillow's private endpoints, scrape
visitors, or import Zillow Pro / Follow Up Boss private insights without a
separate written data sharing agreement and documented API authorization.

A mortgage calculator interaction is a weak *engagement* signal, not proof of
loan eligibility, purchasing power, income, or preapproval.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .consumer import ConsumerLead, LeadAssessment

EventType = Literal[
    "property_viewed",
    "property_saved",
    "site_returned",
    "calculator_interacted",
    "calculator_scenario_saved",
    "tour_requested",
    "agent_question_submitted",
]
AllowedSource = Literal["broker_owned_site", "approved_first_party_analytics"]

class FirstPartyEngagement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # The broker's own analytics backend must bind this activity to a
    # verified first-party registration. No anonymous deanonymization.
    original_lead_event_id: str = Field(min_length=8, max_length=200)
    source_account_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=8, max_length=200)
    event_type: EventType
    source: AllowedSource
    occurred_at: datetime
    consent_proof_id: str = Field(min_length=8, max_length=128)
    # This flag must be independently verified at the receiving backend
    # against a real consumer notice and the original consent proof.
    analytics_consent_recorded: bool = False

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone-aware engagement event required")
        return value.astimezone(timezone.utc)

class IntentInsight(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_qualification_score: int = Field(ge=0, le=100)
    engagement_points: int = Field(ge=0, le=25)
    follow_up_priority: int = Field(ge=0, le=100)
    explanations: list[str]
    event_count: int
    status: Literal["human_review_only", "blocked"]
    automatic_contact_authorized: bool = False
    financial_qualification_inferred: bool = False
    external_zillow_activity_used: bool = False

POINTS = {
    "property_viewed": 1,
    "site_returned": 3,
    "calculator_interacted": 2,
    "property_saved": 5,
    "calculator_scenario_saved": 6,
    "tour_requested": 12,
    "agent_question_submitted": 10,
}
PER_TYPE_CAPS = {
    "property_viewed": 3,
    "site_returned": 6,
    "calculator_interacted": 2,
    "property_saved": 10,
    "calculator_scenario_saved": 6,
    "tour_requested": 12,
    "agent_question_submitted": 10,
}

def evaluate_first_party_intent(
    lead: ConsumerLead,
    assessment: LeadAssessment,
    events: list[FirstPartyEngagement],
    *,
    verified_source_account_id: str,
    consent_for_engagement: bool,
    now: datetime | None = None,
) -> IntentInsight:
    """Returns a human review priority, never a send/financial decision.

    A third-party Zillow event is not accepted as a first-party signal; if a
    sanctioned partner API becomes available, it requires an independently
    designed license-aware adapter (with no model-training rights assumed).
    """
    current = (now or datetime.now(timezone.utc))
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Evaluation time must have timezone")
    current = current.astimezone(timezone.utc)
    if assessment.kind != lead.kind:
        raise ValueError("Assessment kind must match underlying lead")
    if verified_source_account_id != lead.source_account_id:
        raise ValueError("Cross-source account lead linkage forbidden")
    if not consent_for_engagement or assessment.tier == "suppressed" or lead.status == "opted_out":
        return IntentInsight(
            base_qualification_score=assessment.score,
            engagement_points=0,
            follow_up_priority=0,
            explanations=["Contact suppressed or consent to engagement analysis absent"],
            event_count=0,
            status="blocked",
        )
    if lead.kind != "buyer":
        # Seller valuation behavior uses a separate explicit-inquiry evaluator.
        # We should not reinterpret mortgage activity as seller intent.
        return IntentInsight(
            base_qualification_score=assessment.score,
            engagement_points=0,
            follow_up_priority=assessment.score,
            explanations=["Buyer browsing evidence is not used for seller signals"],
            event_count=0,
            status="human_review_only",
        )
    seen: set[str] = set()
    points_by_type: dict[str, int] = {}
    considered: list[FirstPartyEngagement] = []
    for event in events:
        if event.original_lead_event_id != lead.source_event_id:
            raise ValueError("Engagement event cannot be linked to another prospect")
        if event.source_account_id != verified_source_account_id:
            raise ValueError("Cross-tenant or cross-site engagement event forbidden")
        if event.consent_proof_id != lead.consent.proof_id or not event.analytics_consent_recorded:
            raise ValueError("Engagement consent evidence mismatch")
        if event.occurred_at > current + timedelta(minutes=1):
            raise ValueError("Future activity event rejected")
        if event.occurred_at < current - timedelta(days=30):
            continue
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        value = POINTS[event.event_type]
        already = points_by_type.get(event.event_type, 0)
        added = max(0, min(value, PER_TYPE_CAPS[event.event_type] - already))
        points_by_type[event.event_type] = already + added
        if added:
            considered.append(event)
    engagement = min(25, sum(points_by_type.values()))
    priority = min(100, assessment.score + engagement)
    explanations = [
        "Follow-up priority combines consent-backed first-party inquiry and recent engagement.",
        "Engagement is not a mortgage underwriting, affordability or credit decision.",
    ]
    if points_by_type.get("calculator_interacted", 0):
        explanations.append("Buyer adjusted the brokerage-owned payment estimator")
    if points_by_type.get("calculator_scenario_saved", 0):
        explanations.append("Buyer explicitly saved a payment scenario")
    if points_by_type.get("tour_requested", 0):
        explanations.append("Buyer requested a property tour")
    if points_by_type.get("property_saved", 0):
        explanations.append("Buyer saved one or more brokerage-site listings")
    return IntentInsight(
        base_qualification_score=assessment.score,
        engagement_points=engagement,
        follow_up_priority=priority,
        explanations=explanations,
        event_count=len(considered),
        status="human_review_only",
    )
