# AES key rotation — HMRC tokens at rest

The `HMRC_TOKEN_ENCRYPTION_KEY` env var encrypts every HMRC OAuth
access + refresh token in `hmrc_connections`. Rotation is needed when:

- Someone with key access leaves the team
- The key has been shared insecurely (e.g. pasted into chat)
- A scheduled hygiene rotation (annual is reasonable)
- A suspected compromise

## The design

`hmrc/services/crypto.py` supports **two-key rotation** without
downtime and without flushing every user's HMRC connection:

- `HMRC_TOKEN_ENCRYPTION_KEY` — the **active** key. New encrypts use
  this.
- `HMRC_TOKEN_ENCRYPTION_KEY_OLD` — optional. Comma-separated fallback
  keys tried on decrypt only.

There's no key-version byte in the blob format — `decrypt()` just tries
each candidate key until one tag verifies. With ≤3 keys in flight, this
is cheap.

## Procedure (10 minutes, no downtime)

### Step 1 — Mint the new key (local)

```bash
NEW_KEY=$(python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())")
echo "$NEW_KEY"   # save to password manager IMMEDIATELY
```

### Step 2 — Switch on Railway (1 redeploy)

In the Railway dashboard, set BOTH variables on the prod service:

```
HMRC_TOKEN_ENCRYPTION_KEY_OLD = <whatever the active key was up to now>
HMRC_TOKEN_ENCRYPTION_KEY     = <NEW_KEY from step 1>
```

Save → wait for the redeploy → verify with the health endpoint:

```bash
curl https://bankscanai.com/api/health
```

At this point:
- ALL existing blobs in `hmrc_connections` still decrypt successfully
  (fallback to OLD).
- ALL new connections + token refreshes encrypt under the new key.

### Step 3 — Re-encrypt existing rows

Open a Railway shell and run:

```bash
python3 scripts/rotate_hmrc_token_key.py --dry-run    # see the count
python3 scripts/rotate_hmrc_token_key.py              # actually do it
```

The script reads each row, decrypts under either key, and re-encrypts
under the active key. Per-row transactions — safe to re-run if it dies
halfway.

### Step 4 — Retire the old key

Once step 3 reports `0 failed`:

In Railway dashboard:
```
HMRC_TOKEN_ENCRYPTION_KEY_OLD   ← DELETE this variable
```
Redeploy. Decrypt now only accepts blobs encrypted under the new key.

In your password manager: keep the old key for 30 days as a recovery
hedge, then delete.

## Verification

After step 4, every existing user should be able to:
- Fetch obligations (proves decrypt of access_token works)
- Submit a sandbox quarterly update (proves decrypt + sign-with-token
  round-trip)

If anyone reports "Connect to HMRC again" prompts en masse, you missed
step 3 — set OLD back, re-run rotate.

## What can go wrong

| Symptom | Cause | Fix |
|---|---|---|
| `InvalidTag: decrypt failed under all active + fallback keys` for ALL users right after step 2 | Wrong base64 value for HMRC_TOKEN_ENCRYPTION_KEY_OLD | Re-check OLD matches the previous active value EXACTLY (whitespace counts) |
| Same error for SOME users only | Some rows were never encrypted (legacy plaintext?) | Manually inspect those rows; either re-OAuth them or delete |
| `script failed=N` after rotate | Mixed-key state | Investigate per-user; re-run after fixing |
| 502s on /api/hmrc/* after step 4 | A row still encrypted under OLD slipped through | Set OLD back, re-run rotate, then retry step 4 |

## Logging hygiene

Never log the raw env var values, not even at DEBUG. The crypto module
already avoids this; if you add new code that touches keys, follow suit.

## Schedule

| Trigger | Cadence |
|---|---|
| Team member with key access leaves | within 24h |
| Suspected leak | immediately |
| Hygiene rotation | annually, end of UK tax year (5 April) |
| HMRC compliance audit | as required |
