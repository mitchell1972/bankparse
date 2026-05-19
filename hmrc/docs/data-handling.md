# Data handling — BankScan AI

Companion to `security-questionnaire.md`. This document covers what data
we collect, why, where it lives, and how it's protected.

## Data we collect

| Category | What | Why |
|---|---|---|
| Account | Email, bcrypt password hash, email-verified flag | Sign-in, account recovery, lawful basis: contract |
| Subscription | Stripe customer id, subscription status, trial end | Billing, lawful basis: contract |
| HMRC connection | OAuth access + refresh tokens (encrypted), NINO (encrypted), MTD business IDs | Calling HMRC on the user's behalf, lawful basis: explicit consent via HMRC OAuth |
| Uploaded statements | Parsed transaction rows (description, date, amount), source file size | Building the user's tax submission, lawful basis: contract |
| Categorisation | Per-user merchant → category overrides | User experience, lawful basis: contract |
| Audit | Every HMRC API call (request + response, bearer stripped) | HMRC requirement (6-year retention), lawful basis: legal obligation |
| Telemetry | `hmrc_categorisation_events` (cache hit rate, AI call counts) | Service improvement, lawful basis: legitimate interest |

## What we do NOT collect

- Original PDF / image files (parsed then deleted)
- Government Gateway username or password (HMRC's OAuth screen handles this; we never see it)
- Bank login credentials (we don't connect to banks, only parse statements the user uploads)
- Card details (Stripe Checkout hosts the card form; we only see Stripe's customer id + last 4 digits via webhook)
- Marketing / behavioural tracking beyond first-party usage telemetry

## Where data lives

| Layer | Provider | Region |
|---|---|---|
| Web tier | Railway | UK / EU |
| Database | Turso (managed libSQL) | EU |
| Email (OTP) | Resend | EU |
| Billing | Stripe | EU/UK |
| AI categorisation | Anthropic (Claude Haiku) | US/EU — content sent: bank descriptions + signed amounts only, never NINO / tokens / personal identifiers |

## Encryption

| Field | At rest | In transit |
|---|---|---|
| HMRC `access_token` | AES-256-GCM (`crypto.py`) | TLS 1.2+ |
| HMRC `refresh_token` | AES-256-GCM | TLS 1.2+ |
| User NINO | AES-256-GCM | TLS 1.2+ |
| User password | bcrypt (cost 12) | TLS 1.2+ (never decrypted) |
| Transaction rows | DB-provider encryption | TLS 1.2+ |
| Audit log payloads | DB-provider encryption (bearers pre-stripped at write time) | TLS 1.2+ |

## Retention

| Data | Retention | Trigger to delete |
|---|---|---|
| Account, transactions, overrides | 12 months after last login | Account deletion request |
| HMRC tokens | Active session; revoked on user request or 18-month HMRC refresh-token expiry | "Disconnect HMRC" button |
| Audit log of HMRC calls | 6 years (HMRC requirement) | Cannot be deleted before then |
| Stripe customer record | Until subscription end + 6 years (UK tax record requirement) | Stripe customer deletion |
| AI-categorisation telemetry | 24 months | Pruned by background job |
| Uploaded PDFs | Deleted within 30 seconds of parsing | Automatic |

## User rights (UK GDPR)

| Right | How |
|---|---|
| Access | `/api/account/export` (planned) — emails the user a JSON dump |
| Rectification | The user can edit their own categorisation overrides anytime |
| Erasure | Email support; account closure removes everything except the 6-year HMRC audit log (legal obligation to retain) |
| Restriction | "Disconnect HMRC" — stops all HMRC calls without deleting the account |
| Portability | Same as Access — JSON export |
| Object to processing | Cancel subscription; account becomes read-only |

## Sub-processors

| Vendor | Purpose | DPA |
|---|---|---|
| Railway | Hosting | Standard DPA via dashboard |
| Turso | Database | Standard DPA |
| Resend | Transactional email | Standard DPA |
| Stripe | Payments | Standard DPA |
| Anthropic | AI categorisation | Standard DPA — content sent is bank descriptions + amounts only |
| HMRC | Tax submissions | Crown immunity / lawful basis: legal obligation |

## Data we send to Anthropic (Claude) for categorisation

The categorisation prompt sends:

- Transaction description (e.g. "STRIPE PAYOUT", "MIPERMIT LTD CHIPPENHAM")
- Signed amount (e.g. -2.50)
- Business type ("se" or "property")

We deliberately do NOT send:

- The user's email, name, NINO, or any other PII
- Account numbers
- Dates beyond what's already embedded in the transaction description
- Anything from outside the current parse

This means an Anthropic incident is bounded — at worst an attacker would
learn which merchants UK sole traders pay, without being able to link
those merchants to specific people.
