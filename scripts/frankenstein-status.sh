#!/usr/bin/env bash
# Product Owner <-> Claude protocol helper. See .frankenstein/PROTOCOL.md
#
#   bash scripts/frankenstein-status.sh            current turn/status/commits
#   bash scripts/frankenstein-status.sh --check    validate state + consistency
#   bash scripts/frankenstein-status.sh --next-id  propose the next FC-### id
#
# Prints protocol state only — never secrets, env values or tokens.
set -uo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-status}"

python3 - "$MODE" <<'PY'
import json
import os
import re
import subprocess
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "status"
STATE = ".frankenstein/STATE.json"
DIRECTIVE = ".frankenstein/PRODUCT_DIRECTIVE.md"
HANDOFF = ".frankenstein/IMPLEMENTATION_HANDOFF.md"

TURNS = {"product_owner", "claude", "none"}
STATUSES = {"awaiting_directive", "ready_for_implementation", "implementing",
            "awaiting_review", "changes_requested", "accepted", "blocked"}
# Claude may only start product work in these states.
CLAUDE_GO = {"ready_for_implementation", "changes_requested"}
REQUIRED = {"protocol_version", "task_id", "turn", "status", "directive_commit",
            "implementation_commit", "last_actor", "updated_at"}
TASK_RE = re.compile(r"^FC-\d{3,}$")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, f"{STATE} not found"
    except json.JSONDecodeError as e:
        return None, f"{STATE} is not valid JSON: {e}"


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def field(path, name):
    """Read 'Name: value' out of a protocol markdown file."""
    try:
        for line in open(path):
            if line.lower().startswith(name.lower() + ":"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def validate(state):
    """Returns a list of problems. Empty list == consistent."""
    problems = []
    missing = REQUIRED - set(state)
    if missing:
        problems.append(f"STATE.json missing field(s): {', '.join(sorted(missing))}")
    if state.get("turn") not in TURNS:
        problems.append(f"turn={state.get('turn')!r} is not one of {sorted(TURNS)}")
    if state.get("status") not in STATUSES:
        problems.append(f"status={state.get('status')!r} is not one of {sorted(STATUSES)}")
    if not isinstance(state.get("protocol_version"), int):
        problems.append("protocol_version must be an integer")
    if not TASK_RE.match(str(state.get("task_id", ""))):
        problems.append(f"task_id={state.get('task_id')!r} is not FC-### form")
    if state.get("last_actor") not in (None, "product_owner", "claude"):
        problems.append(f"last_actor={state.get('last_actor')!r} is invalid")

    # Cross-file consistency: the directive must name the task in flight.
    d_task = field(DIRECTIVE, "Task ID")
    if d_task and d_task != state.get("task_id") and d_task != "—":
        problems.append(f"PRODUCT_DIRECTIVE.md Task ID ({d_task}) != "
                        f"STATE.json task_id ({state.get('task_id')})")

    # A review can't be pending on an implementation that doesn't exist.
    if state.get("status") == "awaiting_review" and not state.get("implementation_commit"):
        problems.append("status=awaiting_review but implementation_commit is null")

    # Referenced commits must actually exist in this repo.
    for key in ("directive_commit", "implementation_commit"):
        sha = state.get(key)
        if sha and not git("cat-file", "-t", str(sha)):
            problems.append(f"{key}={sha} is not a commit in this repository")

    if state.get("status") == "accepted" and state.get("turn") == "claude":
        problems.append("status=accepted with turn=claude — no task is authorized")
    return problems


def next_id():
    """Highest FC-### seen anywhere, plus one. Never guessed from memory."""
    seen = {0}
    log = git("log", "--all", "--pretty=%s%n%b")
    for text in (log, _read(DIRECTIVE), _read(HANDOFF), _read(STATE)):
        for m in re.finditer(r"FC-(\d{3,})", text or ""):
            seen.add(int(m.group(1)))
    return f"FC-{max(seen) + 1:03d}", max(seen)


def _read(path):
    try:
        return open(path).read()
    except OSError:
        return ""


state, err = load_state()

if MODE == "--next-id":
    if err:
        print(f"! {err}")
        sys.exit(1)
    nxt, highest = next_id()
    print(f"Highest FC id seen: {'FC-%03d' % highest if highest else '(none)'}")
    print(f"Next task id:       {nxt}")
    sys.exit(0)

if err:
    print(f"! {err}")
    print("! Protocol state unreadable — treat as BLOCKED and stop.")
    sys.exit(1)

problems = validate(state)

if MODE == "--check":
    if problems:
        print("PROTOCOL STATE INVALID")
        for p in problems:
            print(f"  - {p}")
        print("\nDo not guess. Set status=blocked and stop for Product Owner resolution.")
        sys.exit(1)
    print("Protocol state valid and internally consistent.")
    sys.exit(0)

# ---- default: human-readable status ----
print("Frankenstein Development Protocol")
print()
print(f"  Task:                  {state.get('task_id')}")
print(f"  Turn:                  {state.get('turn')}")
print(f"  Status:                {state.get('status')}")
print(f"  Directive commit:      {state.get('directive_commit') or '—'}")
print(f"  Implementation commit: {state.get('implementation_commit') or '—'}")
print(f"  Last actor:            {state.get('last_actor') or '—'}")
print(f"  Updated:               {state.get('updated_at')}")
print(f"  Deployment auth:       {field(DIRECTIVE, 'Deployment Authorization') or '(unset -> treat as none)'}")
print()

# --- the review/production boundary -------------------------------------
prod_branch = os.environ.get("FRANKENSTEIN_BRANCH", "production")
prod_sha = git("rev-parse", "--short", f"origin/{prod_branch}") or \
           git("rev-parse", "--short", prod_branch)
print(f"  Production branch:     {prod_branch}")
print(f"  Desired commit:        {prod_sha or '— (branch not found)'}   (origin/{prod_branch})")

# What is actually RUNNING, recorded by deploy.sh outside the repo.
rec_path = os.path.join(os.environ.get("FRANKENSTEIN_STATE_DIR",
                                       os.path.expanduser("~/.frankenstein")),
                        "deployed.json")
try:
    with open(rec_path) as f:
        rec = json.load(f)
    # Desired, running and attempted are three different facts. A failed
    # attempt must never read as success: the box keeps running the last good
    # commit while last_attempt_commit moves on.
    running = rec.get("running_commit")
    attempt = rec.get("last_attempt_commit")
    result = rec.get("last_result", "?")
    # Only slice real SHAs — truncating placeholder text produced "— (none".
    short = lambda v, dash="—": v[:7] if v else dash
    print(f"  Running commit:        {short(running, '— (none confirmed)')}"
          f"   (last SUCCESSFUL deploy)")
    print(f"  Last attempted:        {short(attempt)}   at {rec.get('last_attempt_at','?')}")
    print(f"  Last deploy result:    {result}")
    # A null/absent running_commit is PENDING too: no successfully deployed SHA
    # is confirmed in the record. That says nothing about whether containers
    # happen to be up — only that no deployment has been confirmed.
    if not prod_sha:
        # Desired unknown means the comparison below cannot be made at all.
        # Saying nothing would read as "nothing is wrong".
        print("  ! DEPLOYMENT PENDING — the desired commit cannot be "
              "determined, so the running commit cannot be confirmed")
    if not running:
        print(f"  ! DEPLOYMENT PENDING — desired {prod_sha or '(unknown)'} has no "
              f"confirmed running deployment")
        print("  !   (no successfully deployed SHA is recorded; this does not "
              "mean containers are down)")
    elif prod_sha and not running.startswith(prod_sha):
        print(f"  ! DEPLOYMENT PENDING — desired {prod_sha} is not the running "
              f"commit {running[:7]}")
    if result != "success":
        print(f"  ! last attempt did NOT succeed; the box is still on "
              f"{short(running, 'no confirmed commit')}")
except (OSError, ValueError):
    print("  Running commit:        — (no deploy record here; this file is "
          "written on the OptiPlex)")
    print("  Last attempted:        —")
    print("  Last deploy result:    — (unknown; a poll would treat this as "
          "'deploy required')")
print()

may_work = state.get("turn") == "claude" and state.get("status") in CLAUDE_GO
if may_work:
    print("  => Claude MAY implement the authorized scope in PRODUCT_DIRECTIVE.md.")
elif state.get("status") == "blocked":
    print("  => BLOCKED. Report the blocker; do not start work.")
elif state.get("status") == "accepted":
    print("  => Accepted; no task in flight. Do not invent a new task.")
else:
    print(f"  => Not Claude's turn ({state.get('turn')}/{state.get('status')}). "
          "Do not start product work.")

if problems:
    print()
    print("  ! STATE IS INCONSISTENT:")
    for p in problems:
        print(f"      - {p}")
    print("  ! Do not guess — stop for Product Owner resolution.")
    sys.exit(1)
PY
