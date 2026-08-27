#!/usr/bin/env bash
# Run every skill's tests from the repo root. Green here = safe to push.
set -euo pipefail
cd "$(dirname "$0")"
rc=0
for t in */tests; do
  echo "== ${t%/tests}"
  python3 -m pytest "$t" -q || rc=1
done
exit $rc
