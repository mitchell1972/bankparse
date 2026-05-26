# Security questionnaire — HMRC recognition

HMRC asks every recognised software vendor a standard set of security
questions. These are the answers BankScan AI gives, with references to
the code so HMRC's reviewers can verify each one.

## Authentication & authorisation

**Q: How is the user authenticated into BankScan AI?**
A: Email + password registration with bcrypt password hashing. Email
verification via 6-digit OTP before access. See `app.py` `/api/register`,
`/api/login`. CSRF protection on every state-changing endpoint via
double-submit cookie pattern (`csrf.py::CSRFMiddleware`).

**Q: How is the user authenticated into HMRC?**
A: HMRC's OAuth 2.0 authorisation-code flow. We never see, store, or
transmit the user's Government Gateway credentials. The flow lives in
`hmrc/services/oauth.py` and `hmrc/routers/oauth.py`. State parameter is
generated with `secrets.token_urlsafe(32)` and bound to a `Secure`,
`HttpOnly`, `SameSite=Lax` cookie.

**Q: How are HMRC OAuth tokens stored?**
A: AES-256-GCM authenticated encryption at rest. The encryption key is
read from `HMRC_TOKEN_ENCRYPTION_KEY` env var (base64-encoded 32 bytes)
and never appears in source or logs. Implementation: `hmrc/services/crypto.py`.
Storage: `hmrc_connections` table, columns `access_token_enc`, `refresh_token_enc`.
Plaintext tokens are decrypted only at the moment of an outbound HMRC call
and are never logged.

**Q: How is the user's NINO stored?**
A: AES-256-GCM encrypted in the `nino_enc` column on `hmrc_connections`,
using the same key as the OAuth tokens.

**Q: How are tokens refreshed?**
A: On HMRC 401, `hmrc/services/client.py::request` calls the refresh
endpoint exactly once. HMRC rotates the refresh token on each use; we
persist the new token immediately so the next call uses it. Refresh
failures surface to the user as "Reconnect to HMRC".

## Data handling

**Q: What user data does BankScan AI store?**
A: Email, bcrypt-hashed password, encrypted HMRC tokens + NINO,
parsed transaction rows the user uploaded, audit log of every HMRC call
made on their behalf. No raw PDFs are retained after parsing.

**Q: How long is data retained?**
A: HMRC submissions and the audit log are retained for at least 6 years
(per HMRC requirements). Other data is retained until the user closes
their account or 12 months after their last login, whichever is later.

**Q: Where is data stored?**
A: Production runs on Railway (UK / EU regions). The application database
is Turso (a managed libSQL, EU region). No data leaves the EEA except
for HMRC API calls themselves.

**Q: Is data encrypted in transit?**
A: TLS 1.2+ everywhere. HSTS enabled on `bankscanai.com`. HMRC API calls
are HTTPS-only by HMRC's enforcement.

**Q: Is data encrypted at rest?**
A: HMRC tokens and NINOs: AES-256-GCM with a per-deploy key. Other
fields: the database provider's encryption-at-rest.

## Audit trail

**Q: How do you prove what was sent to HMRC?**
A: The `hmrc_submissions` table records every HMRC API call we make:
endpoint, method, request headers (bearer stripped), request body,
response status, response headers, response body, idempotency key,
timestamp. Append-only — no UPDATE / DELETE statements exist against
this table in code. Schema: `database.py`, recording: `hmrc/repositories/submissions.py`.

**Q: How long is the audit kept?**
A: 6 years minimum (HMRC requirement).

## Idempotency

**Q: How do you prevent duplicate submissions?**
A: Every POST to a write endpoint includes an `Idempotency-Key` header.
We auto-generate a UUID per request; callers (e.g. background retry
jobs) can pass their own for replay safety. HMRC's server-side
deduplication uses this key. Tests pin the contract:
`tests/hmrc/test_quarterly_updates.py::test_submit_se_honours_caller_supplied_idempotency_key`.

## Penetration testing

**Q: When was the last pen test?**
A: GitGuardian secret-scanning runs on every commit (pass on PR #29).
Static analysis via ruff + pyright (planned). Manual penetration test
scheduled before applying for production credentials.

## Incident response

**Q: How do you handle a security incident affecting HMRC data?**

A: Full runbook at [`incident-response.md`](incident-response.md).
Headline:

- **Single point of contact:** Mitchell Agoma (Director), `security@bankscanai.com` —
  owns the 72-hour clock for both HMRC and ICO notifications.
- **Containment:** revoke the affected HMRC connection
  (`hmrc/repositories/tokens.py::revoke`); rotate
  `HMRC_TOKEN_ENCRYPTION_KEY` per [`key-rotation.md`](key-rotation.md)
  if a token leak is suspected.
- **HMRC notification (within 72h of discovery):** SDST support portal
  ticket at <https://developer.service.hmrc.gov.uk/developer/support>,
  "I have a security concern" category. Template provided in the runbook.
- **ICO notification (within 72h, UK GDPR Article 33):** via
  <https://ico.org.uk/for-organisations/report-a-breach/personal-data-breach/>
  or call 0303 123 1113. Template provided in the runbook.
- **User notification:** affected users emailed within 72h with re-OAuth
  + audit-review instructions.
- **Recovery:** force users to re-OAuth. Public incident report on
  `bankscanai.com/security` within 7 days post-resolution.
- **Post-mortem:** blameless review filed at
  `hmrc/docs/incidents/[date]-[short-name].md` within 14 days.

Drills: quarterly tabletop exercises after recognition is granted; first
drill within 30 days of approval.

**Reporting channel for outside parties** (RFC 9116):
<https://bankscanai.com/.well-known/security.txt> +
<https://bankscanai.com/security> — published disclosure policy with
safe harbour for good-faith researchers.

## Source code review

The HMRC integration is fully isolated under `hmrc/`. Reviewers can find:

- `hmrc/services/oauth.py` — OAuth flow
- `hmrc/services/client.py` — every outbound HMRC call passes through here
- `hmrc/services/crypto.py` — encryption at rest
- `hmrc/services/fraud_headers.py` — all 13 fraud-prevention headers
- `hmrc/repositories/submissions.py` — audit log writer
- `hmrc/repositories/tokens.py` — token storage
- `tests/hmrc/test_architecture.py` — automated guards that fail CI if a
  router gets too thick, imports the database directly, hardcodes a
  category string, or pulls FastAPI into a service.
