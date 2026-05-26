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

---

# PER-SECOND SHOT LIST (use this when actually recording)

This is the version you have on a second monitor while QuickTime /
OBS / Loom is rolling. Every line is a single mouse-click or one
spoken sentence — recording-in-progress, no decisions to make.

## Pre-recording one-time setup (5 minutes — do before hitting Record)

1. Open Terminal, run:
   ```bash
   PROD_TEST_USER_EMAIL='mitchell_agoma@yahoo.co.uk' \
     PROD_TEST_USER_PASSWORD='<your password>' \
     python3.10 scripts/verify_obligations_fix_live.py
   ```
   This auto-mints a fresh sandbox test user and OAuths the account.
   Copy the NINO + GG userId + GG password the script prints.

2. In a fresh incognito Chrome window: navigate to
   `https://bankscanai.com/login` — DO NOT log in yet.

3. Quit Slack, Mail, Messages. Turn on Do Not Disturb.

4. Resize Chrome to 1440 × 900. QuickTime → File → New Screen Recording
   → "Record Selected Portion" → drag a rectangle exactly over Chrome.

5. Hit Record. Take a breath. Begin.

## Take 1 — full recording (~4:00 total)

| Time | Mouse | Voiceover (read verbatim) |
|---|---|---|
| 0:00 | Static intro slide: "BankScan AI — MTD ITSA demo" | "Hi. This is BankScan AI, a Making Tax Digital ITSA filing tool. I'll walk through the full submission flow against the HMRC sandbox in under five minutes." |
| 0:08 | Click into Chrome | "I start as a new user, signed out." |
| 0:11 | Type test email → Tab → type password → Enter | "I sign in with a verified test account." |
| 0:18 | Dashboard loads | "The dashboard shows the HMRC deadlines panel in demo state — no connection yet." |
| 0:26 | Click "Connect to HMRC" button | "I click Connect to HMRC." |
| 0:32 | Page redirects to test-www.tax.service.gov.uk | "We hand off to HMRC's sandbox Government Gateway via OAuth 2.0. We never see the user's Gateway password." |
| 0:40 | Type GG userId → Tab → type password → click Sign in | "I sign in with my sandbox Gateway credentials." |
| 0:50 | Grant authority page | "HMRC asks the user to grant authority. I click Continue." |
| 0:54 | Click Continue / Grant | (silent — let HMRC's redirect chain play out) |
| 1:00 | Back on bankscanai.com | "We're back on BankScan AI. The OAuth tokens are AES-256-GCM encrypted at rest." |
| 1:08 | Click on NINO input | "Next, I type my National Insurance Number." |
| 1:13 | Type the sandbox NINO (from the script's output) | (silent — typing audible) |
| 1:20 | Click "Discover my businesses" | "I click Discover my businesses." |
| 1:25 | Panel reloads, shows real obligations | "BankScan calls HMRC's Business Details API and enumerates every business HMRC has on file — no manual business-ID entry needed." |
| 1:33 | Hover over the Submit button on Q1 obligation | "There's an open quarterly obligation here. I'll submit it." |
| 1:38 | Click Submit | "I click Submit on the Q1 self-employment quarterly update." |
| 1:42 | Modal appears showing income/expenses/net | "BankScan shows me exactly what's about to be sent to HMRC — income, expenses, net — derived from my AI-categorised bank statement rows." |
| 1:52 | Point to the values | "These numbers match the wire payload exactly. There's no chance of submitting something different from what I see." |
| 1:58 | Click confirm Submit | "I confirm." |
| 2:02 | Success state with HMRC reference | "HMRC accepted the submission. We get an idempotency-keyed audit row for compliance." |
| 2:10 | Navigate to /hmrc/submissions | "Here's the immutable audit log — every HMRC API call BankScan has ever made on this user's behalf." |
| 2:20 | Show a row, click Audit Confidence Certificate | "Each submission can be exported as an Audit Confidence Certificate — a PDF for the user's records." |
| 2:30 | Return to /hmrc/file | "Back to the deadlines panel — the Q1 row now shows Filed." |
| 2:40 | Briefly show the property quarterly | "We support both self-employment AND UK property income, in the same flow." |
| 2:50 | Scroll down to EOPS row | "End of Period Statement and Final Declaration use the same submission UI." |
| 3:00 | Show the dashboard | "Throughout, the user sees a live penalty-points tracker and the next deadline." |
| 3:10 | Click logout | "I log out — the user can leave at any point and resume." |
| 3:15 | End slide: logo + URL + version + email | "BankScan AI version 2.3.0. Live at bankscanai.com. Support at mitchellagoma@gmail.com. Thanks for reviewing." |
| 3:25 | (end) | |

## Post-recording

1. Stop QuickTime → File → Export As → 1080p.
2. Open the file → trim any pre-record silence (Edit → Trim).
3. Upload to YouTube → set visibility **Unlisted**.
4. Copy the URL into the HMRC application form under "Demo video".

## What HMRC's reviewer will check (from their published guidance)

- The URL in the address bar shows `test-www.tax.service.gov.uk` during
  the OAuth — confirms sandbox.
- The full Submit → success flow is visible (not cut at the click).
- The voiceover names "MTD ITSA" explicitly (we do).
- The video is under 5 minutes (we're ~3:30).

## What HMRC's reviewer is NOT looking for

- Cinematic editing. They reject videos that look too polished —
  suggests they're not real-time.
- Background music. They've explicitly published "no music" guidance.
- A live person on screen. Screen + voice only is preferred.
