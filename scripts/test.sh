#!/usr/bin/env bash
# Every test in the repo. Run by CI on every push and by scripts/deploy.sh
# BEFORE containers are restarted, so a broken build never reaches the box.
#
# These suites exist because the money section broke on 2026-09-01 with
# nothing deployed to cause it: the code asked Firefly for a zero-length
# date range, which only happens on the 1st of a month. Tests that run
# against "today" would have passed on the 31st and failed on the 1st, so
# the date-sensitive ones sweep a two-year calendar instead.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python3 -c "import pytest, fastapi, httpx" 2>/dev/null; then
  echo "installing test deps..."
  pip install --quiet --break-system-packages pytest fastapi httpx uvicorn
fi

echo "== python: engine, service + protocol tests =="
python3 -m pytest services/ tests/ -q "$@"

echo
echo "== javascript: syntax =="
for f in gateway/static/*.js; do
  node --check "$f" >/dev/null && echo "  ok  $f"
done

echo
echo "== shell: syntax =="
for f in scripts/*.sh; do
  bash -n "$f" && echo "  ok  $f"
done

echo
echo "ALL TESTS PASSED"
