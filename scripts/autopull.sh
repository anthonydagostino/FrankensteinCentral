#!/usr/bin/env bash
# Auto-deploy poller. Runs on the OptiPlex every minute via
# frankenstein-deploy.timer (see docs/SETUP-DEPLOY.md).
#
# IT WATCHES ONE BRANCH: the production branch, and nothing else.
#
# This is the boundary between "code exists on GitHub" and "code is running in
# production". Task branches (claude/FC-###-*) can be pushed freely for Product
# Owner review without touching the running stack; only a change to the
# production branch deploys. Promotion onto that branch is an explicit,
# separate act (scripts/promote.sh).
#
# ── DEPLOYMENT STATE MODEL ───────────────────────────────────────────────
#   DESIRED = origin/<production branch>
#   RUNNING = deployed.json .running_commit  (last SUCCESSFUL deployment)
#   deploy when DESIRED != RUNNING
#
# Local git HEAD is NOT consulted. It is a working-copy detail, not proof that
# anything is running: deploy.sh checks out and resets the repo to production
# BEFORE running the test gate, so a commit whose tests failed still leaves
# HEAD == origin/production while the containers run the previous build.
# Comparing HEAD (the earlier behavior) made that state look converged and
# permanently suppressed the retry. That wedge happened live during the
# protocol bootstrap.
#
# Fail safe: if the deployment record is missing, unparseable, or has no
# running_commit, the running state is UNKNOWN and a normal test-gated deploy
# is attempted. A running SHA is never inferred or manufactured.
#
#   FRANKENSTEIN_DIR           repo clone (default: $HOME/FrankensteinCentral)
#   FRANKENSTEIN_BRANCH        production branch to track (default: production)
#   FRANKENSTEIN_STATE_DIR     where deployed.json lives (default: ~/.frankenstein)
set -uo pipefail

DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
cd "$DIR"

BRANCH="${FRANKENSTEIN_BRANCH:-production}"
RECORD="${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}/deployed.json"

git fetch --prune origin "$BRANCH" >/dev/null 2>&1 || true

# Fail SAFE: if the production branch is missing or unfetchable, deploy
# nothing. Never fall back to the checked-out branch — that fallback is the
# bug this file exists to fix. Exit 0 so the timer doesn't spin on a failing
# unit, but say so unmistakably in the journal.
if ! git rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
  echo "$(date -Is)  production branch 'origin/$BRANCH' not found — NOT deploying."
  echo "$(date -Is)  create it, or set FRANKENSTEIN_BRANCH in frankenstein-deploy.service."
  exit 0
fi

DESIRED="$(git rev-parse "origin/$BRANCH")"

# What is actually RUNNING — the last successful deployment, recorded by
# deploy.sh. Any failure to read it yields an empty string, which means
# "unknown" and therefore "attempt a deploy".
RUNNING=""
if [ -f "$RECORD" ] && command -v python3 >/dev/null 2>&1; then
  RUNNING="$(python3 -c "
import json
try:
    print(json.load(open('$RECORD')).get('running_commit') or '')
except Exception:
    print('')" 2>/dev/null)"
fi

if [ -n "$RUNNING" ] && [ "$RUNNING" = "$DESIRED" ]; then
  exit 0  # the desired commit is the one actually running — nothing to do
fi

if [ -z "$RUNNING" ]; then
  echo "$(date -Is)  no confirmed running commit (record missing/unreadable) — deploying ${DESIRED:0:7} on $BRANCH"
else
  echo "$(date -Is)  desired ${DESIRED:0:7} != running ${RUNNING:0:7} on $BRANCH — deploying"
fi
exec bash "$DIR/scripts/deploy.sh" "$BRANCH"
