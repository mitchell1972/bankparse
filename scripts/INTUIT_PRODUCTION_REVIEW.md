# Intuit Production Review — BankScan AI

Continuation doc for the in-progress Intuit App Store submission.

**App ID**: `1ce35acb-4991-498e-a443-7d1d0572aaf9`
**Direct link**: https://developer.intuit.com/appdetail/keys?appId=djQuMTo6OGQzYmJlYTI3Yg:1ce35acb-4991-498e-a443-7d1d0572aaf9&id=9341457056878290

---

## State at 2026-05-11

### App details — 100% complete ✓

All six sections green. Filled values:

- **Profile**: Mitchell Agoma · mitchell_agoma@yahoo.co.uk · +44 7940 361848 · 3rd Floor, 86-90 Paul Street, London EC2A 4NE, United Kingdom (Greater London)
- **EULA + Privacy Policy**: both pointing at `https://bankscanai.com/privacy`
- **Host domain + URLs**:
  - Host domain: `bankscanai.com`
  - Launch URL: `https://bankscanai.com/`
  - Disconnect URL: `https://bankscanai.com/`
  - Connect/Reconnect URL: `https://bankscanai.com/api/qbo/connect`
- **App categories**: Accounting, Banking, Receipt Management, Expense Management
- **Regulated industries**: None of the above
- **Hosting**: United States, single IP `76.76.21.21` (Vercel anycast)

### Development OAuth — working ✓

- Sandbox keys live in `.env` and on Vercel as `INTUIT_CLIENT_ID` / `INTUIT_CLIENT_SECRET` (Production + Preview)
- `INTUIT_ENVIRONMENT=sandbox`
- `INTUIT_REDIRECT_URI=https://bankscanai.com/api/qbo/callback`
- OAuth round-trip verified against Sandbox Company US 19b3

### Compliance questionnaire — ~15% complete

Saved progress: General Questions (6 Qs + AI follow-ups). Stuck mid-way through App Information because the Salesforce-hosted questionnaire page kept hanging the Chrome automation extension.

---

## Answer sheet — paste these to finish the questionnaire

URL: open the BankScan AI app overview → **Get production keys** → **Compliance** card → **Continue questionnaire**.

### General Questions (already saved — skip)

1. Regulatory complaints/lawsuits → **No**
2. Worked with legal counsel → **No**
3. Confirm compliance with security policies → **Yes**
4. App designed to enhance QuickBooks experience or facilitate business process → **Yes**
5. Sanctions list / embargoed countries → **No**
6. Generative AI functionality → **Yes**
   - *How are you using generative AI:* BankScan AI uses Anthropic Claude (claude-haiku-4-5) as a vision-capable model to parse user-uploaded PDF bank statements and receipts. The AI extracts transaction fields — date, description, amount, balance — and outputs them as a structured spreadsheet that the customer downloads or pushes to QuickBooks Online. The AI is used solely for OCR-style extraction of existing transactions from the user's own documents. It does not generate financial advice, create transactions, or modify customer QuickBooks data. User data sent to Anthropic is governed by Anthropic's standard API terms (no training on customer data). The QBO integration uses the extracted data to create Purchase and Deposit records in a bank account the user explicitly selects in our app.
   - *Uses QuickBooks data for training:* **No**

### App Information (resume here)

1. About your app → check **"You built your app from scratch and wrote the code that lets it interact with Intuit APIs and data"** only
2. Platforms → move **Web/SaaS** to Chosen
3. How interacts with Intuit data → move **It reads data from Intuit product(s)** AND **It writes data to Intuit product(s)...** to Chosen
4. Private or public → **We plan to make our app publicly available**
   - *How many QBO customers anticipated:* **500**
5. Which QBO users can use the app → **Only the QuickBooks Online company admin who connected the app**
6. Integrates with other platforms → **Yes**
   - *Name the platforms:* Anthropic (Claude API — used for AI-assisted parsing of bank statement PDFs into structured data). Stripe (payment processing for our subscription tiers). Vercel (web hosting). Turso (managed SQLite for user accounts and OAuth tokens).

### Authorization and Authentication

- OAuth implementation: **OAuth 2.0 authorization code flow**
- Where access tokens stored: **Encrypted server-side in our database** (Turso, one row per user_id in `qbo_connections` table)
- Refresh token rotation: **Yes** — we use the refresh token returned in each token exchange
- Token expiry handling: **Refresh on demand; if refresh token expires (100 days), user re-OAuths**
- Redirect URI host: **Our own server** (`https://bankscanai.com/api/qbo/callback`) — not a third-party
- PKCE: **No** (server-side flow with confidential client)
- State parameter: **Yes — signed JWT, validates user_id + nonce, 10-min TTL**

### API Usage

- Which Intuit APIs: **Accounting API only**
- Endpoints used: **Account (query), Purchase (create), Deposit (create), CompanyInfo (read)**
- Triggered by user action: **Yes — only when a logged-in user clicks "Push to QuickBooks" in our UI**
- Scheduled / cron / background jobs: **No**
- Rate-limit handling: **HTTP 429 → exponential backoff with jitter; per-user push capped at 500 transactions per request**
- Webhooks consumed: **No**
- Minor version: **75**
- API call volume estimate: **<100 calls per user per day** typically; spike up to 500 calls during a bulk push

### Error Handling

- Server-side logging: **Structured logs via Python `logging`, no PII (no bank statement content, no email body, no API keys logged)**
- User-facing error messages: **Generic — "Push to QuickBooks failed. Please try again." Never expose stack traces, internal IDs, or third-party error text**
- Retry logic: **5xx and 429 → retry once with backoff. 4xx → surface a friendly error to the user**
- Failed-transaction handling: **Per-row failures are collected and shown back to user. First 5 consecutive failures abort the batch to prevent runaway errors**
- Alerting: **Manual log review** (no PagerDuty/Sentry yet)

### Security

- TLS: **TLS 1.2+ everywhere; HTTPS-only; HSTS via Vercel**
- Authentication for our own users: **Email + bcrypt password, signed session cookies with `itsdangerous` (`URLSafeTimedSerializer`)**
- CSRF protection: **Double-submit cookie pattern, `X-CSRF-Token` header validated on all POSTs**
- Password storage: **bcrypt** for our own user accounts. **No QuickBooks passwords stored** — OAuth only
- Encryption at rest: **Turso DB encryption at rest (managed by Turso). OAuth tokens stored in standard DB rows** — no additional application-level encryption layer
- PII collected: **Email address, optional Stripe customer ID. Bank statement contents are processed in-memory during parsing and not persisted after the response is sent** (uploads dir cleaned within minutes; outputs cleaned hourly)
- Penetration test: **No** (small business, future plan)
- SOC 2 / ISO certification: **No**
- Access controls: **Single admin (Mitoba Consulting founder); Vercel + Turso dashboards both protected by 2FA**
- Secrets management: **Env vars in Vercel; no secrets in git**
- Incident response: **Manual playbook — review logs, rotate credentials, notify affected users by email, post a status note**
- Data deletion on user request: **Account + all rows deleted via app's delete-account flow; OAuth tokens are also revoked at Intuit's revoke endpoint**
- Vulnerability management: **Dependabot enabled on GitHub; Python deps pinned in requirements.txt**

Click **Save** after each tab. After the last tab, the button below the form should change from **Save / Next Tab** to **Submit** — click Submit.

---

## After compliance hits 100%

The padlocks on Production keys disappear and the Submit-for-review button enables. Run the four steps below in order.

### Step 1 — Add the production Redirect URI

1. App → **Settings** → **Redirect URIs** tab
2. Switch from **Development** to **Production**
3. Click **Add URI** → enter `https://bankscanai.com/api/qbo/callback`
4. **Save**

### Step 2 — Reveal and copy production keys

1. App → **Keys and credentials** → **Production** tab
2. Click the **Show credentials** toggle
3. Copy **Client ID** and **Client secret** (have them ready for step 3)

### Step 3 — Update Vercel env vars

Project: [bankparse](https://vercel.com/upwork-product/bankparse/settings/environment-variables)

Edit existing vars (do not delete and recreate — keep them as Sensitive):

| Variable | New value |
| --- | --- |
| `INTUIT_CLIENT_ID` | Production Client ID from step 2 |
| `INTUIT_CLIENT_SECRET` | Production Client Secret from step 2 |
| `INTUIT_ENVIRONMENT` | `production` |
| `INTUIT_REDIRECT_URI` | `https://bankscanai.com/api/qbo/callback` (unchanged) |

After saving, click the **Redeploy** prompt in the toast to push the new vars to the live site.

### Step 4 — Submit for review

Back to Intuit → App overview → **Get production keys** → at the bottom, **Submit for review**.

Intuit's reviewers typically take **a few business days to ~2 weeks**. They may come back with questions (most commonly: terms of service URL different from privacy URL, screenshots, more detail on data flow). Address those in the review portal — no code changes usually needed.

---

## Quick repro of where we left off

```
# What's deployed:
git log --oneline -3
# ed2cb70 fix: mirror QBO routes onto Vercel entry (api/index.py)
# 8e38874 feat: QuickBooks Online integration — OAuth + Push to QBO

# Sandbox live test against bankscanai.com:
# 1. log in
# 2. parse a statement
# 3. click green "Connect to QuickBooks"
# 4. authorise sandbox QBO company at Intuit
# 5. click "Push to QuickBooks" → modal opens
# 6. pick bank/expense/income accounts → Push
# 7. confirm rows appear in QBO sandbox bank register
```

If something breaks post-merge to production keys, the easiest rollback is to swap the four Vercel env vars back to the sandbox values and `INTUIT_ENVIRONMENT=sandbox`. No code rollback needed — the same code handles both environments via the env var.
