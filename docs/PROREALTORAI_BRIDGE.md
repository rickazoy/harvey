# Harvey to ProRealtorAI — Lead Intelligence Adapter

This fork includes an **opt-in, review-only lead pipeline** for the
ProRealtorAI brokerage platform.

The supported public-business discovery path is `brokerage_b2b`.
Harvey is effective at finding brokerage owners and operating decision
makers, qualifying relevance and preparing a customized draft.

**This is not a consumer lead scraper.** Consumer buyer/seller prospects
must come from documented, first-party inquiries. No contact sourced from
business discovery is automatically treated as a buyer or seller lead.

## Pipeline

1. Harvey discovers/profile businesses using its provider sources and confirmed signals.
2. `harvey.realestate.qualify_prospect` scores a verified brokerage prospect.
3. `draft_nurture` creates a fact-limited, human-review-only email draft.
4. `harvey prorealtorai` prepares a batch with source details.
5. For live handoff, operators explicitly approve **both sourcing and the batch**.
6. The HMAC-signed `send_batch` posts to the ProRealtorAI intake Edge Function.
7. The receiver verifies timestamp, HMAC, request UUID and schema,
   deduplicates and places items in a platform-only review table.
8. Only a separate compliance-approved CRM workflow may eventually contact them.
   This adapter sends **zero** marketing emails, SMS messages or DMs.

## Commands

First configure ProRealtorAI's dedicated ingestion service and secret
in *server environment variables*:

```bash
export PROREALTORAI_HARVEY_INGEST_URL="https://YOUR_SUPABASE_PROJECT.functions.supabase.co/harvey-lead-ingest"
export PROREALTORAI_HARVEY_INGEST_SECRET="REPLACE_WITH_32_OR_MORE_RANDOM_CHARACTERS"
```

Preview data locally without contacting the CRM:

```bash
harvey prorealtorai --segment brokerage_b2b --out ./prospects-review.json
```

After verifying the provenance of all contacts, and reviewing the selected batch:

```bash
harvey prorealtorai --segment brokerage_b2b \
  --approve-source --approve-batch --send
```

Both approvals are required for network submission. Neither one enables
automatic campaign sends. `--out` writes personal data; secure or delete
the file after your human review.

## Hosted mode versus local development

Legacy Harvey invokes the user's Claude CLI login. That is NOT suitable
for a hosted multi-customer SaaS or reselling the user's personal Claude
subscription quota.

The fork now supports a separately paid Anthropic API inference path:

```bash
export HARVEY_BRAIN_PROVIDER=anthropic_api
export ANTHROPIC_API_KEY="YOUR_PROVIDER_API_KEY"
export HARVEY_ANTHROPIC_MODEL="claude-sonnet-4-6"
export HARVEY_API_MAX_DAILY_CALLS=80
```

This incurs standard API charges. Pricing, token-cost reconciliation,
per-tenant budgets and provider agreements require additional work before
a commercial multi-broker rollout. Personal Claude CLI login remains
supported for *local, individual use*, not as an enterprise backend.

## Safeguards

- Tracked harvey.yaml defaults to email and LinkedIn sending OFF.
- Email candidates require business relevance, a decision-maker title,
  source URL and verified delivery evidence.
- Opt-outs are blocked; consumer entries require an allowed first-party
  source and documented contact permission.
- Nurture drafts require human review and contain no unsupported claims.
- HMAC signatures bind a 5-minute timestamp and unique request ID to each batch.
- A server-side fixed platform ingestion scope prevents arbitrary tenant selection.
- Never add browser automation that circumvents platform terms or access limits.
- Follow applicable commercial email, privacy and data source restrictions.

## Deployment prerequisites

The ProRealtorAI receiving endpoint, its SQL migration, data retention
rules, user admin roles, shared-secret vault and end-to-end ingestion
validation must be in place before using `--send`.
