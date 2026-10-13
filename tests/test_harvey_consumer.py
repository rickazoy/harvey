"""Buyer/seller lead qualification, consent, nurturing and broker isolation tests."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import uuid

import pytest
from pydantic import ValidationError

from harvey.realestate.consumer import (
    ConsumerLead, ConsentEvidence, qualify_consumer_lead, lead_identity,
)
from harvey.realestate.consumer_nurture import create_nurture_plan
from harvey.realestate.consumer_bridge import (
    build_consumer_batch, derive_integration_key, sign_consumer_request,
    send_consumer_batch,
)

NOW = datetime.now(timezone.utc)
BROKER_A = "123e4567-e89b-42d3-a456-426614174000"
BROKER_B = "123e4567-e89b-42d3-a456-426614174001"
INTEGRATION = "123e4567-e89b-42d3-a456-426614174002"


def make_lead(kind: str = "buyer", *, email_marketing: bool = True,
              source: str | None = None, **changes) -> ConsumerLead:
    data = {
        "source": source or ("seller_valuation" if kind == "seller" else "first_party_idx"),
        "source_event_id": "submission-event-00001",
        "source_account_id": "website-1",
        "kind": kind, "first_name": "Alex", "last_name": "Example",
        "email": "alex@example.net", "phone": None,
        "status": "new", "submitted_at": NOW,
        "consent": ConsentEvidence(
            proof_id="proof-00001", disclosure_version="2026-10-A",
            disclosure_text="I consent to email follow-up from this named real estate brokerage.",
            source_url="https://broker.example/valuation",
            captured_at=NOW - timedelta(minutes=3),
            email_marketing=email_marketing,
        ),
        "preferred_cities": ["Tampa"],
        "property_types": ["Townhouse"],
        "timeline": "immediate", "budget_min": 100000, "budget_max": 900000,
        "requested_showing": kind == "buyer",
        "requested_valuation": kind == "seller",
        "requested_consultation": False,
        "property_address": "123 Example Street" if kind == "seller" else None,
        "inquiry_message": "Please contact me about my inquiry.",
    }
    data.update(changes)
    return ConsumerLead.model_validate(data)


def assess(lead: ConsumerLead):
    return qualify_consumer_lead(lead, allowed_sources={lead.source}, now=NOW)


@pytest.mark.parametrize("kind", ["buyer", "seller"])
def test_buyer_seller_intent_scored_only_from_supplied_evidence(kind):
    lead = make_lead(kind)
    result = assess(lead)
    assert result.kind == kind
    assert result.tier == "hot"
    assert result.score == 100
    assert result.requires_human_review is True
    assert result.marketing_channels == ["email"]
    assert all("race" not in s.lower() for s in result.reasons)
    assert all("neighborhood demographics" not in s.lower() for s in result.reasons)


def test_protected_attributes_cannot_be_injected_to_scorer():
    lead = make_lead().model_dump(mode="json")
    lead["race"] = "any"
    with pytest.raises(ValidationError):
        ConsumerLead.model_validate(lead)


def test_missing_consent_prevents_nurturing_but_keeps_inquiry_for_agent():
    lead = make_lead(email_marketing=False)
    result = assess(lead)
    assert result.can_prepare_nurture is False
    assert result.score == 0
    assert result.marketing_channels == []
    plan = create_nurture_plan(lead, result, brokerage_name="Example Brokerage")
    assert plan.status == "blocked_consent"
    assert plan.steps == []


def test_optout_is_suppressed_even_when_prior_consent_exists():
    lead = make_lead(status="opted_out")
    result = assess(lead)
    assert result.tier == "suppressed"
    assert result.marketing_channels == []
    assert create_nurture_plan(lead, result,
        brokerage_name="Example Brokerage").status == "suppressed"


def test_illegitimate_source_is_rejected():
    lead = make_lead()
    with pytest.raises(ValueError, match="source"):
        qualify_consumer_lead(lead, allowed_sources={"seller_valuation"}, now=NOW)


def test_cross_tenant_dedup_and_input_identity_proofs():
    lead = make_lead()
    assert lead_identity(lead, BROKER_A) != lead_identity(lead, BROKER_B)
    assert lead_identity(lead, BROKER_A) == lead_identity(lead, BROKER_A)
    assert len(lead_identity(lead, BROKER_A)) == 64


def test_timestamps_and_claims_cannot_be_fabricated_without_evidence():
    lead = make_lead()
    invalid = lead.model_dump(mode="json")
    invalid["consent"]["source_url"] = "http://broker.example/form"
    with pytest.raises(ValidationError):
        ConsumerLead.model_validate(invalid)
    invalid = lead.model_dump(mode="json")
    invalid["consent"]["captured_at"] = (
        NOW + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError):
        ConsumerLead.model_validate(invalid)


def test_nurture_has_three_review_only_steps_and_never_fabricates_comps():
    buyer = make_lead()
    seller = make_lead("seller")
    for lead in [buyer, seller]:
        plan = create_nurture_plan(lead, assess(lead),
                                   brokerage_name="Example Brokerage")
        assert plan.status == "pending_review"
        assert len(plan.steps) == 3
        assert [s.offset_days for s in plan.steps] == [0, 3, 10]
        assert all(s.requires_human_approval and not s.send_allowed
                   for s in plan.steps)
        assert all(s.channel == "email" for s in plan.steps)
        assert all("estimated home value:" not in s.body.lower()
                   for s in plan.steps)


def test_seller_showing_and_buyer_valuation_request_rejected():
    with pytest.raises(ValidationError):
        make_lead("seller", requested_showing=True)
    with pytest.raises(ValidationError):
        make_lead("buyer", requested_valuation=True)


def test_batched_leads_are_deduped_and_no_outbound_delivery_enabled():
    lead = make_lead()
    rows = build_consumer_batch([lead, lead], brokerage_id=BROKER_A,
        allowed_sources={lead.source}, brokerage_name="Example Brokerage")
    assert len(rows) == 1
    assert rows[0]["lead"]["kind"] == "buyer"
    assert rows[0]["assessment"]["score"] == 100
    assert rows[0]["nurture_plan"]["marketing_contact_authorized"] is False
    assert rows[0]["nurture_plan"]["steps"][0]["send_allowed"] is False


def test_secret_is_derived_by_integration_and_key_epoch():
    master = "M" * 48
    key = derive_integration_key(master, INTEGRATION, key_epoch=1)
    assert key == hmac.new(
        master.encode(), ("consumer.v1:" + INTEGRATION + ":1").encode(),
        hashlib.sha256).hexdigest()
    assert key != derive_integration_key(master, INTEGRATION, key_epoch=2)
    assert key != derive_integration_key(master, BROKER_A, key_epoch=1)
    body = b'{"schema_version":2}'
    sig = sign_consumer_request(key, 1700000000, INTEGRATION,
                                BROKER_B, body)
    assert sig == hmac.new(bytes.fromhex(key),
        f"1700000000.{INTEGRATION}.{BROKER_B}.".encode() + body,
        hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_consumer_bridge_rejects_non_https_and_weak_credentials():
    with pytest.raises(ValueError):
        await send_consumer_batch([{"lead":{}}],integration_id=INTEGRATION,
            endpoint="http://broker.example/ingest",signing_key="0"*64)
    with pytest.raises(ValueError):
        await send_consumer_batch([{"lead":{}}],integration_id=INTEGRATION,
            endpoint="https://broker.example/ingest",signing_key="short")
    with pytest.raises(ValueError):
        await send_consumer_batch([],integration_id=INTEGRATION,
            endpoint="https://broker.example/ingest",signing_key="0"*64)


@pytest.mark.asyncio
async def test_consumer_bridge_requires_positive_server_ack(monkeypatch):
    class Response:
        def raise_for_status(self): return None
        def json(self): return {"success":False,"error":"not authorized"}
    class MockClient:
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def post(self,url,*,headers,content):
            assert headers["X-Harvey-Integration-Id"] == INTEGRATION
            assert headers["X-Harvey-Signature"].startswith("sha256=")
            assert b'"schema_version":2' in content
            return Response()
    import harvey.realestate.consumer_bridge as bridge
    monkeypatch.setattr(bridge.httpx,"AsyncClient",
                        lambda **kwargs: MockClient())
    with pytest.raises(ValueError,match="not confirmed"):
        await send_consumer_batch([{"lead":{}}],integration_id=INTEGRATION,
            endpoint="https://broker.example/ingest",signing_key="0"*64)
