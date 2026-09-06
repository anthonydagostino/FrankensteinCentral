#!/usr/bin/env bash
# Roll production back by moving it FORWARD.
#
#   bash scripts/rollback.sh --dry-run <good-sha>
#   bash scripts/rollback.sh <good-sha>
#
# Production history is append-only. Instead of rewinding the branch, this
# creates a NEW commit on top of production whose tree is identical to the
# known-good commit. The box then deploys that new commit like any other
# change, and the rollback itself stays in the audit trail.
#
#   production:  A --- B --- C(bad) --- D(tree of B, "rollback")
#
# This is the NORMAL rollback path. Rewriting the branch with --force-with-lease
# is an emergency-recovery action requiring explicit high-risk approval; it
# destroys the record that the bad deploy ever happened.
set -euo pipefail
cd "$(dirname "$0")/.."

PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
DRY=0
GOOD=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -*)        echo "unknown flag: $arg"; exit 2 ;;
    *)         GOOD="$arg" ;;
  esac
done

fail() { echo "REFUSED: $1"; exit 1; }

[ -n "$GOOD" ] || fail "give the known-good commit to restore, e.g. bash scripts/rollback.sh bf6192e"
git rev-parse --verify --quiet "$GOOD^{commit}" >/dev/null || fail "$GOOD is not a commit in this repository"

# read-tree below overwrites the working tree, so uncommitted work has to stop
# us — but "uncommitted" is two different questions, and conflating them made
# rollback unavailable exactly when it is needed. A stray log file next to the
# repo is not a reason to refuse to fix production.
#
#   tracked modifications -> always refuse. That is work someone can lose.
#   untracked files       -> refuse only where the restored tree lands on them.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  fail "working tree is dirty — commit or stash first (this rewrites the working tree)"
fi

# `read-tree -u --reset` silently overwrites untracked files (unlike checkout,
# which errors), so this collision check is ours to make. Git also cannot put a
# file where a directory sits, or a directory where a file sits, so a path
# PREFIX relationship collides just as surely as an equal path does:
#
#   untracked `foo`      target `foo`      -> the file would be overwritten
#   untracked `foo`      target `foo/bar`  -> a file blocks the directory
#   untracked `foo/bar`  target `foo`      -> a directory blocks the file
#
# Ignored files are included in the check, not in the dirty gate: .env is
# ignored and irreplaceable, and silently losing it to a rollback would be
# worse than the bad deploy. An ignored file that collides with nothing is
# left alone and never blocks anything.
COLLISIONS="$(python3 - "$GOOD" <<'PY'
import subprocess, sys

good = sys.argv[1]


def paths(*args):
    out = subprocess.run(["git", *args], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


target = set(paths("ls-tree", "-r", "--name-only", "-z", good + "^{tree}"))
held = (paths("ls-files", "--others", "--exclude-standard", "-z")
        + paths("ls-files", "--others", "--ignored", "--exclude-standard", "-z"))
held_set = set(held)


def parents(path):
    parts = path.split("/")
    for i in range(1, len(parts)):
        yield "/".join(parts[:i])


hits = set()
for h in held:
    if h in target:                       # same path: it would be overwritten
        hits.add(h)
        continue
    for d in parents(h):                  # we hold a dir the target wants as a file
        if d in target:
            hits.add(h)
            break
for t in target:                          # we hold a file the target wants as a dir
    for d in parents(t):
        if d in held_set:
            hits.add(d)
            break

for h in sorted(hits):
    print(h)
PY
)"
if [ -n "$COLLISIONS" ]; then
  echo "REFUSED: untracked files sit on paths the restored tree writes to:"
  printf '%s\n' "$COLLISIONS" | sed 's/^/    /'
  echo
  echo "Move or delete just those paths and re-run. Untracked files elsewhere"
  echo "are fine and do not block a rollback."
  exit 1
fi

git fetch --prune origin "$PROD_BRANCH" >/dev/null 2>&1 || true
git rev-parse --verify --quiet "origin/$PROD_BRANCH" >/dev/null \
  || fail "origin/$PROD_BRANCH does not exist"

current="$(git rev-parse "origin/$PROD_BRANCH")"
echo "Rollback plan"
echo "  Production now:   $(git rev-parse --short "$current")"
echo "  Restore tree of:  $(git rev-parse --short "$GOOD")"
echo "  Method:           new commit on top of production (append-only)"
echo

if [ "$(git rev-parse "$GOOD^{tree}")" = "$(git rev-parse "$current^{tree}")" ]; then
  echo "Production already has that exact tree. Nothing to do."
  exit 0
fi

if [ "$DRY" = "1" ]; then
  echo "Files that would change:"
  git diff --stat "$current" "$GOOD" | tail -20
  echo
  echo "(dry run — nothing committed or pushed.)"
  exit 0
fi

START_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
TMP="fc-rollback-$(date +%s)"
cleanup() {
  git checkout -q "$START_BRANCH" 2>/dev/null || true
  git branch -D "$TMP" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git checkout -q -B "$TMP" "$current"
# Make index+worktree exactly match the good commit — including deletions —
# while HEAD stays on production, so the result is a forward commit.
git read-tree -u --reset "$GOOD"
git commit -q -m "rollback: restore tree of $(git rev-parse --short "$GOOD")

Production moves forward, not backward: this commit's tree is identical to
$(git rev-parse --short "$GOOD"), so the bad deploy stays in the audit trail
instead of being erased. Rolled back from $(git rev-parse --short "$current")."

NEW="$(git rev-parse HEAD)"
git push origin "$NEW:refs/heads/$PROD_BRANCH"
echo
echo "Pushed rollback commit $(git rev-parse --short "$NEW") to '$PROD_BRANCH'."
echo "The OptiPlex will deploy it within ~60s (tests gate the deploy)."
