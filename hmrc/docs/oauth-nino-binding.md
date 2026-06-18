# OAuth ↔ NINO binding — root cause and fixes (2026-06-18)

## What was broken

Against the live HMRC sandbox, **every** user-restricted call (Business
Details `list`, Obligations) returned `404 MATCHING_RESOURCE_NOT_FOUND` —
for every test NINO ever tried, across 100+ logged calls. Not one
successful obligations retrieval or quarterly submission had ever happened.
The dashboard hid this by rendering **demo data** over the failures, so a
non-working connection looked like it was filing fine.

This was found while verifying the app against HMRC SDST's 18 Jun
"Defining ready" email — and it directly contradicted the production
application we'd sent (which claimed a passing end-to-end journey).

## Root cause (three compounding bugs)

1. **HMRC ignores `prompt=login`.** The reconnect flow asks for a forced
   re-login (`/api/hmrc/connect?fresh=1` → `prompt=login`), but HMRC's
   OAuth endpoint is RFC 6749 and silently ignores the OIDC `prompt`
   param. So HMRC re-authorises using its **sticky Government Gateway
   session cookie** — binding the new token to a *previous* test user,
   never the freshly-minted one. (`hmrc/services/oauth.py::build_authorize_url`
   already documented this risk.)

2. **The callback never verifies identity.** `hmrc/routers/oauth.py::hmrc_callback`
   stores the access/refresh tokens blind — it never establishes which NINO
   the token is actually for. The NINO comes entirely from what the user
   types later (`business_details.py` / `obligations.py` read it from the
   request body). Token-for-user-X + typed-NINO-Y = permanent 404, with
   nothing to catch it.

3. **The dashboard masked the failure.** `obligations.py::fetch_for_user`
   fell back to `_demo_obligations()` whenever `businesses` was empty — which
   is exactly the state a mismatch 404 leaves behind. Fake obligations were
   shown as real.

## Fixes shipped in this change

- **Fix 3 (keystone): no demo data on a real connection.**
  `obligations.py` now only returns the demo fixture when genuinely
  *not connected* (or under the explicit `HMRC_DEMO_OBLIGATIONS=1` flag).
  A connected user with no NINO/businesses gets a truthful empty response
  with an `error` naming the likely `OAUTH_NINO_MISMATCH` cause. Regression
  tests: `tests/hmrc/test_obligations.py::test_connected_without_business_setup_does_NOT_show_demo`
  and `::test_connected_with_nino_but_no_businesses_explains_mismatch`.

- **Fix 1 (messaging): connect-time 404 leads with the real cause.**
  `business_details.py::_friendly_detail_for_hmrc` no longer implies that
  "create a test business" fixes a 404 (it doesn't, if the cause is a
  mismatch). It now leads with "your sign-in doesn't match this NINO →
  reconnect with the matching credentials," and offers the sandbox
  provisioner only as the secondary path.

These make the app **honest**: a broken connection now looks broken, to
both HMRC's reviewers and real customers (today a real user who fat-fingers
their NINO would have been shown fabricated obligations).

## Still needed to get a GREEN journey

Fixes 1 + 3 surface the failure correctly but do **not** by themselves
defeat HMRC's sticky GG session. To actually bind the token to the intended
test user:

- **Operational (works today):** do each test-user sign-in in a **fresh
  incognito window** (no sticky HMRC cookie). Mint one test user, connect,
  sign in as exactly that user → `business/details/{NINO}/list` returns 200,
  obligations populate, and a quarterly submit can be made. That is the
  journey to put in front of HMRC — under the NINO you actually signed in as.

- **Deeper follow-up (Fix 2, not yet shipped — needs careful prod testing):**
  redirect through HMRC's Government Gateway sign-out URL before `authorize`
  so the sticky session is torn down server-side; and, after callback, make
  a Business Details probe to confirm the token's identity before treating
  the connection as usable.

## Bearing on the production application

The application sent on 2026-06-15 (NINO `RZ507241A`) was premature: that
NINO never had a working journey in HMRC's logs. Once a genuinely green
journey is captured under a correctly-bound NINO, re-engage SDST with that
NINO. See [[project_hmrc_production_approval]].
