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

# This script's own absolute path, captured BEFORE the cd below: `$0` may be
# relative to whatever directory the caller was in, and the self-upgrade check
# further down compares against it. Resolving it after the cd would compare
# against a path that does not exist, which reads as "changed" and would hand
# over on every single run.
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
cd "$DIR"

BRANCH="${1:-${FRANKENSTEIN_BRANCH:-production}}"

# Deployment record lives OUTSIDE the repo: `git reset --hard` below would
# erase anything tracked, and the whole point is to be able to answer "what
# commit is actually running?" even after a failed deploy.
STATE_DIR="${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}"
mkdir -p "$STATE_DIR"
RECORD="$STATE_DIR/deployed.json"

# EXPORT it, so `docker compose` below mounts the directory this script is
# actually writing to.
#
# Without this the two ends kept their own defaults and silently disagreed:
# here it is `$HOME/.frankenstein`, and in docker-compose.yml the assistant
# mounts `${FRANKENSTEIN_STATE_DIR:-/root/.frankenstein}`. Those are the same
# path only when the deploy runs as root — and the systemd unit template ships
# as `User=REPLACE_WITH_USER` with a /home/... layout, so on a normal install
# they are not. Docker then creates the missing /root/.frankenstein, mounts an
# EMPTY directory, and the dashboard can never see a deploy record at all: it
# reports the build as unknown/unconfirmed forever, no matter how many deploys
# succeed. Exporting the resolved path makes them agree by construction
# instead of by two hand-kept defaults.
export FRANKENSTEIN_STATE_DIR="$STATE_DIR"

# ── hand over to the version of this script being deployed ─────────────────
#
# Below, this script runs `git reset --hard` on the repository it lives in —
# which includes itself. Bash does not load a script into memory; it executes
# it by byte offset and re-reads the file as it goes. Rewriting deploy.sh
# mid-run therefore makes the interpreter resume at a stale offset inside NEW
# content: it can skip a block, run a fragment of a line, or execute something
# that was never a statement. Nothing reports an error. The symptom is a
# deploy that half-applies.
#
# The tamer half of the same bug is that a change to deploy.sh only takes
# effect on the deploy AFTER the one that pulls it, because the pull is
# performed by the old copy. That is exactly how the FRANKENSTEIN_STATE_DIR
# export shipped and then did nothing for a cycle.
#
# So the handover happens FIRST, while the working tree is still untouched:
# fetch, compare the running script against the one being deployed, and if
# they differ, exec the new one from a copy OUTSIDE the repo. That copy is
# what the reset cannot reach, so there is never a moment when the file being
# interpreted changes underneath the interpreter. The new script does the
# actual pull, finds itself already current, and proceeds.
#
# Every step degrades to "carry on with the script we have", which is the
# behaviour this replaces — a deploy must not be blocked by its own upgrade
# check. An unreachable remote is handled by the real fetch further down,
# which is still gated by `set -e`.
SELF_IN_REPO="scripts/deploy.sh"
UPGRADE="$STATE_DIR/.deploy-upgrade.sh"

if [ "${FRANKENSTEIN_DEPLOY_REEXEC:-0}" != "1" ]; then
  if git fetch --prune origin "$BRANCH" >/dev/null 2>&1 \
     && git show "origin/$BRANCH:$SELF_IN_REPO" >"$UPGRADE.tmp" 2>/dev/null \
     && [ -s "$UPGRADE.tmp" ] \
     && ! cmp -s "$UPGRADE.tmp" "$SELF"; then
    mv -f "$UPGRADE.tmp" "$UPGRADE"
    echo "==> deploy.sh itself changed in '$BRANCH' — running the new one"
    # Set on the child only. The guard makes the handover strictly one-shot:
    # the new script skips this block, so a pathological pair of scripts that
    # each considered the other newer could not ping-pong.
    export FRANKENSTEIN_DEPLOY_REEXEC=1
    exec bash "$UPGRADE" "$BRANCH"
  fi
  rm -f "$UPGRADE.tmp"
fi

record() {  # record <result> <sha> <tests>
  # <tests> is "passed", "skipped" or "failed" — what actually gated THIS
  # attempt.
  #
  # SCRUM-108: the record used to look identical whether the suite ran or
  # was skipped with DEPLOY_SKIP_TESTS=1, so an hour later nothing could
  # tell a tested deploy from an untested one. An override you can see is a
  # safety net; one you cannot is a hole.
  #
  # `running_tests` is sticky in the same way `running_commit` is: it
  # describes the build that is SERVING. A failed attempt must not
  # overwrite it, and a skipped deploy keeps saying "skipped" until a
  # tested deploy replaces it.
  local result="$1" sha="$2" tests="$3" prev="" prev_tests=""
  if [ -f "$RECORD" ]; then
    prev="$(python3 -c "
import json,sys
try: print(json.load(open('$RECORD')).get('running_commit') or '')
except Exception: print('')
" 2>/dev/null)"
    prev_tests="$(python3 -c "
import json,sys
try: print(json.load(open('$RECORD')).get('running_tests') or '')
except Exception: print('')
" 2>/dev/null)"
  fi
  local running="$prev" running_tests="$prev_tests"
  if [ "$result" = "success" ]; then
    running="$sha"
    running_tests="$tests"
  fi
  python3 - "$RECORD" "$result" "$sha" "$running" "$BRANCH" "$tests" "$running_tests" <<'PY'
import json, sys, datetime
path, result, sha, running, branch, tests, running_tests = sys.argv[1:8]
try:
    doc = json.load(open(path))
except Exception:
    doc = {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
doc.update({"production_branch": branch, "last_attempt_commit": sha,
            "last_attempt_at": now, "last_result": result,
            "running_commit": running or None,
            # What gated this attempt, and what gated the build now
            # serving. ABSENT (not "passed") when the record was written
            # by a deploy.sh predating SCRUM-108: unknown and passed are
            # different facts and must not be collapsed.
            "last_attempt_tests": tests or None,
            "running_tests": running_tests or None})
if result == "success":
    doc["last_success_at"] = now
    if tests == "skipped":
        doc["last_skipped_tests_at"] = now
        doc["last_skipped_tests_commit"] = sha
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

# The secret the sub-apps authenticate to each other with (SCRUM-114). gmail's
# /internal/token hands out a live Google credential and now demands this
# header; an empty value fails closed, which is correct and also means Google
# Calendar silently stops syncing until somebody edits .env by hand.
#
# So generate one. There is nothing for a person to decide here: the value is
# random, it is never printed, and it only has to match between two containers
# on the same box. Asking an operator to paste `openssl rand -hex 32` buys no
# security and costs a working calendar every time the step is missed.
#
# Only ever ADDED, never rewritten: rotating it mid-deploy would break the
# running pair until both restarted. Rotation is a deliberate act, not a
# side effect of deploying.
if ! grep -q "^FC_INTERNAL_SECRET=." .env 2>/dev/null; then
  # No openssl dependency — the box had python3 without pip once already.
  NEW_SECRET="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  if [ -n "$NEW_SECRET" ]; then
    # Drop any empty placeholder first so the file does not end up with the
    # key twice, where the LAST one wins and it would be the blank one.
    if [ -f .env ] && grep -q "^FC_INTERNAL_SECRET=" .env; then
      grep -v "^FC_INTERNAL_SECRET=" .env > .env.tmp && mv -f .env.tmp .env
    fi
    printf '\n# Generated by scripts/deploy.sh — shared between the sub-apps.\nFC_INTERNAL_SECRET=%s\n' \
      "$NEW_SECRET" >> .env
    echo "==> Generated FC_INTERNAL_SECRET (inter-service auth) into .env"
  else
    echo "!! Could not generate FC_INTERNAL_SECRET — Google Calendar sync will"
    echo "!! report 'not configured' until one is set in .env by hand."
  fi
  unset NEW_SECRET
fi

# Gate the deploy on the test suite. The running stack is only touched after
# the freshly-pulled code passes, so a bad push leaves the box on the last
# good build instead of taking the dashboard down. Set DEPLOY_SKIP_TESTS=1
# to force a deploy past this (emergencies only).
TESTS_STATUS="passed"
if [ "${DEPLOY_SKIP_TESTS:-0}" != "1" ]; then
  echo "==> Running tests before touching the running stack"
  if ! bash scripts/test.sh >/tmp/fc-test.log 2>&1; then
    echo "!! TESTS FAILED — deploy aborted, containers left running as-is."
    echo "!! Commit under test: $(git rev-parse --short HEAD)"
    tail -30 /tmp/fc-test.log
    echo "!! Full output: /tmp/fc-test.log"
    record "tests_failed" "$(git rev-parse HEAD)" "failed"
    exit 1
  fi
  echo "==> Tests passed ($(grep -oE '[0-9]+ passed' /tmp/fc-test.log | tail -1))"
else
  # Loud here, and recorded in deployed.json so it is still visible
  # tomorrow, when the person reading the dashboard is not the person who
  # typed the override.
  TESTS_STATUS="skipped"
  echo "!! ==============================================================="
  echo "!! DEPLOY_SKIP_TESTS=1 — THE TEST GATE IS OFF FOR THIS DEPLOY."
  echo "!! Whatever is about to start has not been checked. This is"
  echo "!! recorded in deployed.json and shown on the dashboard until a"
  echo "!! tested deploy replaces it."
  echo "!! ==============================================================="
fi

# Build changed images and (re)start everything. --remove-orphans cleans up
# any services that were removed from the compose file.
$DC up -d --build --remove-orphans

# Keep disk tidy — drop dangling images from old builds.
docker image prune -f >/dev/null 2>&1 || true

# ── verify the stack is actually SERVING before claiming success ──────────
#
# SCRUM-107. `up -d` returns when containers have been STARTED, not when they
# are serving; a container that starts and instantly crash-loops satisfies it.
# Recording "success" here regardless put `running_commit: <sha>` in
# deployed.json for a stack that might be entirely down — and autopull.sh
# treats that field as ground truth, so it concluded DESIRED == RUNNING and
# STOPPED RETRYING. The box would then sit on a broken deploy while the
# deployment system believed it had converged.
#
# `started_unhealthy` is a distinct result on purpose. `record` only advances
# running_commit when the result is exactly "success", so an unhealthy deploy
# leaves running_commit at the PREVIOUS good commit — which is both the honest
# answer for the dashboard and the thing that makes the poller try again.
SHA="$(git rev-parse HEAD)"
if bash scripts/stack-health.sh; then
  record "success" "$SHA" "$TESTS_STATUS"
  echo "==> Deployed $(git rev-parse --short HEAD) on '$BRANCH'"
  docker compose ps
else
  record "started_unhealthy" "$SHA" "$TESTS_STATUS"
  echo "!! Containers were started but the stack is not serving."
  echo "!! deployed.json still names the last commit that DID serve, so the"
  echo "!! poller will retry this deploy on its next tick."
  docker compose ps
  exit 1
fi
