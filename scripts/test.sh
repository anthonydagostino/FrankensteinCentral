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
  # Never invoke a bare `pip`: the OptiPlex had python3 with no `pip` in PATH,
  # which failed a live deploy. Use the interpreter's own pip module, and if
  # that is missing say exactly which host package installs it rather than
  # running apt/sudo from a test script.
  if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "!! Test dependencies are missing and python3 has no working pip module."
    echo "!! Install the host prerequisite, then re-run:"
    echo "!!     sudo apt-get install -y python3-pip"
    echo "!! (this script deliberately does not run apt or sudo itself)"
    exit 1
  fi
  echo "installing test deps via python3 -m pip..."
  python3 -m pip install --quiet --break-system-packages \
    pytest fastapi httpx uvicorn \
    || python3 -m pip install --quiet pytest fastapi httpx uvicorn \
    || { echo "!! could not install test dependencies"; exit 1; }
fi

echo "== python: engine, service + protocol tests =="
python3 -m pytest services/ tests/ -q "$@"

echo
echo "== javascript: syntax =="
for f in gateway/static/*.js; do
  node --check "$f" >/dev/null && echo "  ok  $f"
done

echo
echo "== javascript: unit tests =="
# The week grid's midnight rollover is date-dependent code living in the
# browser, so it gets the same calendar sweep the Python date logic gets
# rather than only a syntax check. Explicit glob: `node --test <dir>` is not
# portable across the Node versions this runs on.
TZ=America/New_York node --test gateway/tests/*.test.js

echo
echo "== shell: syntax =="
for f in scripts/*.sh; do
  bash -n "$f" && echo "  ok  $f"
done

echo
echo "ALL TESTS PASSED"
