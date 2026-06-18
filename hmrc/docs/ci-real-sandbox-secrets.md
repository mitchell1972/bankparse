# CI real-sandbox E2E — secrets to add

The `real-sandbox` job in `.github/workflows/tests.yml` runs the gated
HMRC tests against the **real** sandbox in a **fresh CI browser context** —
which is what defeats the sticky-session `OAUTH_NINO_MISMATCH` bug that
blocks the journey when reconnecting in a normal browser.

It runs **after merges to `main`** and **on demand** (Actions → tests →
"Run workflow"). It is **not** a PR gate (HMRC's sandbox occasionally
CAPTCHAs the Government Gateway sign-in, so it must never block a merge).

Every test skips cleanly when its secret is unset, so the job is safe to
merge before the secrets exist — it just skips until you add them.

## Secrets to add

Add these at **Settings → Secrets and variables → Actions → New repository
secret** (https://github.com/mitchell1972/bankparse/settings/secrets/actions),
or with the `gh` CLI (run these yourself — each prompts for the value;
**do not** paste secrets into chat):

```
gh secret set HMRC_CLIENT_ID            # from developer.service.hmrc.gov.uk (sandbox app)
gh secret set HMRC_CLIENT_SECRET        # same app — the sandbox client secret
gh secret set PROD_TEST_USER_EMAIL      # a BankScan AI login used only for CI
gh secret set PROD_TEST_USER_PASSWORD   # that login's password
```

| Secret | Used by | Proves |
|---|---|---|
| `HMRC_CLIENT_ID` + `HMRC_CLIENT_SECRET` | `test_hmrc_real_sandbox.py` (app-restricted) | OAuth client is registered + subscribed; create-test-user / create-test-business wire shapes are accepted |
| `PROD_TEST_USER_EMAIL` + `PROD_TEST_USER_PASSWORD` | `test_prod_hmrc_smoke.py` Tier 3 | The **full journey**: mint user → GG OAuth in a fresh context → obligations **200** → quarterly **preview**. This is the green journey HMRC needs to see. |

## Recommendations

- **Use a dedicated CI login**, not your personal `mitchell_agoma@…`
  account, for `PROD_TEST_USER_*`. Easier to rotate, and it keeps your
  main account's password out of CI.
- After adding the secrets, trigger the job: **Actions → tests → Run
  workflow** (or just merge to `main`). Watch the "Full OAuth journey"
  step — if obligations come back **200** there, the sticky-session theory
  is confirmed and the product works end-to-end in a clean context. If it
  still 404s in a fresh CI browser, there's a deeper bug and the trace
  artifact (`real-sandbox-traces`) will show exactly where.
- Tier 3 stops at **preview** (it does not POST a real submit) so it
  doesn't mutate filed data. To extend to a real `201` submit later, add a
  submit step guarded by an explicit `PROD_SMOKE_SUBMIT=1`.

## Why this is the right test

A normal browser can't get a green journey because HMRC's sandbox OAuth
session is sticky (it ignores `prompt=login` and isn't cleared by the
bas-gateway sign-out — verified live 2026-06-18, see
[[oauth-nino-binding]] / `hmrc/docs/oauth-nino-binding.md`). A fresh CI
browser context has no such cookie, so the OAuth binds to the
just-minted test user correctly — letting us finally prove (or disprove)
the journey automatically and reproducibly.
