# HMRC recognition demo — 3-5 minute video script

HMRC's recognition application asks for a short demo of the end-to-end
MTD ITSA flow. This script is what to record. Target runtime: **4 minutes**.

> Use the sandbox throughout (`HMRC_ENV=sandbox`). Use a freshly-created
> sandbox test user with at least one self-employment business
> provisioned. Run through it once before recording.

## Scene 1 — sign in (~20s)

- Open `bankscanai.com`, click Login.
- Sign in with a verified test user.
- Land on the dashboard.

> **Voiceover:** "BankScan AI is a Making Tax Digital ITSA filing tool
> for UK sole traders and landlords."

## Scene 2 — connect HMRC (~40s)

- Click the "Your HMRC deadlines" panel showing the demo state.
- Click **Connect to HMRC**.
- Sign in on HMRC's sandbox with the test user credentials.
- Grant authority. Return to the dashboard.

> **Voiceover:** "OAuth 2.0 against HMRC's API. We never see the user's
> Government Gateway password. The access and refresh tokens come back
> encrypted at rest using AES-256-GCM."

## Scene 3 — NINO + business discovery (~30s)

- Panel now shows "Setup needed" with the NINO form.
- Type the sandbox NINO.
- Click **Discover my businesses**.
- Panel reloads — badge flips to **Live (HMRC)** with the real
  obligations.

> **Voiceover:** "The user types only their National Insurance Number.
> We call HMRC's Business Details API to enumerate every business
> registered against that NINO — no manual ID entry."

## Scene 4 — upload statement + categorisation (~40s)

- Upload a sample PDF bank statement.
- Show the parsed transactions table with HMRC categories appearing.
- Click an obviously-wrong category, change it via the dropdown.
- Show the override saved.

> **Voiceover:** "Every transaction is auto-categorised into HMRC's
> exact category names — turnover, travelCosts, adminCosts, and so on.
> The user can override any row; the override is remembered for that
> merchant forever."

## Scene 5 — quarterly update submit (~50s)

- Open the (planned) "Submit Q2 Update" section.
- Select business + period.
- Click **Preview** — show the exact HMRC payload.
- Confirm — show success + the HMRC transaction reference.

> **Voiceover:** "Before any submission, the user sees the exact payload
> we would send to HMRC. Idempotency-Key is auto-generated to prevent
> duplicate submissions. The response is captured in our audit log."

## Scene 6 — annual finalisation (~40s)

- Show the End of Period Statement screen with finalised toggle.
- Trigger calculation. Show the calculation result.
- Show the Final Declaration confirm dialog.
- Submit.

> **Voiceover:** "End of Period Statement, tax calculation, and final
> declaration are all built. Each requires explicit confirmation
> before any data leaves the browser."

## Scene 7 — audit + fraud headers (~30s)

- Open the (planned) admin / status page showing audit log row count
  and recent HMRC API call outcomes.
- Open the network tab in the browser dev tools, show one of the HMRC
  calls and the 13 fraud-prevention headers.

> **Voiceover:** "Every HMRC call is logged immutably with full request
> and response data — bearer tokens stripped. All 13 fraud-prevention
> headers are populated from real browser data and verified against
> HMRC's validator endpoint."

## Close (~10s)

> **Voiceover:** "BankScan AI: bank statement to tax return, in one
> place, with no manual data entry."

End screen: logo + URL + version + support email.

## Recording checklist

- [ ] Use 1080p, 30fps, screen recording with mouse highlight.
- [ ] Mute system notifications.
- [ ] Use the test user's name in the recording (Gabi Quinn or similar
      sandbox identity) — never a real user.
- [ ] Show the sandbox URL prominently (`test-www.tax.service.gov.uk`)
      so HMRC's reviewer sees we're on the right environment.
- [ ] No real NINO. No real bank statement. Sandbox data only.
- [ ] Cut to under 5 minutes total.

## Submitting

Upload the video to a private YouTube / Vimeo link with "unlisted"
visibility. Include the link in the HMRC recognition application
under the "demonstration video" field.
