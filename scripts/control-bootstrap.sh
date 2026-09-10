#!/usr/bin/env bash
# Create or refresh the `control` branch — the Product Owner <-> Claude
# orchestration channel.
#
#   bash scripts/control-bootstrap.sh --dry-run
#   bash scripts/control-bootstrap.sh
#
# control is an ORPHAN branch carrying ONLY .frankenstein/ (protocol state,
# directive, handoff). Deliberately:
#   * it shares no history with production, so it can never be fast-forwarded
#     into production by accident, and a merge would be unmistakable
#   * it holds no product code, so a directive never requires touching
#     production to be written
#   * the deploy poller watches production only, so control never deploys
#
# Safe: creates/updates one branch, never touches production, never force
# pushes, never rewrites history.
set -uo pipefail
cd "$(dirname "$0")/.."
SRC_DIR="$PWD"

CONTROL_BRANCH="${FRANKENSTEIN_CONTROL_BRANCH:-control}"
PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

die() { echo "STOPPED: $*"; exit 1; }

[ -d .frankenstein ] || die ".frankenstein/ not found — run from the repo"
[ -n "$(git status --porcelain --untracked-files=no)" ] \
  && die "working tree has uncommitted tracked changes; commit or stash first"

git fetch --prune --quiet origin || die "fetch failed"

EXISTS=0
git rev-parse --verify --quiet "origin/$CONTROL_BRANCH" >/dev/null && EXISTS=1

echo "Control branch bootstrap"
echo "  branch:            $CONTROL_BRANCH"
echo "  exists on remote:  $([ "$EXISTS" = 1 ] && echo yes || echo no)"
echo "  production:        $(git rev-parse --short "origin/$PROD_BRANCH" 2>/dev/null || echo '—') (will NOT be touched)"
echo "  payload:           .frankenstein/ only (orphan; no product code)"
echo

if [ "$EXISTS" = "1" ]; then
  echo "control already exists — nothing to bootstrap."
  echo "The Product Owner updates it directly; this script only creates it."
  exit 0
fi

if [ "$DRY" = "1" ]; then
  echo "(dry run) would create orphan branch '$CONTROL_BRANCH' containing:"
  git ls-files .frankenstein | sed 's/^/    /'
  exit 0
fi

# Do ALL of this in a throwaway clone under /tmp. An earlier version created
# the orphan branch in place; switching back afterwards failed because the
# orphan index left every product file untracked, and git refused to overwrite
# them. That stranded the working checkout on a temp branch. The user's
# checkout is now never touched at all.
WORK="$(mktemp -d -t fc-control-XXXXXX)" || die "could not create a temp dir"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT INT TERM

git clone --quiet "$(git remote get-url origin)" "$WORK/repo" || die "clone failed"
cd "$WORK/repo" || die "could not enter the temp clone"
git config user.name "Protocol Bootstrap"
git config user.email "noreply@anthropic.com"

git checkout --quiet --orphan control-bootstrap || die "could not create orphan branch"
git reset --quiet                      # unstage everything the orphan inherited

# Copy the protocol files from the source checkout, and nothing else.
mkdir -p .frankenstein
cp "$OLDPWD/.frankenstein"/* .frankenstein/ 2>/dev/null \
  || cp "$SRC_DIR/.frankenstein"/* .frankenstein/ \
  || die "could not copy .frankenstein"

git add -f .frankenstein || die "could not stage .frankenstein"
if git diff --cached --name-only | grep -qv '^\.frankenstein/'; then
  die "refusing: something outside .frankenstein/ was staged"
fi

git commit --quiet -m "[PO-DIRECTIVE] control branch bootstrap

Orphan orchestration channel for the Product Owner <-> Claude protocol.
Carries .frankenstein/ only: STATE.json, PRODUCT_DIRECTIVE.md,
IMPLEMENTATION_HANDOFF.md, PROTOCOL.md.

Shares no history with production, so it cannot be fast-forwarded into it.
The deployment poller watches production only, so changes here never deploy." \
  || die "commit failed"

git push --quiet origin "HEAD:refs/heads/$CONTROL_BRANCH" || die "push failed"
echo "Created '$CONTROL_BRANCH' at $(git rev-parse --short HEAD)."
echo "production was not touched, and neither was your working checkout."
