#!/usr/bin/env bash
# Autonomous Claude worker — the implementation side of the Product Owner
# <-> Claude loop. Polls the `control` branch and, ONLY when control state
# authorizes it, runs Claude non-interactively inside a sandbox.
#
#   bash scripts/claude-worker.sh              one poll cycle
#   bash scripts/claude-worker.sh --dry-run    full flow, mocked Claude, NO pushes
#   bash scripts/claude-worker.sh --status     decision only, no action
#
# ── THREE BRANCHES ───────────────────────────────────────────────────────
#   production          only PO-accepted code; the ONLY branch the deploy
#                       poller watches. This worker NEVER touches it.
#   control             PO <-> Claude orchestration state. Never deploys.
#   claude/FC-###-work  implementation work. Never deploys.
#
# ── THE CHILD PROCESS BOUNDARY ───────────────────────────────────────────
# A prompt is not containment. The Claude child runs with:
#   * a user+mount namespace in which the production checkout is bind-mounted
#     READ-ONLY (so it cannot write ~/FrankensteinCentral at all)
#   * NO git remote in its clone — the remote is removed for the duration of
#     the run and restored by the orchestrator afterwards, so the child has
#     nothing to push to
#   * a scratch HOME, no git global/system config, no askpass, no terminal
#     prompt, and GitHub/SSH credentials stripped from its environment
# Publication happens in the orchestrator AFTER the child exits and
# verification passes. If the sandbox cannot be established the worker
# REFUSES to run rather than running unconfined.
set -uo pipefail

AGENT_DIR="${FRANKENSTEIN_AGENT_DIR:-$HOME/.frankenstein/agent}"
CLONE_ROOT="${FRANKENSTEIN_WORKTREE_ROOT:-$HOME/.frankenstein/worktrees}"
PROD_DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
CONTROL_BRANCH="${FRANKENSTEIN_CONTROL_BRANCH:-control}"
PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
MAX_RUNTIME="${FRANKENSTEIN_CLAUDE_TIMEOUT:-3600}"
CLAUDE_BIN="${FRANKENSTEIN_CLAUDE_BIN:-claude}"
MOCK_CLAUDE="${FRANKENSTEIN_MOCK_CLAUDE:-}"
SUPPORTED_PROTOCOL_VERSION="${FRANKENSTEIN_PROTOCOL_VERSION:-1}"
# Escape hatch for hosts without user namespaces. NOT used by the systemd
# template; running unconfined removes the child boundary entirely.
ALLOW_UNSANDBOXED="${FRANKENSTEIN_ALLOW_UNSANDBOXED:-0}"

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run" ;;
  --status)  MODE="status" ;;
  "")        ;;
  *) echo "usage: claude-worker.sh [--dry-run|--status]"; exit 2 ;;
esac

mkdir -p "$AGENT_DIR" "$CLONE_ROOT"
LOG="$AGENT_DIR/worker.log"
log()  { echo "$(date -Is)  $*" | tee -a "$LOG" >&2; }
noop() { log "NO-OP: $*"; exit 0; }
fail() { log "FAILED: $*"; exit 1; }

# ── kill switch (independent of the production deployer) ─────────────────
[ -e "$AGENT_DIR/DISABLED" ] && noop "kill switch present ($AGENT_DIR/DISABLED)"
[ -e "$AGENT_DIR/ENABLED" ] || noop "not enabled — create $AGENT_DIR/ENABLED to allow autonomous runs"

# ── single flight ────────────────────────────────────────────────────────
exec 9>"$AGENT_DIR/worker.lock"
flock -n 9 || noop "another worker run holds the lock"

# ── repository location (production checkout used READ-ONLY) ─────────────
REPO_URL="${FRANKENSTEIN_REPO_URL:-$(git -C "$PROD_DIR" remote get-url origin 2>/dev/null)}"
[ -n "$REPO_URL" ] || fail "cannot determine repository URL (set FRANKENSTEIN_REPO_URL)"
AGENT_CLONE="$CLONE_ROOT/agent-repo"

abspath() { python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$1" 2>/dev/null || echo "$1"; }
PROD_REAL="$(abspath "$PROD_DIR")"
CLONE_REAL="$(abspath "$AGENT_CLONE")"
case "$CLONE_REAL/" in "$PROD_REAL"/*)
  fail "isolation violation: agent clone $CLONE_REAL is inside the production checkout" ;;
esac
[ "$CLONE_REAL" = "$PROD_REAL" ] && fail "isolation violation: agent clone is the production checkout"

# ── authoritative control state ──────────────────────────────────────────
if [ ! -d "$AGENT_CLONE/.git" ]; then
  log "cloning into isolated agent clone $AGENT_CLONE"
  git clone --quiet "$REPO_URL" "$AGENT_CLONE" || fail "clone failed"
fi
git -C "$AGENT_CLONE" remote set-url origin "$REPO_URL" 2>/dev/null
git -C "$AGENT_CLONE" fetch --prune --quiet origin || fail "fetch failed — control state unknown"

CONTROL_COMMIT="$(git -C "$AGENT_CLONE" rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
[ -n "$CONTROL_COMMIT" ] || noop "control branch 'origin/$CONTROL_BRANCH' not found"

ctl_file() { git -C "$AGENT_CLONE" show "$CONTROL_COMMIT:.frankenstein/$1" 2>/dev/null; }
STATE_JSON="$(ctl_file STATE.json)"
[ -n "$STATE_JSON" ] || noop "control commit carries no .frankenstein/STATE.json"

# ── exact state validation (PROTOCOL.md semantics, not loose globs) ──────
VALIDATION="$(printf '%s' "$STATE_JSON" | python3 -c "
import json, re, sys
ALLOWED_TURNS = {'product_owner', 'claude', 'none'}
ALLOWED_STATUS = {'awaiting_directive', 'ready_for_implementation', 'implementing',
                  'awaiting_review', 'changes_requested', 'accepted', 'blocked'}
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('INVALID|unparseable STATE.json: %s' % e); raise SystemExit
if not isinstance(d, dict):
    print('INVALID|STATE.json is not an object'); raise SystemExit
if d.get('protocol_version') != $SUPPORTED_PROTOCOL_VERSION:
    print('INVALID|protocol_version %r is not the supported version $SUPPORTED_PROTOCOL_VERSION'
          % d.get('protocol_version')); raise SystemExit
turn, status, task = d.get('turn'), d.get('status'), d.get('task_id')
if turn not in ALLOWED_TURNS:
    print('INVALID|turn %r is not an allowed value' % turn); raise SystemExit
if status not in ALLOWED_STATUS:
    print('INVALID|status %r is not an allowed value' % status); raise SystemExit
if not isinstance(task, str) or not re.fullmatch(r'FC-[0-9]{3,}', task):
    print('INVALID|task_id %r is not ^FC-[0-9]{3,}\$' % task); raise SystemExit
print('OK|%s|%s|%s|%s' % (turn, status, task, d.get('implementation_commit') or ''))
" 2>/dev/null)"

case "$VALIDATION" in
  OK\|*) ;;
  INVALID\|*) noop "control state rejected: ${VALIDATION#INVALID|}" ;;
  *) noop "control state could not be validated — invoking nothing" ;;
esac
IFS='|' read -r _ TURN STATUS TASK_ID PRIOR_IMPL <<<"$VALIDATION"

# The directive must exist on control and name the same task.
DIRECTIVE_TEXT="$(ctl_file PRODUCT_DIRECTIVE.md)"
[ -n "$DIRECTIVE_TEXT" ] || noop "control carries no PRODUCT_DIRECTIVE.md — nothing authoritative to implement"
DIRECTIVE_TASK="$(printf '%s' "$DIRECTIVE_TEXT" | grep -m1 -i '^Task ID:' | cut -d: -f2- | tr -d ' \r')"
if [ -n "$DIRECTIVE_TASK" ] && [ "$DIRECTIVE_TASK" != "$TASK_ID" ]; then
  noop "directive names $DIRECTIVE_TASK but STATE.json says $TASK_ID — inconsistent, refusing to guess"
fi

# ── wake condition ───────────────────────────────────────────────────────
[ "$TURN" = "claude" ] || noop "turn=$TURN (not claude) status=$STATUS — nothing authorized"
case "$STATUS" in
  ready_for_implementation|changes_requested) ;;
  *) noop "turn=claude but status=$STATUS is not an authorized start state" ;;
esac

TASK_BRANCH="claude/${TASK_ID}-work"
log "AUTHORIZED: task=$TASK_ID status=$STATUS control=${CONTROL_COMMIT:0:7} branch=$TASK_BRANCH"

if [ "$MODE" = "status" ]; then
  echo "would run: task=$TASK_ID status=$STATUS control=${CONTROL_COMMIT:0:7}"
  echo "clone:     $AGENT_CLONE"
  echo "branch:    $TASK_BRANCH"
  exit 0
fi

# ── branch preparation: continue, don't restart ──────────────────────────
if [ "$STATUS" = "changes_requested" ]; then
  # Corrections must ADVANCE the implementation already under review, never
  # silently restart it from production.
  if git -C "$AGENT_CLONE" rev-parse --verify --quiet "origin/$TASK_BRANCH^{commit}" >/dev/null; then
    git -C "$AGENT_CLONE" checkout --quiet -B "$TASK_BRANCH" "origin/$TASK_BRANCH" \
      || fail "could not resume $TASK_BRANCH"
    RESUMED_AT="$(git -C "$AGENT_CLONE" rev-parse HEAD)"
    log "resuming existing implementation at ${RESUMED_AT:0:7}"
    if [ -n "$PRIOR_IMPL" ]; then
      if ! git -C "$AGENT_CLONE" merge-base --is-ancestor "$PRIOR_IMPL" HEAD 2>/dev/null; then
        fail "control names implementation_commit ${PRIOR_IMPL:0:7} but it is not in $TASK_BRANCH history — refusing to continue on a divergent branch"
      fi
    fi
  elif [ -n "$PRIOR_IMPL" ]; then
    git -C "$AGENT_CLONE" checkout --quiet -B "$TASK_BRANCH" "$PRIOR_IMPL" \
      || fail "could not resume from implementation_commit ${PRIOR_IMPL:0:7}"
    log "resuming from implementation_commit ${PRIOR_IMPL:0:7}"
  else
    fail "status=changes_requested but neither origin/$TASK_BRANCH nor implementation_commit exists — nothing to continue"
  fi
else
  git -C "$AGENT_CLONE" checkout --quiet -B "$TASK_BRANCH" "origin/$PROD_BRANCH" \
    || fail "could not start $TASK_BRANCH from origin/$PROD_BRANCH"
  log "new task branch from the approved production baseline"
fi

# ── materialize the AUTHORITATIVE control snapshot ───────────────────────
# The task branch descends from production, whose .frankenstein/ copies may be
# stale placeholders. Claude must read the directive and state that actually
# authorized this run, not production's copy.
mkdir -p "$AGENT_CLONE/.frankenstein"
for f in PRODUCT_DIRECTIVE.md STATE.json PROTOCOL.md; do
  if ctl_file "$f" > "$AGENT_CLONE/.frankenstein/$f.tmp" 2>/dev/null \
     && [ -s "$AGENT_CLONE/.frankenstein/$f.tmp" ]; then
    mv "$AGENT_CLONE/.frankenstein/$f.tmp" "$AGENT_CLONE/.frankenstein/$f"
  else
    rm -f "$AGENT_CLONE/.frankenstein/$f.tmp"
    [ "$f" = "PROTOCOL.md" ] || fail "control commit is missing .frankenstein/$f"
  fi
done
printf '%s\n' "$CONTROL_COMMIT" > "$AGENT_CLONE/.frankenstein/AUTHORIZING_CONTROL_COMMIT"
git -C "$AGENT_CLONE" add .frankenstein >/dev/null 2>&1
if ! git -C "$AGENT_CLONE" diff --cached --quiet; then
  git -C "$AGENT_CLONE" -c user.name="Claude Worker" -c user.email="noreply@anthropic.com" \
    commit --quiet -m "[CLAUDE] $TASK_ID authoritative control snapshot ${CONTROL_COMMIT:0:7}" \
    || fail "could not commit the control snapshot"
  log "materialized control snapshot from ${CONTROL_COMMIT:0:7}"
fi

# Defence in depth for the orchestrator's own clone. The child has no remote
# at all, but this hook makes the forbidden pushes impossible from this clone
# regardless of who invokes git in it.
HOOK="$AGENT_CLONE/.git/hooks/pre-push"
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<'HOOKEOF'
#!/usr/bin/env bash
# Installed by claude-worker.sh.
#   1. production/main/master may never be pushed from this clone
#   2. no push may be a force push (non-fast-forward), detected by ancestry
ZERO=0000000000000000000000000000000000000000
status=0
while read -r _local_ref local_sha remote_ref remote_sha; do
  case "$remote_ref" in
    refs/heads/production|refs/heads/main|refs/heads/master)
      echo "pre-push: REFUSED — this clone may not push $remote_ref" >&2
      echo "pre-push: production promotion is a Product Owner action." >&2
      status=1; continue ;;
  esac
  # A real force-push check: if the remote tip is not an ancestor of what we
  # are pushing, this would discard remote history.
  if [ -n "$remote_sha" ] && [ "$remote_sha" != "$ZERO" ] && [ "$local_sha" != "$ZERO" ]; then
    if ! git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
      echo "pre-push: REFUSED — non-fast-forward push to $remote_ref would discard history" >&2
      status=1
    fi
  fi
done
exit $status
HOOKEOF
chmod +x "$HOOK"

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_LOG="$AGENT_DIR/run-$TASK_ID-$RUN_ID.log"
STARTED="$(date -Is)"
BASELINE="$(git -C "$AGENT_CLONE" rev-parse HEAD)"

record_run() {
  python3 - "$AGENT_DIR/runs.jsonl" "$TASK_ID" "$CONTROL_COMMIT" "$TASK_BRANCH" \
           "$STARTED" "$1" "${CLAUDE_RC:-0}" "${2:-}" "$RUN_LOG" "$MODE" <<'PY'
import json, sys, datetime
path, task, control, branch, started, result, rc, handoff, runlog, mode = sys.argv[1:11]
open(path, "a").write(json.dumps({
    "task_id": task, "control_commit": control, "task_branch": branch,
    "started": started, "ended": datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds"),
    "result": result, "claude_exit": int(rc), "mode": mode,
    "handoff_commit": handoff or None, "log": runlog}) + "\n")
PY
}

# ── the child sandbox ────────────────────────────────────────────────────
# Remove the remote so the child has nothing to push to, whatever it tries.
git -C "$AGENT_CLONE" remote remove origin 2>/dev/null
restore_remote() { git -C "$AGENT_CLONE" remote add origin "$REPO_URL" 2>/dev/null \
                   || git -C "$AGENT_CLONE" remote set-url origin "$REPO_URL" 2>/dev/null; }

CHILD_HOME="$AGENT_DIR/child-home"
rm -rf "$CHILD_HOME"; mkdir -p "$CHILD_HOME"

sandbox_available() {
  unshare --user --map-root-user --mount true >/dev/null 2>&1
}

# The child's environment, built with `env -i`: nothing from the orchestrator
# is inherited, so a GitHub token in this process cannot reach the child. No
# git identity, no global/system config, no interactive credential path.
CHILD_ENV=(env -i
  "HOME=$CHILD_HOME"
  "PATH=$PATH"
  "TERM=${TERM:-dumb}"
  "LANG=${LANG:-C.UTF-8}"
  GIT_CONFIG_GLOBAL=/dev/null
  GIT_CONFIG_NOSYSTEM=1
  GIT_TERMINAL_PROMPT=0
  GIT_ASKPASS=/bin/false
  SSH_ASKPASS=/bin/false
  "FRANKENSTEIN_TASK_ID=$TASK_ID"
  "FRANKENSTEIN_TASK_BRANCH=$TASK_BRANCH"
  "FRANKENSTEIN_CONTROL_COMMIT=$CONTROL_COMMIT")

# Run "$@" with the production checkout bind-mounted read-only for the child.
run_sandboxed() {
  unshare --user --map-root-user --mount -- /bin/bash -c '
    prod="$1"; shift
    if [ -d "$prod" ]; then
      mount --bind "$prod" "$prod" 2>/dev/null &&
      mount -o remount,bind,ro "$prod" 2>/dev/null ||
      { echo "sandbox: could not make $prod read-only" >&2; exit 97; }
    fi
    exec "$@"
  ' _ "$PROD_REAL" "$@"
}

PROMPT="You are the autonomous implementation worker for FrankensteinCentral.

1. Read CLAUDE.md and .frankenstein/PROTOCOL.md.
2. Read .frankenstein/PRODUCT_DIRECTIVE.md — the authoritative scope. It and
   .frankenstein/STATE.json were placed here from control commit
   $CONTROL_COMMIT (see .frankenstein/AUTHORIZING_CONTROL_COMMIT).
3. Verify STATE.json says turn=claude with status $STATUS. If not, stop.
4. Implement ONLY that scope. Do not choose extra work or edit the directive.
5. Stay on branch $TASK_BRANCH.
6. Run: bash scripts/test.sh — all tests must pass.
7. Update .frankenstein/IMPLEMENTATION_HANDOFF.md honestly, including a
   'Deviations From Directive' section ('No deviations' if none).
8. Update .frankenstein/STATE.json: turn=product_owner, status=awaiting_review,
   last_actor=claude, updated_at=<current UTC>.
9. Commit with a [CLAUDE] $TASK_ID message. Do NOT push — you have no remote.
10. Stop.

You may NOT push or merge production, run promote.sh, rollback.sh or deploy.sh,
modify systemd units, force push, use sudo, or issue a directive."

CLAUDE_RC=0
if [ "$MODE" = "dry-run" ] || [ -n "$MOCK_CLAUDE" ]; then
  RUNNER="${MOCK_CLAUDE:-true}"
  log "using mock runner instead of Claude"
  # SEEN_DIR is a MOCK-HARNESS channel only: it lets a test mock record what
  # it was actually handed. It is never forwarded to the real Claude path.
  MOCK_ENV=("${CHILD_ENV[@]}" "SEEN_DIR=${SEEN_DIR:-}")
  if sandbox_available; then
    ( cd "$AGENT_CLONE" && run_sandboxed "${MOCK_ENV[@]}" \
        /bin/bash -c "$RUNNER" ) >>"$RUN_LOG" 2>&1
    CLAUDE_RC=$?
  elif [ "$ALLOW_UNSANDBOXED" = "1" ]; then
    log "WARNING: running the mock UNSANDBOXED (FRANKENSTEIN_ALLOW_UNSANDBOXED=1)"
    ( cd "$AGENT_CLONE" && "${MOCK_ENV[@]}" /bin/bash -c "$RUNNER" ) >>"$RUN_LOG" 2>&1
    CLAUDE_RC=$?
  else
    restore_remote
    record_run "no_sandbox" ""
    fail "no user-namespace sandbox available on this host — refusing to run a child unconfined. Install/enable unprivileged user namespaces, or set FRANKENSTEIN_ALLOW_UNSANDBOXED=1 to accept the loss of the child boundary."
  fi
else
  command -v "$CLAUDE_BIN" >/dev/null 2>&1 || { restore_remote; fail "Claude CLI '$CLAUDE_BIN' not found"; }
  sandbox_available || [ "$ALLOW_UNSANDBOXED" = "1" ] || {
    restore_remote; record_run "no_sandbox" ""
    fail "no user-namespace sandbox available — refusing to run Claude unconfined."; }
  log "invoking Claude sandboxed (timeout ${MAX_RUNTIME}s); log: $RUN_LOG"
  ( cd "$AGENT_CLONE" && run_sandboxed "${CHILD_ENV[@]}" \
      timeout --signal=TERM --kill-after=60 "$MAX_RUNTIME" \
      "$CLAUDE_BIN" -p "$PROMPT" \
      --permission-mode acceptEdits --add-dir "$AGENT_CLONE" \
  ) >>"$RUN_LOG" 2>&1
  CLAUDE_RC=$?
fi

restore_remote

[ "$CLAUDE_RC" -eq 97 ] && { record_run "sandbox_failed" ""; fail "the sandbox could not be established — nothing ran"; }
if [ "$CLAUDE_RC" -ne 0 ]; then
  record_run "claude_failed" ""
  fail "Claude exited $CLAUDE_RC — no handoff published, production untouched. Log: $RUN_LOG"
fi

# ── the run must have produced real work ─────────────────────────────────
git -C "$AGENT_CLONE" rev-parse --verify --quiet "$TASK_BRANCH" >/dev/null \
  || { record_run "no_branch" ""; fail "task branch vanished"; }
if [ -z "$(git -C "$AGENT_CLONE" log "$BASELINE..$TASK_BRANCH" --oneline 2>/dev/null)" ]; then
  record_run "no_commits" ""
  fail "the run produced no commits — refusing to publish an empty handoff"
fi

# ── independent verification: never trust the run's own claim ────────────
log "re-running the test suite independently of the Claude run"
( cd "$AGENT_CLONE" && bash scripts/test.sh ) >>"$RUN_LOG" 2>&1 \
  || { record_run "tests_failed" ""; fail "tests fail on the produced branch — no handoff published. Log: $RUN_LOG"; }

IMPL_COMMIT="$(git -C "$AGENT_CLONE" rev-parse "$TASK_BRANCH")"

# ── concurrency token, stage 1 ───────────────────────────────────────────
git -C "$AGENT_CLONE" fetch --prune --quiet origin \
  || { record_run "fetch_failed" ""; fail "could not re-fetch control before publishing"; }
CONTROL_NOW="$(git -C "$AGENT_CLONE" rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
[ "$CONTROL_NOW" = "$CONTROL_COMMIT" ] || {
  record_run "control_conflict" ""
  fail "control moved ${CONTROL_COMMIT:0:7} -> ${CONTROL_NOW:0:7} during the run. NOT overwriting newer Product Owner state; the work stays on $TASK_BRANCH locally."; }

# ── dry run stops HERE: nothing is ever pushed ───────────────────────────
if [ "$MODE" = "dry-run" ]; then
  record_run "dry_run" ""
  log "DRY RUN — nothing pushed. WOULD publish:"
  log "  task branch:        $TASK_BRANCH at ${IMPL_COMMIT:0:7}"
  log "  implementation SHA: $IMPL_COMMIT"
  log "  control transition: $STATUS -> awaiting_review on ${CONTROL_COMMIT:0:7}"
  log "production, control and remote task branches are unchanged."
  exit 0
fi

# ── publish: task branch, then the control handoff ───────────────────────
git -C "$AGENT_CLONE" push --quiet -u origin "$TASK_BRANCH" \
  || { record_run "push_failed" ""; fail "pushing $TASK_BRANCH failed — no handoff published"; }
log "pushed $TASK_BRANCH at ${IMPL_COMMIT:0:7}"

CONTROL_DIR="$AGENT_DIR/control-clone"
if [ ! -d "$CONTROL_DIR/.git" ]; then
  git clone --quiet --branch "$CONTROL_BRANCH" --single-branch "$REPO_URL" "$CONTROL_DIR" \
    || { record_run "control_clone_failed" ""; fail "could not clone the control branch"; }
fi
git -C "$CONTROL_DIR" fetch --quiet origin "$CONTROL_BRANCH" \
  || { record_run "control_fetch_failed" ""; fail "could not fetch control before publishing"; }

# ── concurrency token, stage 2 ───────────────────────────────────────────
# Between stage 1 and here the Product Owner may have moved control. Resetting
# onto origin/control would silently rebase this run's stale state on top of
# newer PO state and then fast-forward cleanly. Re-check, and reset to the
# AUTHORIZING commit explicitly rather than to whatever origin now points at.
CONTROL_AT_PUBLISH="$(git -C "$CONTROL_DIR" rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
[ "$CONTROL_AT_PUBLISH" = "$CONTROL_COMMIT" ] || {
  record_run "control_conflict_late" ""
  fail "control moved to ${CONTROL_AT_PUBLISH:0:7} before publication (authorized ${CONTROL_COMMIT:0:7}). Newer Product Owner state preserved; no handoff published."; }
git -C "$CONTROL_DIR" reset --hard --quiet "$CONTROL_COMMIT" \
  || { record_run "control_reset_failed" ""; fail "could not reset the control clone to the authorizing commit"; }

for f in STATE.json IMPLEMENTATION_HANDOFF.md; do
  git -C "$AGENT_CLONE" show "$TASK_BRANCH:.frankenstein/$f" > "$CONTROL_DIR/.frankenstein/$f" 2>/dev/null
done
python3 - "$CONTROL_DIR/.frankenstein/STATE.json" "$IMPL_COMMIT" <<'PY'
import json, sys, datetime
path, impl = sys.argv[1:3]
try:
    doc = json.load(open(path))
except Exception:
    raise SystemExit(1)
doc["implementation_commit"] = impl
doc["last_actor"] = "claude"
doc["updated_at"] = datetime.datetime.now(
    datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
json.dump(doc, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
[ $? -eq 0 ] || { record_run "state_write_failed" ""; fail "could not stamp the implementation commit into STATE.json"; }

HANDOFF_COMMIT=""
if git -C "$CONTROL_DIR" diff --quiet -- .frankenstein; then
  log "control already carries this handoff — nothing to publish"
else
  git -C "$CONTROL_DIR" add .frankenstein
  git -C "$CONTROL_DIR" -c user.name="Claude Worker" -c user.email="noreply@anthropic.com" \
      commit --quiet -m "[CLAUDE-HANDOFF] $TASK_ID ready for review

Implementation commit: $IMPL_COMMIT
Task branch: $TASK_BRANCH
Authorizing control commit: $CONTROL_COMMIT" \
    || { record_run "handoff_commit_failed" ""; fail "could not commit the handoff"; }
  # Non-forcing: a race that slipped past both checks still cannot clobber.
  git -C "$CONTROL_DIR" push --quiet origin "HEAD:$CONTROL_BRANCH" \
    || { record_run "handoff_push_rejected" ""; fail "publishing the handoff was rejected (control moved). Newer Product Owner state preserved."; }
  HANDOFF_COMMIT="$(git -C "$CONTROL_DIR" rev-parse HEAD)"
  log "published handoff ${HANDOFF_COMMIT:0:7} to $CONTROL_BRANCH"
fi

record_run "success" "$HANDOFF_COMMIT"
log "DONE task=$TASK_ID impl=${IMPL_COMMIT:0:7} handoff=${HANDOFF_COMMIT:0:7} — production untouched"
