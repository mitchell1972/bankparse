#!/usr/bin/env bash
# Generate a transcript of the HMRC conformance test suite suitable for
# attaching to the recognition application.
#
# Output: hmrc/docs/conformance-test-transcript.txt
#
# Usage:
#   ./scripts/hmrc_conformance_report.sh
#
# Runs against the mocked HMRC suite (tests/hmrc/). For the recognition
# application, also run the manual end-to-end against the real sandbox
# (see hmrc/docs/demo-script.md).

set -euo pipefail

cd "$(dirname "$0")/.."

OUTPUT="hmrc/docs/conformance-test-transcript.txt"
DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

{
    echo "BankScan AI — HMRC MTD ITSA conformance test transcript"
    echo "========================================================="
    echo "Generated:        ${DATE}"
    echo "Git commit:       $(git rev-parse HEAD)"
    echo "Git branch:       $(git rev-parse --abbrev-ref HEAD)"
    echo ""
    echo "Suite command:    python3 -m pytest tests/hmrc/ -v --no-header"
    echo ""
    echo "---"
    echo ""
} > "${OUTPUT}"

python3 -m pytest tests/hmrc/ -v --no-header --color=no 2>&1 >> "${OUTPUT}" || true

echo ""
echo "Transcript written to: ${OUTPUT}"
echo ""
echo "Summary line:"
grep -E "passed|failed" "${OUTPUT}" | tail -3
