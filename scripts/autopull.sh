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
# It used to default to `git rev-parse --abbrev-ref HEAD` — whatever happened
# to be checked out — which meant the branch Claude pushed work to WAS the
# deploy trigger, so nothing could be reviewed before it went live.
#
#   FRANKENSTEIN_DIR           repo clone (default: $HOME/FrankensteinCentral)
#   FRANKENSTEIN_BRANCH        production branch to track (default: production)
set -euo pipefail

DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
cd "$DIR"

BRANCH="${FRANKENSTEIN_BRANCH:-production}"

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

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # running commit already matches production — nothing to do
fi

echo "$(date -Is)  production moved on $BRANCH: $LOCAL -> $REMOTE, redeploying"
exec bash "$DIR/scripts/deploy.sh" "$BRANCH"
