# HMRC recognition — manual steps Mitchell has to do himself

This is the human checklist. Code can't do these — they involve filling in
HMRC's web forms, recording video, and talking to HMRC support.

**Calendar reality:** the application takes HMRC 8–16 weeks to process
once submitted. Every day not on the clock is a day shaved off the
April 2027 launch window. Submit this week.

> **For local dev / sandbox** (no recognition needed), use
> [`dev-hub-setup.md`](dev-hub-setup.md) — 10-15 minutes from zero to a
> verified sandbox connection on your laptop. Run
> `python scripts/hmrc_dev_hub_check.py` to confirm everything is wired.

---

## Step 1 — Get production HMRC credentials

> ~1 hour, can do today

1. Sign in to <https://developer.service.hmrc.gov.uk/developer/applications>.
2. Open the existing application (sandbox app id
   `c16a75dc-378d-4171-a2ca-4a4f1cd068b0`) or click **Add new application**.
3. On the application's page, click **Get production credentials**.
4. HMRC ask: contact name, business reason, expected volume. Use:
   - Contact: Mitchell Agoma, mitchellagoma@gmail.com
   - Reason: "MTD ITSA bridging tool for UK sole traders and landlords. AI
     categorises bank-statement transactions to HMRC's published category
     taxonomy; users review and submit quarterly updates, EOPS, and final
     declarations through the application."
   - Expected volume: "100 active users in year 1, growing to ~10,000 by
     April 2028 in line with the mandation expansion to £20k+ qualifying
     income."
5. Once issued (usually same-day), copy the production client ID + secret
   into Railway env vars:
   - `HMRC_CLIENT_ID`
   - `HMRC_CLIENT_SECRET`
   - `HMRC_ENV=production`
   - `HMRC_REDIRECT_URI=https://bankscanai.com/api/hmrc/callback`
6. Restart the Railway service. Confirm `GET /api/hmrc/connect` redirects
   to `api.service.hmrc.gov.uk` (not `test-api.service…`).

---

## Step 2 — Run the recognition conformance suite

> ~1 day

Run every test in `tests/hmrc/` against the **sandbox** with a real
sandbox test user (not mocked). Capture transcripts. HMRC want to see:

```
pytest tests/hmrc/ -v --tb=short 2>&1 | tee hmrc/docs/conformance-test-transcript.txt
```

If anything fails, fix before applying. The transcript file is referenced
in the application package.

---

## Step 3 — Validate fraud-prevention headers

> ~30 minutes

Hit HMRC's validator endpoint with a real authenticated request:

```
GET https://test-api.service.hmrc.gov.uk/test/fraud-prevention-headers/validate
Authorization: Bearer <sandbox access token>
+ all 13 Gov-Client-* / Gov-Vendor-* headers
```

We have a helper at `hmrc/services/fraud_headers.py` — boot the app
locally, complete the OAuth flow, then visit
`/api/hmrc/fraud-validate-self-check` (TODO: add this thin wrapper if it
doesn't already exist).

Save the validator's pass response as a screenshot. HMRC will ask for it.

---

## Step 4 — Record the demo video

> 30 minutes prep + recording

Script: [`demo-script.md`](demo-script.md).

Must show:
1. New user signing up + verifying email.
2. Uploading a sandbox bank statement (any of the seeded fixtures).
3. AI categorising the rows.
4. Pressing **Connect to HMRC**, completing OAuth against the sandbox.
5. Pressing **Discover my businesses** (uses the sandbox NINO `CX139207A`).
6. On `/hmrc/file`: clicking **Submit** on an open quarterly obligation,
   seeing the totals preview, confirming, receiving the HMRC reference.
7. The submission appearing in the Submission History list.
8. Downloading the Audit Confidence Certificate.

Length: 3-5 minutes. No professional editing required — screen recording
+ voice-over is fine. Upload to YouTube as Unlisted; paste the URL into
the application form.

---

## Step 5 — Verify Terms & Privacy URLs

HMRC's form checks these URLs return 200 with substantive content.

- <https://bankscanai.com/terms> — added in this PR ✓
- <https://bankscanai.com/privacy> — already live ✓

Open both in an incognito window and confirm they render.

---

## Step 6 — Submit the recognition application

Form: <https://www.tax.service.gov.uk/recognition-software>

Fields, with values pre-filled from
[`recognition-application-package.md`](recognition-application-package.md):

| Field | Value |
|---|---|
| Product name | BankScan AI |
| Vendor name | Mitoba Consulting Ltd (confirm legal entity) |
| Vendor software identifier | `bankscan-ai` |
| Production URL | <https://bankscanai.com> |
| Application ID | (from Step 1) |
| Terms of Service URL | <https://bankscanai.com/terms> |
| Privacy Policy URL | <https://bankscanai.com/privacy> |
| Demo video URL | (from Step 4 — unlisted YouTube) |
| APIs you integrate with | Self-Employment Business v5, UK Property Business v6, Obligations v3, Business Details v1, Authorisation, Individual Calculations v7 |
| Support contact | mitchellagoma@gmail.com |

Attach:
- `hmrc/docs/conformance-test-transcript.txt` (Step 2 output)
- Screenshot of the fraud-headers validator pass (Step 3)
- `hmrc/docs/security-questionnaire.md`
- `hmrc/docs/data-handling.md`

Click **Submit**. HMRC email an acknowledgement within 2–3 working days
with a case reference. Save the reference.

---

## Step 7 — While you wait (4–16 weeks)

HMRC will email back with one of:
1. **Approved** — they list you on GOV.UK. Update CLAUDE.md and ship.
2. **Approved with conditions** — usually a fraud-header tweak or extra
   documentation. Fix and resubmit.
3. **Rejected** — read the reason carefully and resubmit. Most rejections
   are fraud-prevention-header related; the test in
   `tests/hmrc/test_fraud_headers_validator.py` already covers the
   common failures.

Ship the other roadmap items (penalty-points refinements, accountant
sharing, etc.) during the wait. The code is ready — the gate is HMRC's
processing time.

---

## Step 8 — Once approved, flip the switch

- Update `HMRC_ENV=production` (was sandbox).
- Verify a real submission end-to-end with your own NINO + business.
- Email any waiting-list users that the service is now live.

That's the whole path. Code is built; the gate is calendar time at HMRC.
