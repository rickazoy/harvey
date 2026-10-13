"""Opt-in, authenticated bridge from Harvey to ProRealtorAI's review inbox.

No auto-email/SMS/DM. Uses JSON payload, HMAC-SHA256 signatures,
timestamp expiry, a unique request ID and explicit tenant scope verified by
the RECEIVING platform — not a client-supplied realtor ID.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx

from .qualification import qualify_prospect
from .nurture import draft_nurture

MAX_RECORDS = 50

def build_batch(records: Iterable[dict[str, Any]], *, segment: str,
                source_permission: bool, sender_company: str = "ProRealtorAI") -> list[dict[str, Any]]:
    items = []
    seen: set[str] = set()
    for record in records:
        qualification = qualify_prospect(record,segment=segment,
            source_permission=source_permission)
        if qualification.dedup_key in seen: continue
        seen.add(qualification.dedup_key)
        # Keep the original provenance, but never send more than necessary.
        item = {
            "dedup_key": qualification.dedup_key,
            "segment": segment,
            "first_name": str(record.get("first_name") or "")[:100],
            "last_name": str(record.get("last_name") or "")[:100],
            "company_name": str(record.get("company") or record.get("company_name") or "")[:200],
            "industry": str(record.get("industry") or "")[:100],
            "email": str(record.get("email") or "").strip().lower()[:254],
            "title": str(record.get("title") or "")[:120],
            "source_url": str(record.get("source_url") or "")[:1500],
            "source": str(record.get("source") or "")[:100],
            "score": qualification.score,
            "tier": qualification.tier,
            "qualification": qualification.to_dict(),
            "nurture": draft_nurture(record, qualification,
                sender_company=sender_company).to_dict(),
        }
        items.append(item)
        if len(items)>=MAX_RECORDS:break
    return items

def signature(secret: str, timestamp: int, request_id: str, body: bytes) -> str:
    signing = f"{timestamp}.{request_id}.".encode() + body
    return hmac.new(secret.encode(),signing,hashlib.sha256).hexdigest()

async def send_batch(records: list[dict[str, Any]], *,
                     endpoint: str | None = None, secret: str | None = None,
                     timeout_seconds: float = 15.0) -> dict:
    url = endpoint or os.environ.get("PROREALTORAI_HARVEY_INGEST_URL","")
    signing_secret = secret or os.environ.get("PROREALTORAI_HARVEY_INGEST_SECRET","")
    parsed = urlsplit(url)
    if parsed.scheme!="https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Valid HTTPS ProRealtorAI ingestion endpoint required")
    if not signing_secret or len(signing_secret)<32:
        raise ValueError("32+ character ingestion secret required")
    if not records or len(records)>MAX_RECORDS:
        raise ValueError("Provide 1-50 qualified records")
    payload={"schema_version":1,"records":records}
    body=json.dumps(payload,separators=(",",":"),sort_keys=True,ensure_ascii=False).encode()
    timestamp=int(time.time())
    request_id=str(uuid.uuid4())
    headers={
        "Content-Type":"application/json",
        "X-Harvey-Timestamp":str(timestamp),
        "X-Harvey-Request-Id":request_id,
        "X-Harvey-Signature":"sha256="+signature(signing_secret,timestamp,request_id,body),
    }
    async with httpx.AsyncClient(timeout=timeout_seconds,follow_redirects=False) as client:
        response=await client.post(url,content=body,headers=headers)
        response.raise_for_status()
        data=response.json()
        if data.get("success") is not True:
            raise ValueError("ProRealtorAI rejected the ingestion batch")
        return data
