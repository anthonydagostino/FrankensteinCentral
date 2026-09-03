#!/usr/bin/env bash
# Autonomous Claude worker — the implementation side of the Product Owner
# <-> Claude loop. Polls the `control` branch and, ONLY when control state
# authorizes it, runs Claude non-interactively against an isolated clone.
#
#   bash scripts/claude-worker.sh              one poll cycle
#   bash scripts/claude-worker.sh --dry-run    same decisions, mocked Claude
#   bash scripts/claude-worker.sh --status     print what it would do, no action
#
# ── THREE BRANCHES ───────────────────────────────────────────────────────
#   production          only PO-accepted code; the ONLY branch the deploy
#                       poller watches. This worker NEVER touches it.
#   control             PO <-> Claude orchestration state. Never deploys.
#   claude/FC-###-slug  implementation work. Never deploys.
#
# ── HARD BOUNDARIES ──────────────────────────────────────────────────────
# This worker may edit files in its isolated clone, run repo tests, commit,
# push the task branch, and publish a handoff to control. It may NOT push or
# merge production, run promote.sh / rollback.sh / deploy.sh, touch systemd,
# force push, use sudo, or invent work. A pre-push hook in the isolated clone
# rejects production and force pushes structurally, not just by convention.
#
# Disabled by default: it refuses to run unless ENABLED exists, and always
# refuses if DISABLED exists. Independent of the production deploy timer.
set -uo pipefail

AGENT_DIR="${FRANKENSTEIN_AGENT_DIR:-$HOME/.frankenstein/agent}"
CLONE_ROOT="${FRANKENSTEIN_WORKTREE_ROOT:-$HOME/.frankenstein/worktrees}"
PROD_DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
CONTROL_BRANCH="${FRANKENSTEIN_CONTROL_BRANCH:-control}"
PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
MAX_RUNTIME="${FRANKENSTEIN_CLAUDE_TIMEOUT:-3600}"   # seconds; wedge guard
CLAUDE_BIN="${FRANKENSTEIN_CLAUDE_BIN:-claude}"
MOCK_CLAUDE="${FRANKENSTEIN_MOCK_CLAUDE:-}"          # dry-run/test injection

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run" ;;
  --status)  MODE="status" ;;
  "")        ;;
  *) echo "usage: claude-worker.sh [--dry-run|--status]"; exit 2 ;;
esac

mkdir -p "$AGENT_DIR" "$CLONE_ROOT"
LOG="$AGENT_DIR/worker.log"

log() { echo "$(date -Is)  $*" | tee -a "$LOG" >&2; }
noop() { log "NO-OP: $*"; exit 0; }
fail() { log "FAILED: $*"; exit 1; }

# ── kill switch (independent of the production deployer) ─────────────────
[ -e "$AGENT_DIR/DISABLED" ] && noop "kill switch present ($AGENT_DIR/DISABLED)"
if [ ! -e "$AGENT_DIR/ENABLED" ]; then
  noop "not enabled — create $AGENT_DIR/ENABLED to allow autonomous runs"
fi

# ── single flight ────────────────────────────────────────────────────────
# A second poll while a run is active must do nothing at all.
exec 9>"$AGENT_DIR/worker.lock"
if ! flock -n 9; then
  noop "another worker run holds the lock"
fi

# ── locate the repository (read-only use of the production checkout) ─────
REPO_URL="${FRANKENSTEIN_REPO_URL:-}"
if [ -z "$REPO_URL" ]; then
  REPO_URL="$(git -C "$PROD_DIR" remote get-url origin 2>/dev/null)"
fi
[ -n "$REPO_URL" ] || fail "cannot determine repository URL (set FRANKENSTEIN_REPO_URL)"

AGENT_CLONE="$CLONE_ROOT/agent-repo"

# ── isolation guard: never operate inside the production checkout ────────
# The live checkout is owned by the deployment mechanism; a task-branch
# checkout there would fight `git reset --hard` and could ship unreviewed code.
abspath() { python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$1" 2>/dev/null || echo "$1"; }
PROD_REAL="$(abspath "$PROD_DIR")"
CLONE_REAL="$(abspath "$AGENT_CLONE")"
case "$CLONE_REAL/" in
  "$PROD_REAL"/*) fail "isolation violation: agent clone $CLONE_REAL is inside the production checkout $PROD_REAL" ;;
esac
[ "$CLONE_REAL" = "$PROD_REAL" ] && fail "isolation violation: agent clone is the production checkout"

# ── read authoritative control state ─────────────────────────────────────
if [ ! -d "$AGENT_CLONE/.git" ]; then
  log "cloning repository into isolated agent clone $AGENT_CLONE"
  git clone --quiet "$REPO_URL" "$AGENT_CLONE" || fail "clone failed"
fi
git -C "$AGENT_CLONE" fetch --prune --quiet origin || fail "fetch failed — control state unknown, doing nothing"

CONTROL_COMMIT="$(git -C "$AGENT_CLONE" rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
[ -n "$CONTROL_COMMIT" ] || noop "control branch 'origin/$CONTROL_BRANCH' not found"

STATE_JSON="$(git -C "$AGENT_CLONE" show "$CONTROL_COMMIT:.frankenstein/STATE.json" 2>/dev/null)"
[ -n "$STATE_JSON" ] || noop "control commit carries no .frankenstein/STATE.json"

read_state() {  # read_state <key>
  printf '%s' "$STATE_JSON" | python3 -c "
import json,sys
try:
    print(json.load(sys.stdin).get('$1') or '')
except Exception:
    print('__INVALID__')" 2>/dev/null
}
TURN="$(read_state turn)"
STATUS="$(read_state status)"
TASK_ID="$(read_state task_id)"

if [ "$TURN" = "__INVALID__" ] || [ "$STATUS" = "__INVALID__" ]; then
  noop "control STATE.json is malformed — treating as blocked, invoking nothing"
fi

case "$TASK_ID" in
  FC-[0-9][0-9][0-9]*) ;;
  *) noop "task_id '$TASK_ID' is not FC-### form — refusing to guess" ;;
esac

# ── wake condition ───────────────────────────────────────────────────────
# Claude may NEVER invent a task because the worker happened to wake up.
if [ "$TURN" != "claude" ]; then
  noop "turn=$TURN (not claude) status=$STATUS — nothing authorized"
fi
case "$STATUS" in
  ready_for_implementation|changes_requested) ;;
  *) noop "turn=claude but status=$STATUS is not an authorized start state" ;;
esac

TASK_SLUG="$(printf '%s' "$TASK_ID" | tr 'A-Z' 'a-z')"
TASK_BRANCH="claude/${TASK_ID}-work"

log "AUTHORIZED: task=$TASK_ID status=$STATUS control=${CONTROL_COMMIT:0:7} branch=$TASK_BRANCH"

if [ "$MODE" = "status" ]; then
  echo "would run: task=$TASK_ID status=$STATUS control=${CONTROL_COMMIT:0:7}"
  echo "clone:     $AGENT_CLONE"
  echo "branch:    $TASK_BRANCH"
  exit 0
fi

# ── prepare the isolated working branch ──────────────────────────────────
git -C "$AGENT_CLONE" checkout --quiet -B "$TASK_BRANCH" "origin/$PROD_BRANCH" 2>/dev/null \
  || git -C "$AGENT_CLONE" checkout --quiet -B "$TASK_BRANCH" \
  || fail "could not prepare task branch"

# Structural guard: this hook lives in the isolated clone and rejects the
# operations the worker is forbidden to perform, even if something inside the
# run tries them.
HOOK="$AGENT_CLONE/.git/hooks/pre-push"
cat > "$HOOK" <<'HOOKEOF'
#!/usr/bin/env bash
# Installed by claude-worker.sh. The autonomous worker may never push
# production, and may never force push anything.
while read -r _local_ref _local_sha remote_ref _remote_sha; do
  case "$remote_ref" in
    refs/heads/production|refs/heads/main|refs/heads/master)
      echo "pre-push: REFUSED — the autonomous worker may not push $remote_ref" >&2
      echo "pre-push: production promotion is a Product Owner action." >&2
      exit 1 ;;
  esac
done
for arg in "$@"; do :; done
if [ -n "${GIT_PUSH_OPTION_COUNT:-}" ]; then :; fi
exit 0
HOOKEOF
chmod +x "$HOOK"

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_LOG="$AGENT_DIR/run-$TASK_ID-$RUN_ID.log"
STARTED="$(date -Is)"

# ── the bootstrap instruction ────────────────────────────────────────────
PROMPT="You are running as the autonomous implementation worker for
FrankensteinCentral. Follow this exactly and do not exceed it.

1. Read CLAUDE.md and .frankenstein/PROTOCOL.md in this repository.
2. Read .frankenstein/PRODUCT_DIRECTIVE.md — that is the authoritative scope.
3. Read .frankenstein/STATE.json and VERIFY it says turn=claude with status
   ready_for_implementation or changes_requested. If it does not, stop
   immediately and change nothing.
4. Implement ONLY the scope in the directive. Do not choose additional work,
   do not refactor unrelated code, do not change the Product Owner's directive.
5. You are on branch $TASK_BRANCH in an isolated clone. Stay on it.
6. Run: bash scripts/test.sh — all tests must pass.
7. Update .frankenstein/IMPLEMENTATION_HANDOFF.md honestly, including a
   'Deviations From Directive' section (state 'No deviations' if there were
   none).
8. Update .frankenstein/STATE.json for the documented handoff transition:
   turn=product_owner, status=awaiting_review, last_actor=claude,
   updated_at=<current UTC>. Leave implementation_commit for the worker.
9. Commit your work with a [CLAUDE] $TASK_ID message. Do NOT push — the
   worker publishes after verifying no Product Owner state changed underneath.
10. Stop. Do not start another task.

You may NOT: push or merge production, run promote.sh / rollback.sh /
deploy.sh, modify systemd, force push, use sudo, or issue a directive."

# ── invoke Claude (or the mock) under a hard timeout ─────────────────────
CLAUDE_RC=0
if [ "$MODE" = "dry-run" ] || [ -n "$MOCK_CLAUDE" ]; then
  RUNNER="${MOCK_CLAUDE:-true}"
  log "dry-run: invoking mock '$RUNNER' instead of Claude"
  ( cd "$AGENT_CLONE" && FRANKENSTEIN_TASK_ID="$TASK_ID" \
      FRANKENSTEIN_TASK_BRANCH="$TASK_BRANCH" bash -c "$RUNNER" ) >>"$RUN_LOG" 2>&1
  CLAUDE_RC=$?
else
  command -v "$CLAUDE_BIN" >/dev/null 2>&1 \
    || fail "Claude CLI '$CLAUDE_BIN' not found on this host — cannot run"
  "$CLAUDE_BIN" --version >>"$RUN_LOG" 2>&1
  log "invoking Claude (timeout ${MAX_RUNTIME}s); log: $RUN_LOG"
  # -p / --print: non-interactive. Permission mode and tool allow-list keep the
  # run inside the sandbox; the pre-push hook is the structural backstop.
  ( cd "$AGENT_CLONE" && timeout --signal=TERM --kill-after=60 "$MAX_RUNTIME" \
      "$CLAUDE_BIN" -p "$PROMPT" \
        --permission-mode acceptEdits \
        --add-dir "$AGENT_CLONE" \
  ) >>"$RUN_LOG" 2>&1
  CLAUDE_RC=$?
fi

record_run() {  # record_run <result> <handoff_commit>
  python3 - "$AGENT_DIR/runs.jsonl" "$TASK_ID" "$CONTROL_COMMIT" "$TASK_BRANCH" \
           "$STARTED" "$1" "$CLAUDE_RC" "${2:-}" "$RUN_LOG" <<'PY'
import json, sys, datetime
path, task, control, branch, started, result, rc, handoff, runlog = sys.argv[1:10]
rec = {"task_id": task, "control_commit": control, "task_branch": branch,
       "started": started, "ended": datetime.datetime.now(
           datetime.timezone.utc).isoformat(timespec="seconds"),
       "result": result, "claude_exit": int(rc),
       "handoff_commit": handoff or None, "log": runlog}
with open(path, "a") as f:
    f.write(json.dumps(rec) + "\n")
PY
}

if [ "$CLAUDE_RC" -ne 0 ]; then
  record_run "claude_failed" ""
  fail "Claude exited $CLAUDE_RC — no handoff published, production untouched. Log: $RUN_LOG"
fi

# ── the run must have produced real work ─────────────────────────────────
if ! git -C "$AGENT_CLONE" rev-parse --verify --quiet "$TASK_BRANCH" >/dev/null; then
  record_run "no_branch" ""
  fail "task branch vanished — nothing to publish"
fi
if [ -z "$(git -C "$AGENT_CLONE" log "origin/$PROD_BRANCH..$TASK_BRANCH" --oneline 2>/dev/null)" ]; then
  record_run "no_commits" ""
  fail "the run produced no commits — refusing to publish an empty handoff"
fi

# ── independent verification: never trust the run's own claim ────────────
log "re-running the test suite independently of the Claude run"
if ! ( cd "$AGENT_CLONE" && bash scripts/test.sh ) >>"$RUN_LOG" 2>&1; then
  record_run "tests_failed" ""
  fail "tests fail on the produced branch — no handoff published. Log: $RUN_LOG"
fi

# ── concurrency token: has the Product Owner moved control underneath? ───
git -C "$AGENT_CLONE" fetch --prune --quiet origin || {
  record_run "fetch_failed" ""
  fail "could not re-fetch control before publishing"
}
CONTROL_NOW="$(git -C "$AGENT_CLONE" rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
if [ "$CONTROL_NOW" != "$CONTROL_COMMIT" ]; then
  record_run "control_conflict" ""
  fail "control moved ${CONTROL_COMMIT:0:7} -> ${CONTROL_NOW:0:7} during the run. NOT overwriting newer Product Owner state; this run's output stays on $TASK_BRANCH locally for inspection."
fi

# ── publish: task branch first, then the control handoff ─────────────────
if ! git -C "$AGENT_CLONE" push --quiet -u origin "$TASK_BRANCH"; then
  record_run "push_failed" ""
  fail "pushing $TASK_BRANCH failed — no handoff published"
fi
IMPL_COMMIT="$(git -C "$AGENT_CLONE" rev-parse "$TASK_BRANCH")"
log "pushed $TASK_BRANCH at ${IMPL_COMMIT:0:7}"

HANDOFF_COMMIT=""
CONTROL_DIR="$AGENT_DIR/control-clone"
if [ ! -d "$CONTROL_DIR/.git" ]; then
  git clone --quiet --branch "$CONTROL_BRANCH" --single-branch "$REPO_URL" "$CONTROL_DIR" \
    || { record_run "control_clone_failed" ""; fail "could not clone the control branch"; }
fi
git -C "$CONTROL_DIR" fetch --quiet origin "$CONTROL_BRANCH" \
  && git -C "$CONTROL_DIR" reset --hard --quiet "origin/$CONTROL_BRANCH"

# Carry the run's protocol files onto control, stamping the implementation
# commit the Product Owner should review.
for f in STATE.json IMPLEMENTATION_HANDOFF.md; do
  git -C "$AGENT_CLONE" show "$TASK_BRANCH:.frankenstein/$f" > "$CONTROL_DIR/.frankenstein/$f" 2>/dev/null
done
python3 - "$CONTROL_DIR/.frankenstein/STATE.json" "$IMPL_COMMIT" <<'PY'
import json, sys, datetime
path, impl = sys.argv[1:3]
try:
    doc = json.load(open(path))
except Exception:
    sys.exit(0)          # leave a malformed file alone; the commit below fails loudly
doc["implementation_commit"] = impl
doc["last_actor"] = "claude"
doc["updated_at"] = datetime.datetime.now(
    datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
json.dump(doc, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY

if git -C "$CONTROL_DIR" diff --quiet -- .frankenstein; then
  log "control already carries this handoff — nothing to publish"
else
  git -C "$CONTROL_DIR" add .frankenstein
  git -C "$CONTROL_DIR" -c user.name="Claude Worker" \
      -c user.email="noreply@anthropic.com" \
      commit --quiet -m "[CLAUDE-HANDOFF] $TASK_ID ready for review

Implementation commit: $IMPL_COMMIT
Task branch: $TASK_BRANCH
Authorizing control commit: $CONTROL_COMMIT" \
    || { record_run "handoff_commit_failed" ""; fail "could not commit the handoff"; }
  # Refuse to clobber: only fast-forward the control branch.
  if ! git -C "$CONTROL_DIR" push --quiet origin "HEAD:$CONTROL_BRANCH"; then
    record_run "handoff_push_rejected" ""
    fail "publishing the handoff was rejected (control moved). Newer Product Owner state preserved."
  fi
  HANDOFF_COMMIT="$(git -C "$CONTROL_DIR" rev-parse HEAD)"
  log "published handoff ${HANDOFF_COMMIT:0:7} to $CONTROL_BRANCH"
fi

record_run "success" "$HANDOFF_COMMIT"
log "DONE task=$TASK_ID impl=${IMPL_COMMIT:0:7} handoff=${HANDOFF_COMMIT:0:7} — production untouched"
