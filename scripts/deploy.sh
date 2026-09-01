#!/usr/bin/env bash
# Redeploy FrankensteinCentral from the latest pushed code.
#
# Runs on the OptiPlex (the box that hosts the stack). The GitHub Actions
# self-hosted runner calls this on every push; you can also run it by hand.
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

record() {  # record <result> <sha>
  local result="$1" sha="$2" prev=""
  [ -f "$RECORD" ] && prev="$(python3 -c "
import json,sys
try: print(json.load(open('$RECORD')).get('running_commit') or '')
except Exception: print('')
" 2>/dev/null)"
  local running="$prev"
  [ "$result" = "success" ] && running="$sha"
  python3 - "$RECORD" "$result" "$sha" "$running" "$BRANCH" <<'PY'
import json, sys, datetime
path, result, sha, running, branch = sys.argv[1:6]
try:
    doc = json.load(open(path))
except Exception:
    doc = {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
doc.update({"production_branch": branch, "last_attempt_commit": sha,
            "last_attempt_at": now, "last_result": result,
            "running_commit": running or None})
if result == "success":
    doc["last_success_at"] = now
json.dump(doc, open(path, "w"), indent=2)
PY
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
if [ "${DEPLOY_SKIP_TESTS:-0}" != "1" ]; then
  echo "==> Running tests before touching the running stack"
  if ! bash scripts/test.sh >/tmp/fc-test.log 2>&1; then
    echo "!! TESTS FAILED — deploy aborted, containers left running as-is."
    echo "!! Commit under test: $(git rev-parse --short HEAD)"
    tail -30 /tmp/fc-test.log
    echo "!! Full output: /tmp/fc-test.log"
    record "tests_failed" "$(git rev-parse HEAD)"
    exit 1
  fi
  echo "==> Tests passed ($(grep -oE '[0-9]+ passed' /tmp/fc-test.log | tail -1))"
fi

# Build changed images and (re)start everything. --remove-orphans cleans up
# any services that were removed from the compose file.
$DC up -d --build --remove-orphans

# Keep disk tidy — drop dangling images from old builds.
docker image prune -f >/dev/null 2>&1 || true

record "success" "$(git rev-parse HEAD)"
echo "==> Deployed $(git rev-parse --short HEAD) on '$BRANCH'"
docker compose ps
