"""
One-shot generator: produce the ZIP Mitchell uploads to HMRC's recognition
form.

Inside:

  recognition-application-package.md  (cover document)
  MANUAL_STEPS.md                     (Mitchell's checklist)
  conformance-test-transcript.txt     (run pytest yourself first)
  data-handling.md
  security-questionnaire.md
  fraud-prevention-implementation.md
  sandbox-test-user-runbook.md
  demo-script.md
  README.txt                          (TOC + sumbit instructions)

Run:
    python scripts/build_recognition_bundle.py

Output:
    HMRC_Recognition_Bundle_2026-05-21.zip  (in CWD)
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "hmrc", "docs")

DOC_FILES = [
    "recognition-application-package.md",
    "MANUAL_STEPS.md",
    "data-handling.md",
    "security-questionnaire.md",
    "fraud-prevention-implementation.md",
    "sandbox-test-user-runbook.md",
    "demo-script.md",
    "conformance-test-evidence.md",
    "conformance-test-transcript.txt",
]


README = """\
================================================================
HMRC MTD ITSA Recognition Application — BankScan AI
================================================================

This ZIP contains every document HMRC asks for on the recognition form
at https://www.tax.service.gov.uk/recognition-software .

If you only read one file: open MANUAL_STEPS.md.

Files:

  README.txt
        You are here.

  MANUAL_STEPS.md
        Step-by-step checklist of everything Mitchell has to do by
        hand — credential request, conformance run, demo video, the
        recognition form itself.

  recognition-application-package.md
        Cover document mapping every field HMRC asks for to its
        source-of-truth file or commit.

  data-handling.md
        How we store, protect, and delete user data.
        Answers HMRC's standard data-handling questionnaire.

  security-questionnaire.md
        HMRC's 30-question security questionnaire, answered.

  fraud-prevention-implementation.md
        Every Gov-Client-* / Gov-Vendor-* fraud-prevention header,
        where we collect it, where we inject it, and the test that
        validates it.

  sandbox-test-user-runbook.md
        How HMRC's reviewer can recreate any test we describe.

  demo-script.md
        The 3-5 minute demo video script. Record this against the
        sandbox before submitting the application.

  conformance-test-transcript.txt
        Output of `pytest tests/hmrc/` — proof the conformance suite
        passes. (Re-run before each submission.)

  conformance-test-evidence.md
        Mapping of HMRC endpoints → tests that prove the wire contract.

Submission steps (5 minutes):
  1. Read MANUAL_STEPS.md
  2. Apply for production OAuth credentials in your HMRC developer hub
  3. Re-run `pytest tests/hmrc/` and overwrite the transcript file
  4. Record the demo video, upload as Unlisted YouTube
  5. Open https://www.tax.service.gov.uk/recognition-software
  6. Paste the YouTube URL, attach this whole ZIP, hit Submit
  7. Wait 4-16 weeks for HMRC's email back
================================================================
"""


def main() -> int:
    if not os.path.isdir(DOCS):
        print(f"ERROR: docs dir not found at {DOCS}", file=sys.stderr)
        return 1

    today = _dt.date.today().isoformat()
    out_name = f"HMRC_Recognition_Bundle_{today}.zip"
    out_path = os.path.join(os.getcwd(), out_name)

    missing: list[str] = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", README)
        for name in DOC_FILES:
            src = os.path.join(DOCS, name)
            if not os.path.exists(src):
                missing.append(name)
                # Write a stub so the recipient still sees the placeholder
                zf.writestr(
                    name,
                    f"# {name}\n\nThis file was missing at bundle-build "
                    f"time ({today}). Generate it before submitting.\n",
                )
                continue
            with open(src, "rb") as fh:
                zf.writestr(name, fh.read())

    print(f"Wrote {out_path} ({os.path.getsize(out_path):,} bytes).")
    if missing:
        print(
            f"\nWARN: {len(missing)} file(s) were missing and stubbed in:",
            file=sys.stderr,
        )
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("\nGenerate them before attaching the bundle to the form.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
