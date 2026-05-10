# Intuit (QuickBooks Online) Setup — BankScan AI

This is the step-by-step for wiring the existing Intuit Developer app
(workspace: **Mitoba Consulting**) into BankScan AI.

End result: a logged-in BankScan AI user can click "Connect to QuickBooks",
authorise the company, then push parsed bank statement transactions straight
into the QBO bank register as Purchase (debits) or Deposit (credits) entries.

---

## 1. Configure the Intuit app

1. Open https://developer.intuit.com/workspaces and pick the **Mitoba
   Consulting** workspace.
2. Open the existing app (the "1 apps" card).
3. **Keys & credentials** → make a note of both the **Development** and
   **Production** keys. You'll plug these into env vars below.
4. **Redirect URIs** — add:
   - `http://localhost:8000/api/qbo/callback` (development)
   - `https://bankscanai.com/api/qbo/callback` (production)
5. **Scopes** — make sure `com.intuit.quickbooks.accounting` is checked.
   That's the only scope BankScan AI needs (Accounts, Purchase, Deposit,
   JournalEntry, CompanyInfo).

> The Production keys only become usable after Intuit approves the app for
> the App Store. Until then, every user goes through Development keys against
> a Sandbox company.

## 2. Local env vars

Add these to `.env` (already scaffolded — fill the blanks):

```env
INTUIT_CLIENT_ID=<Development Client ID from Intuit>
INTUIT_CLIENT_SECRET=<Development Client Secret from Intuit>
INTUIT_ENVIRONMENT=sandbox
INTUIT_REDIRECT_URI=http://localhost:8000/api/qbo/callback
```

For Vercel/Railway production, set the same four variables but with the
**Production** Client ID/Secret, `INTUIT_ENVIRONMENT=production`, and
`INTUIT_REDIRECT_URI=https://bankscanai.com/api/qbo/callback`.

## 3. Create a sandbox company (one-off, for testing)

1. developer.intuit.com → **Dashboard** → **Sandbox**.
2. **Add a sandbox company** (UK locale recommended to match BankScan's
   default currency formatting).
3. Note the company name — that's what BankScan AI will show after a
   successful connect.

## 4. Try the flow

1. `uvicorn app:app --reload` (already wired in `app.py`).
2. Log in to BankScan AI in the browser.
3. Upload a PDF or CSV statement, parse it.
4. On the results card, the "Push to QuickBooks" button now appears.
5. First click → redirected to Intuit consent → pick the sandbox company →
   sent back to `/?qbo=connected`.
6. Click "Push to QuickBooks" again → modal lists your sandbox accounts.
   Pick a bank account, an expense account, and an income account, then
   click **Push**.
7. In QBO sandbox, open the bank register for that account — every parsed
   row is there as a Purchase (debit) or Deposit (credit).

## 5. Going to production

Intuit requires an app review before you can call the production API on
real customer companies. The review form is at:

- developer.intuit.com → your app → **Production Settings** → **Submit for
  Production**.

You'll need:

- Production Redirect URI: `https://bankscanai.com/api/qbo/callback`
- EULA + Privacy URLs (already live at `/privacy`).
- A short "what the app does" description — the [solutions/import-bank-statements-into-quickbooks-online](../templates/solutions/import-bank-statements-into-quickbooks-online.html) page covers most of it.
- A test QBO login Intuit's reviewer can use (create a dedicated sandbox
  company with one parsed statement already pushed).

Once approved, swap the env vars on Vercel/Railway to the production keys
and flip `INTUIT_ENVIRONMENT=production`.

---

## What's in the codebase

| File | Purpose |
| --- | --- |
| [core.py](../core.py) | `INTUIT_*` env constants, `INTUIT_AVAILABLE` flag |
| [quickbooks.py](../quickbooks.py) | OAuth flow, token refresh, accounts list, push transactions |
| [database.py](../database.py) | `qbo_connections` table + CRUD helpers |
| [app.py](../app.py) | `/api/qbo/{status,connect,callback,disconnect,accounts,push}` |
| [templates/index.html](../templates/index.html) | "Push to QuickBooks" button + account-picker modal |

## Token storage

Per-user OAuth tokens live in `qbo_connections` (one row per user, keyed on
`user_id`). Access tokens auto-refresh on demand; refresh tokens are good
for 100 days, after which the user has to re-connect — the helper
`get_valid_access_token` handles all of that transparently.

## Push semantics

Each parsed transaction → one QBO transaction:

- **Money out** (negative amount) → `Purchase` posted against the chosen
  bank account, with one line item against the chosen expense account
  ("Uncategorised Expense" by default).
- **Money in** (positive amount) → `Deposit` posted into the chosen bank
  account, with one line item from the chosen income account.

This keeps the entries visible directly in the QBO bank register where
bookkeepers expect them, so they can categorise individual rows inside QBO
without round-tripping to BankScan AI.

A push of more than 500 transactions in one go is blocked at the API
layer to stay well inside Intuit's per-minute throttle (500 reqs/minute
per realm).
