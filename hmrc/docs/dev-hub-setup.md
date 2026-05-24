# HMRC Developer Hub — local setup

Goal: get a working **sandbox** connection from this laptop to
`https://test-api.service.hmrc.gov.uk` so the Playwright journey, the
real-sandbox tests, and ad-hoc submit experiments can all run without
touching production.

**Time:** 10–15 minutes the first time. Zero on every subsequent machine
once you have the client id + secret saved somewhere you can retrieve.

> If you already have client credentials and just want to wire them up,
> skip to [Step 3](#step-3--wire-them-up-locally).

---

## Current state (as of 2026-05-24)

A sandbox application **already exists** in the BankScan dev-hub account:

| Field | Value |
|---|---|
| Application name | `BankScan AI` |
| Application ID | `c16a75dc-378d-4171-a2ca-4a4f1cd068b0` |
| Environment | Sandbox |
| Client ID | `UhqfKGKZlsk3dBHxqW4gkZVT2iG5` |
| Last API call | 23 May 2026 |
| Direct link | <https://developer.service.hmrc.gov.uk/developer/applications/c16a75dc-378d-4171-a2ca-4a4f1cd068b0/manage> |

**APIs already subscribed** (no action needed):

| API | Version |
|---|---|
| Self Assessment Test Support (MTD) | 1.0 |
| Create Test User | 1.0 |
| Business Details (MTD) | 2.0 |
| Obligations (MTD) | 3.0 |
| Self Employment Business (MTD) | 5.0 |
| Property Business (MTD) | 6.0 |
| Business Source Adjustable Summary (MTD) | 7.0 |
| Individual Calculations (MTD) | 8.0 ⚠ |
| Test Fraud Prevention Headers | 1.0 |

> ⚠ Our code (`hmrc/services/annual.py`) currently pins Calculations API
> v7.0 in the Accept header but the app is now subscribed to v8.0.
> Spawned task on the queue to investigate; the real-sandbox tests will
> 404 on calculation calls until that's reconciled.

**Redirect URIs currently registered** (1 of 5 used):

  - `https://bankscanai.com/api/hmrc/callback` (production)
  - **MISSING for local dev:** `http://127.0.0.1:8000/api/hmrc/callback`

**Client secret:** one exists (ending `…75b0`, created 18 May 2026, last
used 23 May 2026). HMRC's policy is that the full secret is only shown
**once** at creation time, so unless you saved it the first time, you'll
need to generate a new one.

### Two manual actions to finish setup

Because both are state-changing on a shared dev-hub app, the automation
deliberately stops short of them. Do them yourself in 2 minutes:

1. **Add the localhost redirect URI** —
   <https://developer.service.hmrc.gov.uk/developer/applications/c16a75dc-378d-4171-a2ca-4a4f1cd068b0/redirect-uris>
   → **Add a redirect URI** →
   `http://127.0.0.1:8000/api/hmrc/callback` → **Add**.
2. **Get a client secret you can copy** —
   <https://developer.service.hmrc.gov.uk/developer/applications/c16a75dc-378d-4171-a2ca-4a4f1cd068b0/client-secrets>
   → if you saved the `…75b0` secret at creation, reuse that; otherwise
   click **Generate another client secret**, copy the new value
   immediately (HMRC will only show it once), and optionally delete the
   old one once you've confirmed the new one works.

---

## Step 1 — Find or create your sandbox application

1. Open <https://developer.service.hmrc.gov.uk/developer/applications>.
2. Sign in. (Sign up if this is your first time — HMRC ask for name +
   email and verify by clicking a link. The account is free.)
3. You'll see a list of applications. **If one already exists**, click
   it. Otherwise click **Add a new application**:
   - **Application name:** `BankScan AI (local dev)` — anything memorable
     to YOU, the user never sees it.
   - **Environment:** Sandbox (default).
4. On the application's page, note the **Application ID** (UUID).

> Our existing sandbox app id is recorded in
> [`recognition-application-package.md`](recognition-application-package.md)
> as `c16a75dc-378d-4171-a2ca-4a4f1cd068b0`. If you have access to that
> account, reuse it — you'll inherit existing API subscriptions.

---

## Step 2 — Subscribe to the right APIs and grab the secret

On the application page:

1. Click **API Subscriptions**. Tick at minimum:
   - **Self Assessment Test Support** (v1.0) — needed to mint test users
   - **Business Details** (v1.0)
   - **Obligations (MTD)** (v3.0)
   - **Self-Employment Business** (v5.0)
   - **UK Property Business** (v6.0)
   - **Individual Calculations** (v7.0)
   - **Authorisation**
   - **Fraud Prevention Headers Validator** (always tick — cheap & free)
2. Click **Credentials** in the left nav.
3. The page shows **Client ID** (visible) and **Client secret**
   (revealable once — copy it the moment you see it; HMRC will
   regenerate-only after that).
4. Click **Manage redirect URIs** and add:
   - `http://127.0.0.1:8000/api/hmrc/callback` — local dev
   - `https://bankscanai.com/api/hmrc/callback` — production (if not already)

---

## Step 3 — Wire them up locally

```bash
cp .env.hmrc.example .env.hmrc
$EDITOR .env.hmrc       # paste your CLIENT_ID + CLIENT_SECRET
```

Then load it into your shell before booting the app or running tests:

```bash
export $(grep -v '^#' .env.hmrc | xargs)
```

(Or use [`direnv`](https://direnv.net/) and add `dotenv .env.hmrc` to a
`.envrc` at the repo root so the values auto-load when you `cd` in.)

---

## Step 4 — Verify

One command — runs every check and prints PASS/FAIL with the exact next
action when something is wrong:

```bash
python scripts/hmrc_dev_hub_check.py
```

What it actually does:

1. Confirms `HMRC_CLIENT_ID`, `HMRC_CLIENT_SECRET`, `HMRC_REDIRECT_URI`
   and `HMRC_TOKEN_ENCRYPTION_KEY` are all present.
2. Exchanges the client credentials for an application-restricted
   access token against `https://test-api.service.hmrc.gov.uk/oauth/token`
   — fails fast if the app id is wrong or `Self Assessment Test Support`
   isn't subscribed.
3. Mints a fresh sandbox test individual via
   `POST /create-test-user/individuals`.
4. Provisions a self-employment business under the new NINO.
5. Calls the Fraud Prevention Headers Validator with the headers our
   client builds — proves the 13 mandatory headers are correctly
   populated.

Each step prints `PASS` / `FAIL` and any failure carries a one-liner
telling you what to fix.

---

## Step 5 — Run the real-sandbox tests

Once the check passes:

```bash
HMRC_REAL_SANDBOX_E2E=1 \
  pytest tests/e2e/test_hmrc_real_sandbox.py -xvs
```

These three tests hit `test-api.service.hmrc.gov.uk` directly and prove
our wire shapes are sandbox-accepted. They're the cheapest insurance
against an SDST recognition rejection — failures here mean the same
shape would be rejected at recognition.

For the full Playwright journey against the real sandbox (rather than
the stub), keep the env vars loaded and run the existing journey
file — it'll automatically use the real sandbox because no
`HMRC_BASE_URL` override is set.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Step 1 FAIL` (env vars missing) | `.env.hmrc` not exported | `export $(grep -v '^#' .env.hmrc \| xargs)` and re-run |
| Token endpoint returns 401 | Wrong client id/secret OR app not subscribed to any API | Re-copy the secret from the Developer Hub; subscribe to **Self Assessment Test Support** as a minimum |
| `create-test-user` returns 403 `INVALID_SCOPE` | App is subscribed but not in a state HMRC has activated | Wait 1–2 min after subscribing; refresh the Developer Hub page; try again |
| `create-test-user` returns 404 | App still using v0 of the API | Re-tick the v1.0 version on the Subscriptions page |
| Callback fails locally with `redirect_uri_mismatch` | Local URL not added | Add `http://127.0.0.1:8000/api/hmrc/callback` to the application's redirect URIs |
| Fraud-headers validator says `Gov-Client-Public-IP` invalid | You're running with no browser request context (e.g. CLI script with no `request`) | Real journey traffic always carries it; the dev-hub check uses a stub set of headers that satisfies the validator |

---

## What this DOESN'T cover

Production credentials (different secret set, different base URL,
different subscriptions). That's [`MANUAL_STEPS.md`](MANUAL_STEPS.md) —
do that once you're ready to submit the recognition application.
