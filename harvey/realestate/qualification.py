"""Deterministic, explainable real-estate lead qualification.

B2B brokerage recruiting is the supported default for Harvey's company
discovery system. Consumer contacts MUST originate from documented opt-in
first-party inquiries; they are never inferred from business scraping.
Protected characteristics and proxies must not affect qualification scores.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from typing import Any, Mapping

BROKER_TERMS = (
    "broker", "brokerage", "realty", "real estate", "realtor",
    "property group", "real estate office", "real estate team",
)
DECISION_MAKER_TERMS = (
    "owner", "broker of record", "principal broker",
    "managing broker", "broker-owner", "president", "founder",
    "ceo", "chief executive", "chief operating", "director",
    "head of operations", "regional manager",
)
PROBLEM_TERMS = (
    "lead generation", "lead conversion", "crm", "follow up",
    "recruiting", "marketing", "automation", "ai agent", "technology",
)
CONSUMER_POSITIVE_SOURCES = frozenset((
    "first_party_idx", "open_house_form", "buyer_registration",
    "seller_valuation", "agent_referral_with_consent",
))

@dataclass(frozen=True)
class Qualification:
    segment: str
    score: int
    tier: str
    ready_for_review: bool
    contact_permission: str
    reasons: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    dedup_key: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        data["missing_evidence"] = list(self.missing_evidence)
        return data

def _value(item: Mapping[str, Any] | object, key: str) -> str:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    return str(value or "").strip()

def _true(item: Mapping[str, Any] | object, key: str) -> bool:
    value = item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)
    return value is True

def _dedup(item: Mapping[str, Any] | object, segment: str) -> str:
    # Privacy-preserving identity for cross-service deduplication. Emails
    # never appear in logs, URLs, or the dedup key sent to the customer CRM.
    email = _value(item, "email").lower()
    external = _value(item, "id") or _value(item, "source_url")
    stable = email or external
    if not stable:
        raise ValueError("A stable email or provider identifier is required")
    return hashlib.sha256((segment + "\0" + stable).encode()).hexdigest()

def qualify_prospect(
    prospect: Mapping[str, Any] | object,
    *,
    segment: str = "brokerage_b2b",
    source_permission: bool = False,
) -> Qualification:
    if segment not in ("brokerage_b2b", "consumer_real_estate"):
        raise ValueError("Unsupported real estate segment")
    reasons: list[str] = []
    missing: list[str] = []
    status = _value(prospect, "status").lower()
    if status in ("do_not_contact", "opted_out", "lost", "invalid", "unsubscribed"):
        return Qualification(segment, 0, "suppressed", False,
            "blocked", ("Contact suppression or opt-out on record",), (),
            _dedup(prospect, segment))
    score = 0
    if segment == "brokerage_b2b":
        company = (_value(prospect, "company") or
            _value(prospect, "company_name")).lower()
        industry = _value(prospect, "industry").lower()
        title = _value(prospect, "title").lower()
        if any(x in industry or x in company for x in BROKER_TERMS):
            score += 35
            reasons.append("Identifiable real-estate brokerage or real-estate business")
        else:
            missing.append("Evidence that the company is a brokerage or real-estate business")
        if any(x in title for x in DECISION_MAKER_TERMS):
            score += 25
            reasons.append("Contact title suggests operating or buying authority")
        else:
            missing.append("Verified decision-maker title")
        if _value(prospect, "email_status").lower() == "verified":
            score += 15
            reasons.append("Email marked verified by contact provider")
        else:
            missing.append("Verified business email")
        text = " ".join((_value(prospect, "personalization_notes"),
            _value(prospect, "description"))).lower()
        matched = [x for x in PROBLEM_TERMS if x in text]
        if matched:
            score += min(15, len(matched) * 5)
            reasons.append("Observed business needs: " + ", ".join(matched[:3]))
        if _value(prospect, "source_url").startswith(("https://","http://")):
            score += 10
            reasons.append("Source URL recorded for verification")
        else:
            missing.append("Evidence URL")
        ready = (score >= 55 and not missing and source_permission)
        permission = "review_business_outreach" if ready else "not_authorized"
    else:
        source = _value(prospect, "source")
        explicit = _true(prospect, "consent_to_contact")
        # Consumer leads are never generated from scraped people lists.
        allowed = source in CONSUMER_POSITIVE_SOURCES and explicit and source_permission
        if allowed:
            score += 50
            reasons.append("Consent-backed first-party consumer inquiry")
        else:
            missing.append("Recorded consumer opt-in and approved first-party source")
        if _value(prospect, "preferred_cities") or _value(prospect, "location"):
            score += 15
            reasons.append("Search geography supplied by consumer")
        if _value(prospect, "timeline"):
            score += 15
            reasons.append("Consumer-supplied timeframe")
        if _value(prospect, "budget_min") or _value(prospect, "budget_max"):
            score += 15
            reasons.append("Consumer-supplied price range")
        if _value(prospect, "email"):
            score += 5
        ready = allowed and score >= 50
        permission = "consent_recorded" if allowed else "not_authorized"
    score = min(score, 100)
    tier = "high" if score >= 75 else "medium" if score >= 50 else "research"
    return Qualification(segment,score,tier,ready,permission,tuple(reasons),
        tuple(missing),_dedup(prospect,segment))
