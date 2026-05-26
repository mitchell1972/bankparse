# HMRC SDST recognition application — pre-filled form text

Copy-paste destination for every field on
<https://www.tax.service.gov.uk/recognition-software>.
Designed so the founder can fill the form in 5 minutes, not 30.

**Read this file alongside the live application form** — open both in
adjacent windows and copy line-by-line.

---

## Section 1 — Vendor identity

| HMRC field | Your value |
|---|---|
| Software vendor name | `Mitoba Consulting Ltd` *(verify against your Companies House registered name — change here if the legal vendor entity differs)* |
| Software product name | `BankScan AI` |
| Product version | `2.3.0` *(matches `app.py` → `FastAPI(version=...)`)* |
| Vendor identifier | `bankscan-ai` |
| Production URL | `https://bankscanai.com` |
| Support contact name | `Mitchell Agoma` |
| Support email | `mitchellagoma@gmail.com` *(swap to `support@bankscanai.com` once that mailbox is set up — required for ongoing recognition)* |
| Support phone | *(your number — UK landline preferred if you have one)* |

---

## Section 2 — APIs you integrate with

Tick every box that matches the list below. The HMRC form will show
checkboxes for each API. The version numbers match what we're
subscribed to on the dev hub (sandbox app
`c16a75dc-378d-4171-a2ca-4a4f1cd068b0`):

- [x] Authorisation (OAuth 2.0)
- [x] Business Details (MTD) — v2.0
- [x] Obligations (MTD) — v3.0
- [x] Self Assessment Test Support — v1.0 *(sandbox-only)*
- [x] Create Test User — v1.0 *(sandbox-only)*
- [x] Self-Employment Business (MTD) — v5.0
- [x] Property Business (MTD) — v6.0
- [x] Business Source Adjustable Summary (MTD) — v7.0 *(subscribed but not yet called — for ITSA adjustments)*
- [x] Individual Calculations (MTD) — v8.0
- [x] Test Fraud Prevention Headers — v1.0

Plus operations within those APIs:

- [x] List obligations
- [x] Submit quarterly period summary (Self-Employment)
- [x] Submit quarterly period summary (UK Property)
- [x] Submit End of Period Statement
- [x] Trigger tax calculation
- [x] Retrieve tax calculation
- [x] Submit Final Declaration

---

## Section 3 — Customer base

| HMRC field | Your value |
|---|---|
| Target user types | UK sole traders + landlords (and their accountants) |
| Expected first-year volume | 100 active users in year 1 |
| Expected three-year volume | ~10,000 by April 2028 (in line with the £20k+ mandation expansion) |
| Geographic scope | United Kingdom — England, Scotland, Wales, Northern Ireland |
| Primary use case | "MTD ITSA bridging tool. AI categorises bank-statement transactions to HMRC's published category taxonomy; users review and submit quarterly updates, EOPS, and final declarations." |

---

## Section 4 — Compliance + security

| HMRC field | Your value / link |
|---|---|
| Terms of Service URL | `https://bankscanai.com/terms` |
| Privacy Policy URL | `https://bankscanai.com/privacy` |
| Data handling description | "See attached `data-handling.md`. NINOs encrypted at rest (AES-GCM via `HMRC_TOKEN_ENCRYPTION_KEY`). OAuth tokens encrypted at rest with two-key rotation support. Audit trail of every HMRC call retained for 6 years per HMRC software-recognition rules." |
| Security questionnaire | Attached: `hmrc/docs/security-questionnaire.md` |
| Vendor IP allowlisting | Railway-managed egress; specific IP range provided on request to SDST. *(Tip: when SDST emails for this, get the current range from Railway dashboard at the time of the email — Railway rotates them.)* |
| Anti-money-laundering supervision | N/A — we are not a regulated financial-services firm. We never hold or move client money. |

---

## Section 5 — Evidence attachments

Upload these in the order HMRC's form lists them:

1. **Conformance test transcript** — `hmrc/docs/conformance-test-transcript.txt` *(regenerated 2026-05-26; Phase 1 + Phase 3 PASS against live HMRC sandbox)*
2. **Fraud-headers validator evidence** — `hmrc/docs/fraud-headers-validator-response.txt` *(captured 2026-05-26 — HMRC sandbox accepted all 13 headers)*
3. **Security questionnaire** — `hmrc/docs/security-questionnaire.md`
4. **Data handling** — `hmrc/docs/data-handling.md`
5. **Audit log durability + retention** — `hmrc/docs/audit-log.md`
6. **Demo video URL** — *unlisted YouTube link generated from `hmrc/docs/demo-script.md` recording*
7. **Production cutover procedure** — `hmrc/docs/production-cutover.md` *(shows HMRC we have a controlled production rollout plan)*

---

## Section 6 — Declarations + signatures

The form ends with a series of yes/no declarations. The expected values:

- "We agree to abide by HMRC's MTD vendor terms" → **Yes**
- "Our software complies with HMRC's penalty-points model" → **Yes** *(documented in `hmrc/docs/recognition-application-package.md` + `tests/hmrc/test_penalties.py`)*
- "We provide an audit trail of all HMRC submissions" → **Yes** *(`hmrc/repositories/submissions.py`)*
- "We notify users when HMRC returns an error" → **Yes** *(friendly-error mapping in `hmrc/services/obligations.py`)*
- "We will support customers for at least 12 months post-launch" → **Yes**
- "Customer data is held in the UK or EU" → **Yes** *(Railway europe-west4)*

Sign as `Mitchell Agoma, Director` *(adjust if vendor entity differs)*.

---

## After submission

1. HMRC emails an acknowledgement within 2-3 working days. **Save the
   case reference** — you'll need it for every follow-up.
2. HMRC review window: **8-16 weeks**. Most rejections at week 8 are
   fraud-header tweaks or evidence requests. Be quick to respond — the
   clock pauses while you owe them a reply.
3. While you wait, do NOT release the production cutover. Stay on
   sandbox until recognition is granted.
4. Once granted: follow `hmrc/docs/production-cutover.md` step-by-step.
