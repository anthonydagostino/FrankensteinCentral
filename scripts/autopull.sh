#!/usr/bin/env bash
# Dead-simple auto-deploy WITHOUT GitHub Actions.
#
# Checks the remote branch every run; if there's new code, redeploys. Pair it
# with the systemd timer in docs/SETUP-DEPLOY.md to run it every minute. Use
# this if you don't want to register a GitHub runner — no tokens, no inbound
# connections, works fully behind your home network.
#
#   FRANKENSTEIN_DIR   repo clone (default: $HOME/FrankensteinCentral)
#   FRANKENSTEIN_BRANCH branch to track (default: whatever is checked out)
set -euo pipefail

DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
cd "$DIR"

BRANCH="${FRANKENSTEIN_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

git fetch --prune origin "$BRANCH" >/dev/null 2>&1
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0  # already up to date, nothing to do
fi

echo "$(date -Is)  new code on $BRANCH: $LOCAL -> $REMOTE, redeploying"
exec bash "$DIR/scripts/deploy.sh" "$BRANCH"
