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
# ── THE ONE RULE THAT SHAPES EVERYTHING BELOW ────────────────────────────
# ONCE THE CHILD HAS RUN, THE TRUSTED HOST NEVER EXECUTES A COMMAND AGAINST,
# NOR WRITES THROUGH, THE CHILD'S WORKSPACE — INCLUDING ITS .git.
#
# The child may replace .git, .git/config or any descendant with a symlink
# pointing anywhere on the host, so "sanitizing" that tree from the host is
# itself the vulnerability. Instead the workspace is EXPORTED from inside the
# sandbox as an inert artifact — a git bundle plus a small manifest, written
# into a directory the child never saw — and the host consumes only that.
#
# The run is therefore four separate sandbox invocations, each in its own PID
# namespace so nothing survives between them:
#
#   A. the child (Claude, or a mock)
#   B. structure check   — fixed git commands only
#   C. verification      — scripts/test.sh, which the child may have edited
#   D. export            — fixed git commands only, writing the bundle out
#
# Publication happens on the host from FRESH clones of origin, fed only by the
# artifact from D.
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
# Read-only exposures: the CLI's own authentication material. The install
# directories of the CLI and its interpreter are detected and added to this
# automatically — a CLI installed under the home directory (npm --prefix, nvm,
# asdf) would otherwise be hidden by the home mask and simply not exist for the
# child. Colon-separated absolute paths.
CLAUDE_EXPOSE="${FRANKENSTEIN_CLAUDE_EXPOSE:-$HOME/.claude/.credentials.json}"
# Copied WRITABLE into the scratch home instead of bind-mounted read-only.
# ~/.claude.json is configuration the CLI rewrites on startup, so a read-only
# bind would break it; the child gets a throwaway copy and the host's file is
# never touched by the run.
CLAUDE_WRITABLE="${FRANKENSTEIN_CLAUDE_WRITABLE:-$HOME/.claude.json}"
# Environment forwarded to the child: Claude authentication only. No GitHub
# token, no SSH agent, nothing else.
CLAUDE_ENV_KEYS="${FRANKENSTEIN_CLAUDE_ENV:-ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL ANTHROPIC_MODEL CLAUDE_CODE_OAUTH_TOKEN CLAUDE_CONFIG_DIR}"
# Outbound egress allowlist for the Claude invocation ONLY. Everything else
# runs with no egress at all. host:port entries, comma separated.
EGRESS_ALLOW="${FRANKENSTEIN_EGRESS_ALLOW:-api.anthropic.com:443}"
EGRESS_PORT="${FRANKENSTEIN_EGRESS_PORT:-8118}"
HERE="$(cd "$(dirname "$0")" && pwd)"
EGRESS_PROXY_BIN="${FRANKENSTEIN_EGRESS_PROXY:-$HERE/agent/egress-proxy.py}"
EGRESS_RELAY_BIN="${FRANKENSTEIN_EGRESS_RELAY:-$HERE/agent/egress-relay.py}"

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
CHILD_HOME="$AGENT_DIR/child-home"
# A SECOND scratch home for the structure/verification/export invocations. The
# child never had access to it, so no Claude credential — staged or copied —
# can be present when child-controlled code runs during verification.
TOOL_HOME="$AGENT_DIR/tool-home"
EXPORT_DIR="$AGENT_DIR/export"

# ── containment plumbing ─────────────────────────────────────────────────
# `contains $a $b` is true when $b is $a or lives inside it.
contains() { [ "$1" = "$2" ] && return 0; case "$2/" in "$1"/*) return 0 ;; esac; return 1; }

# Two neutral mountpoints OUTSIDE the home: one for the workspace, one for the
# export directory. They must be outside the home because the home is about to
# be replaced wholesale. Prefer empty conventional mountpoints so nothing real
# is shadowed.
MOUNT_CANDIDATES="${FRANKENSTEIN_MOUNTPOINTS:-/mnt /media /srv}"
pick_mountpoints() {
  local c empty="" any=""
  for c in $MOUNT_CANDIDATES; do
    [ -d "$c" ] || continue
    contains "$c" "$REAL_HOME" && continue
    any="$any $c"
    [ -z "$(ls -A "$c" 2>/dev/null)" ] && empty="$empty $c"
  done
  set -- $empty $any
  WORKSPACE_MNT="${1:-}"; shift 2>/dev/null || true
  for c in "$@"; do [ "$c" != "$WORKSPACE_MNT" ] && { EXPORT_MNT="$c"; return; }; done
  EXPORT_MNT=""
}
WORKSPACE_MNT=""; EXPORT_MNT=""
pick_mountpoints

# Shared runtime/temp locations get a private tmpfs. /run matters most: on this
# host Docker is usable without sudo, so /run/docker.sock is host-control
# capability. Masking /run as a whole beats enumerating sockets forever.
build_mask_list() {
  local c out=""
  for c in /run /tmp /var/tmp; do
    [ -d "$c" ] || continue
    contains "$c" "$REAL_HOME"  && continue
    contains "$c" "$PROD_REAL"  && continue
    contains "$c" "$CLONE_REAL" && continue
    contains "$c" "$AGENT_DIR"  && continue
    [ -n "$WORKSPACE_MNT" ] && contains "$c" "$WORKSPACE_MNT" && continue
    [ -n "$EXPORT_MNT" ]    && contains "$c" "$EXPORT_MNT"    && continue
    out="$out $c"
  done
  echo "${out# }"
}
MASK_PATHS="$(build_mask_list)"

# The scratch home. Bind-mounted OVER the real home inside the namespace, so
# every absolute path under ~ resolves into this directory instead.
# Where an executable is installed, when that is inside the home directory.
# For ~/.npm-global/bin/claude that is ~/.npm-global; for an nvm node it is the
# version directory. Both hold bin/ and lib/node_modules/, which is what the
# CLI needs to run at all. Nothing under either is credential material.
install_root_for() {
  local link real root
  link="$(command -v "$1" 2>/dev/null)" || return 0
  [ -n "$link" ] || return 0
  if contains "$REAL_HOME" "$link"; then
    root="$(dirname "$(dirname "$link")")"
    [ "$root" = "$REAL_HOME" ] && root="$(dirname "$link")"
    contains "$REAL_HOME" "$root" && [ "$root" != "$REAL_HOME" ] && echo "$root"
    return 0
  fi
  real="$(abspath "$link")"
  if contains "$REAL_HOME" "$real"; then
    root="$(dirname "$(dirname "$real")")"
    contains "$REAL_HOME" "$root" && [ "$root" != "$REAL_HOME" ] && echo "$root"
  fi
}

# Add the CLI and its interpreter to the read-only exposure list. Without this
# the home mask hides the very binary the child is meant to run.
AUTO_EXPOSED=""
autodetect_claude_install() {
  local exe root
  AUTO_EXPOSED=""
  for exe in "$CLAUDE_BIN" node; do
    root="$(install_root_for "$exe")"
    [ -n "$root" ] || continue
    case ":$CLAUDE_EXPOSE:$AUTO_EXPOSED:" in *":$root:"*) continue ;; esac
    AUTO_EXPOSED="$AUTO_EXPOSED:$root"
  done
  AUTO_EXPOSED="${AUTO_EXPOSED#:}"
  [ -n "$AUTO_EXPOSED" ] && CLAUDE_EXPOSE="$CLAUDE_EXPOSE:$AUTO_EXPOSED"
  return 0
}
autodetect_claude_install

EXPOSE_LIST=""
RESOLV_STAGE=""
stage_resolv_conf() {
  RESOLV_STAGE=""
  if [ -e /etc/resolv.conf ] \
     && cp -L /etc/resolv.conf "$AGENT_DIR/resolv.conf.staged" 2>/dev/null; then
    RESOLV_STAGE="$AGENT_DIR/resolv.conf.staged"
  fi
}

# prepare_stage <dir> <expose?>
# Builds a scratch home that will be bind-mounted over the real one. Claude's
# credential paths are staged ONLY when asked for — that is, only for the child.
prepare_stage() {
  local stage="$1" with_creds="$2" p rel
  rm -rf "$stage"
  mkdir -p "$stage/tmp" "$stage/.cache"
  chmod 700 "$stage"
  EXPOSE_LIST=""
  [ "$with_creds" = "creds" ] || return 0
  IFS=':' read -r -a _expose <<<"$CLAUDE_EXPOSE"
  for p in "${_expose[@]:-}"; do
    [ -n "$p" ] || continue
    [ -e "$p" ] || continue
    contains "$REAL_HOME" "$p" || continue      # only home paths need staging
    rel="${p#$REAL_HOME/}"
    if [ -d "$p" ]; then
      mkdir -p "$stage/$rel"
    else
      mkdir -p "$(dirname "$stage/$rel")"; : > "$stage/$rel"
    fi
    EXPOSE_LIST="$EXPOSE_LIST $p|$stage/$rel"
  done
  EXPOSE_LIST="${EXPOSE_LIST# }"
  # writable throwaway copies of the configuration the CLI rewrites
  IFS=':' read -r -a _writable <<<"$CLAUDE_WRITABLE"
  for p in "${_writable[@]:-}"; do
    [ -n "$p" ] || continue
    [ -e "$p" ] || continue
    contains "$REAL_HOME" "$p" || continue
    rel="${p#$REAL_HOME/}"
    mkdir -p "$(dirname "$stage/$rel")"
    cp -a "$p" "$stage/$rel" 2>/dev/null || continue
    chmod u+w "$stage/$rel" 2>/dev/null
  done
}

# A PID namespace is part of the credential boundary, not a nicety: without it
# the child can read /proc/<pid>/environ of the orchestrator and every other
# process this user owns, and env -i buys nothing.
sandbox_available() {
  unshare --user --map-root-user --mount --pid --fork --mount-proc --net true >/dev/null 2>&1
}

# Run "$@" inside the containment boundary. WS_SRC is bind-mounted at
# WORKSPACE_MNT and becomes the working directory. EXPORT_SRC, when set, is
# bind-mounted at EXPORT_MNT — it is set ONLY for the export invocation, so the
# child never sees the directory its work will be exported into.
WS_SRC=""
EXPORT_SRC=""
STAGE_HOME=""
NET_SOCK=""
run_sandboxed() {
  FCS_PROD="$PROD_REAL" FCS_WS_SRC="$WS_SRC" FCS_WS_MNT="$WORKSPACE_MNT" \
  FCS_EXPORT_SRC="$EXPORT_SRC" FCS_EXPORT_MNT="$EXPORT_MNT" \
  FCS_HOME_STAGE="$STAGE_HOME" FCS_REAL_HOME="$REAL_HOME" \
  FCS_MASK="$MASK_PATHS" FCS_EXPOSE="$EXPOSE_LIST" FCS_RESOLV="$RESOLV_STAGE" \
  FCS_NET_HELPER="$REAL_HOME/.egress-relay.py" FCS_NET_SOCK="$NET_SOCK" \
  FCS_NET_PORT="$EGRESS_PORT" \
  unshare --user --map-root-user --mount --pid --fork --mount-proc --net -- /bin/bash -c '
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
    # the export directory, present only for the export invocation
    if [ -n "$FCS_EXPORT_SRC" ]; then
      [ -n "$FCS_EXPORT_MNT" ] || die "no usable export mountpoint"
      mount --bind "$FCS_EXPORT_SRC" "$FCS_EXPORT_MNT" 2>/dev/null \
        || die "cannot bind the export directory"
    fi
    # narrow read-only exposures, staged before the home disappears
    for e in $FCS_EXPOSE; do
      src="${e%%|*}"; dst="${e#*|}"
      mount --bind "$src" "$dst" 2>/dev/null || die "cannot expose $src"
      mount -o remount,bind,ro "$dst" 2>/dev/null || die "cannot make $src read-only"
    done
    # keep DNS working across the /run mask
    if [ -n "$FCS_RESOLV" ] && [ -e /etc/resolv.conf ]; then
      mount --bind "$FCS_RESOLV" /etc/resolv.conf 2>/dev/null
      mount -o remount,bind,ro /etc/resolv.conf 2>/dev/null
    fi
    # THE REAL HOME DISAPPEARS: gh credentials, ssh keys, ~/.frankenstein,
    # shell configuration, the production checkout, the agent directory.
    mount --rbind "$FCS_HOME_STAGE" "$FCS_REAL_HOME" 2>/dev/null \
      || die "cannot mask the home directory $FCS_REAL_HOME"
    # /run carries the docker socket and other host-control sockets; /tmp and
    # /var/tmp are shared scratch. Private tmpfs for each.
    for m in $FCS_MASK; do
      [ -d "$m" ] || continue
      mount -t tmpfs none "$m" 2>/dev/null || die "cannot mask $m"
    done
    # THE NETWORK NAMESPACE IS PRIVATE AND EMPTY. Loopback is brought up
    # inside it, so 127.0.0.1 is the sandbox own loopback and reaches nothing
    # on the OptiPlex; there is no route to the LAN, to link-local ranges, or
    # to anything else. Only the child gets a way out, and only through the
    # UNIX socket the host egress proxy owns.
    # env -i for the helper too: it lives in the sandbox PID namespace, so
    # anything it inherited would be readable at /proc/<pid>/environ by the
    # very process this boundary exists to contain. (The probe caught this.)
    if [ -n "$FCS_NET_SOCK" ]; then
      env -i PATH="$PATH" python3 "$FCS_NET_HELPER" \
        --socket "$FCS_NET_SOCK" --port "$FCS_NET_PORT" >/dev/null 2>&1 &
      for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        ( exec 3<>"/dev/tcp/127.0.0.1/$FCS_NET_PORT" ) 2>/dev/null && break
        sleep 0.2
      done
    else
      env -i PATH="$PATH" python3 "$FCS_NET_HELPER" --loopback-only >/dev/null 2>&1 || true
    fi
    cd "$FCS_WS_MNT" 2>/dev/null || die "cannot enter the workspace"
    exec "$@"
  ' _ "$@"
}

# The child's environment. env -i so nothing is inherited: no GITHUB_TOKEN,
# no GH_TOKEN, no SSH_AUTH_SOCK, no git identity, no interactive credential
# path. Only the explicitly listed Claude authentication variables are added.
build_env() {
  local seen_home="$1" with_creds="$2" k v
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
  [ "$with_creds" = "creds" ] || return 0
  for k in $CLAUDE_ENV_KEYS; do
    v="${!k:-}"
    [ -n "$v" ] && CHILD_ENV+=("$k=$v")
  done
  # the only way out of the private network namespace
  local proxy="http://127.0.0.1:$EGRESS_PORT"
  CHILD_ENV+=("HTTPS_PROXY=$proxy" "https_proxy=$proxy"
              "HTTP_PROXY=$proxy" "http_proxy=$proxy"
              "ALL_PROXY=$proxy" "all_proxy=$proxy"
              "GLOBAL_AGENT_HTTPS_PROXY=$proxy"
              "NO_PROXY=" "no_proxy=")
}

# The host half of the narrow egress channel. It owns a UNIX socket inside the
# child scratch home and speaks HTTP CONNECT over it, refusing any target that
# is not on the allowlist or that resolves to a loopback, private, link-local
# or reserved address. Started only for the Claude invocation.
EGRESS_PID=""
start_egress() {
  NET_SOCK=""
  cp -f "$EGRESS_RELAY_BIN" "$CHILD_HOME/.egress-relay.py" 2>/dev/null \
    || { log "egress: could not stage the relay"; return 1; }
  chmod 0500 "$CHILD_HOME/.egress-relay.py" 2>/dev/null
  python3 "$EGRESS_PROXY_BIN" --socket "$CHILD_HOME/.egress.sock" \
    --allow "$EGRESS_ALLOW" >>"${RUN_LOG:-$LOG}" 2>&1 &
  EGRESS_PID=$!
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    [ -S "$CHILD_HOME/.egress.sock" ] && { NET_SOCK="$REAL_HOME/.egress.sock"; return 0; }
    sleep 0.2
  done
  log "egress: the proxy did not come up"
  return 1
}
stop_egress() {
  [ -n "$EGRESS_PID" ] && kill "$EGRESS_PID" 2>/dev/null
  EGRESS_PID=""; NET_SOCK=""
}

# Tool invocations (structure, verification, export) get a scratch home the
# child never saw, no Claude credential, and no egress whatsoever.
stage_for_tools() {
  prepare_stage "$TOOL_HOME" nocreds
  STAGE_HOME="$TOOL_HOME"
  NET_SOCK=""
  cp -f "$EGRESS_RELAY_BIN" "$TOOL_HOME/.egress-relay.py" 2>/dev/null
  chmod 0500 "$TOOL_HOME/.egress-relay.py" 2>/dev/null
  build_env "$1" nocreds
}

# ── --probe: prove containment and Claude auth BEFORE activation ─────────
if [ "$MODE" = "probe" ]; then
  PROBE_FAIL=0
  check() {  # check <label> <ok?>
    if [ "$2" = "0" ]; then printf '  PASS  %s\n' "$1"
    else printf '  FAIL  %s\n' "$1"; PROBE_FAIL=1; fi
  }
  echo "FrankensteinCentral autonomous-worker containment probe"
  echo "  home:               $REAL_HOME"
  echo "  workspace mount:    ${WORKSPACE_MNT:-<none found>}"
  echo "  export mount:       ${EXPORT_MNT:-<none found>}"
  echo "  masked locations:   ${MASK_PATHS:-<none>}"
  echo "  claude binary:      $(command -v "$CLAUDE_BIN" 2>/dev/null || echo '<not found>')"
  echo "  auto-exposed install roots: ${AUTO_EXPOSED:-<none needed>}"
  if [ -n "${ANTHROPIC_API_KEY:-}${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "  claude credential:          dedicated API key from the environment"
    echo "                              (long-lived; not tied to the interactive"
    echo "                              OAuth session lifecycle. Still revocable,"
    echo "                              and still rotatable by a human.)"
  elif [ -e "$REAL_HOME/.claude/.credentials.json" ]; then
    echo "  claude credential:          the host interactive OAuth session (EXPIRES)"
  else
    echo "  claude credential:          none found"
  fi
  echo "  writable config copies:     $CLAUDE_WRITABLE"
  echo "  exposure candidates:"
  IFS=':' read -r -a _probe_expose <<<"$CLAUDE_EXPOSE"
  for p in "${_probe_expose[@]:-}"; do
    [ -n "$p" ] || continue
    echo "    $p  $([ -e "$p" ] && echo present || echo absent)"
  done
  echo
  echo "-- namespace availability --"
  unshare --user true >/dev/null 2>&1; check "user namespace" $?
  unshare --user --map-root-user --mount true >/dev/null 2>&1; check "mount namespace" $?
  unshare --user --map-root-user --mount --pid --fork --mount-proc true >/dev/null 2>&1
  check "PID namespace / private proc" $?
  unshare --user --map-root-user --net true >/dev/null 2>&1
  check "network namespace" $?
  [ -n "$WORKSPACE_MNT" ]; check "workspace mountpoint available" $?
  [ -n "$EXPORT_MNT" ];    check "export mountpoint available" $?
  if [ "$PROBE_FAIL" != "0" ]; then
    echo
    echo "RESULT: NOT READY — the host cannot provide the required namespaces."
    exit 1
  fi

  stage_resolv_conf
  prepare_stage "$CHILD_HOME" creds
  STAGE_HOME="$CHILD_HOME"
  PROBE_WS="$(mktemp -d "$AGENT_DIR/probe-XXXXXX")" || { echo "RESULT: NOT READY — no temp dir"; exit 1; }
  PROBE_OUT="$(mktemp -d "$AGENT_DIR/probeout-XXXXXX")" || { echo "RESULT: NOT READY — no temp dir"; exit 1; }
  trap 'rm -rf "$PROBE_WS" "$PROBE_OUT"' EXIT
  # a decoy in the host's runtime area, and a secret in this process, so the
  # containment checks are measured rather than asserted
  PROBE_SOCK="$AGENT_DIR/probe-fake.sock"
  python3 -c "
import socket, sys, os
p = sys.argv[1]
try: os.unlink(p)
except OSError: pass
s = socket.socket(socket.AF_UNIX); s.bind(p)
" "$PROBE_SOCK" 2>/dev/null || PROBE_SOCK=""
  # decoy listeners on the host: one on loopback, one on the OptiPlex own LAN
  # address, so LAN isolation is measured rather than asserted
  PROBE_LISTENERS="$(python3 - <<'PYL'
import os, socket, sys, threading, time

def serve(host):
    """A decoy listener the sandbox must not be able to reach."""
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        s.listen(4)
    except OSError:
        return None, None
    return s, "%s:%d" % (host, s.getsockname()[1])

def lan_address():
    try:
        p = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        p.connect(("8.8.8.8", 53))
        addr = p.getsockname()[0]
        p.close()
        return addr
    except OSError:
        return ""

hosts = ["127.0.0.1"]
lan = lan_address()
if lan and lan != "127.0.0.1":
    hosts.append(lan)
socks, names = [], []
for h in hosts:
    sock, name = serve(h)
    if sock:
        socks.append(sock)
        names.append(name)
if not socks:
    sys.exit(0)
pid = os.fork()
if pid:
    print(" ".join(names) + " pid=%d" % pid)
    sys.exit(0)
# The child must not hold the command substitution pipe open.
devnull = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(devnull, fd)
def accept_forever(s):
    while True:
        try:
            c, _ = s.accept()
            c.close()
        except OSError:
            return
for s in socks:
    threading.Thread(target=accept_forever, args=(s,), daemon=True).start()
time.sleep(180)
PYL
)"
  PROBE_LOOPBACK="$(printf '%s' "$PROBE_LISTENERS" | tr ' ' '\n' | grep '^127\.' | head -1)"
  PROBE_LAN="$(printf '%s' "$PROBE_LISTENERS" | tr ' ' '\n' | grep -v '^127\.' | grep ':' | head -1)"
  PROBE_HELPER_PID="$(printf '%s' "$PROBE_LISTENERS" | tr ' ' '\n' | sed -n 's/^pid=//p')"
  WS_SRC="$PROBE_WS"
  EXPORT_SRC="$PROBE_OUT"
  build_env "$REAL_HOME" creds
  start_egress || echo "  FAIL  narrow egress channel could not be started"
  echo
  echo "-- containment, measured from inside the sandbox --"
  FRANKENSTEIN_PROBE_SECRET="probe_secret_$$_do_not_leak" \
  run_sandboxed "${CHILD_ENV[@]}" \
    FRANKENSTEIN_PROBE_PROD="$PROD_REAL" \
    FRANKENSTEIN_PROBE_EXPORT="$EXPORT_MNT" \
    FRANKENSTEIN_PROBE_LOOPBACK="$PROBE_LOOPBACK" \
    FRANKENSTEIN_PROBE_LAN="$PROBE_LAN" \
    /bin/bash -c '
      rc=0
      say() { if [ "$2" = 0 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; rc=1; fi; }
      grep -qs "probe_secret_" /proc/*/environ; [ $? -ne 0 ]
      say "host process environment inaccessible via /proc" $?
      [ "$(ls /proc | grep -c "^[0-9][0-9]*$")" -lt 12 ]
      say "private /proc shows only sandbox processes" $?
      [ ! -e /run/docker.sock ] && [ ! -e /var/run/docker.sock ]
      say "docker socket unavailable" $?
      # util-linux writes its own /run/mount bookkeeping into this fresh
      # tmpfs, so emptiness is the wrong test. The right test is: /run is a
      # private tmpfs carrying none of the host runtime state.
      grep -q "^[^ ][^ ]* /run tmpfs " /proc/self/mounts
      say "host /run replaced by a private tmpfs" $?
      [ -z "$(find /run -xdev ! -path /run ! -path "/run/mount*" -print -quit 2>/dev/null)" ]
      say "no host runtime state visible under /run" $?
      [ -z "$(find /run /var/run -xdev -type s -print -quit 2>/dev/null)" ]
      say "no host runtime sockets reachable" $?
      [ ! -e "$HOME/.config/gh/hosts.yml" ] && [ ! -d "$HOME/.ssh" ] \
        && [ ! -e "$HOME/.gitconfig" ] && [ ! -d "$HOME/.frankenstein" ]
      say "real home hidden (gh, ssh, gitconfig, .frankenstein)" $?
      [ -z "${GITHUB_TOKEN:-}${GH_TOKEN:-}${SSH_AUTH_SOCK:-}" ]
      say "no GitHub or SSH credential in the environment" $?
      ( echo ok > ./probe-write ) 2>/dev/null; say "workspace writable" $?
      ( echo ok > "$HOME/probe-write" ) 2>/dev/null; say "scratch home writable" $?
      # hidden entirely is stronger than read-only; either satisfies this
      ! ( echo x > "$FRANKENSTEIN_PROBE_PROD/.probe" ) 2>/dev/null
      say "production checkout not writable" $?
      ( echo ok > "$FRANKENSTEIN_PROBE_EXPORT/probe-export" ) 2>/dev/null
      say "export directory writable" $?
      cannot_reach() {
        [ -z "$1" ] && return 0
        h="${1%:*}"; p="${1##*:}"
        ! ( exec 3<>"/dev/tcp/$h/$p" ) 2>/dev/null
      }
      cannot_reach "$FRANKENSTEIN_PROBE_LOOPBACK"
      say "child cannot reach a host loopback listener" $?
      cannot_reach "$FRANKENSTEIN_PROBE_LAN"
      say "child cannot reach the host RFC1918 LAN address" $?
      cannot_reach "169.254.169.254:80"
      say "child cannot reach IPv4 link-local / metadata" $?
      cannot_reach "[::1]:22"
      say "child cannot reach IPv6 loopback" $?
      cannot_reach "[fe80::1]:80"
      say "child cannot reach IPv6 link-local" $?
      cannot_reach "[fc00::1]:80"
      say "child cannot reach IPv6 unique-local" $?
      exit $rc'
  CONTAIN_RC=$?
  [ "$CONTAIN_RC" = 0 ] || PROBE_FAIL=1
  [ -n "$PROBE_SOCK" ] && rm -f "$PROBE_SOCK"
  EXPORT_SRC=""
  stop_egress

  echo
  echo "-- the verification zone, where child-controlled code runs --"
  stage_for_tools "$REAL_HOME"
  run_sandboxed "${CHILD_ENV[@]}" \
    FRANKENSTEIN_PROBE_LOOPBACK="$PROBE_LOOPBACK" \
    FRANKENSTEIN_PROBE_LAN="$PROBE_LAN" \
    /bin/bash -c '
      rc=0
      say() { if [ "$2" = 0 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; rc=1; fi; }
      [ -z "${ANTHROPIC_API_KEY:-}${ANTHROPIC_AUTH_TOKEN:-}${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_BASE_URL:-}" ]
      say "verification has no Claude credential in its environment" $?
      [ -z "${HTTPS_PROXY:-}${HTTP_PROXY:-}${ALL_PROXY:-}${https_proxy:-}" ]
      say "verification has no egress proxy configured" $?
      [ ! -s "$HOME/.claude/.credentials.json" ] && [ ! -s "$HOME/.claude.json" ]
      say "verification cannot read exposed Claude credential files" $?
      [ ! -S "$HOME/.egress.sock" ]
      say "verification has no egress socket" $?
      cannot_reach() {
        [ -z "$1" ] && return 0
        h="${1%:*}"; p="${1##*:}"
        ! ( exec 3<>"/dev/tcp/$h/$p" ) 2>/dev/null
      }
      cannot_reach "$FRANKENSTEIN_PROBE_LOOPBACK"
      say "verification cannot reach a host loopback listener" $?
      cannot_reach "$FRANKENSTEIN_PROBE_LAN"
      say "verification cannot reach the host LAN address" $?
      ! ( exec 3<>/dev/tcp/api.anthropic.com/443 ) 2>/dev/null
      say "verification has no outbound network at all" $?
      exit $rc'
  VERIFY_RC=$?
  [ "$VERIFY_RC" = 0 ] || PROBE_FAIL=1

  # back to the child profile for the authentication check
  prepare_stage "$CHILD_HOME" creds
  STAGE_HOME="$CHILD_HOME"
  build_env "$REAL_HOME" creds
  WS_SRC="$PROBE_WS"
  start_egress || { echo "  FAIL  narrow egress channel could not be started"; PROBE_FAIL=1; }

  echo
  echo "-- Claude authentication, from inside the sandbox --"
  if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
    echo "  FAIL  Claude CLI '$CLAUDE_BIN' not found on PATH"
    PROBE_FAIL=1
  else
    CLAUDE_PATH="$(command -v "$CLAUDE_BIN")"
    if contains "$REAL_HOME" "$CLAUDE_PATH"; then
      root="$(install_root_for "$CLAUDE_BIN")"
      echo "  NOTE: the CLI lives under the home directory ($CLAUDE_PATH), which the"
      echo "        home mask hides. Its install root ${root:-<undetermined>} is"
      echo "        exposed read-only so the child can execute it."
    fi
    OUT="$(run_sandboxed "${CHILD_ENV[@]}" timeout 120 "$CLAUDE_BIN" -p \
            'Reply with the single word READY and nothing else.' </dev/null 2>&1)"
    AUTH_RC=$?
    if [ "$AUTH_RC" = 0 ]; then
      echo "  PASS  Claude API authentication through the permitted egress"
    else
      echo "  FAIL  Claude exited $AUTH_RC through the permitted egress"
      case "$OUT" in
        *401*|*expired*|*Re-authenticate*|*re-authenticate*|*"Invalid API key"*)
          echo "        DIAGNOSIS: the request REACHED Anthropic and was rejected on"
          echo "        credentials, so the sandbox, the install root and the egress"
          echo "        tunnel are all working. Only the credential is bad." ;;
        *"not found"*|*"No such file"*)
          echo "        DIAGNOSIS: the CLI or its interpreter could not be executed"
          echo "        inside the sandbox. Report the auto-exposed install roots." ;;
        *)
          echo "        DIAGNOSIS: not a credential rejection. The CLI may not honour"
          echo "        the proxy environment, or may need another allowlist entry."
          echo "        Report the output and the allowlist; do not widen it here." ;;
      esac
      PROBE_FAIL=1
    fi
    echo "  allowlist: $EGRESS_ALLOW"
    echo "  output: ${OUT:0:400}"
  fi
  stop_egress
  [ -n "${PROBE_HELPER_PID:-}" ] && kill "$PROBE_HELPER_PID" 2>/dev/null

  echo
  if [ -z "${ANTHROPIC_API_KEY:-}${ANTHROPIC_AUTH_TOKEN:-}" ]; then
    echo "ADVISORY: this host authenticates Claude with the interactive OAuth"
    echo "session in ~/.claude/.credentials.json. That token EXPIRES, and the"
    echo "sandbox mounts it read-only so a run cannot refresh it — deliberately,"
    echo "because a refresh from inside could rotate and invalidate the session"
    echo "you use interactively. An unattended worker should therefore use a"
    echo "dedicated API key (ANTHROPIC_API_KEY) supplied through the systemd"
    echo "unit, not a personal login. A probe that passes today will start"
    echo "failing on its own otherwise."
    echo
  fi
  if [ "$PROBE_FAIL" = "0" ]; then
    echo "RESULT: PASS — containment holds and Claude authenticated from inside it."
  else
    echo "RESULT: NOT READY — see the FAIL lines above. Do not enable the worker."
  fi
  echo "This probe created and removed local temporary directories under"
  echo "$AGENT_DIR. It made no GitHub ref changes, no deployment, and ran no"
  echo "product task."
  exit "$PROBE_FAIL"
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

# The workspace is EPHEMERAL: discarded and recreated every run, so no state a
# previous child left behind can influence this one. `rm -rf` removes symlinks
# rather than following them, which is why this is safe to do from the host on
# a tree a previous child controlled.
rm -rf "$AGENT_CLONE"
log "cloning a fresh agent workspace"
git clone --quiet "$REPO_URL" "$AGENT_CLONE" || fail "clone failed"

# Git against the workspace, used ONLY before the child runs, and never after.
agit() { git -c core.hooksPath=/dev/null -c core.fsmonitor= -c uploadpack.packObjectsHook= \
             -C "$AGENT_CLONE" "$@"; }

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
print('OK|%s|%s|%s|%s|%s' % (turn, status, task, d.get('implementation_commit') or '',
                             d.get('directive_commit') or ''))
" 2>/dev/null)"

case "$VALIDATION" in
  OK\|*) ;;
  INVALID\|*) noop "control state rejected: ${VALIDATION#INVALID|}" ;;
  *) noop "control state could not be validated — invoking nothing" ;;
esac
IFS='|' read -r _ TURN STATUS TASK_ID PRIOR_IMPL AUTHORIZED_DIRECTIVE <<<"$VALIDATION"

# ── the directive must carry EXACTLY ONE Task ID naming this task ────────
DIRECTIVE_TEXT="$(ctl_file PRODUCT_DIRECTIVE.md)"
[ -n "$DIRECTIVE_TEXT" ] || noop "control carries no PRODUCT_DIRECTIVE.md — nothing authoritative to implement"
DIRECTIVE_CHECK="$(printf '%s' "$DIRECTIVE_TEXT" | python3 -c "
import re, sys
text = sys.stdin.read()
ids = re.findall(r'(?mi)^[ \t]*Task[ \t]*ID[ \t]*:[ \t]*(.*?)[ \t]*\$', text)
if len(ids) == 0:
    print('INVALID|PRODUCT_DIRECTIVE.md has no \"Task ID:\" line'); raise SystemExit
if len(ids) != 1:
    print('INVALID|PRODUCT_DIRECTIVE.md has %d \"Task ID:\" lines; exactly one is required'
          % len(ids)); raise SystemExit
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
# The immutable authorized baseline, recorded on the host BEFORE the child can
# influence anything. Every later claim is checked against this.
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

# ══ INVOCATION A — the child ═════════════════════════════════════════════
# From here to the end of the run, the workspace is HOSTILE.
agit remote remove origin 2>/dev/null

stage_resolv_conf
prepare_stage "$CHILD_HOME" creds
STAGE_HOME="$CHILD_HOME"
if sandbox_available; then
  SEEN_HOME="$REAL_HOME"
elif [ "$ALLOW_UNSANDBOXED" = "1" ]; then
  SEEN_HOME="$CHILD_HOME"
else
  record_run "no_sandbox" ""
  fail "this host cannot create the required user+mount+PID namespaces — refusing to run a child unconfined. Enable unprivileged user namespaces, or set FRANKENSTEIN_ALLOW_UNSANDBOXED=1 to accept the loss of the child boundary."
fi
[ -n "$WORKSPACE_MNT" ] && [ -n "$EXPORT_MNT" ] || { record_run "no_workspace_mount" ""
  fail "need two neutral mountpoints outside the home (tried: $MOUNT_CANDIDATES) — refusing to run without them"; }
build_env "$SEEN_HOME" creds
WS_SRC="$AGENT_CLONE"
if sandbox_available; then
  start_egress || { record_run "egress_unavailable" ""
    fail "the narrow egress channel could not be started — refusing to run the child with no way to reach Claude and no containment story"; }
  log "egress: allowlist $EGRESS_ALLOW; everything else, including localhost and the LAN, is unreachable"
fi

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
      --permission-mode acceptEdits --add-dir "$WORKSPACE_MNT" \
      </dev/null >>"$RUN_LOG" 2>&1
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

stop_egress

# ══ THE TRUST TRANSITION ═════════════════════════════════════════════════
# Nothing below reads, writes, or runs a command against the workspace from
# the host. Only sandbox invocations touch it, and only the exported artifact
# leaves.
[ "$CLAUDE_RC" -eq 97 ] && { record_run "sandbox_failed" ""; fail "the sandbox could not be established — nothing ran"; }
if [ "$CLAUDE_RC" -ne 0 ]; then
  record_run "claude_failed" ""
  fail "Claude exited $CLAUDE_RC — no handoff published, production untouched. Log: $RUN_LOG"
fi

# Structure, verification and export run with a scratch home the child never
# saw, no Claude credential in the environment or on disk, and NO egress: the
# network namespace stays empty, so child-controlled scripts/test.sh cannot
# send anything anywhere.
stage_for_tools "$SEEN_HOME"

sandboxed_or_local() {  # run fixed commands in the sandbox where available
  if sandbox_available; then run_sandboxed "${CHILD_ENV[@]}" "$@"
  else ( cd "$AGENT_CLONE" && "${CHILD_ENV[@]}" "$@" ); fi
}

# ══ INVOCATION B — structure check (fixed git commands only) ═════════════
sandboxed_or_local /bin/bash -c '
  g() { git -c core.hooksPath=/dev/null -c core.fsmonitor= \
            -c uploadpack.packObjectsHook= -c protocol.ext.allow=never "$@"; }
  g rev-parse --verify --quiet "refs/heads/$1" >/dev/null || exit 91
  [ -n "$(g log --oneline "$2..refs/heads/$1" 2>/dev/null)" ] || exit 92
  exit 0' _ "$TASK_BRANCH" "$BASELINE" >>"$RUN_LOG" 2>&1
STRUCT_RC=$?
case "$STRUCT_RC" in
  0) ;;
  91) record_run "no_branch" ""; fail "the task branch is gone from the workspace — nothing to publish" ;;
  92) record_run "no_commits" ""; fail "the run produced no commits — refusing to publish an empty handoff" ;;
  *)  record_run "workspace_unreadable" ""; fail "the workspace could not be inspected (status $STRUCT_RC) — refusing to publish" ;;
esac

# ══ INVOCATION C — independent verification ══════════════════════════════
# scripts/test.sh is a file Claude may edit. Running it on the host would hand
# a compromised run arbitrary code execution as the deploying user. It runs
# behind the same masks, in its own PID namespace, so nothing it starts in the
# background can survive into the export.
log "re-running the test suite independently, in its own sandbox"
sandboxed_or_local /bin/bash -c 'bash scripts/test.sh' >>"$RUN_LOG" 2>&1 \
  || { record_run "tests_failed" ""; fail "tests fail on the produced branch — no handoff published. Log: $RUN_LOG"; }

# ══ INVOCATION D — export an inert artifact ══════════════════════════════
# A git bundle plus a tiny manifest, written into a directory the child never
# saw. This is the ONLY thing that crosses back to the trusted host.
rm -rf "$EXPORT_DIR"; mkdir -p "$EXPORT_DIR"
EXPORT_SRC="$EXPORT_DIR"
if sandbox_available; then EXPORT_OUT="$EXPORT_MNT"; else EXPORT_OUT="$EXPORT_DIR"; fi
sandboxed_or_local /bin/bash -c '
  set -e
  out="$1"; branch="$2"
  g() { git -c core.hooksPath=/dev/null -c core.fsmonitor= \
            -c uploadpack.packObjectsHook= -c protocol.ext.allow=never "$@"; }
  g rev-parse --verify "refs/heads/$branch" > "$out/implementation.sha"
  g rev-parse --verify "refs/heads/$branch^{tree}" > "$out/tree.sha"
  printf "%s\n" "$branch" > "$out/task_branch"
  g bundle create "$out/implementation.bundle" "refs/heads/$branch" 2>/dev/null
  ' _ "$EXPORT_OUT" "$TASK_BRANCH" >>"$RUN_LOG" 2>&1
EXPORT_RC=$?
EXPORT_SRC=""
[ "$EXPORT_RC" -eq 0 ] || { record_run "export_failed" ""
  fail "the workspace could not be exported (status $EXPORT_RC) — nothing published"; }
for f in implementation.bundle implementation.sha task_branch tree.sha; do
  [ -s "$EXPORT_DIR/$f" ] || { record_run "export_incomplete" ""
    fail "the export is missing $f — nothing published"; }
done
EXPORTED_SHA="$(tr -d ' \n\r' < "$EXPORT_DIR/implementation.sha")"
EXPORTED_BRANCH="$(tr -d ' \n\r' < "$EXPORT_DIR/task_branch")"
[ "$EXPORTED_BRANCH" = "$TASK_BRANCH" ] || { record_run "export_branch_mismatch" ""
  fail "the export names branch $EXPORTED_BRANCH, not $TASK_BRANCH — nothing published"; }
log "exported ${EXPORTED_SHA:0:7} as an inert bundle"

# ── concurrency token, stage 1 ───────────────────────────────────────────
CONTROL_NOW="$(git ls-remote "$REPO_URL" "refs/heads/$CONTROL_BRANCH" 2>/dev/null | awk 'NR==1{print $1}')"
[ -n "$CONTROL_NOW" ] || { record_run "fetch_failed" ""; fail "could not re-read control before publishing"; }
[ "$CONTROL_NOW" = "$CONTROL_COMMIT" ] || {
  record_run "control_conflict" ""
  fail "control moved ${CONTROL_COMMIT:0:7} -> ${CONTROL_NOW:0:7} during the run. NOT overwriting newer Product Owner state; the work stays in the export locally."; }

# ── dry run stops HERE: nothing is ever pushed ───────────────────────────
if [ "$MODE" = "dry-run" ]; then
  record_run "dry_run" ""
  log "DRY RUN — nothing pushed. WOULD publish:"
  log "  task branch:        $TASK_BRANCH at ${EXPORTED_SHA:0:7}"
  log "  implementation SHA: $EXPORTED_SHA"
  log "  control transition: $STATUS -> awaiting_review on ${CONTROL_COMMIT:0:7}"
  log "production, control and remote task branches are unchanged."
  exit 0
fi

# ══ THE PUBLISHER ZONE — fresh clones, fed only by the bundle ════════════
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

git -C "$PUB_DIR" bundle verify "$EXPORT_DIR/implementation.bundle" >/dev/null 2>&1 \
  || { record_run "bundle_invalid" ""; fail "the exported bundle did not verify — nothing published"; }
git -C "$PUB_DIR" fetch --quiet --no-tags "$EXPORT_DIR/implementation.bundle" \
    "refs/heads/$TASK_BRANCH:refs/frankenstein/impl" \
  || { record_run "impl_import_failed" ""; fail "could not import the exported bundle — nothing published"; }

IMPL_COMMIT="$(git -C "$PUB_DIR" rev-parse refs/frankenstein/impl)"
[ "$IMPL_COMMIT" = "$EXPORTED_SHA" ] || {
  record_run "impl_mismatch" ""
  fail "the bundle carries ${IMPL_COMMIT:0:7} but the export claims ${EXPORTED_SHA:0:7} — refusing to publish"; }
[ "$IMPL_COMMIT" != "$BASELINE" ] || {
  record_run "no_commits" ""; fail "the export is identical to the baseline — nothing to publish"; }
git -C "$PUB_DIR" merge-base --is-ancestor "$BASELINE" refs/frankenstein/impl \
  || { record_run "impl_not_descendant" ""
       fail "the implementation does not descend from the authorized baseline ${BASELINE:0:7}"; }

# ── the exported state must be a COMPLETE, VALID handoff ─────────────────
# Not merely "names the right task". A buggy or hostile run that left
# turn=claude, or an authorized status, would re-authorize itself on the very
# next poll. Everything the Product Owner owns is checked against what
# authorized this run; only implementation_commit is the publisher's to set.
SNAPSHOT="$(git -C "$PUB_DIR" show "refs/frankenstein/impl:.frankenstein/AUTHORIZING_CONTROL_COMMIT" 2>/dev/null | tr -d ' \n\r')"
[ "$SNAPSHOT" = "$CONTROL_COMMIT" ] || {
  record_run "authorizing_snapshot_missing" ""
  fail "the exported work does not carry the authorizing control commit ${CONTROL_COMMIT:0:7} — refusing to publish"; }

EXPORTED_HANDOFF="$(git -C "$PUB_DIR" show "refs/frankenstein/impl:.frankenstein/IMPLEMENTATION_HANDOFF.md" 2>/dev/null)"
case "$EXPORTED_HANDOFF" in
  "") record_run "handoff_missing" ""
      fail "the exported work carries no .frankenstein/IMPLEMENTATION_HANDOFF.md — refusing to publish" ;;
esac
printf '%s' "$EXPORTED_HANDOFF" | grep -qi "Deviations From Directive" || {
  record_run "handoff_incomplete" ""
  fail "IMPLEMENTATION_HANDOFF.md omits the 'Deviations From Directive' section — refusing to publish an unreviewable handoff"; }

STATE_CHECK="$(git -C "$PUB_DIR" show "refs/frankenstein/impl:.frankenstein/STATE.json" 2>/dev/null \
  | python3 -c "
import json, sys
want_task, want_directive, version = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('INVALID|exported STATE.json is unparseable: %s' % e); raise SystemExit
if not isinstance(d, dict):
    print('INVALID|exported STATE.json is not an object'); raise SystemExit
checks = [
    ('protocol_version', d.get('protocol_version'), version),
    ('task_id',          d.get('task_id'),          want_task),
    ('turn',             d.get('turn'),             'product_owner'),
    ('status',           d.get('status'),           'awaiting_review'),
    ('last_actor',       d.get('last_actor'),       'claude'),
]
for field, got, want in checks:
    if got != want:
        print('INVALID|exported STATE.json %s is %r, must be %r' % (field, got, want))
        raise SystemExit
got_directive = d.get('directive_commit') or ''
if got_directive != want_directive:
    print('INVALID|exported STATE.json directive_commit is %r; the Product Owner set %r '
          'and a run may not rewrite it' % (got_directive, want_directive))
    raise SystemExit
print('OK')
" "$TASK_ID" "$AUTHORIZED_DIRECTIVE" "$SUPPORTED_PROTOCOL_VERSION" 2>/dev/null)"
case "$STATE_CHECK" in
  OK) ;;
  INVALID\|*) record_run "handoff_state_invalid" ""
      fail "${STATE_CHECK#INVALID|} — refusing to publish. A run that does not hand the turn back would re-authorize itself." ;;
  *)  record_run "handoff_state_invalid" ""
      fail "the exported STATE.json could not be validated — refusing to publish" ;;
esac
log "exported state validated as a complete handoff (product_owner/awaiting_review)"

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

# The handoff content is read from the PUBLISHER's object database, not from
# the child's workspace.
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
doc["implementation_commit"] = impl      # the publisher is the authority here
doc["turn"] = "product_owner"
doc["status"] = "awaiting_review"
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

# ── read back what actually landed on control ────────────────────────────
PUBLISHED_STATE="$(git -C "$CONTROL_DIR" show "HEAD:.frankenstein/STATE.json" 2>/dev/null \
  | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('unreadable'); raise SystemExit
print('%s|%s|%s|%s' % (d.get('turn'), d.get('status'),
                       d.get('implementation_commit'), d.get('last_actor')))
" 2>/dev/null)"
[ "$PUBLISHED_STATE" = "product_owner|awaiting_review|$IMPL_COMMIT|claude" ] || {
  record_run "published_state_wrong" ""
  fail "the control commit that was just written reads '$PUBLISHED_STATE', not 'product_owner|awaiting_review|$IMPL_COMMIT|claude'. Investigate before the next poll."; }
log "control now reads product_owner/awaiting_review at ${IMPL_COMMIT:0:7}"

record_run "success" "$HANDOFF_COMMIT"
log "DONE task=$TASK_ID impl=${IMPL_COMMIT:0:7} handoff=${HANDOFF_COMMIT:0:7} — production untouched"
