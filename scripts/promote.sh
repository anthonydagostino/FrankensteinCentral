#!/usr/bin/env bash
# Promote work to production. This is the ONLY way code reaches the running
# stack: the OptiPlex poller watches the production branch and nothing else,
# so pushing a task branch never deploys.
#
#   bash scripts/promote.sh --dry-run <sha>   show what would happen
#   bash scripts/promote.sh <sha>             promote that commit
#   bash scripts/promote.sh                   promote HEAD
#
# There is ONE guard, and it is not an approval:
#
#   * the promotion must be a fast-forward — production history is never
#     rewritten, so nothing already shipped can be silently erased.
#
# There is no product-acceptance gate and no deployment-authorization gate.
# They were removed at Anthony's explicit instruction on 2026-09-07: the
# review layer was costing more in shipping speed than it was returning, and
# on a personal dashboard owned and operated by one person the owner is the
# authority. Do not reintroduce them.
#
# What still protects the running stack, and is NOT an approval layer:
#
#   * scripts/deploy.sh runs the full suite ON THE BOX before touching any
#     container, and aborts the deploy if it fails. A red suite still cannot
#     reach production.
#   * the fast-forward check here.
#
# Those are safety nets, not permission slips. Nobody has to be asked.
set -euo pipefail
cd "$(dirname "$0")/.."

PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
DRY=0
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    # Accepted and ignored: they used to skip the acceptance/authorization
    # gates, which no longer exist. Kept so old muscle memory and any script
    # still passing them keeps working instead of erroring.
    --force|--bootstrap) ;;
    -*)        echo "unknown flag: $arg"; exit 2 ;;
    *)         TARGET="$arg" ;;
  esac
done

[ -z "$TARGET" ] && TARGET="HEAD"

fail() { echo "REFUSED: $1"; exit 1; }

git rev-parse --verify --quiet "$TARGET^{commit}" >/dev/null \
  || fail "$TARGET is not a commit in this repository"
RESOLVED="$(git rev-parse "$TARGET")"

echo "Promotion"
echo "  Candidate commit:      $(git rev-parse --short "$RESOLVED")  $(git log -1 --format='%s' "$RESOLVED")"
echo "  Production branch:     ${PROD_BRANCH}"
echo

git fetch --prune origin "$PROD_BRANCH" >/dev/null 2>&1 || true
if git rev-parse --verify --quiet "origin/$PROD_BRANCH" >/dev/null; then
  current="$(git rev-parse "origin/$PROD_BRANCH")"
  # Fast-forward only: production must be an ancestor of what we promote.
  if ! git merge-base --is-ancestor "$current" "$RESOLVED"; then
    fail "not a fast-forward — origin/$PROD_BRANCH ($(git rev-parse --short "$current")) is not an ancestor of $(git rev-parse --short "$RESOLVED"). Merge or rebase onto production first; this script will not rewrite history."
  fi
  if [ "$current" = "$RESOLVED" ]; then
    echo "Already promoted: production is at $(git rev-parse --short "$RESOLVED"). Nothing to do."
    exit 0
  fi
  echo "Fast-forward: $(git rev-parse --short "$current") -> $(git rev-parse --short "$RESOLVED")"
else
  echo "Creating $PROD_BRANCH at $(git rev-parse --short "$RESOLVED")"
fi

if [ "$DRY" = "1" ]; then
  echo
  echo "(dry run — nothing pushed. The OptiPlex deploys within ~60s of a real promotion.)"
  exit 0
fi

git push origin "$RESOLVED:refs/heads/$PROD_BRANCH"
echo
echo "Promoted $(git rev-parse --short "$RESOLVED") to '$PROD_BRANCH'."
echo "The OptiPlex poller will deploy it within ~60s (the test suite gates the deploy)."
echo "Check afterwards with: bash scripts/frankenstein-status.sh"
