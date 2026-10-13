"""First-party engagement scoring does not claim Zillow data access."""
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from harvey.realestate.consumer import ConsumerLead, qualify_consumer_lead
from harvey.realestate.intent_signals import (
    FirstPartyEngagement, evaluate_first_party_intent,
)

NOW=datetime(2026,10,13,18,0,0,tzinfo=timezone.utc)

def lead():
    return ConsumerLead.model_validate({
        "source":"buyer_registration","source_event_id":"form-event-0001",
        "source_account_id":"broker-site-001",
        "kind":"buyer","first_name":"Casey","last_name":"Example",
        "email":"casey@example.org","phone":None,"status":"new",
        "submitted_at":(NOW-timedelta(hours=2)).isoformat(),
        "consent":{
            "proof_id":"consent-proof-001",
            "disclosure_version":"2026-10",
            "disclosure_text":"I allow email follow-up from my brokerage about my inquiry.",
            "source_url":"https://broker.example/buyer",
            "captured_at":(NOW-timedelta(hours=3)).isoformat(),
            "email_marketing":True,
            "sms_marketing":False,
            "voice_marketing":False,
        },
        "preferred_cities":[],"property_types":[],
        "timeline":"undecided","requested_showing":False,
        "requested_valuation":False,"requested_consultation":False,
    })

def event(kind,idx=1,**changes):
    data=dict(
        original_lead_event_id="form-event-0001",
        source_account_id="broker-site-001",
        event_id=f"engagement-000{idx}",
        event_type=kind,source="broker_owned_site",
        occurred_at=NOW-timedelta(minutes=20),
        consent_proof_id="consent-proof-001",
        analytics_consent_recorded=True,
    )
    return FirstPartyEngagement.model_validate({**data,**changes})

def evaluate(events,**kw):
    record=lead()
    assessment=qualify_consumer_lead(record,
        allowed_sources={record.source},now=NOW)
    return evaluate_first_party_intent(record,assessment,events,
        verified_source_account_id="broker-site-001",
        consent_for_engagement=True,now=NOW,**kw)

def test_payment_calculator_interaction_is_weak_and_non_financial():
    result=evaluate([event("calculator_interacted")])
    assert result.engagement_points==2
    assert result.financial_qualification_inferred is False
    assert result.external_zillow_activity_used is False
    assert result.automatic_contact_authorized is False
    assert result.status=="human_review_only"

def test_event_dedup_capping_and_high_intent_actions():
    events=[event("calculator_interacted",idx=1),
        event("calculator_interacted",idx=1),
        event("calculator_interacted",idx=2),
        event("tour_requested",idx=3),
        event("property_saved",idx=4)]
    result=evaluate(events)
    assert result.engagement_points==19
    assert result.event_count==3

def test_requires_exact_broker_site_and_consent_proof():
    for candidate in [
        event("tour_requested",source_account_id="other-broker-site"),
        event("property_saved",consent_proof_id="different-proof"),
        event("calculator_interacted",analytics_consent_recorded=False),
        event("site_returned",original_lead_event_id="another-form-event"),
    ]:
        with pytest.raises(ValueError):
            evaluate([candidate])

def test_anonymous_or_unapproved_zillow_signals_cannot_be_passed():
    with pytest.raises(ValidationError):
        event("calculator_interacted",source="zillow_browsing")
    with pytest.raises(ValidationError):
        event("mortgage_preapproval")
    with pytest.raises(ValidationError):
        event("calculator_interacted",mortgage_amount=900000)

def test_suppressed_consent_or_unlinked_engagement_stays_blocked():
    blocked=evaluate([event("property_saved")])
    assert blocked.follow_up_priority>=0
    record=lead()
    assessment=qualify_consumer_lead(record,
        allowed_sources={record.source},now=NOW)
    result=evaluate_first_party_intent(record,assessment,
        [event("property_saved")],
        verified_source_account_id="broker-site-001",
        consent_for_engagement=False,now=NOW)
    assert result.follow_up_priority==0
    assert result.status=="blocked"

def test_stale_events_do_not_raise_priority():
    result=evaluate([event("calculator_interacted",
        occurred_at=NOW-timedelta(days=35))])
    assert result.engagement_points==0
    assert result.event_count==0
