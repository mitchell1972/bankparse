# Production Approvals Checklist — readiness pack

> ## ⛔ NOT READY — DO NOT TREAT AS PASSING (updated 2026-06-18)
>
> On 2026-06-18 we discovered the MTD journey **has never worked against the
> live HMRC sandbox**: every user-restricted call (Business Details `list`,
> Obligations) returns `404 OAUTH_NINO_MISMATCH`, and the app had been
> masking it with demo data. **There is no verified end-to-end journey.** The
> testing claims in §4 below describe what was *intended/mocked*, not what
> passes against real HMRC — they are retained struck-through for honesty.
> The application already sent to SDST (NINO `RZ507241A`, see §6) was
> **premature** and should be expected to bounce.
>
> Root cause + status: `hmrc/docs/oauth-nino-binding.md`. A reproducible
> fresh-context test to prove the real journey is wired into CI
> (`hmrc/docs/ci-real-sandbox-secrets.md`) and runs once the HMRC secrets
> are added. **Re-engage SDST only after that run shows obligations 200 +
> a real submit under a correctly-bound NINO.**

**HMRC ref:** 2026-MCW021 (SDST email from `softwaredevelopersupport@service.hmrc.gov.uk`, 15 Jun 2026)
**Application:** BankScan AI (production app on the HMRC Developer Hub)
**Build type declared:** **In-year product** (quarterly updates) — see §2 for why.

> **What this doc is.** HMRC issues an actual *Production Approvals Checklist*
> form (Word/Excel) that you complete and email back to
> `softwaredevelopersupport@service.hmrc.gov.uk`. This file pre-answers every
> item that form asks about, mapped to our code and test evidence, so filling
> in HMRC's form is copy-paste. It is **not** a substitute for HMRC's form —
> it's the source material for it.
>
> ⚠️ **The form was not attached to the Yahoo copy of the email** — only links
> rendered. Before replying, open the email in a mail client that shows
> attachments (or reply to SDST asking them to re-send the checklist
> document). Don't send the reply below until you have their actual form.

---

## 1. Build-type decision (read this first)

HMRC's [how-to-integrate guide](https://developer.service.hmrc.gov.uk/guides/income-tax-mtd-end-to-end-service-guide/documentation/how-to-integrate.html)
defines three build types. The APIs required for each:

| Build type | Required APIs |
|---|---|
| **In-year product** | Business Details, Obligations, SE Business and/or Property Business, Individual Calculations (if displaying the estimate) |
| End-of-year product | Business Details, SE/Property Business (annual endpoints), **BSAS**, **Individual Losses**, Obligations (if final declaration), Individual Calculations (if final declaration) |
| Full end-to-end | Both of the above |

**Our production subscription set** (after BSAS was un-ticked during the
application):

- Business Details 2.0 ✅
- Obligations 3.0 ✅
- Self-Employment Business 5.0 ✅
- UK Property Business 6.0 ✅
- Individual Calculations 8.0 ✅

That set is **exactly the in-year product** list. We do **not** subscribe to
BSAS or Individual Losses, so we cannot — and should not — claim end-of-year
or full end-to-end on this checklist. HMRC explicitly warns that subscribing
to APIs the software doesn't use blocks production credentials, and that the
checklist must *align with the testing*.

**Recommendation: declare as an in-year product.** Rationale:

1. It is the honest match to what we've subscribed to and tested.
2. It is the fastest path to production for the **quarterly-update mandate**
   (April 2026 / April 2027 waves) — which is the immediate commercial target.
3. HMRC explicitly supports **iterative builds**: *"If you choose to build
   iteratively, you are required to test the relevant APIs and complete the
   Production Approvals Checklist for each stage of the build."* We add the
   end-of-year stage (BSAS + Individual Losses + final-declaration testing) as
   a **second checklist** before the first final-declaration deadline
   (31 Jan 2028 for the 2026/27 year) — there is no time pressure on it now.

> Our code already implements EOPS and final declaration via the Individual
> Calculations API. Those paths stay dormant in production until the
> end-of-year checklist is approved. Declaring in-year now does not delete
> that work — it sequences the approval.

---

## 2. Minimum functionality standards — line-by-line

HMRC's required functionality (for the income types we support: **UK
self-employment + UK property**), mapped to our implementation:

| HMRC requirement | Supported? | Where |
|---|---|---|
| Provide transaction-monitoring fraud-prevention header data | ✅ | All 13 `Gov-Client-*` / `Gov-Vendor-*` headers via [`hmrc/services/fraud_headers.py`](../services/fraud_headers.py), attached on **every** call through the single chokepoint [`hmrc/services/client.py`](../services/client.py) |
| Obtain a business ID unique to each business | ✅ | Business Details API — [`hmrc/services/business_details.py`](../services/business_details.py) |
| Create/maintain digital records; user owns + can export them | ✅ | Parsed transactions stored per user; export to Excel/CSV; raw statements deleted post-parse (see privacy policy) |
| Submit quarterly updates for each mandated source (SE, multiple SE, UK property) | ⚠️ Coded, **not verified vs real HMRC** | [`hmrc/services/quarterly_updates.py`](../services/quarterly_updates.py) — SE + UK property. Passes against mocks/stub only; has never succeeded against the live sandbox (see §4 + the banner). |
| — foreign property income | ❌ Not supported | Declared as out of scope; we support UK SE + UK property only. Notify SDST of unsupported data items. |
| View an estimate of income-tax liability (display or signpost) | ✅ Display, with disclaimer | Individual Calculations 8.0; the estimate is shown with an accuracy disclaimer — **verify the disclaimer text is present before submitting the checklist** (see §6 open item) |
| Make adjustments and finalise business income for the year | ⏭️ End-of-year stage | Implemented in code; deferred to the second (end-of-year) checklist |
| Brought/carried-forward & sideways loss relief | ⏭️ End-of-year stage | Requires Individual Losses API — second checklist |
| Submit non-mandated income, or divert the customer | ✅ Divert | We signpost customers to HMRC/other software for unsupported sources |
| Make a final declaration, or divert | ⏭️ End-of-year stage | Final declaration implemented in code; deferred to the second checklist |

Most rows are coded, but the **core in-year function — submitting a
quarterly update (and even reading obligations) — has never succeeded
against the real HMRC sandbox** (see the banner + §4). So this product is
**not** in-year-ready until that's proven. The ⏭️ rows are end-of-year
functionality, correctly excluded from this checklist.

---

## 3. Fraud prevention headers

- **Connection method:** `WEB_APP_VIA_SERVER`.
- **All 13 headers** are built in [`hmrc/services/fraud_headers.py`](../services/fraud_headers.py)
  and merged into every request in `client.py::_compose_headers`.
- **No bypass:** there is exactly one outbound HTTP path to HMRC
  (`client.py::request`); a repo grep confirms no router or service calls
  `httpx`/`requests` against HMRC directly.
- **Validated against HMRC's Test Fraud Prevention Headers API** — the
  validator accepted our headers and returned a real NINO
  (`JA057968B`). Evidence: [`fraud-headers-validator-response.txt`](fraud-headers-validator-response.txt).
- Load-tested: median build time <500µs, p99 <2ms (`tests/perf/test_fraud_headers_load.py`).

---

## 4. Testing requirements

> **⛔ Corrected 2026-06-18 — the original claims here were false.** What
> actually passes vs what was claimed:

- **Mocked conformance suite:** 258 tests, all passing (`tests/hmrc/`).
  ✅ True — but these **mock** HMRC; they prove our wire shapes, not that
  the live journey works.
- ~~**Real-sandbox conformance (phase 3):** 16 tests against the live HMRC
  sandbox, all passing.~~ **Misleading.** Those real-sandbox checks are
  *app-restricted* only (client-credentials token, create-test-user,
  create-test-business → 201). They do **not** exercise the
  *user-restricted* journey. Against real HMRC, Business Details `list` and
  Obligations have **never** returned 200 (100% `404 OAUTH_NINO_MISMATCH`).
- ~~**End-to-end user journey** (Playwright): … SE + property quarterly
  submit → obligations …~~ This Playwright journey runs against a **stub**
  HMRC server (`tests/e2e/_hmrc_stub.py`), **not** the real sandbox. CI
  "E2E passing" is against the stub.
- ~~Every endpoint … is exercised~~ **False against real HMRC.** No
  obligations retrieval or quarterly submission has ever succeeded against
  `test-api.service.hmrc.gov.uk`.

**Net:** there is **no** verified passing real-HMRC journey yet. The
fresh-context CI job (`hmrc/docs/ci-real-sandbox-secrets.md`) exists to
produce one once the secrets are added.

> **Action — the 14-day NINO window.** HMRC checks the fraud-header data in
> their sandbox logs against **the dummy NINO you tested with**, and requires
> that NINO *within 14 days of completing API testing*. Confirm which NINO
> our sandbox conformance run used and put it in the reply (see §5). If the
> testing was >14 days ago, re-run the sandbox conformance pass so the log
> data is fresh, then send that NINO.

---

## 5. Reply email — DRAFT (do not send until NINO confirmed + form attached)

> Send **from the Developer Hub account email**, to
> `softwaredevelopersupport@service.hmrc.gov.uk`, subject keeping the ref
> `2026-MCW021`. Attach HMRC's completed Production Approvals Checklist form.

```
To:      softwaredevelopersupport@service.hmrc.gov.uk
Subject: RE: Production Application Credential Request - Support Ref: 2026-MCW021

Hi Nathan,

Thank you for the requirements. BankScan AI is ready to request production
access as an IN-YEAR product (quarterly updates).

Application name: BankScan AI
Build type:       In-year product
Income sources:   UK self-employment + UK property (UK only; foreign property
                  not supported — customers are diverted for that source)

Production API subscriptions:
  - Business Details 2.0
  - Obligations 3.0
  - Self-Employment Business 5.0
  - UK Property Business 6.0
  - Individual Calculations 8.0

Sandbox testing:
  - Dummy National Insurance Number used: RZ507241A
  - Connection method: WEB_APP_VIA_SERVER
  - All 13 fraud-prevention headers sent on every MTD API call; validated
    against the Test Fraud Prevention Headers API.

The completed Production Approvals Checklist is attached.

We intend to add end-of-year functionality (final declaration, BSAS,
Individual Losses) as a later iterative build and will submit a separate
Production Approvals Checklist for that stage.

Kind regards,
Mitchell Agoma
BankScan AI (Mitoba Consulting Ltd)
```

---

## 6. Status — SENT 2026-06-15

The reply was sent to `softwaredevelopersupport@service.hmrc.gov.uk` on
2026-06-15 with the in-year declaration above, **dummy NINO `RZ507241A`**,
and this checklist as a PDF attachment. `RZ507241A` is the live
sandbox-connected test user (read from the production front end, where the
NINO is decrypted for display — it is AES-256-GCM encrypted at rest, so it
does not appear in logs). The earlier `CX139207A` (Gabi Quinn, 2026-05-19)
was superseded by the current connected account.

Now awaiting HMRC's review (up to 10 working days, plus the specialist
fraud-header log check). If they ask for their own checklist template,
transcribe these same answers into it.

**Watch items:**
1. **14-day NINO freshness** — HMRC must inspect fraud-header data for
   `RZ507241A` in their sandbox logs. If the last sandbox conformance pass
   under that NINO was >14 days before 2026-06-15, re-run the phase-3
   conformance pass so the log data is current, and tell SDST.
2. **End-of-year stage** — a second Production Approvals Checklist (BSAS +
   Individual Losses + final-declaration testing) is required before the
   first final-declaration deadline. No time pressure now.
