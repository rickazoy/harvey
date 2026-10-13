"""Broker-owned inbound lead generation campaigns.

Harvey can create *offers and intake flows* for the brokerage's authorized
site, not create consumer identities from tax files, MLS records or search
results. A person becomes a lead only when they intentionally submit a form
with separately recorded channel preferences and disclosure evidence.

These campaigns are DRAFTS. Publishing and paid-ad audience configuration
must be broker-reviewed and comply with housing advertising platform policies.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

from .consumer import LeadKind, LeadSource

CampaignType = Literal[
    "buyer_search", "showing_request", "open_house_rsvp",
    "seller_valuation", "seller_consultation",
]
CONTENT: dict[CampaignType, tuple[LeadKind, LeadSource, str, str, str]] = {
    "buyer_search": (
        "buyer", "buyer_registration",
        "Find a home that fits your plans",
        "Tell us the type of property you are looking for and your expected "
        "moving timeline. A local agent can follow up about your options.",
        "Tell us your preferences",
    ),
    "showing_request": (
        "buyer", "first_party_idx",
        "Have a property in mind?",
        "Request a conversation about a property you found. An agent can "
        "confirm its current listing status and available showing options.",
        "Ask about a showing",
    ),
    "open_house_rsvp": (
        "buyer", "open_house_form",
        "Planning to visit an open house?",
        "Share your contact preference to ask an agent about the visit. "
        "Details and availability must be confirmed with the listing agent.",
        "Request open house details",
    ),
    "seller_valuation": (
        "seller", "seller_valuation",
        "Explore your home's potential market position",
        "Ask for an agent-reviewed, evidence-based comparative market "
        "analysis. No estimate is guaranteed without current source data.",
        "Request a market conversation",
    ),
    "seller_consultation": (
        "seller", "seller_consultation_form",
        "Thinking about selling?",
        "A local agent can discuss your priorities, preferred timeline, "
        "and the practical steps involved in preparing for a sale.",
        "Speak with an agent",
    ),
}

@dataclass(frozen=True)
class LeadGenerationCampaign:
    kind: LeadKind
    campaign_type: CampaignType
    source: LeadSource
    headline: str
    description: str
    call_to_action: str
    channel: str
    status: str
    required_form_fields: tuple[str, ...]
    consent_fields: tuple[str, ...]
    approval_note: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["required_form_fields"] = list(self.required_form_fields)
        data["consent_fields"] = list(self.consent_fields)
        return data

def create_lead_generation_campaign(
    campaign_type: CampaignType, *,
    brokerage_name: str,
    owned_site_origin: str,
) -> LeadGenerationCampaign:
    """Produce a broker-reviewable campaign that collects future opt-ins.

    Campaigns cannot be deployed without approved brokerage site ownership,
    HTTPS origin, source event capture, anti-bot checks and consent logging.
    """
    from urllib.parse import urlsplit
    if campaign_type not in CONTENT:
        raise ValueError("Unsupported first-party lead generation campaign")
    broker = brokerage_name.strip()
    if not broker or len(broker)>100:
        raise ValueError("Verified brokerage name is required")
    origin = urlsplit(owned_site_origin)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password:
        raise ValueError("Brokerage-owned HTTPS site required")
    kind,source,headline,description,cta = CONTENT[campaign_type]
    return LeadGenerationCampaign(
        kind=kind,campaign_type=campaign_type,source=source,
        headline=headline,description=description,call_to_action=cta,
        channel="brokerage_owned_website",status="pending_broker_approval",
        required_form_fields=("first_name","last_name","email_or_phone","timeline"),
        consent_fields=("source_event_id","source_account_id","proof_id",
                        "disclosure_text","disclosure_version","captured_at",
                        "email_marketing","sms_marketing","voice_marketing"),
        approval_note=(
            "Verify broker-owned site, accessibility, privacy notice, fair "
            "housing and source permissions before publishing. No marketing "
            "consent is inferred from merely viewing or completing the form."
        ),
    )
