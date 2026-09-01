#!/usr/bin/env bash
# Promote reviewed work to production. This is the ONLY way code reaches the
# running stack: the OptiPlex poller watches the production branch and nothing
# else, so pushing a task branch never deploys.
#
#   bash scripts/promote.sh --dry-run     show what would happen
#   bash scripts/promote.sh               promote STATE.json's implementation_commit
#   bash scripts/promote.sh <sha>         promote a specific commit
#
# Guards (all must pass; --force overrides only with an explicit reason):
#   * STATE.json status must be "accepted"        — product acceptance
#   * PRODUCT_DIRECTIVE.md must say deploy-approved — deployment authorization
#   * the promotion must be a fast-forward         — no history rewriting
#
# Product acceptance and deployment authorization are SEPARATE gates: accepting
# work does not by itself mean "ship it now". See .frankenstein/PROTOCOL.md.
set -euo pipefail
cd "$(dirname "$0")/.."

PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
DRY=0
FORCE=0
TARGET=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --force)   FORCE=1 ;;
    -*)        echo "unknown flag: $arg"; exit 2 ;;
    *)         TARGET="$arg" ;;
  esac
done

STATE=".frankenstein/STATE.json"
DIRECTIVE=".frankenstein/PRODUCT_DIRECTIVE.md"

status="$(python3 -c "import json;print(json.load(open('$STATE')).get('status',''))" 2>/dev/null || echo "")"
impl="$(python3 -c "import json;print(json.load(open('$STATE')).get('implementation_commit') or '')" 2>/dev/null || echo "")"
task="$(python3 -c "import json;print(json.load(open('$STATE')).get('task_id',''))" 2>/dev/null || echo "")"
auth="$(grep -i '^Deployment Authorization:' "$DIRECTIVE" 2>/dev/null | head -1 | cut -d: -f2- | tr -d ' ' || echo "")"
[ -z "$auth" ] && auth="none"   # missing/unreadable is treated as none

[ -z "$TARGET" ] && TARGET="$impl"

echo "Promotion check"
echo "  Task:                  ${task:-—}"
echo "  Protocol status:       ${status:-—}          (need: accepted)"
echo "  Deployment auth:       ${auth}              (need: deploy-approved)"
echo "  Candidate commit:      ${TARGET:-—}"
echo "  Production branch:     ${PROD_BRANCH}"
echo

fail() { echo "REFUSED: $1"; exit 1; }

[ -n "$TARGET" ] || fail "no commit to promote (STATE.json implementation_commit is null and none given)"
git rev-parse --verify --quiet "$TARGET^{commit}" >/dev/null || fail "$TARGET is not a commit in this repository"

if [ "$FORCE" != "1" ]; then
  [ "$status" = "accepted" ] || fail "protocol status is '${status:-unset}', not 'accepted' — the Product Owner has not accepted this work"
  [ "$auth" = "deploy-approved" ] || fail "Deployment Authorization is '${auth}', not 'deploy-approved'"
fi

git fetch --prune origin "$PROD_BRANCH" >/dev/null 2>&1 || true
if git rev-parse --verify --quiet "origin/$PROD_BRANCH" >/dev/null; then
  current="$(git rev-parse "origin/$PROD_BRANCH")"
  # Fast-forward only: production must be an ancestor of what we promote.
  if ! git merge-base --is-ancestor "$current" "$TARGET"; then
    fail "not a fast-forward — origin/$PROD_BRANCH ($(git rev-parse --short "$current")) is not an ancestor of $(git rev-parse --short "$TARGET"). Resolve deliberately; this script will not rewrite history."
  fi
  if [ "$current" = "$(git rev-parse "$TARGET")" ]; then
    echo "Already promoted: production is at $(git rev-parse --short "$TARGET"). Nothing to do."
    exit 0
  fi
  echo "Fast-forward: $(git rev-parse --short "$current") -> $(git rev-parse --short "$TARGET")"
else
  echo "Creating $PROD_BRANCH at $(git rev-parse --short "$TARGET")"
fi

if [ "$DRY" = "1" ]; then
  echo
  echo "(dry run — nothing pushed. The OptiPlex deploys within ~60s of a real promotion.)"
  exit 0
fi

git push origin "$TARGET:refs/heads/$PROD_BRANCH"
echo
echo "Promoted $(git rev-parse --short "$TARGET") to '$PROD_BRANCH'."
echo "The OptiPlex poller will deploy it within ~60s (tests gate the deploy)."
echo "Check afterwards with: bash scripts/frankenstein-status.sh"
