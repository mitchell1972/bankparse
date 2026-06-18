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
| Business Details | 2.0 | List businesses for NINO | `hmrc/services/business_details.py` |
| Obligations | 3.0 | List obligations | `hmrc/services/obligations.py` |
| Self-Employment Business | 5.0 | Create period summary, EOPS | `hmrc/services/quarterly_updates.py`, `hmrc/services/annual.py` |
| UK Property Business | 6.0 | Create period summary, EOPS | `hmrc/services/quarterly_updates.py` |
| ~~Business Source Adjustable Summary~~ | ~~7.0~~ | **Removed from the production subscription** — not called by the in-year build. Re-add for the end-of-year checklist. See [`production-approvals-checklist.md`](production-approvals-checklist.md) §1. | n/a |
| Individual Calculations | **8.0** | Trigger, retrieve, final declaration | `hmrc/services/annual.py` |

> **2026-05-25 update:** Calculations API moved from v7.0 → v8.0 (HMRC
> retired v7). The v8 trigger endpoint requires an explicit
> `calculationType` URL segment. See `hmrc/services/annual.py` and
> `tests/hmrc/test_annual_flow.py::test_calculation_trigger_hits_correct_url_with_idempotency_key`.

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

## Status as of 2026-05-26

| Item | Status | Evidence |
|---|---|---|
| OAuth flow against sandbox | ✅ Verified end-to-end | `tests/e2e/test_prod_hmrc_smoke.py::test_full_oauth_handshake_with_sandbox_gg` — 16/16 PASS against live `bankscanai.com` on 2026-05-25 |
| 13 fraud-prevention headers | ✅ All present, structurally correct | `hmrc/services/fraud_headers.py`, `tests/hmrc/test_fraud_headers.py`, `tests/hmrc/test_fraud_headers_validator.py`, perf budget `tests/perf/test_fraud_headers_load.py` |
| Business Details integration | ✅ Working | mocked: `tests/hmrc/test_business_details.py`; real sandbox: tier-3 prod smoke |
| Obligations integration | ✅ Working + friendly errors | `tests/hmrc/test_obligations.py` (14 tests incl. `test_matching_resource_not_found_returns_oauth_mismatch_hint`) |
| Quarterly Updates (SE + Property) | ✅ Working | `tests/hmrc/test_quarterly_updates.py` + tier-3 preview test against real sandbox |
| End of Period Statement | ✅ Working | `tests/hmrc/test_annual_flow.py::test_eops_*` |
| Tax Calculation v8 (trigger + get) | ✅ Working | `test_annual_flow.py::test_calculation_*` — v8 trigger path verified |
| Final Declaration | ✅ Working | `test_annual_flow.py::test_final_declaration_*` |
| Audit log + durability | ✅ Documented, backup procedure | [`audit-log.md`](audit-log.md), `hmrc/repositories/submissions.py` |
| Outbound rate limiting | ✅ Token bucket on every HMRC call | `hmrc/services/rate_limiter.py` + 11 tests in `tests/hmrc/test_rate_limiter.py` |
| Prod monitoring + alerting | ✅ Sentry wired (DSN-gated no-op) | [`monitoring.md`](monitoring.md), `hmrc/services/monitoring.py` |
| Token-key rotation procedure | ✅ Two-key fallback + script + runbook | [`key-rotation.md`](key-rotation.md), `scripts/rotate_hmrc_token_key.py` |
| Conformance tests | ✅ **969+ HMRC-related + general tests passing** | run via `scripts/run_conformance_suite.py` |
| Terms of Service URL | ⏳ Verify renders at <https://bankscanai.com/terms> | open in incognito |
| Privacy Policy URL | ⏳ Verify renders at <https://bankscanai.com/privacy> | open in incognito |
| HMRC validator endpoint screenshot | ⏳ Run against live `test/fraud-prevention-headers/validate` | needs sandbox token + capture |
| Demo video | ⏳ Record per `demo-script.md` | unlisted YouTube |
| Production HMRC app credentials | ⏳ Apply at developer hub (MANUAL_STEPS step 1) | identity-bound — founder action |
| **Submit the application** | ⏳ <https://www.tax.service.gov.uk/recognition-software> | once every row above is ✅ |

When every row above is ✅, submit the application.

## How to refresh this evidence pack

The conformance transcript HMRC asks for is **regenerated by one
command**, so every submission is reproducible:

```bash
# Phase 1 (offline, mocked) — proves wire shapes pinned in code:
python3 scripts/run_conformance_suite.py --phase 1

# Phase 2 (real sandbox app-restricted) — proves token + test-user shapes:
HMRC_CLIENT_ID=...  HMRC_CLIENT_SECRET=... \
  python3 scripts/run_conformance_suite.py --phase 2

# Phase 3 (real sandbox user-restricted, full OAuth) — the gold standard:
HMRC_CLIENT_ID=...  HMRC_CLIENT_SECRET=... \
PROD_TEST_USER_EMAIL=...  PROD_TEST_USER_PASSWORD=... \
  python3 scripts/run_conformance_suite.py --phase 3
```

Each run writes to `hmrc/docs/conformance-test-transcript.txt` with a
git-SHA header so HMRC reviewers can match the transcript to the
deploy.

## Identity-bound actions only the founder can do

These can't be automated — they need Mitchell signed in to HMRC's
own web forms:

1. **Apply for production credentials** —
   <https://developer.service.hmrc.gov.uk/developer/applications/c16a75dc-378d-4171-a2ca-4a4f1cd068b0/manage>
   → "Get production credentials". ~1 hour to fill in the application.
   HMRC issues the new client_id/secret within 1 working day.
2. **Submit the recognition application** —
   <https://www.tax.service.gov.uk/recognition-software>. Attach the
   files referenced in [`MANUAL_STEPS.md`](MANUAL_STEPS.md).
3. **Record the demo video** per [`demo-script.md`](demo-script.md).
4. **Production cutover** — once recognition is granted, follow
   [`production-cutover.md`](production-cutover.md).
