#!/usr/bin/env bash
# Autonomous Claude worker — the implementation side of the Product Owner
# <-> Claude loop. Polls the `control` branch and, ONLY when control state
# authorizes it, runs Claude non-interactively inside a sandbox.
#
#   bash scripts/claude-worker.sh              one poll cycle
#   bash scripts/claude-worker.sh --status     decision only, no action
#   bash scripts/claude-worker.sh --dry-run    full flow, mocked Claude, NO pushes
#   bash scripts/claude-worker.sh --probe      containment + Claude-auth self test
#
# ── THREE BRANCHES ───────────────────────────────────────────────────────
#   production          only PO-accepted code; the ONLY branch the deploy
#                       poller watches. This worker NEVER touches it.
#   control             PO <-> Claude orchestration state. Never deploys.
#   claude/FC-###-work  implementation work. Never deploys.
#
# ── THE CONTAINMENT MODEL ────────────────────────────────────────────────
# Three separate trust zones, because a prompt is not containment:
#
#   1. THE CHILD ZONE — everything Claude can influence. Runs in a user+mount
#      namespace where the ONLY writable thing is the workspace (the agent
#      clone, bind-mounted at a neutral path outside the home) and a scratch
#      home. The real home directory is REPLACED by that scratch home, so
#      ~/.config/gh, ~/.ssh, ~/.frankenstein and shell configuration simply do
#      not exist for the child. Shared runtime/temp locations are masked with
#      private tmpfs. The clone has no git remote for the duration of the run.
#      Only narrowly configured Claude authentication paths are exposed, and
#      only read-only.
#
#   2. THE VERIFICATION ZONE — the independent test re-run. Claude can edit
#      scripts/test.sh, so running it on the host would be a direct escape.
#      It runs inside the SAME namespace, with the same masks.
#
#   3. THE PUBLISHER ZONE — trusted, outside the sandbox. It NEVER executes
#      anything originating in the child's workspace: not scripts/test.sh, not
#      .git/hooks, not .git/config. The child's git control plane is restored
#      from a trusted copy before any git command touches that clone, and
#      publication happens from FRESH clones the child never had access to.
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

# Narrow, READ-ONLY exposure for Claude's own authentication: colon-separated
# absolute paths. Everything else under the home stays hidden. These defaults
# are CANDIDATES, not assumptions — `--probe` reports which ones exist on the
# host and whether the installed CLI can actually authenticate with them.
CLAUDE_EXPOSE="${FRANKENSTEIN_CLAUDE_EXPOSE:-$HOME/.claude/.credentials.json:$HOME/.claude.json}"
# Environment forwarded to the child: Claude authentication only. No GitHub
# token, no SSH agent, nothing else.
CLAUDE_ENV_KEYS="${FRANKENSTEIN_CLAUDE_ENV:-ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CONFIG_DIR}"

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run" ;;
  --status)  MODE="status" ;;
  --probe)   MODE="probe" ;;
  "")        ;;
  *) echo "usage: claude-worker.sh [--dry-run|--status|--probe]"; exit 2 ;;
esac

mkdir -p "$AGENT_DIR" "$CLONE_ROOT"
LOG="$AGENT_DIR/worker.log"
log()  { echo "$(date -Is)  $*" | tee -a "$LOG" >&2; }
noop() { log "NO-OP: $*"; exit 0; }
fail() { log "FAILED: $*"; exit 1; }

abspath() { python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$1" 2>/dev/null || echo "$1"; }
REAL_HOME="$(abspath "$HOME")"
PROD_REAL="$(abspath "$PROD_DIR")"
AGENT_CLONE="$CLONE_ROOT/agent-repo"
CLONE_REAL="$(abspath "$AGENT_CLONE")"

# ── containment plumbing ─────────────────────────────────────────────────
# `contains a` is true when $2 is $1 or lives inside it.
contains() { [ "$1" = "$2" ] && return 0; case "$2/" in "$1"/*) return 0 ;; esac; return 1; }

# Where the workspace is bind-mounted for the child. It must be OUTSIDE the
# home directory, because the home is about to be replaced wholesale. Prefer an
# empty conventional mountpoint so nothing real is shadowed.
pick_workspace_mount() {
  local c
  if [ -n "${FRANKENSTEIN_WORKSPACE_MOUNT:-}" ]; then echo "$FRANKENSTEIN_WORKSPACE_MOUNT"; return; fi
  for c in /mnt /media /srv; do
    [ -d "$c" ] || continue
    contains "$c" "$REAL_HOME" && continue
    [ -z "$(ls -A "$c" 2>/dev/null)" ] && { echo "$c"; return; }
  done
  for c in /mnt /media /srv; do
    [ -d "$c" ] || continue
    contains "$c" "$REAL_HOME" && continue
    echo "$c"; return
  done
  echo ""
}
WORKSPACE_MNT="$(pick_workspace_mount)"

# Shared runtime/temp locations get a private tmpfs — unless one of them is an
# ancestor of something the child legitimately needs, in which case masking it
# would remove the workspace or the scratch home along with it.
build_mask_list() {
  local c out=""
  for c in "/run/user/$(id -u)" /tmp /var/tmp; do
    [ -d "$c" ] || continue
    contains "$c" "$REAL_HOME"     && continue
    contains "$c" "$PROD_REAL"     && continue
    contains "$c" "$CLONE_REAL"    && continue
    contains "$c" "$AGENT_DIR"     && continue
    [ -n "$WORKSPACE_MNT" ] && contains "$c" "$WORKSPACE_MNT" && continue
    out="$out $c"
  done
  echo "${out# }"
}
MASK_PATHS="$(build_mask_list)"

# The scratch home. It is bind-mounted OVER the real home inside the namespace,
# so every absolute path under ~ resolves into this directory instead.
CHILD_HOME="$AGENT_DIR/child-home"
EXPOSE_LIST=""
prepare_child_home() {
  local p rel
  rm -rf "$CHILD_HOME"
  mkdir -p "$CHILD_HOME/tmp" "$CHILD_HOME/.cache"
  chmod 700 "$CHILD_HOME"
  EXPOSE_LIST=""
  IFS=':' read -r -a _expose <<<"$CLAUDE_EXPOSE"
  for p in "${_expose[@]:-}"; do
    [ -n "$p" ] || continue
    [ -e "$p" ] || continue
    contains "$REAL_HOME" "$p" || continue      # only home paths need staging
    rel="${p#$REAL_HOME/}"
    if [ -d "$p" ]; then
      mkdir -p "$CHILD_HOME/$rel"
    else
      mkdir -p "$(dirname "$CHILD_HOME/$rel")"; : > "$CHILD_HOME/$rel"
    fi
    EXPOSE_LIST="$EXPOSE_LIST $p|$CHILD_HOME/$rel"
  done
  EXPOSE_LIST="${EXPOSE_LIST# }"
}

sandbox_available() { unshare --user --map-root-user --mount true >/dev/null 2>&1; }

# Run "$@" inside the containment boundary. WS_SRC is bind-mounted at
# WORKSPACE_MNT and becomes the working directory.
WS_SRC=""
run_sandboxed() {
  FCS_PROD="$PROD_REAL" FCS_WS_SRC="$WS_SRC" FCS_WS_MNT="$WORKSPACE_MNT" \
  FCS_HOME_STAGE="$CHILD_HOME" FCS_REAL_HOME="$REAL_HOME" \
  FCS_MASK="$MASK_PATHS" FCS_EXPOSE="$EXPOSE_LIST" \
  unshare --user --map-root-user --mount -- /bin/bash -c '
    set -u
    die() { echo "sandbox: $1" >&2; exit 97; }
    # the production checkout is read-only even where it is not hidden
    if [ -d "$FCS_PROD" ]; then
      mount --bind "$FCS_PROD" "$FCS_PROD" 2>/dev/null || die "cannot bind $FCS_PROD"
      mount -o remount,bind,ro "$FCS_PROD" 2>/dev/null || die "cannot make $FCS_PROD read-only"
    fi
    # the workspace, at a neutral path outside the home
    [ -n "$FCS_WS_MNT" ] || die "no usable workspace mountpoint"
    mount --bind "$FCS_WS_SRC" "$FCS_WS_MNT" 2>/dev/null \
      || die "cannot bind the workspace at $FCS_WS_MNT"
    # narrow read-only exposures, staged before the home disappears
    for e in $FCS_EXPOSE; do
      src="${e%%|*}"; dst="${e#*|}"
      mount --bind "$src" "$dst" 2>/dev/null || die "cannot expose $src"
      mount -o remount,bind,ro "$dst" 2>/dev/null || die "cannot make $src read-only"
    done
    # THE REAL HOME DISAPPEARS: gh credentials, ssh keys, ~/.frankenstein,
    # shell configuration, the production checkout, the agent directory.
    mount --rbind "$FCS_HOME_STAGE" "$FCS_REAL_HOME" 2>/dev/null \
      || die "cannot mask the home directory $FCS_REAL_HOME"
    for m in $FCS_MASK; do
      [ -d "$m" ] || continue
      mount -t tmpfs none "$m" 2>/dev/null || die "cannot mask $m"
    done
    cd "$FCS_WS_MNT" 2>/dev/null || die "cannot enter the workspace"
    exec "$@"
  ' _ "$@"
}

# The child's environment. env -i so nothing is inherited: no GITHUB_TOKEN,
# no GH_TOKEN, no SSH_AUTH_SOCK, no git identity, no interactive credential
# path. Only the explicitly listed Claude authentication variables are added.
build_child_env() {
  local seen_home="$1" k v
  CHILD_ENV=(env -i
    "HOME=$seen_home"
    "TMPDIR=$seen_home/tmp"
    "PATH=$PATH"
    "TERM=${TERM:-dumb}"
    "LANG=${LANG:-C.UTF-8}"
    GIT_CONFIG_GLOBAL=/dev/null
    GIT_CONFIG_NOSYSTEM=1
    GIT_TERMINAL_PROMPT=0
    GIT_ASKPASS=/bin/false
    SSH_ASKPASS=/bin/false
    "GIT_AUTHOR_NAME=Claude Worker"
    "GIT_AUTHOR_EMAIL=noreply@anthropic.com"
    "GIT_COMMITTER_NAME=Claude Worker"
    "GIT_COMMITTER_EMAIL=noreply@anthropic.com")
  for k in $CLAUDE_ENV_KEYS; do
    v="${!k:-}"
    [ -n "$v" ] && CHILD_ENV+=("$k=$v")
  done
}

# ── --probe: prove containment and Claude auth BEFORE activation ─────────
if [ "$MODE" = "probe" ]; then
  echo "FrankensteinCentral autonomous-worker containment probe"
  echo "  home:               $REAL_HOME"
  echo "  workspace mount:    ${WORKSPACE_MNT:-<none found>}"
  echo "  masked locations:   ${MASK_PATHS:-<none>}"
  echo "  claude binary:      $(command -v "$CLAUDE_BIN" 2>/dev/null || echo '<not found>')"
  echo "  exposure candidates:"
  IFS=':' read -r -a _probe_expose <<<"$CLAUDE_EXPOSE"
  for p in "${_probe_expose[@]:-}"; do
    [ -n "$p" ] || continue
    echo "    $p  $([ -e "$p" ] && echo present || echo absent)"
  done
  if ! sandbox_available; then
    echo "RESULT: FAIL — this host cannot create user+mount namespaces."
    exit 1
  fi
  echo "  sandbox:            available"
  prepare_child_home
  PROBE_WS="$(mktemp -d "${TMPDIR:-/tmp}/fc-probe-XXXXXX")" || { echo "RESULT: FAIL — no temp dir"; exit 1; }
  trap 'rm -rf "$PROBE_WS"' EXIT
  WS_SRC="$PROBE_WS"
  build_child_env "$REAL_HOME"
  echo
  echo "-- containment checks (inside the sandbox) --"
  run_sandboxed "${CHILD_ENV[@]}" /bin/bash -c '
    fail=0
    for p in "$HOME/.config/gh/hosts.yml" "$HOME/.gitconfig" "$HOME/.ssh/id_rsa" \
             "$HOME/.ssh/id_ed25519" "$HOME/.frankenstein/deployed.json"; do
      if [ -e "$p" ]; then echo "  LEAK: $p is visible"; fail=1; fi
    done
    [ -d "$HOME/.ssh" ] && { echo "  LEAK: ~/.ssh exists"; fail=1; }
    if echo probe > "$HOME/.probe-write" 2>/dev/null; then
      echo "  ok:   scratch home is writable (and is not the real home)"
    else
      echo "  FAIL: scratch home is not writable"; fail=1
    fi
    echo "  home now contains: $(ls -A "$HOME" | tr "\n" " ")"
    echo "  GITHUB_TOKEN visible: ${GITHUB_TOKEN:-<unset>}"
    echo "  GH_TOKEN visible:     ${GH_TOKEN:-<unset>}"
    echo "  SSH_AUTH_SOCK:        ${SSH_AUTH_SOCK:-<unset>}"
    exit $fail'
  CONTAIN_RC=$?
  echo
  echo "-- Claude authentication check (inside the sandbox) --"
  if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
    echo "  Claude CLI '$CLAUDE_BIN' not found on PATH — cannot test authentication."
    AUTH_RC=1
  else
    CLAUDE_PATH="$(command -v "$CLAUDE_BIN")"
    if contains "$REAL_HOME" "$CLAUDE_PATH"; then
      echo "  NOTE: the CLI lives under the home directory ($CLAUDE_PATH), which the"
      echo "        sandbox hides. Add its install directory to FRANKENSTEIN_CLAUDE_EXPOSE."
    fi
    OUT="$(run_sandboxed "${CHILD_ENV[@]}" timeout 120 "$CLAUDE_BIN" -p \
            'Reply with the single word READY and nothing else.' 2>&1)"
    AUTH_RC=$?
    echo "  exit status: $AUTH_RC"
    echo "  output:      ${OUT:0:400}"
  fi
  echo
  if [ "$CONTAIN_RC" -eq 0 ] && [ "${AUTH_RC:-1}" -eq 0 ]; then
    echo "RESULT: PASS — containment holds and Claude authenticated from inside it."
  else
    echo "RESULT: NOT READY — containment=$CONTAIN_RC claude-auth=${AUTH_RC:-1}."
    echo "Report both numbers and the output above; do not enable the worker yet."
  fi
  echo "Nothing was fetched, pushed, deployed or changed by this probe."
  exit 0
fi

# ── kill switch (independent of the production deployer) ─────────────────
[ -e "$AGENT_DIR/DISABLED" ] && noop "kill switch present ($AGENT_DIR/DISABLED)"
[ -e "$AGENT_DIR/ENABLED" ] || noop "not enabled — create $AGENT_DIR/ENABLED to allow autonomous runs"

# ── single flight ────────────────────────────────────────────────────────
exec 9>"$AGENT_DIR/worker.lock"
flock -n 9 || noop "another worker run holds the lock"

# ── repository location (production checkout used READ-ONLY) ─────────────
REPO_URL="${FRANKENSTEIN_REPO_URL:-$(git -C "$PROD_DIR" remote get-url origin 2>/dev/null)}"
[ -n "$REPO_URL" ] || fail "cannot determine repository URL (set FRANKENSTEIN_REPO_URL)"

case "$CLONE_REAL/" in "$PROD_REAL"/*)
  fail "isolation violation: agent clone $CLONE_REAL is inside the production checkout" ;;
esac
[ "$CLONE_REAL" = "$PROD_REAL" ] && fail "isolation violation: agent clone is the production checkout"

# Git in the agent clone, with the child's control plane defused: no hooks, no
# fsmonitor, no pack-objects hook, whatever its .git/config was left saying.
agit() { git -c core.hooksPath=/dev/null -c core.fsmonitor= -c uploadpack.packObjectsHook= \
             -C "$AGENT_CLONE" "$@"; }

if [ ! -d "$AGENT_CLONE/.git" ]; then
  log "cloning into isolated agent clone $AGENT_CLONE"
  git clone --quiet "$REPO_URL" "$AGENT_CLONE" || fail "clone failed"
fi
agit remote add origin "$REPO_URL" 2>/dev/null || agit remote set-url origin "$REPO_URL" 2>/dev/null
agit fetch --prune --quiet origin || fail "fetch failed — control state unknown"

CONTROL_COMMIT="$(agit rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
[ -n "$CONTROL_COMMIT" ] || noop "control branch 'origin/$CONTROL_BRANCH' not found"

ctl_file() { agit show "$CONTROL_COMMIT:.frankenstein/$1" 2>/dev/null; }
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

# ── the directive must exist and name exactly this task ──────────────────
DIRECTIVE_TEXT="$(ctl_file PRODUCT_DIRECTIVE.md)"
[ -n "$DIRECTIVE_TEXT" ] || noop "control carries no PRODUCT_DIRECTIVE.md — nothing authoritative to implement"
DIRECTIVE_CHECK="$(printf '%s' "$DIRECTIVE_TEXT" | python3 -c "
import re, sys
text = sys.stdin.read()
ids = re.findall(r'(?mi)^[ \t]*Task[ \t]*ID[ \t]*:[ \t]*(.*?)[ \t]*\$', text)
if not ids:
    print('INVALID|PRODUCT_DIRECTIVE.md has no \"Task ID:\" line'); raise SystemExit
if len(set(ids)) > 1:
    print('INVALID|PRODUCT_DIRECTIVE.md names %d different task ids: %s'
          % (len(set(ids)), ', '.join(sorted(set(ids))))); raise SystemExit
tid = ids[0]
if not re.fullmatch(r'FC-[0-9]{3,}', tid):
    print('INVALID|directive task id %r is not ^FC-[0-9]{3,}\$' % tid); raise SystemExit
print('OK|%s' % tid)
" 2>/dev/null)"
case "$DIRECTIVE_CHECK" in
  OK\|*) ;;
  INVALID\|*) noop "directive rejected: ${DIRECTIVE_CHECK#INVALID|}" ;;
  *) noop "PRODUCT_DIRECTIVE.md could not be validated — invoking nothing" ;;
esac
DIRECTIVE_TASK="${DIRECTIVE_CHECK#OK|}"
[ "$DIRECTIVE_TASK" = "$TASK_ID" ] \
  || noop "directive names $DIRECTIVE_TASK but STATE.json says $TASK_ID — inconsistent, refusing to guess"

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
  if agit rev-parse --verify --quiet "origin/$TASK_BRANCH^{commit}" >/dev/null; then
    agit checkout --quiet -B "$TASK_BRANCH" "origin/$TASK_BRANCH" \
      || fail "could not resume $TASK_BRANCH"
    RESUMED_AT="$(agit rev-parse HEAD)"
    log "resuming existing implementation at ${RESUMED_AT:0:7}"
    if [ -n "$PRIOR_IMPL" ]; then
      if ! agit merge-base --is-ancestor "$PRIOR_IMPL" HEAD 2>/dev/null; then
        fail "control names implementation_commit ${PRIOR_IMPL:0:7} but it is not in $TASK_BRANCH history — refusing to continue on a divergent branch"
      fi
    fi
  elif [ -n "$PRIOR_IMPL" ]; then
    agit checkout --quiet -B "$TASK_BRANCH" "$PRIOR_IMPL" \
      || fail "could not resume from implementation_commit ${PRIOR_IMPL:0:7}"
    log "resuming from implementation_commit ${PRIOR_IMPL:0:7}"
  else
    fail "status=changes_requested but neither origin/$TASK_BRANCH nor implementation_commit exists — nothing to continue"
  fi
else
  agit checkout --quiet -B "$TASK_BRANCH" "origin/$PROD_BRANCH" \
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
agit add .frankenstein >/dev/null 2>&1
if ! agit diff --cached --quiet; then
  agit -c user.name="Claude Worker" -c user.email="noreply@anthropic.com" \
    commit --quiet -m "[CLAUDE] $TASK_ID authoritative control snapshot ${CONTROL_COMMIT:0:7}" \
    || fail "could not commit the control snapshot"
  log "materialized control snapshot from ${CONTROL_COMMIT:0:7}"
fi

RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN_LOG="$AGENT_DIR/run-$TASK_ID-$RUN_ID.log"
STARTED="$(date -Is)"
BASELINE="$(agit rev-parse HEAD)"

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

# ── entering the child zone ──────────────────────────────────────────────
# No remote for the duration of the run: the child has nothing to push to.
agit remote remove origin 2>/dev/null
# A trusted copy of the git control plane, taken while it is still ours. It is
# restored before ANY git command runs in this clone again.
TRUSTED_CONFIG="$AGENT_DIR/agent-git-config.trusted"
cp -f "$AGENT_CLONE/.git/config" "$TRUSTED_CONFIG" || fail "could not snapshot the clone's git config"
rm -rf "$AGENT_CLONE/.git/hooks"; mkdir -p "$AGENT_CLONE/.git/hooks"

# Restore the control plane the child was free to rewrite. Pure file
# operations — no git command runs in that clone until this has happened.
resanitize_clone() {
  cp -f "$TRUSTED_CONFIG" "$AGENT_CLONE/.git/config" 2>/dev/null
  rm -rf "$AGENT_CLONE/.git/hooks"; mkdir -p "$AGENT_CLONE/.git/hooks"
  rm -f "$AGENT_CLONE/.git/config.worktree" 2>/dev/null
}

prepare_child_home
if sandbox_available; then
  SEEN_HOME="$REAL_HOME"
elif [ "$ALLOW_UNSANDBOXED" = "1" ]; then
  SEEN_HOME="$CHILD_HOME"
else
  record_run "no_sandbox" ""
  fail "no user-namespace sandbox available on this host — refusing to run a child unconfined. Enable unprivileged user namespaces, or set FRANKENSTEIN_ALLOW_UNSANDBOXED=1 to accept the loss of the child boundary."
fi
[ -n "$WORKSPACE_MNT" ] || { record_run "no_workspace_mount" ""
  fail "no usable workspace mountpoint (/mnt, /media, /srv) — refusing to run without one"; }
build_child_env "$SEEN_HOME"
WS_SRC="$AGENT_CLONE"

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
  if sandbox_available; then
    run_sandboxed "${CHILD_ENV[@]}" /bin/bash -c "$RUNNER" >>"$RUN_LOG" 2>&1
    CLAUDE_RC=$?
  else
    log "WARNING: running the mock UNSANDBOXED (FRANKENSTEIN_ALLOW_UNSANDBOXED=1)"
    ( cd "$AGENT_CLONE" && "${CHILD_ENV[@]}" /bin/bash -c "$RUNNER" ) >>"$RUN_LOG" 2>&1
    CLAUDE_RC=$?
  fi
else
  command -v "$CLAUDE_BIN" >/dev/null 2>&1 || fail "Claude CLI '$CLAUDE_BIN' not found"
  log "invoking Claude sandboxed (timeout ${MAX_RUNTIME}s); log: $RUN_LOG"
  if sandbox_available; then
    run_sandboxed "${CHILD_ENV[@]}" \
      timeout --signal=TERM --kill-after=60 "$MAX_RUNTIME" \
      "$CLAUDE_BIN" -p "$PROMPT" \
      --permission-mode acceptEdits --add-dir "$WORKSPACE_MNT" >>"$RUN_LOG" 2>&1
    CLAUDE_RC=$?
  else
    log "WARNING: running Claude UNSANDBOXED (FRANKENSTEIN_ALLOW_UNSANDBOXED=1)"
    ( cd "$AGENT_CLONE" && "${CHILD_ENV[@]}" \
        timeout --signal=TERM --kill-after=60 "$MAX_RUNTIME" \
        "$CLAUDE_BIN" -p "$PROMPT" \
        --permission-mode acceptEdits --add-dir "$AGENT_CLONE" ) >>"$RUN_LOG" 2>&1
    CLAUDE_RC=$?
  fi
fi

# ── leaving the child zone ───────────────────────────────────────────────
resanitize_clone

[ "$CLAUDE_RC" -eq 97 ] && { record_run "sandbox_failed" ""; fail "the sandbox could not be established — nothing ran"; }
if [ "$CLAUDE_RC" -ne 0 ]; then
  record_run "claude_failed" ""
  fail "Claude exited $CLAUDE_RC — no handoff published, production untouched. Log: $RUN_LOG"
fi

# ── the run must have produced real work ─────────────────────────────────
agit rev-parse --verify --quiet "$TASK_BRANCH" >/dev/null \
  || { record_run "no_branch" ""; fail "task branch vanished"; }
if [ -z "$(agit log "$BASELINE..$TASK_BRANCH" --oneline 2>/dev/null)" ]; then
  record_run "no_commits" ""
  fail "the run produced no commits — refusing to publish an empty handoff"
fi

# ── independent verification, INSIDE the same containment ────────────────
# scripts/test.sh is a file Claude may edit. Running it on the host would hand
# a compromised run arbitrary code execution as the deploying user, so it runs
# behind the same masks the child had.
log "re-running the test suite independently, inside the sandbox"
if sandbox_available; then
  run_sandboxed "${CHILD_ENV[@]}" /bin/bash -c 'bash scripts/test.sh' >>"$RUN_LOG" 2>&1 \
    || { record_run "tests_failed" ""; fail "tests fail on the produced branch — no handoff published. Log: $RUN_LOG"; }
else
  ( cd "$AGENT_CLONE" && "${CHILD_ENV[@]}" /bin/bash -c 'bash scripts/test.sh' ) >>"$RUN_LOG" 2>&1 \
    || { record_run "tests_failed" ""; fail "tests fail on the produced branch — no handoff published. Log: $RUN_LOG"; }
fi

IMPL_COMMIT="$(agit rev-parse "$TASK_BRANCH")"

# ── concurrency token, stage 1 ───────────────────────────────────────────
CONTROL_NOW="$(git ls-remote "$REPO_URL" "refs/heads/$CONTROL_BRANCH" 2>/dev/null | awk 'NR==1{print $1}')"
[ -n "$CONTROL_NOW" ] || { record_run "fetch_failed" ""; fail "could not re-read control before publishing"; }
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

# ── the publisher zone: clones the child never touched ───────────────────
# The child could have planted a pre-push hook or rewritten .git/config in its
# workspace. Nothing from there is executed here: this is a fresh clone from
# origin, and the implementation commit is imported as data.
install_hook() {
  local hook="$1/.git/hooks/pre-push"
  mkdir -p "$(dirname "$hook")"
  cat > "$hook" <<'HOOKEOF'
#!/usr/bin/env bash
# Installed by claude-worker.sh in a clone the child never had access to.
#   1. production/main/master may never be pushed from here
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
  if [ -n "$remote_sha" ] && [ "$remote_sha" != "$ZERO" ] && [ "$local_sha" != "$ZERO" ]; then
    if ! git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
      echo "pre-push: REFUSED — non-fast-forward push to $remote_ref would discard history" >&2
      status=1
    fi
  fi
done
exit $status
HOOKEOF
  chmod +x "$hook"
}

PUB_DIR="$AGENT_DIR/publisher"
rm -rf "$PUB_DIR"
git clone --quiet --no-checkout "$REPO_URL" "$PUB_DIR" \
  || { record_run "publisher_clone_failed" ""; fail "could not create the publisher clone"; }
install_hook "$PUB_DIR"

# Import the commit as data. -c uploadpack.packObjectsHook= disarms the one
# config knob in the child's clone that the fetch side would otherwise honour.
git -c uploadpack.packObjectsHook= -c core.hooksPath=/dev/null \
    -C "$PUB_DIR" fetch --quiet --no-tags "$AGENT_CLONE" \
    "refs/heads/$TASK_BRANCH:refs/frankenstein/impl" \
  || { record_run "impl_import_failed" ""; fail "could not import the implementation commit into the publisher"; }

FETCHED="$(git -C "$PUB_DIR" rev-parse refs/frankenstein/impl)"
[ "$FETCHED" = "$IMPL_COMMIT" ] || {
  record_run "impl_mismatch" ""
  fail "imported ${FETCHED:0:7} but verification ran on ${IMPL_COMMIT:0:7} — refusing to publish"; }
git -C "$PUB_DIR" merge-base --is-ancestor "$BASELINE" refs/frankenstein/impl \
  || { record_run "impl_not_descendant" ""
       fail "the implementation does not descend from the authorized baseline ${BASELINE:0:7}"; }

git -C "$PUB_DIR" push --quiet origin "refs/frankenstein/impl:refs/heads/$TASK_BRANCH" \
  || { record_run "push_failed" ""; fail "pushing $TASK_BRANCH failed — no handoff published"; }
log "pushed $TASK_BRANCH at ${IMPL_COMMIT:0:7} from the clean publisher"

CONTROL_DIR="$AGENT_DIR/control-clone"
rm -rf "$CONTROL_DIR"
git clone --quiet --branch "$CONTROL_BRANCH" --single-branch "$REPO_URL" "$CONTROL_DIR" \
  || { record_run "control_clone_failed" ""; fail "could not clone the control branch"; }
install_hook "$CONTROL_DIR"
git -C "$CONTROL_DIR" fetch --quiet origin "$CONTROL_BRANCH" \
  || { record_run "control_fetch_failed" ""; fail "could not fetch control before publishing"; }

# ── concurrency token, stage 2 ───────────────────────────────────────────
# Between stage 1 and here the Product Owner may have moved control — the task
# branch push sits in that window. Re-check, and reset to the AUTHORIZING
# commit explicitly rather than to whatever origin now points at: resetting
# onto origin would rebase this run's stale state on top of newer Product
# Owner state and then fast-forward cleanly over it.
CONTROL_AT_PUBLISH="$(git -C "$CONTROL_DIR" rev-parse --verify --quiet "origin/$CONTROL_BRANCH^{commit}")"
[ "$CONTROL_AT_PUBLISH" = "$CONTROL_COMMIT" ] || {
  record_run "control_conflict_late" ""
  fail "control moved to ${CONTROL_AT_PUBLISH:0:7} before publication (authorized ${CONTROL_COMMIT:0:7}). Newer Product Owner state preserved; no handoff published."; }
git -C "$CONTROL_DIR" reset --hard --quiet "$CONTROL_COMMIT" \
  || { record_run "control_reset_failed" ""; fail "could not reset the control clone to the authorizing commit"; }

# The handoff content is read from the PUBLISHER, not from the child's clone.
for f in STATE.json IMPLEMENTATION_HANDOFF.md; do
  git -C "$PUB_DIR" show "refs/frankenstein/impl:.frankenstein/$f" \
    > "$CONTROL_DIR/.frankenstein/$f" 2>/dev/null
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
