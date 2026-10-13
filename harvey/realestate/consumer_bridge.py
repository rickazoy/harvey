"""Tenant-bound, signed first-party buyer/seller intake into ProRealtorAI.

The caller must be provisioned with an integration-specific signing key:
hex(HMAC_SHA256(HARVEY_CONSUMER_MASTER_SECRET, "consumer.v1:"+integration_id)).
The receiving service validates that key against an enrolled source belonging
to an active brokerage and permitted realtor. The CLIENT never supplies
brokerage_id or realtor_id to select the destination.

This is an INTAKE path, not an outbound marketing path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Iterable
from urllib.parse import urlsplit

import httpx

from .consumer import ConsumerLead, qualify_consumer_lead, lead_identity
from .consumer_nurture import create_nurture_plan

MAX_BATCH = 25
MAX_PAYLOAD_BYTES = 160 * 1024


def derive_integration_key(
    master_secret: str, integration_id: str, *, key_epoch: int = 1,
) -> str:
    """Secure ops-only provisioning helper, never for a browser."""
    if len(master_secret) < 32:
        raise ValueError("Master secret must be at least 32 characters")
    if not isinstance(key_epoch, int) or not 1 <= key_epoch <= 100_000:
        raise ValueError("Integration key epoch must be a positive version")
    integration_id = str(uuid.UUID(integration_id))
    return hmac.new(master_secret.encode(),
                    f"consumer.v1:{integration_id}:{key_epoch}".encode(),
                    hashlib.sha256).hexdigest()


def sign_consumer_request(
    signing_key: str, timestamp: int, integration_id: str,
    request_id: str, body: bytes,
) -> str:
    if not isinstance(signing_key, str) or len(signing_key) != 64:
        raise ValueError("Use a 64-character provisioned integration signing key")
    try:
        key = bytes.fromhex(signing_key)
    except ValueError as exc:
        raise ValueError("Integration signing key must be hexadecimal") from exc
    parts = f"{timestamp}.{integration_id}.{request_id}.".encode() + body
    return hmac.new(key, parts, hashlib.sha256).hexdigest()


def build_consumer_batch(
    leads: Iterable[ConsumerLead],
    *,
    brokerage_id: str,
    allowed_sources: set[str],
    brokerage_name: str,
) -> list[dict]:
    """Preserve evidence with immutable, broker-scoped identifiers.

    Since user-entered PII crosses a security boundary, every row is strictly
    validated by Pydantic and will be independently revalidated by the server.
    """
    brokerage_id = str(uuid.UUID(brokerage_id))
    items: list[dict] = []
    seen: set[str] = set()
    for source_record in leads:
        lead = ConsumerLead.model_validate(source_record)
        if lead.source not in allowed_sources:
            raise ValueError("Lead source has not been explicitly allowed")
        assessment = qualify_consumer_lead(lead, allowed_sources=allowed_sources)
        dedup_key = lead_identity(lead, brokerage_id)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        plan = create_nurture_plan(lead, assessment,
                                   brokerage_name=brokerage_name)
        items.append({
            "dedup_key": dedup_key,
            "lead": lead.model_dump(mode="json"),
            "assessment": assessment.model_dump(mode="json"),
            "nurture_plan": plan.model_dump(mode="json"),
        })
        if len(items) > MAX_BATCH:
            raise ValueError("At most 25 consumer leads per signed batch")
    return items


async def send_consumer_batch(
    records: list[dict],
    *,
    integration_id: str | None = None,
    endpoint: str | None = None,
    signing_key: str | None = None,
    timeout: float = 20.0,
) -> dict:
    """Post a signed batch and require positive intake acknowledgement.

    Never sends messages, invokes Claude CLI or mutates Harvey's B2B database.
    """
    integration_id = str(uuid.UUID(integration_id or os.getenv(
        "PROREALTORAI_CONSUMER_INTEGRATION_ID", "")))
    endpoint = endpoint or os.getenv("PROREALTORAI_HARVEY_CONSUMER_URL", "")
    signing_key = signing_key or os.getenv("PROREALTORAI_CONSUMER_SIGNING_KEY", "")
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.fragment):
        raise ValueError("HTTPS ProRealtorAI consumer receiver URL required")
    if not records or len(records) > MAX_BATCH:
        raise ValueError("Provide 1–25 validated consumer records")
    payload = {"schema_version": 2, "records": records}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True,
                      ensure_ascii=False).encode()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise ValueError("Consumer batch exceeds 160 KB")
    timestamp = int(time.time())
    request_id = str(uuid.uuid4())
    signed = sign_consumer_request(signing_key, timestamp, integration_id,
                                   request_id, body)
    headers = {
        "Content-Type": "application/json",
        "X-Harvey-Timestamp": str(timestamp),
        "X-Harvey-Request-Id": request_id,
        "X-Harvey-Integration-Id": integration_id,
        "X-Harvey-Signature": "sha256=" + signed,
    }
    async with httpx.AsyncClient(timeout=timeout,
                                 follow_redirects=False) as client:
        response = await client.post(endpoint, headers=headers, content=body)
        response.raise_for_status()
        result = response.json()
        if result.get("success") is not True:
            raise ValueError("Consumer intake was not confirmed; reconcile before retry")
        return result
