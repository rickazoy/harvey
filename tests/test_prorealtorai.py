"""Tests for Harvey's ProRealtorAI lead qualifier and review-only bridge."""
import hashlib
import hmac
import json

import pytest

from harvey.realestate.qualification import qualify_prospect
from harvey.realestate.nurture import draft_nurture
from harvey.realestate.bridge import build_batch, signature, send_batch

BROKER={
    "id":"p-001", "first_name":"Alex", "last_name":"Lane",
    "company":"Summit Realty Brokerage", "industry":"Real Estate",
    "title":"Managing Broker", "email":"alex@summit.example",
    "email_status":"verified", "source_url":"https://summit.example",
    "status":"new", "personalization_notes":"CRM and lead conversion",
}
def test_broker_candidate_requires_verified_source_and_decision_maker():
    good=qualify_prospect(BROKER,source_permission=True)
    assert good.ready_for_review
    assert good.score>=75
    assert good.tier=="high"
    assert len(good.dedup_key)==64
    assert "alex@" not in good.dedup_key
    assert not qualify_prospect(BROKER,source_permission=False).ready_for_review
    assert not qualify_prospect({**BROKER,"title":"Unknown Role"},source_permission=True).ready_for_review
    assert not qualify_prospect({**BROKER,"company":"Bakery","industry":"Food"},source_permission=True).ready_for_review
    assert not qualify_prospect({**BROKER,"email_status":"guess"},source_permission=True).ready_for_review

def test_opt_out_never_qualifies_or_nurtures():
    stopped=qualify_prospect({**BROKER,"status":"do_not_contact"},source_permission=True)
    assert stopped.score==0
    assert stopped.contact_permission=="blocked"
    draft=draft_nurture(BROKER,stopped)
    assert draft.status=="blocked"
    assert draft.body==""

def test_consumer_leads_require_documented_opt_in_and_first_party_source():
    potential={**BROKER,"source":"first_party_idx","consent_to_contact":True,"timeline":"1_month"}
    approved=qualify_prospect(potential,segment="consumer_real_estate",source_permission=True)
    assert approved.ready_for_review
    for change in [
        {"consent_to_contact":False},
        {"source":"google_scrape"},
        {"source":"linkedin"},
    ]:
        d=qualify_prospect({**potential,**change},segment="consumer_real_estate",source_permission=True)
        assert not d.ready_for_review
        assert draft_nurture(potential,d).status=="blocked"

def test_bridge_deduplicates_and_requires_approval_for_every_draft():
    rows=build_batch([BROKER, BROKER],segment="brokerage_b2b",source_permission=True)
    assert len(rows)==1
    assert rows[0]["score"]>=75
    assert rows[0]["nurture"]["status"]=="pending_approval"
    assert rows[0]["nurture"]["requires_human_approval"] is True
    assert rows[0]["nurture"]["channel"]=="email"

def test_invalid_segment_rejected():
    with pytest.raises(ValueError):
        qualify_prospect(BROKER,segment="unknown",source_permission=True)

def test_hmac_signatures_are_deterministic_and_bound_to_request():
    secret="e"*40
    body=b'{"schema_version":1}'
    sig=signature(secret,1700000000,"request-1",body)
    assert sig==hmac.new(secret.encode(),b"1700000000.request-1."+body,hashlib.sha256).hexdigest()
    assert sig!=signature(secret,1700000000,"request-2",body)
    assert sig!=signature(secret,1700000000,"request-1",b'{}')

@pytest.mark.asyncio
async def test_unsafe_endpoints_and_weak_secrets_blocked():
    with pytest.raises(ValueError):
        await send_batch([{"a":1}],endpoint="http://localhost:8000",secret="e"*40)
    with pytest.raises(ValueError):
        await send_batch([{"a":1}],endpoint="https://safe.example/ingest",secret="short")
    with pytest.raises(ValueError):
        await send_batch([],endpoint="https://safe.example/ingest",secret="e"*40)

@pytest.mark.asyncio
async def test_batch_requires_positive_receiver_ack(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"success":False,"error":"rejected"}
    class MockClient:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):
            assert kwargs["headers"]["X-Harvey-Signature"].startswith("sha256=")
            assert b"schema_version" in kwargs["content"]
            return Response()
    import harvey.realestate.bridge as bridge
    monkeypatch.setattr(bridge.httpx,"AsyncClient",lambda **kwargs:MockClient())
    with pytest.raises(ValueError):
        await send_batch([{"dedup_key":"x"}],endpoint="https://safe.example/ingest",secret="e"*40)
