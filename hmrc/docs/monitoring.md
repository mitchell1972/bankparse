# Production monitoring + alerting

Goal: get paged when HMRC starts failing in a way that signals
recognition-rejection-class issues — before our customers tell us on
Twitter, or worse, the morning of 31 January.

## What's wired up

`hmrc/services/monitoring.py` exposes a thin wrapper around the Sentry
SDK that's a NO-OP when `SENTRY_DSN` is unset. Two things are sent to
Sentry when DSN is set:

1. **Uncaught FastAPI exceptions** — the default behaviour of
   `sentry-sdk`'s FastAPI integration. Catches anything we forgot to
   try/except around.
2. **Explicit HMRC failure events** — every call routed through
   `services/client.request()` that returns 5xx or 0 (network) is
   captured with structured tags:

   | Tag | Value | Use |
   |---|---|---|
   | `hmrc.endpoint` | path, NINOs scrubbed to `[NINO]` | group + filter |
   | `hmrc.method`   | `GET` / `POST` / etc.            | grouping |
   | `hmrc.status`   | HMRC HTTP code (e.g. `503`)      | filter |
   | `hmrc.code`     | HMRC error code (e.g. `SERVER_ERROR`) | filter |
   | `user_hash`     | 12-char sha256 of user_id        | impact size |
   | `audit_id`      | row id in `hmrc_submissions`     | join to audit log |

   **4xx is NEVER captured** — that's user error (NINO mismatch,
   validation, etc.). Sentry is reserved for HMRC-side and infrastructure
   problems we need to act on.

## Setup

1. Create a Sentry project at <https://sentry.io/> (free plan: 5k events/mo
   — plenty for our traffic until well after the April 2027 mandation).
2. Copy the DSN from **Settings → Projects → bankscanai → Client Keys**.
3. On Railway, set the env vars:

   ```
   SENTRY_DSN=https://<key>@o<org>.ingest.sentry.io/<project>
   SENTRY_ENV=production   # or sandbox, on staging
   SENTRY_RELEASE=$RAILWAY_GIT_COMMIT_SHA  # auto-set by Railway
   ```

4. Redeploy. First call to the HMRC client confirms the wiring — you
   can trigger one with `gh api repos/.../actions/workflows/.../runs`
   or just wait for a real 5xx in the wild.

## Optional env vars

| Var | Default | Purpose |
|---|---|---|
| `SENTRY_TRACES_RATE` | `0.0` | Fraction of requests to performance-trace. **Leave at 0** until we hit scale issues — saves quota. |
| `SENTRY_SEND_PII` | `0` | Include user IPs + emails in events. **DO NOT enable** — we handle NINOs. |

NINO scrubbing is layered: explicit `_NINO_PATTERN` strips in every event
regardless of `SEND_PII`. The pattern uses HMRC's published
unallocated-prefix exclusion list (no A/B/G/K/N/T/Z leading letters,
etc.) so we don't false-positive on random strings.

## Alert rules to set up in the Sentry UI

These belong in Sentry's **Alerts** page, NOT in code:

1. **HMRC 5xx burst** — issue with tag `hmrc.status:5xx` AND
   `count() > 10 in 5m` → page on-call.
2. **HMRC code = SERVER_ERROR** — issue with tag
   `hmrc.code:SERVER_ERROR` AND `count() > 3 in 1h` → Slack.
3. **Network failures** — issue with tag `hmrc.status:0` AND
   `count() > 5 in 5m` → Slack (Railway egress hiccup or HMRC DNS).
4. **Uncaught exception** — default Sentry issue → Slack.

## How to verify the pipeline manually

Once `SENTRY_DSN` is set, force a 5xx to confirm events arrive:

```python
# From a Railway shell, or locally with SENTRY_DSN exported:
python -c "
import os
os.environ.setdefault('HMRC_ENV', 'sandbox')
from hmrc.services.monitoring import init_sentry, capture_hmrc_failure
init_sentry()
capture_hmrc_failure(
    endpoint='/individuals/business/property/AB123456C/XPIS00000000002/obligations',
    method='GET', status_code=503,
    body={'code': 'SERVICE_UNAVAILABLE', 'message': 'down'},
    user_id=42, audit_id='manual-test-1',
)
import sentry_sdk; sentry_sdk.flush(timeout=2)
"
```

Then check the Sentry project — you should see one event with
`hmrc.status=503`, `hmrc.code=SERVICE_UNAVAILABLE`, and the endpoint
showing `[NINO]` rather than `AB123456C`.

## What's NOT covered (yet)

- **Datadog APM** / structured logs to a SIEM. We're staying on stdout +
  Sentry until traffic justifies a logging vendor.
- **Synthetic monitoring** — Sentry sends events when something fails;
  it can't tell you the HMRC API was down for an hour because nobody
  hit it. Set up an uptime check (e.g. Better Uptime free tier) hitting
  `/api/health` every 60 s.
- **Sentry performance / traces** — disabled by default
  (`SENTRY_TRACES_RATE=0`). Enable selectively if we hit latency issues.

## Pre-go-live checklist

Before mandation day (6 April 2027):

- [ ] `SENTRY_DSN` set in Railway production env
- [ ] All four alert rules above firing into the right channel
- [ ] On-call rota assigned for the first 4 weeks post-launch
- [ ] Slack #hmrc-alerts channel created
- [ ] Trial event sent + visible in Sentry dashboard
