"""Licensed property-market signals used to plan consensual lead generation.

A property opportunity is NOT an identifiable, contactable consumer lead.
This pipeline creates market-research observations for brokers to review.
It does not scrape owner names, numbers, emails, financial hardship or
contact households based on inferred interest.

ProRealtorAI's National MLS Data Gateway must independently verify data
licenses and allowed uses before any MLS-derived signals can enter here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SignalType = Literal[
    "stale_rental", "expired_listing", "withdrawn_listing",
    "price_change", "new_listing",
]
SourceType = Literal[
    "licensed_mls_feed", "authorized_public_records", "broker_owned_data",
]

class PropertyMarketSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: SourceType
    source_code: str = Field(min_length=3,max_length=80)
    source_record_key: str = Field(min_length=3,max_length=200)
    source_url: str = Field(min_length=12,max_length=1500)
    source_license_reference: str = Field(min_length=6,max_length=200)
    broker_licensed_use_verified: bool = False
    signal_type: SignalType
    city: str = Field(min_length=2,max_length=120)
    state: str = Field(min_length=2,max_length=2)
    postal_code: str | None = Field(default=None,max_length=10)
    observed_at: datetime
    days_on_market: int | None = Field(default=None,ge=0,le=3650)
    provider_record_is_active: bool = True
    source_data_fresh: bool = True

    @field_validator("state")
    @classmethod
    def state_code(cls,value:str)->str:
        v=value.upper().strip()
        if len(v)!=2 or not v.isalpha():
            raise ValueError("Two-letter state code required")
        return v

    @field_validator("source_url")
    @classmethod
    def licensed_link(cls,value:str)->str:
        from urllib.parse import urlsplit
        parsed=urlsplit(value)
        if parsed.scheme!="https" or not parsed.hostname:
            raise ValueError("An HTTPS source record is mandatory")
        return value

    @field_validator("observed_at")
    @classmethod
    def aware(cls,value:datetime)->datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone-aware source timestamp required")
        return value.astimezone(timezone.utc)


class PropertyOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_code: str
    source_record_key: str
    signal_type: SignalType
    market_city: str
    market_state: str
    priority: Literal["high","medium","research"]
    evidence: list[str]
    recommended_acquisition_type: Literal["buyer_search","seller_valuation"]
    status: Literal["pending_broker_review"] = "pending_broker_review"
    owner_contact_authorized: bool = False
    personally_identifying_contact_data: bool = False
    next_action: str = (
        "Broker reviews data license and creates a general market-information "
        "campaign with a first-party opt-in form. No direct owner outreach."
    )


def evaluate_property_signal(
    signal: PropertyMarketSignal, *, licensed_source_codes: set[str],
    now:datetime|None=None,
) -> PropertyOpportunity:
    """Only verified feed/licensing evidence may create research opportunities.

    Scores reflect market events, not the owner's predicted willingness,
    creditworthiness or economic hardship.
    """
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    # The allowlist must come from the trusted National MLS Gateway or
    # equivalent broker-signed provider configuration, never the raw record.
    if (not signal.broker_licensed_use_verified or
            signal.source_code not in licensed_source_codes):
        raise ValueError("Brokerage data use rights must be independently verified")
    if not signal.provider_record_is_active or not signal.source_data_fresh:
        raise ValueError("Inactive or stale market observations cannot be used")
    if signal.observed_at>now or (now-signal.observed_at).days>90:
        raise ValueError("Source observation too old or in the future")

    if signal.signal_type in ("expired_listing","withdrawn_listing"):
        priority="high"
        recommendation="seller_valuation"
        why="Documented listing-status change in an authorized feed"
    elif signal.signal_type=="stale_rental":
        # Stale rental listing is a property-market signal, NOT proof that an
        # owner is a motivated seller or consents to a solicitation.
        if signal.days_on_market is None or signal.days_on_market<180:
            raise ValueError("Long-listed rental needs verified 180+ days")
        priority="medium"
        recommendation="seller_valuation"
        why="Rental has remained listed for an extended period"
    elif signal.signal_type=="price_change":
        priority="medium"
        recommendation="buyer_search"
        why="Recent publicly documented listing price change"
    else:
        priority="research"
        recommendation="buyer_search"
        why="Recent listing observation recorded"

    return PropertyOpportunity(
        source_code=signal.source_code,
        source_record_key=signal.source_record_key,
        signal_type=signal.signal_type,
        market_city=signal.city,
        market_state=signal.state,
        priority=priority,
        evidence=[why,"Verified source: "+signal.source_code,
                  "Licensed purpose reviewed by brokerage"],
        recommended_acquisition_type=recommendation,
    )
