# Harvey Consumer Lead Engine — Brokerage Buyer and Seller Pilot

Status: implementation in source, not deployed and not authorized for sending.

Harvey previously focused on B2B company prospecting. This feature adds
a separate first-party real-estate lead pipeline for brokerages:

1. Buyer lead capture (approved IDX registration, buyer form, open house form).
2. Seller capture (approved valuation form or consent-backed agent referral).
3. Strict source, account, submission time and consent-evidence validation.
4. Deterministic, explainable buyer/seller priority based on the person's
   expressed interest, timeframe and showing/valuation/consultation requests.
5. Broker-specific deduplication and HMAC-authenticated CRM intake.
6. Three review-only nurture emails for opt-in contacts. No delivery occurs.
7. Human review in ProRealtorAI. A separate canonical CRM handoff and
   provider-specific delivery gate must be tested before messages can go out.

IMPORTANT: Public property data is NOT a consumer contact. Harvey's business
web scrapers are never reused to harvest homeowner identities or infer email
consent. The CRM will decline inputs lacking first-party provenance.

## Inbound lead generation campaign drafts

Harvey can prepare lead-capture offers for approved brokerage websites.
It does not manufacture people, collect private contact details or deploy ads
without brokerage consent.

  harvey lead-offer --kind buyer_search
    --brokerage-name "Example Brokerage"
    --site-origin "https://homes.example.com"
    --out ./buyer-campaign.json

  harvey lead-offer --kind seller_valuation
    --brokerage-name "Example Brokerage"
    --site-origin "https://homes.example.com"
    --out ./seller-campaign.json

Other supported drafts: showing_request, open_house_rsvp and
seller_consultation. Every campaign is marked pending_broker_approval and
specifies the form and consent evidence it must collect. An approved
website builder integration and anti-bot/consent capture implementation
are still required before actual traffic can use these funnels.

## Client records

Use a JSON array containing 1–25 strictly validated ConsumerLead objects,
including these required fields:

- source: first_party_idx, open_house_form, buyer_registration,
  seller_valuation, seller_consultation_form, or agent_referral_with_consent
- source_event_id: provider-stable original event identifier
- source_account_id: exact source application account configured by broker
- kind: buyer or seller
- first_name, last_name, email/phone, status, submitted_at (offset-aware ISO)
- consent: proof_id, disclosure_version, disclosure_text, source_url,
  captured_at, email_marketing, sms_marketing, voice_marketing
- preferred_cities, property_types, timeline, budget_min, budget_max,
  requested_showing, requested_valuation, requested_consultation,
  property_address, inquiry_message

No income/race/religion/family/disability demographics are accepted in the
capture model. Email marketing, SMS and outbound voice are separate booleans.
No evidence is invented if the website does not actually record it.

## Review from command line

Use this for staging imports from a first-party website backend or a
broker-authorized form platform, not untrusted web scraping.

  harvey consumer --input ./first-party-inquiries.json
    --brokerage-id VERIFIED_BROKERAGE_UUID
    --brokerage-name "Example Brokerage"
    --allowed-source first_party_idx,seller_valuation
    --out ./private-consumer-review.json

The output file contains PII; Harvey creates it with mode 0600 and refuses
to overwrite existing files. Delete after approved review.

For a verified staging handoff add --approve-source, --approve-batch,
and --send. Sending here means HMAC-authenticated CRM INGESTION ONLY, not
sending consumer emails, texts or DMs. This CLI requires all three flags.

## Tenant key provisioning

The ProRealtorAI Edge receiver has HARVEY_CONSUMER_MASTER_SECRET (32+ random
characters) in a managed secret vault. Never supply it to the broker's
website or the hosted Harvey client.

A verified platform operator explicitly creates a harvey_consumer_integrations
row: integration_id, brokerage_id, attached realtor_id, approved external
account ID, allowed first-party source names and exact HTTPS origins.
The integration starts disabled and must be approved separately.

An operator derives a per-integration sender key using:

  derive_integration_key(master_secret, integration_id, key_epoch=1)

Only that *derived hex key* goes into the broker's secure backend environment
as PROREALTORAI_CONSUMER_SIGNING_KEY. Store the integration UUID as
PROREALTORAI_CONSUMER_INTEGRATION_ID and the dedicated Edge Function URL as
PROREALTORAI_HARVEY_CONSUMER_URL.

The receiver derives the same key from its master and database epoch.
This prevents a compromised broker source from signing for another broker.
Rotate one integration by incrementing key_epoch and re-provisioning its
derived key. No secret may be put into front-end JavaScript.

## Hosted inference

HARVEY_BRAIN_PROVIDER=anthropic_api uses regular separately billed Anthropic
API credentials, never subscription credentials belonging to the customer.
The initial consumer scoring and nurturing templates are deterministic.
Model-based custom messaging can be added after an evaluation and approval
policy is established; it must not fabricate prices/availability or
make impermissible fair-housing distinctions.

## Required production work

- Approve source ownership and website-broker association for each customer.
- Integrate signed server-side intake with actual IDX/open-house/valuation forms,
  including timestamped disclosure evidence. Browser apps cannot hold HMAC keys.
- Apply and test ProRealtorAI migrations and worker functions on staging.
- Verify tenant isolation with multiple brokerages and non-admin users.
- Connect the reviewed leads to canonical buyer/seller CRM records without
  creating duplicate or incorrectly attributed leads.
- Add delivery-provider receipts, DNC/optout sync and broker-specific consent
  policies before any marketing sends.
- Integrate licensed property/MLS opportunity feeds separately, without treating
  a listing owner as a contactable consumer.
- Establish rate limiting, anti-bot challenges and privacy retention rules.

Release statement: this is a consent-aware consumer lead engine foundation,
not an autonomous nationwide direct-marketing service.
