# Audit log — storage, durability, retention

The `hmrc_submissions` table is the **legal record** of every HMRC API
call we make on a user's behalf. HMRC's software recognition rules
require we can prove what we sent on every submission; this is the
table they'll subpoena if an MTD ITSA filing is ever disputed.

This doc covers:

- where the table lives
- backup cadence
- retention policy (legal floor vs GDPR ceiling)
- export procedure for an HMRC enquiry
- pre-go-live checklist

## Where it lives

| Surface | Detail |
|---|---|
| Database engine | SQLite (file at `database.py:_db_path()` — Railway volume) |
| Table | `hmrc_submissions` |
| Schema | see `database.py` `_init_hmrc_submissions_table()` |
| Insert code | `hmrc/repositories/submissions.py:record()` |
| Read code | `hmrc/routers/submissions.py` (user-facing history) |
| Indexed columns | `audit_id` (UUID, unique), `user_id`, `created_at` |

The bearer token in the request headers is **stripped before storage**
(see `record()` line 35). Fraud headers are kept verbatim — HMRC may
ask to see them.

Every row contains:
- request payload (post-strip)
- response status, headers, body
- HMRC's `Idempotency-Key`
- our generated `audit_id` (returned to user for support tickets)
- timestamp

## Backup

The SQLite file sits on a Railway-managed volume. **Two-tier backup:**

### Tier 1 — Railway snapshots (automatic)

Railway snapshots the volume nightly. Restorable to any of the last 7
nights from the Railway dashboard. Verify your retention setting on the
service's volume page; the default is 7 days but can be increased.

### Tier 2 — Off-Railway nightly export (manual to configure)

The audit log is the legal record — losing it to a Railway-side
incident is unacceptable. Run a daily backup to S3-compatible
storage:

```bash
# Cron at 03:00 UTC daily, on a separate Railway service or any worker:
sqlite3 /data/bankparse.db ".backup '/tmp/bankparse.bak'"
aws s3 cp /tmp/bankparse.bak s3://bankscanai-audit-backups/$(date +%Y-%m-%d)/bankparse.db \
  --storage-class GLACIER_IR
rm /tmp/bankparse.bak
```

Bucket policy: write-only by the worker IAM role, read-only by named
admins. Encryption at rest (S3-SSE-KMS) is mandatory.

**Test the restore quarterly.** A backup that's never been restored is a
hope, not a backup.

## Retention

| Driver | Period | Reason |
|---|---|---|
| HMRC software recognition (legal floor) | 6 years from end of tax year | HMRC's published record-keeping rules for MTD vendors |
| GDPR (ceiling, sort of) | 6 years | After this, the data must be deleted unless the user has an open dispute |
| Customer dispute hold | indefinite until resolved | Any open ticket referencing an audit_id pauses deletion |

**Implementation today:** the table grows unbounded. There's NO deletion
cron yet. That's fine while we're pre-recognition (we have ~14 months
of accumulation tolerance before the legal floor matters). Before the
April 2027 mandation we MUST add:

```python
# hmrc/repositories/submissions.py — sketch, not implemented yet
def prune_older_than(years: int = 6) -> int:
    """Delete audit rows older than `years` UNLESS flagged with
    `dispute_hold=1`. Returns rows deleted. Runs via cron."""
    cutoff_ts = time.time() - years * 365.25 * 86400
    return _execute_delete(
        "DELETE FROM hmrc_submissions WHERE created_at < ? AND dispute_hold = 0",
        (cutoff_ts,),
    )
```

The `dispute_hold` column doesn't exist yet — add it before the prune
job runs.

## Export procedure (HMRC enquiry)

If HMRC asks for the audit trail of a specific user's submissions over a
period:

```bash
# On the Railway shell (or against the latest backup if you'd rather not
# touch prod):
sqlite3 /data/bankparse.db <<SQL
.mode csv
.headers on
.output /tmp/audit-export.csv
SELECT
    audit_id, user_id, endpoint, method, response_status,
    idempotency_key, datetime(created_at, 'unixepoch') AS created_utc,
    request_body_json, response_body_json
FROM hmrc_submissions
WHERE user_id = <USER_ID>
  AND created_at BETWEEN strftime('%s', '2026-04-06') AND strftime('%s', '2027-04-05')
ORDER BY created_at;
.quit
SQL
```

Hand the CSV to HMRC via their secure portal — DO NOT email it.

## Per-user deletion (GDPR Article 17)

When a user closes their account, we keep their audit rows but anonymise
the user_id link (set to NULL) so the rows themselves are no longer
PII-attributable to them. This balances GDPR's right-to-erasure against
HMRC's record-keeping requirement: HMRC needs the record; the user no
longer needs to be identifiable in it.

**Not implemented yet.** Add the anonymise-on-account-deletion path
before the first paying customer churns.

## Pre-go-live checklist

- [ ] Railway volume snapshot retention set to 30+ days
- [ ] Daily S3 backup wired (see Tier 2 above)
- [ ] Restore drill completed (load yesterday's backup into a staging DB)
- [ ] `dispute_hold` column added to `hmrc_submissions`
- [ ] Pruning job spec'd + scheduled (does not run until first row is
      6+ years old)
- [ ] Per-user anonymise path implemented and tested
- [ ] CSV export procedure rehearsed once
- [ ] DPIA / Privacy Notice updated to reflect 6-year retention
