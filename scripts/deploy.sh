#!/usr/bin/env bash
# Redeploy FrankensteinCentral from the latest pushed code.
#
# Runs on the OptiPlex (the box that hosts the stack). scripts/autopull.sh —
# the systemd poller, and the only supported deployment path — calls this when
# the production branch moves; you can also run it by hand.
#
#   FRANKENSTEIN_DIR   where the repo is cloned (default: $HOME/FrankensteinCentral)
#   $1                 branch to deploy (default: whatever is checked out)
set -euo pipefail

DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
cd "$DIR"

BRANCH="${1:-${FRANKENSTEIN_BRANCH:-production}}"

# Deployment record lives OUTSIDE the repo: `git reset --hard` below would
# erase anything tracked, and the whole point is to be able to answer "what
# commit is actually running?" even after a failed deploy.
STATE_DIR="${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}"
mkdir -p "$STATE_DIR"
RECORD="$STATE_DIR/deployed.json"

# What the test gate actually did for this commit. Initialised before record()
# can reference it: set -u makes an unset default fatal.
TEST_DISPOSITION="skipped"

record() {  # record <result> <sha> [test disposition]
  local result="$1" sha="$2" disposition="${3:-$TEST_DISPOSITION}" prev=""
  [ -f "$RECORD" ] && prev="$(python3 -c "
import json,sys
try: print(json.load(open('$RECORD')).get('running_commit') or '')
except Exception: print('')
" 2>/dev/null)"
  local running="$prev"
  [ "$result" = "success" ] && running="$sha"
  python3 - "$RECORD" "$result" "$sha" "$running" "$BRANCH" "$disposition" <<'PY'
import json, sys, datetime
path, result, sha, running, branch, disposition = sys.argv[1:7]
try:
    doc = json.load(open(path))
except Exception:
    doc = {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
doc.update({"production_branch": branch, "last_attempt_commit": sha,
            "last_attempt_at": now, "last_result": result})

# WHAT IS RUNNING is a claim that needs evidence, and the answer differs by
# how the attempt failed:
#
#   success        -> this commit is running
#   tests_failed   -> the gate runs BEFORE any container is touched, so the
#                     previous commit is genuinely still running
#   compose_failed -> Compose may already have replaced some containers before
#                     it failed. The stack is MIXED, and asserting the old SHA
#                     is still running would be a claim with no evidence.
if result == "success":
    doc["running_commit"] = sha
    doc["running_state"] = "confirmed_started"
    doc["last_success_commit"] = sha
elif result == "compose_failed":
    doc["running_commit"] = None
    doc["running_state"] = "unknown_partial_start"
else:
    doc["running_commit"] = running or None
    doc["running_state"] = "unchanged_gate_failed_before_start"
# The last commit KNOWN to have fully deployed, kept separately so a failed
# attempt never erases it.
doc.setdefault("last_success_commit", running or None)
# Three DIFFERENT facts, each tied to the commit it is about:
#   test_gate     what scripts/test.sh actually did for this commit
#   last_result   what the deploy attempt itself did
#   verification  post-deploy readiness of the running stack
# Collapsing them let an old "success" stand in for present health.
#
# test_gate is passed IN, never inferred from the deploy result: with
# DEPLOY_SKIP_TESTS=1 the suite never ran, and inferring "passed" from a
# successful compose claimed a gate that did not happen.
doc["test_gate"] = {"result": disposition, "commit": sha, "at": now}
doc.setdefault("verification", {"result": "not_run", "commit": None, "at": None})
if result == "success":
    doc["last_success_at"] = now
json.dump(doc, open(path, "w"), indent=2)
PY
}

record_verification() {  # deployed_sha, readiness json, [state]
  python3 - "$RECORD" "$1" "$2" "${3:-}" <<'PY' 2>/dev/null
import json, sys, datetime
path, sha, raw, state = sys.argv[1:5]
try:
    doc = json.load(open(path))
except Exception:
    doc = {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
if state == "pending":
    # The check is in flight. Recording this BEFORE running it means a crash
    # mid-check leaves "pending", not the previous verdict.
    doc["verification"] = {"result": "pending", "commit": sha, "at": now,
                           "detail": "readiness check in progress"}
    json.dump(doc, open(path, "w"), indent=2)
    raise SystemExit
try:
    ready = json.loads(raw)
except Exception:
    ready = None
if not isinstance(ready, dict) or ready.get("result") not in ("pass", "fail"):
    # The check could not be run or gave nothing usable. That is NOT a pass,
    # and it must not silently inherit the previous verification.
    doc["verification"] = {"result": "not_run", "commit": sha, "at": now,
                           "detail": "readiness check produced no usable result"}
else:
    doc["verification"] = {
        "result": ready["result"], "commit": sha, "at": now,
        "degraded": ready.get("degraded", []),
        "required_failed": ready.get("required_failed", []),
    }
json.dump(doc, open(path, "w"), indent=2)
PY
  local v
  v="$(python3 -c "
import json,sys
try: print(json.load(open('$RECORD')).get('verification',{}).get('result','?'))
except Exception: print('?')" 2>/dev/null)"
  if [ "$v" = "pass" ]; then
    echo "==> Readiness PASS"
  else
    echo "!! READINESS $v — containers started but the dashboard did not verify."
    echo "!! Production was NOT rolled back automatically; this is recorded for review."
  fi
}

# Support both Docker Compose v2 ("docker compose") and the older v1
# ("docker-compose"), so the pipeline works whatever the box has installed.
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "!! Neither 'docker compose' nor 'docker-compose' is installed."
  echo "!! Install it with:  sudo apt-get install -y docker-compose-plugin"
  exit 1
fi

echo "==> Deploying '$BRANCH' from $DIR (using: $DC)"

# Pull the exact pushed code. reset --hard leaves untracked files (like .env
# and docker volumes) alone, so your secrets and data survive.
git fetch --prune origin "$BRANCH"
git checkout "$BRANCH" 2>/dev/null || git checkout -b "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"

if [ ! -f .env ]; then
  echo "!! No .env found in $DIR — copy .env.example to .env and fill it in."
  echo "!! Bringing the stack up anyway with built-in defaults / sample data."
fi

# Gate the deploy on the test suite. The running stack is only touched after
# the freshly-pulled code passes, so a bad push leaves the box on the last
# good build instead of taking the dashboard down. Set DEPLOY_SKIP_TESTS=1
# to force a deploy past this (emergencies only).
# The honest record of what the gate actually did for this commit.
TEST_DISPOSITION="skipped"
if [ "${DEPLOY_SKIP_TESTS:-0}" != "1" ]; then
  echo "==> Running tests before touching the running stack"
  if ! bash scripts/test.sh >/tmp/fc-test.log 2>&1; then
    echo "!! TESTS FAILED — deploy aborted, containers left running as-is."
    echo "!! Commit under test: $(git rev-parse --short HEAD)"
    tail -30 /tmp/fc-test.log
    echo "!! Full output: /tmp/fc-test.log"
    TEST_DISPOSITION="failed"
    record "tests_failed" "$(git rev-parse HEAD)" "failed"
    exit 1
  fi
  TEST_DISPOSITION="passed"
  echo "==> Tests passed ($(grep -oE '[0-9]+ passed' /tmp/fc-test.log | tail -1))"
fi

# Build changed images and (re)start everything. --remove-orphans cleans up
# any services that were removed from the compose file.
# set -euo pipefail would abort here WITHOUT recording anything, leaving the
# record showing the previous success — so a failed deploy looked healthy to
# anything reading deployed.json. Capture the status and record it.
if ! $DC up -d --build --remove-orphans; then
  echo "!! COMPOSE FAILED — the stack was not brought up cleanly."
  echo "!! Commit under test: $(git rev-parse --short HEAD)"
  record "compose_failed" "$(git rev-parse HEAD)"
  exit 1
fi

# Keep disk tidy — drop dangling images from old builds.
docker image prune -f >/dev/null 2>&1 || true

DEPLOYED_SHA="$(git rev-parse HEAD)"
record "success" "$DEPLOYED_SHA"

# ── post-deploy readiness ────────────────────────────────────────────────
# Compose starting containers is NOT the application working. This is a
# bounded, read-only check of the CORE dashboard; unconfigured third-party
# integrations are reported as degraded and never fail it. A failure is
# recorded and reported, and deliberately does NOT trigger an automatic
# rollback: reverting unattended is its own hazard and belongs to the Product
# Owner through the ordinary rollback authorization.
echo "==> Checking the deployed dashboard is actually serving"
# Mark the check IN FLIGHT first. If this process dies mid-check, the record
# says "pending" rather than leaving the previous verdict standing, so a
# successful compose is never presented as verified before the check finishes.
record_verification "$DEPLOYED_SHA" "" pending
READY_JSON="$(bash scripts/readiness.sh --json-only 2>/dev/null || true)"
record_verification "$DEPLOYED_SHA" "$READY_JSON"

echo "==> Deployed $(git rev-parse --short HEAD) on '$BRANCH'"
docker compose ps
