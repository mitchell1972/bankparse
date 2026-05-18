# HMRC Sandbox Test User — runbook

Goal: create a sandbox NINO + at least one self-employment business + one
property business under our existing HMRC Developer Hub app so we can hit
the real `GET /individuals/business/{type}/{nino}/{businessId}/obligations`
endpoint instead of the demo fixture.

**Time needed:** ~15 minutes.
**Cost:** £0.

You only do this once. The credentials you create are reusable across every
MTD ITSA endpoint we add after Obligations.

---

## Prereqs

- Your HMRC Developer Hub account (the one that created our app
  `c16a75dc-378d-4171-a2ca-4a4f1cd068b0`).
- The app's `Application ID`, `Client ID` and `Client Secret` to hand.

---

## Step 1 — Open the test user creator

1. Go to <https://developer.service.hmrc.gov.uk/api-test-user>
2. Sign in with your HMRC Developer Hub account if prompted.
3. Click **Create a test user**.
4. Choose **Individual** (NOT Organisation — Self Assessment is for
   individuals).
5. Tick every service you want this test user to have. For us:
   - **Making Tax Digital Income Tax Self-Assessment**
   - **Self Assessment** (the legacy one — leave it ticked, it doesn't
     hurt)
   - **National Insurance**
6. Click **Create**.

HMRC will return a JSON-like block with:

```
User ID:        12345678901
Password:       <generated>
NINO:           AB 12 34 56 C       ← keep this, you need it
SA UTR:         1234567890
MTD ITSA ref:   XAIT00000000001
```

**Save the NINO and the password** — you can't read either again later.

---

## Step 2 — Create a sandbox self-employment business

1. Go to <https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/business-details-api>
2. Use the **API Explorer** ("Try this API" link on the right).
3. Pick the endpoint **`POST /individuals/business/details/{nino}/list`**
   *(create test data → self-employment)*. The exact "create business"
   endpoint sits under the **Test Support APIs**, not the main API — look
   for **"Create Test Business"** in the Test Support section.
4. Use the NINO from Step 1 as the path param.
5. Submit the example body unchanged. HMRC returns a `businessId` that
   looks like `XAIS00000000001` — **save it**.

---

## Step 3 — Create a sandbox property business

Same as Step 2 but with `"typeOfBusiness": "uk-property"`. Saves a
`businessId` like `XPIS00000000002`.

---

## Step 4 — Hand the values to BankParse

Once you have:

```
NINO:        AB123456C
SE business: XAIS00000000001
Prop biz:    XPIS00000000002
HMRC pwd:    (the password from step 1)
```

…and the deployed app on Railway:

1. As your normal BankParse user, click **Connect to HMRC** on the
   dashboard. You'll be bounced to the HMRC consent page.
2. Sign in with the **test User ID + password** from Step 1 (NOT your
   real Government Gateway login).
3. Click **Grant authority**. You'll be sent back to the dashboard.
4. POST `/api/hmrc/obligations/business-setup` from the browser console
   (the dashboard panel will eventually do this for you — for now do it
   manually so we can test today):

   ```js
   await fetch('/api/hmrc/obligations/business-setup', {
     method: 'POST',
     headers: {'Content-Type': 'application/json',
               'X-CSRF-Token': document.cookie.match(/bp_csrf=([^;]+)/)[1]},
     credentials: 'include',
     body: JSON.stringify({
       nino: 'AB123456C',
       businesses: [
         {business_id: 'XAIS00000000001', type_of_business: 'self-employment',
          label: 'Mitoba sole trader (sandbox)'},
         {business_id: 'XPIS00000000002', type_of_business: 'property',
          label: 'Ipswich SA portfolio (sandbox)'},
       ],
     }),
   }).then(r => r.json());
   ```

5. Refresh the dashboard. The "Your HMRC deadlines" panel should switch
   its badge from **Demo** to **Live (HMRC)** and show real obligations
   from the sandbox.

---

## Step 5 — Force-demo on production (anytime)

If something goes wrong on Railway and the real sandbox call starts
erroring, set:

```
HMRC_DEMO_OBLIGATIONS=1
```

…to revert every user to the static fixture without a deploy. Unset it
to go live again.

---

## Things that commonly go wrong

| Symptom | Fix |
|---|---|
| Consent page says "client not recognised" | `HMRC_CLIENT_ID` on Railway doesn't match the developer hub app. |
| Callback returns `state_mismatch` | The `bp_hmrc_state` cookie is being blocked. Check that the redirect URI matches what's registered on the developer hub *exactly*, including https vs http. |
| Real call returns `400 INVALID_NINO` | NINO format is wrong — must be two letters, six digits, one letter (A/B/C/D). No spaces. |
| Real call returns `403 CLIENT_OR_AGENT_NOT_AUTHORISED` | You signed in with your real Gateway user, not the sandbox test user from Step 1. |
| Real call returns `404 NO_OBLIGATIONS_FOUND` | The sandbox test user has no obligations for the date range. Add `?fromDate=2026-04-06&toDate=2027-04-05` to widen the search, or accept the panel showing 0 rows. |

---

## What I (Claude) need from you when this is done

Just confirm in the chat:

> "Sandbox user created. NINO is X, SE business is Y, property business is Z, Railway env updated."

I'll then flip `HMRC_DEMO_OBLIGATIONS=0` on Railway, click around to verify
the live path, and we move on to the next endpoint (likely Business
Details or Quarterly Updates).
