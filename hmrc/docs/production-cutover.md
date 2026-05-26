# Production cutover — sandbox → production HMRC

After HMRC SDST approves the recognition application, they issue a
SEPARATE client_id + client_secret for the production HMRC API at
`https://api.service.hmrc.gov.uk`. This document covers the exact
env-var flip on Railway + the post-flip verification + the rollback
plan if anything is wrong.

**Do not start this until:**
- HMRC has emailed confirmation of recognition (case marked "Approved")
- You have the new production `client_id` and `client_secret` in your
  password manager
- The current sandbox app on bankscanai.com is healthy
  (`/api/health` returns 200, real-sandbox tests green for ≥7 days)

## What changes

| Item | Sandbox value (today) | Production value (after cutover) |
|---|---|---|
| `HMRC_ENV` | `sandbox` (default) | `production` |
| `HMRC_CLIENT_ID` | `UhqfKGKZlsk3dBHxqW4gkZVT2iG5` | (new from HMRC, prod app) |
| `HMRC_CLIENT_SECRET` | (sandbox secret) | (new from HMRC, prod app) |
| `HMRC_REDIRECT_URI` | `https://bankscanai.com/api/hmrc/callback` | unchanged |
| `HMRC_BASE_URL` | unset (defaults to `https://test-api.service.hmrc.gov.uk`) | unset — derived from `HMRC_ENV=production` |

Everything else stays put. `HMRC_TOKEN_ENCRYPTION_KEY`,
`SENTRY_DSN`, rate-limiter knobs, vendor identity etc. don't change.

## Pre-cutover checklist

- [ ] Recognition acceptance email saved + HMRC case reference recorded
- [ ] Prod client_id + secret saved to password manager (NOT chat, NOT
      committed)
- [ ] Sandbox app currently green on `/api/health`
- [ ] Latest conformance transcript shows PASS
      (`scripts/run_conformance_suite.py --phase 2`)
- [ ] Backup of the SQLite audit DB taken in the last 24h (see
      [`audit-log.md`](audit-log.md))
- [ ] One named human (not just the founder) standing by to revert if
      step 5 verification fails
- [ ] Cutover window scheduled OFF the Jan/Apr deadline weeks — pick a
      Tuesday morning when traffic is low

## Cutover steps

### 1. Disconnect every connected user on the sandbox app

Stored sandbox tokens won't work against production. Force a clean
re-OAuth by walking the hmrc_connections table:

```bash
# Railway shell:
sqlite3 /data/bankparse.db "UPDATE hmrc_connections SET access_token=NULL, refresh_token=NULL, expires_at=NULL;"
```

The next time any user opens `/hmrc/file`, they'll be prompted to
**Connect to HMRC** again — this time against the production API.
Users keep their NINO + business IDs (those don't depend on env).

### 2. Flip the env vars on Railway

In the Railway dashboard, on the prod service, set in order:

```
HMRC_CLIENT_ID     = <new prod client id>
HMRC_CLIENT_SECRET = <new prod client secret>
HMRC_ENV           = production
```

Save. Railway auto-redeploys.

### 3. Confirm the redirect destination changed

While the redeploy is happening, check that NO traffic is being
routed yet:

```bash
curl -sI -o /dev/null -w "%{http_code}\n" https://bankscanai.com/api/hmrc/connect
```

Should be 302 (auth-gated, as before). Once the new deploy is live:

```bash
# Authenticate as a test account, then:
curl -s -L --max-redirs 0 \
  -H "Cookie: bp_auth=<your session>" \
  https://bankscanai.com/api/hmrc/connect \
  -o /dev/null -w "%{redirect_url}\n"
```

The `Location:` should now point to `https://api.service.hmrc.gov.uk/oauth/authorize?...`
— NOT `test-api.service.hmrc.gov.uk`. If you still see test-api, the
deploy hasn't picked up — wait + retry.

### 4. End-to-end smoke as the founder

The founder (Mitchell) signs in to bankscanai.com using his **real**
Government Gateway credentials (the ones HMRC issued for his real
self-employment income), then:

1. Connect to HMRC → grants permission on `api.service.hmrc.gov.uk`
2. Discover businesses → real Mitoba sole-trader returned
3. View obligations → real open quarter shown (Apr–Jul tax year)
4. **DO NOT SUBMIT YET** — just preview. Confirm the totals match.

If any step fails, **stop and revert** (see Rollback below).

### 5. Wait 30 minutes, then revisit

- `/api/health` still 200
- No 5xx alerts firing in Sentry
- No customer support tickets mentioning HMRC errors
- Audit log shows the founder's calls landing on
  `api.service.hmrc.gov.uk` (not test-api)

### 6. Announce

Email the waiting list + post the launch announcement only AFTER step
5 has passed cleanly for at least an hour. Customers' first impression
of the live filing path is hard to undo.

## Rollback

If anything in step 3/4/5 goes wrong, immediately revert:

```
HMRC_ENV           = sandbox
HMRC_CLIENT_ID     = UhqfKGKZlsk3dBHxqW4gkZVT2iG5
HMRC_CLIENT_SECRET = <sandbox secret from password manager>
```

Save → wait for redeploy → confirm `/api/hmrc/connect` redirects to
`test-api.service.hmrc.gov.uk` again.

Users who connected during the broken window will have stored prod-
issued tokens that no longer work; the step 1 SQL clears them too,
and they'll re-OAuth back on sandbox. Inconvenient, not destructive.

## What can go wrong

| Symptom | Likely cause | Action |
|---|---|---|
| `/api/hmrc/connect` still redirects to test-api after the flip | Railway hasn't redeployed yet, OR `HMRC_BASE_URL` is hard-set | Wait + check Railway deploy logs; clear `HMRC_BASE_URL` if set |
| All users see `401 invalid_grant` on the next call | `HMRC_CLIENT_SECRET` typoed | Re-paste from password manager — exact match, no surrounding whitespace |
| 403 with `INVALID_SCOPE` on first user OAuth | The prod app isn't subscribed to the API you're calling | Open the prod app in dev hub → Subscriptions → tick the missing API |
| `Gov-Vendor-Public-IP` rejected | Railway's egress IP changed and HMRC's allowlist hasn't caught up | Email the SDST support address with the new IP range |
| Founder's own NINO doesn't return businesses | Mitoba businesses haven't been registered for MTD with HMRC yet | Open HMRC personal tax account → MTD signup → wait 24h |

## After cutover — schedule

| Action | When |
|---|---|
| First paying customer connects to HMRC for real | Same day, watch Sentry like a hawk |
| First customer-initiated quarterly submission to live HMRC | Within 7 days, ideally the founder's own |
| First customer EOPS to live HMRC | Q4 of the tax year (Jan-Apr) |
| First customer final declaration | After year-end (Apr-Jan of following year) |
| Re-run `scripts/run_conformance_suite.py --phase 3` against prod | Once per quarter to catch silent HMRC-side breakage |
