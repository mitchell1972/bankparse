# Security incident response — 72-hour notification process

This is the runbook for a confirmed security incident affecting customer
data, HMRC tokens, or our HMRC API surface. It pins down WHO does WHAT
within HMRC's and the ICO's overlapping 72-hour clocks, so we never
miss a regulatory deadline because nobody knew it existed.

**The clock starts at discovery** — the moment a responsible party
becomes aware of the breach. Not when it's understood, not when it's
contained — when it's KNOWN. That's HMRC's interpretation and the
ICO's per UK GDPR Article 33.

---

## Single point of contact (SPOC)

| Role | Person | Email | Phone |
|---|---|---|---|
| Incident lead (default) | Mitchell Agoma | `security@bankscanai.com` (fallback: `mitchellagoma@gmail.com`) | UK landline TBD |
| Deputy (if SPOC unreachable for >2h) | TBD — appoint before recognition is granted | TBD | TBD |

The SPOC owns the 72-hour clock and is responsible for both HMRC and
ICO notifications. They can delegate the writing but not the
accountability.

---

## Hour-by-hour timeline

### Hour 0 — Discovery

A responsible party (any BankScan AI employee, contractor, or external
researcher via `security@bankscanai.com` / `/.well-known/security.txt`)
reports a suspected security incident.

**Actions:**
- Acknowledge receipt within 1 hour to the reporter.
- Open a private incident log file (e.g. Google Doc or local) and
  write the time of discovery in UTC at the top.
- Page the SPOC if not already involved.

### Hour 0–2 — Triage

**Actions:**
- Confirm: is this a real breach? Did data actually leave our systems
  or was access actually obtained beyond what's authorised?
- If unconfirmed, continue investigation but DO NOT start the
  notification clock yet — the clock is from confirmed discovery.
- If confirmed, write down the discovery time precisely. Both
  notifications hang off this timestamp.
- Scope: how many users affected? What data? Tokens? NINOs? Bank
  statement contents?

### Hour 2–24 — Containment + evidence preservation

**Actions:**
- Containment first: revoke compromised tokens, force re-OAuth, take
  affected endpoints offline if needed.
- For HMRC token compromise specifically: run
  `UPDATE hmrc_connections SET access_token=NULL, refresh_token=NULL;`
  on the affected user IDs (see `audit-log.md` for the exact procedure).
- Preserve evidence: do NOT wipe logs. Capture the relevant
  hmrc_submissions audit rows + Sentry events + Railway logs into a
  separate write-once store.
- Begin draft notifications — both HMRC + ICO templates below.

### Hour 24–48 — Notify HMRC

**HMRC notification channel** for software vendors:

> **HMRC SDST support portal** — log a ticket at
> <https://developer.service.hmrc.gov.uk/developer/support> selecting
> the "I have a security concern" category. **DO NOT** put incident
> details in a public forum or non-encrypted channel.
>
> If the portal is unreachable (rare but happens): email
> `sdsteam@hmrc.gov.uk` directly, marked URGENT in the subject.

Use the **HMRC notification template** below.

### Hour 24–72 — Notify ICO (UK GDPR Article 33)

**Required IF** the breach involves personal data and is likely to
result in a risk to the rights and freedoms of natural persons. For
BankScan AI's data inventory (NINOs, bank-statement contents, OAuth
tokens linked to HMRC identities), almost any breach meets this bar.

> **ICO notification:**
> <https://ico.org.uk/for-organisations/report-a-breach/personal-data-breach/personal-data-breach-reporting-decision-tree/>
>
> Walk through the decision tree, then submit via the form. Or call
> their helpline on **0303 123 1113** (9 am – 5 pm UK weekdays).

Use the **ICO notification template** below.

### Hour 24–72 — Notify affected users

Even when not strictly required by GDPR (low-risk breaches), notify
the affected users directly via email so they can act:
- Force them to re-OAuth to HMRC.
- Recommend they review recent HMRC submissions for anything they
  didn't initiate (via `/hmrc/submissions` audit log).
- Provide a clear contact for follow-up questions.

### Hour 72+ — Public-facing incident report

Within 7 days post-resolution, publish a plain-English incident
report at `https://bankscanai.com/security` describing:
- What happened (no operational details that aid attackers)
- When it happened
- What we did
- What customers should do
- What we've changed to prevent recurrence

---

## HMRC notification template

```
Subject: SECURITY INCIDENT — BankScan AI (vendor id bankscan-ai) — [DATE]

Vendor name:                   BankScan AI Ltd (Mitoba Consulting Ltd)
Vendor software identifier:    bankscan-ai
HMRC application id (prod):    [from developer.service.hmrc.gov.uk]
Discovery date/time (UTC):     [YYYY-MM-DDTHH:MM:SSZ]
Confirmed breach date/time:    [YYYY-MM-DDTHH:MM:SSZ]
Reporting party:               Mitchell Agoma, Director
Contact phone:                 [UK landline]
Contact email:                 security@bankscanai.com

1. Nature of incident
   [One-paragraph description. Stay factual; do not speculate. Name the
   class of data affected — HMRC OAuth tokens, NINOs, both, neither.]

2. Number of users affected
   [Exact count if known, or "≤N users impacted, exact count pending"]

3. Data involved
   [List each category. e.g. HMRC OAuth access + refresh tokens for N
   users, NINOs for M users, bank-statement contents for K users, etc.]

4. Containment actions taken
   [What you've already done. e.g. revoked tokens, forced re-OAuth,
   patched the vulnerability, deployed at commit <sha>.]

5. Audit trail
   [Reference the relevant hmrc_submissions audit_ids if known.]

6. Notifications in flight
   [ICO notification submitted at [timestamp] / pending.]
   [Affected users notified at [timestamp] / pending.]

7. Next steps + timeline
   [What you're doing next + ETA.]
```

---

## ICO notification template

> Use the ICO's online form — it's structured and you fill in the
> boxes. The template below is what you'll paste into their free-text
> fields.

```
Personal data breach report — BankScan AI

Organisation:                  Mitoba Consulting Ltd
Trading as:                    BankScan AI
Discovery date/time (UTC):     [YYYY-MM-DDTHH:MM:SSZ]
Reporting time (UTC):          [YYYY-MM-DDTHH:MM:SSZ]
Time elapsed since discovery:  [≤72 hours — confirm before submitting]
Reporting party:               Mitchell Agoma, Director, security@bankscanai.com

Nature of breach
[As HMRC template section 1.]

Categories of personal data affected
[Tick all that apply on the ICO form. For BankScan: typically
National Insurance numbers, financial transaction data, contact email
addresses. Possibly HMRC Government Gateway tokens (auth credentials).]

Approximate number of data subjects affected
[Number]

Likely consequences of the breach for affected individuals
[Be specific — could attacker file fake returns? Access financial
history? Phish the user using leaked email? Identity theft using NINO?]

Measures already taken or proposed
[List containment + remediation actions.]

Has affected data subjects been informed
[Yes / No / Will be / Reasons not yet]
```

---

## Decision aids

**"Do I need to notify the ICO?"** — Walk through their decision tree
linked above. Quick heuristic: if the breach involves NINOs, OAuth
tokens, financial details, or contact info AND there's any plausible
path to financial harm or identity theft, the answer is **yes, notify**.
A "no" decision must be documented in the incident log with reasoning.

**"Do I need to notify HMRC?"** — Yes, always, if the breach affects
data or functionality covered by our MTD ITSA vendor agreement
(anything in `hmrc_connections`, `hmrc_submissions`, or our OAuth
client_id/secret). Recognition is conditional on this.

**"Do I need to notify affected users?"** — Best practice: yes, even
for low-risk breaches. Legally required only when the breach is "high
risk" per UK GDPR. When in doubt: notify.

---

## Post-incident review

Within 14 days of the incident being closed, hold a blameless post-
mortem and write up:
- Timeline (down to the minute where possible)
- Root cause
- What worked in the response
- What didn't
- Changes to prevent recurrence (code, process, monitoring)

File at `hmrc/docs/incidents/[date]-[short-name].md`. These build the
evidence base for HMRC's annual recognition review.

---

## What this DOESN'T cover

- Non-security customer incidents (billing, parsing bugs, etc.) —
  separate process via support@bankscanai.com.
- Sub-processor incidents (Stripe, Anthropic, Resend, Railway) —
  follow each vendor's own incident-response process and forward
  notifications to affected users.

---

## Drill schedule

Tabletop a simulated breach scenario:
- **Within 30 days of HMRC recognition being granted** — first drill,
  confirms the SPOC + deputy chain works.
- **Quarterly thereafter** — short 30-minute drill.
- **Annually** — full simulation including drafting (but not sending)
  the HMRC + ICO notifications against a fabricated scenario.

Record drill outcomes in `hmrc/docs/incidents/drills/`.
