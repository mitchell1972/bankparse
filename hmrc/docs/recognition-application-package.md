# HMRC MTD ITSA recognition — application package

Master index for the HMRC software recognition application. Every piece of
evidence HMRC asks for has a source-of-truth file in this directory (or a
specific commit / file in the repo) — this document just points at them.

> **HMRC recognition guidance:** <https://www.gov.uk/guidance/find-software-thats-compatible-with-making-tax-digital-for-income-tax>
> **Application form:** <https://www.tax.service.gov.uk/recognition-software>

## Product identity

| Field | Value |
|---|---|
| Product name | BankScan AI |
| Vendor name | BankScan AI Ltd (or Mitoba Consulting Ltd — confirm legal entity) |
| Vendor software identifier | `bankscan-ai` |
| Product version | 2.3.0 (see `app.py` → `FastAPI(version=...)`) |
| Production URL | <https://bankscanai.com> |
| HMRC application id (sandbox) | `c16a75dc-378d-4171-a2ca-4a4f1cd068b0` |
| HMRC application id (production) | *to be created when applying for production credentials* |
| Support contact | mitchellagoma@gmail.com (replace with `support@bankscanai.com` mailbox if available) |

## MTD ITSA APIs we integrate with

| API | Version | Operations we use | Where in repo |
|---|---|---|---|
| Create Test User | 1.0 | Setup only (sandbox) | `hmrc/services/sandbox.py` |
| Authorisation (OAuth 2.0) | n/a | Authorize, Token, Refresh | `hmrc/services/oauth.py`, `hmrc/routers/oauth.py` |
| Business Details | 1.0 | List businesses for NINO | `hmrc/services/business_details.py` |
| Obligations | 3.0 | List obligations | `hmrc/services/obligations.py` |
| Self-Employment Business | 5.0 | Create period summary, EOPS | `hmrc/services/quarterly_updates.py`, `hmrc/services/annual.py` |
| UK Property Business | 6.0 | Create period summary, EOPS | `hmrc/services/quarterly_updates.py` |
| Individual Calculations | 7.0 | Trigger, retrieve, final declaration | `hmrc/services/annual.py` |

## Evidence checklist (what HMRC will ask for)

| Required | Where |
|---|---|
| Working OAuth flow against sandbox | Demonstrated end-to-end on 2026-05-19 with sandbox test user "Gabi Quinn" (NINO `CX139207A`). Tokens are AES-GCM encrypted at rest — see `hmrc/services/crypto.py`. |
| All 13 fraud-prevention headers | [`fraud-prevention-implementation.md`](fraud-prevention-implementation.md) lists each header, where it's collected, where it's injected, and which test validates it. |
| Conformance tests passing | [`conformance-test-evidence.md`](conformance-test-evidence.md) lists every HMRC endpoint we hit and links to the test that asserts the exact wire contract. |
| Security questionnaire | [`security-questionnaire.md`](security-questionnaire.md) — HMRC's standard 30-question security questionnaire answered. |
| Data handling | [`data-handling.md`](data-handling.md) — how we store, protect, and delete user data (NINO, tokens, transactions). |
| Audit log | Immutable `hmrc_submissions` table (see `database.py` schema). Bearer tokens are stripped before storage; fraud headers retained for compliance. |
| Idempotency on submissions | All POSTs to write endpoints include `Idempotency-Key` (auto-generated UUID or caller-supplied for replay safety). Tested. |
| Terms of Service URL | <https://bankscanai.com/terms> *(verify URL — add if missing)* |
| Privacy Policy URL | <https://bankscanai.com/privacy> *(verify URL — add if missing)* |
| Vendor IP allowlisting | Railway egress IPs (provide once they stabilise — Railway docs to confirm). |
| Demo video (3-5 min) | *To record — script in [`demo-script.md`](demo-script.md)* |
| Anti-money-laundering supervision | N/A — we are not a regulated financial-services firm. We never hold client money. Document as such. |

## Recognition application — step-by-step

1. **Stabilise sandbox** (this PR + verification run-throughs).
2. **Run the full conformance suite** against the sandbox using a real
   sandbox test user (not mocked). Capture transcripts for the application.
3. **Verify fraud-prevention headers** via HMRC's `/test/fraud-prevention-headers/validate`
   endpoint. Save the pass output as a screenshot.
4. **Record a 3-5 minute demo video** — script in `demo-script.md`. Show:
   OAuth, business discovery, quarterly submission, calculation,
   final declaration.
5. **Apply for production credentials** at HMRC developer hub (separate
   from the sandbox application id).
6. **Submit the recognition application** at
   <https://www.tax.service.gov.uk/recognition-software>. Attach every
   document in this directory.
7. **HMRC review window: 8-16 weeks.** Expect a callback / clarification
   email. Most common asks: tighter fraud-header values, ToS / Privacy URL
   verification, demo of error handling.

## Status as of 2026-05-19

| Item | Status |
|---|---|
| OAuth flow against sandbox | ✅ Verified end-to-end |
| 13 fraud-prevention headers | ✅ All 13 present, structurally correct |
| Business Details integration | ✅ Working (mocked tests + real sandbox call) |
| Obligations integration | ✅ Working |
| Quarterly Updates (SE + Property) | ✅ Working |
| End of Period Statement | ✅ Working |
| Tax Calculation (trigger + get) | ✅ Working |
| Final Declaration | ✅ Working |
| Audit log | ✅ Immutable, written on every HMRC call |
| Conformance tests | ✅ 126 HMRC-related tests passing |
| Terms of Service URL | ⏳ Verify it exists at `/terms` |
| Privacy Policy URL | ⏳ Verify it exists at `/privacy` |
| HMRC validator endpoint screenshot | ⏳ Run against live fraud-prevention validator |
| Demo video | ⏳ Record using `demo-script.md` |
| Production HMRC app credentials | ⏳ Apply at developer hub |

When every row above is ✅, submit the application.
