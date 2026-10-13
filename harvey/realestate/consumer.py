"""Broker-scoped buyer/seller lead intelligence.

Only consent-backed first-party inquiries are contact leads. Public property
signals are research opportunities, NOT homeowner contact permissions.

Stateless policy: records are processed in memory; customer data is persisted
only in the broker-authorized ProRealtorAI receiver, not Harvey's global SQLite
business-development database. All marketing remains human approved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LeadKind = Literal["buyer", "seller"]
LeadSource = Literal[
    "first_party_idx", "open_house_form", "buyer_registration",
    "seller_valuation", "agent_referral_with_consent",
]
LeadStatus = Literal["new", "inquiry", "engaged", "meeting", "closed", "opted_out"]
_EMAIL_DOMAIN_MARKER = "@"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must have timezone information")
    return value.astimezone(timezone.utc)


class ConsentEvidence(BaseModel):
    """Evidence of a real consumer's authorization, not a blanket opt-in."""
    model_config = ConfigDict(extra="forbid")

    proof_id: str = Field(min_length=8, max_length=128)
    disclosure_version: str = Field(min_length=1, max_length=100)
    disclosure_text: str = Field(min_length=20, max_length=3000)
    source_url: str = Field(min_length=12, max_length=1500)
    captured_at: datetime
    email_marketing: bool = False
    sms_marketing: bool = False
    voice_marketing: bool = False

    @field_validator("source_url")
    @classmethod
    def secure_origin(cls, value: str) -> str:
        from urllib.parse import urlsplit
        p = urlsplit(value)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            raise ValueError("Consent evidence must reference an HTTPS source")
        return value

    @field_validator("captured_at")
    @classmethod
    def timestamp_aware(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def channel_evidence(self) -> "ConsentEvidence":
        if self.sms_marketing and not self.disclosure_text:
            raise ValueError("SMS disclosure required")
        return self


class ConsumerLead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: LeadSource
    source_event_id: str = Field(min_length=8, max_length=200)
    kind: LeadKind
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(default="", max_length=120)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=35)
    status: LeadStatus = "new"
    consent: ConsentEvidence
    preferred_cities: list[str] = Field(default_factory=list, max_length=20)
    property_types: list[str] = Field(default_factory=list, max_length=15)
    timeline: Literal[
        "immediate", "1_3_months", "3_6_months", "6_12_months", "just_looking",
        "undecided",
    ] = "undecided"
    budget_min: int | None = Field(default=None, ge=0, le=100_000_000)
    budget_max: int | None = Field(default=None, ge=0, le=100_000_000)
    property_address: str | None = Field(default=None, max_length=500)
    requested_showing: bool = False
    requested_valuation: bool = False
    requested_consultation: bool = False
    broker_notes: str | None = Field(default=None, max_length=1000)
    inquiry_message: str | None = Field(default=None, max_length=2000)
    submitted_at: datetime
    source_account_id: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        email = value.strip().lower()
        if len(email) < 5 or email.count(_EMAIL_DOMAIN_MARKER) != 1:
            raise ValueError("Valid contact email required")
        local, domain = email.split("@")
        if not local or "." not in domain or domain.startswith("."):
            raise ValueError("Valid contact email required")
        return email

    @field_validator("first_name", "last_name")
    @classmethod
    def normal_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("preferred_cities", "property_types")
    @classmethod
    def bounded_preferences(cls, values: list[str]) -> list[str]:
        return [v.strip()[:100] for v in values if isinstance(v, str) and v.strip()]

    @field_validator("submitted_at")
    @classmethod
    def submitted_timestamp(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def consistency(self) -> "ConsumerLead":
        # Opt-outs may arrive with an event identifier but no reachable
        # destination. All other validation still applies to their
        # provenance, timestamps and consumer-expressed inquiry type.
        if self.status != "opted_out" and not self.email and not self.phone:
            raise ValueError("A buyer/seller inquiry needs a reply address or phone")
        if not self.first_name:
            raise ValueError("First name is required")
        if self.consent.captured_at > self.submitted_at:
            raise ValueError("Consent cannot be recorded after submission")
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_min > self.budget_max:
                raise ValueError("Budget minimum cannot exceed maximum")
        if self.kind == "buyer" and self.requested_valuation:
            raise ValueError("Valuation requests belong to seller inquiries")
        if self.kind == "seller" and self.requested_showing:
            raise ValueError("Showing requests belong to buyer inquiries")
        return self


class LeadAssessment(BaseModel):
    """Evidence and priority are separate from channel-specific permission."""
    model_config = ConfigDict(extra="forbid")

    kind: LeadKind
    score: int = Field(ge=0, le=100)
    tier: Literal["hot", "warm", "nurture", "suppressed"]
    reasons: list[str]
    missing_information: list[str]
    can_prepare_nurture: bool
    marketing_channels: list[Literal["email", "sms", "voice"]]
    requires_human_review: bool = True
    source_is_first_party: bool = True

    @property
    def needs_immediate_agent_attention(self) -> bool:
        return self.tier == "hot"


def lead_identity(lead: ConsumerLead, brokerage_id: str) -> str:
    """A broker-scoped dedup key; no cross-customer lookup or PII in logs."""
    if not brokerage_id.strip():
        raise ValueError("Brokerage scope is mandatory for any lead identity")
    identity = lead.source_account_id + "\0" + lead.source + "\0" + lead.source_event_id
    return sha256((brokerage_id + "\0" + identity).encode()).hexdigest()


def qualify_consumer_lead(
    lead: ConsumerLead, *, allowed_sources: set[str] | frozenset[str],
    now: datetime | None = None,
) -> LeadAssessment:
    """Prioritize only consumer-expressed intent and verified source evidence.

    Never score zip, race, ethnicity, disability, age, family, religion, gender,
    neighborhood demographics, property value, financing type or owner distress.
    Geographic preferences may guide later MLS search, never this priority.
    """
    clock = _aware(now or datetime.now(timezone.utc))
    if lead.source not in allowed_sources:
        raise ValueError("Ingestion credential does not authorize this lead source")
    if lead.consent.captured_at > clock:
        raise ValueError("Future consent evidence not accepted")
    if lead.submitted_at > clock:
        raise ValueError("Future lead submission not accepted")
    if (clock - lead.submitted_at).days > 365:
        raise ValueError("Stale first-party inquiry requires a new consent review")

    allowed_channels: list[Literal["email", "sms", "voice"]] = []
    if lead.consent.email_marketing and lead.email:
        allowed_channels.append("email")
    if lead.consent.sms_marketing and lead.phone:
        allowed_channels.append("sms")
    if lead.consent.voice_marketing and lead.phone:
        allowed_channels.append("voice")

    if lead.status == "opted_out":
        return LeadAssessment(
            kind=lead.kind, score=0, tier="suppressed",
            reasons=["Consumer opted out"], missing_information=[],
            can_prepare_nurture=False, marketing_channels=[],
        )

    # This is not an automatic send authorization; a destination-specific
    # suppression lookup and human approval are still mandatory.
    if not allowed_channels:
        return LeadAssessment(
            kind=lead.kind, score=0, tier="nurture",
            reasons=["First-party inquiry; marketing channel consent unavailable"],
            missing_information=["Explicit channel permission"],
            can_prepare_nurture=False, marketing_channels=[],
        )

    score = 20
    reasons = ["Documented first-party inquiry with approved source"]
    missing: list[str] = []
    timeline_points = {
        "immediate": 30,
        "1_3_months": 22,
        "3_6_months": 12,
        "6_12_months": 4,
        "just_looking": 0,
        "undecided": 0,
    }
    points = timeline_points[lead.timeline]
    score += points
    if points:
        reasons.append("Consumer-provided timeline: " + lead.timeline)
    else:
        missing.append("Consumer's expected timeline")

    if lead.kind == "buyer":
        if lead.requested_showing:
            score += 30
            reasons.append("Explicit showing request")
        if lead.property_types:
            score += 10
            reasons.append("Property preferences supplied")
        if lead.preferred_cities:
            score += 10
            reasons.append("Consumer supplied search area")
        if not lead.property_types:
            missing.append("Desired property type")
    else:
        if lead.requested_valuation:
            score += 30
            reasons.append("Explicit seller valuation request")
        if lead.requested_consultation:
            score += 20
            reasons.append("Seller consultation requested")
        if lead.property_address:
            score += 10
            reasons.append("Property location supplied by owner or inquirer")
        else:
            missing.append("Property address provided by consumer")

    if lead.inquiry_message:
        score += 5
        reasons.append("Consumer provided additional inquiry details")

    score = min(100, score)
    tier: Literal["hot", "warm", "nurture", "suppressed"] = (
        "hot" if score >= 75 else "warm" if score >= 50 else "nurture"
    )
    return LeadAssessment(
        kind=lead.kind, score=score, tier=tier,
        reasons=reasons, missing_information=missing,
        can_prepare_nurture=True, marketing_channels=allowed_channels,
    )
