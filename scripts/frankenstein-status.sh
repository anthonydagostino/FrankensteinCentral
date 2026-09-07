#!/usr/bin/env bash
# What is DESIRED on production, and what is actually RUNNING on the box.
#
#   bash scripts/frankenstein-status.sh
#
# This used to also print Product Owner protocol state (turn, task, directive,
# deployment authorization). That protocol was removed on 2026-09-07 — there
# is no approval gate any more, so there is no turn to report. What remains is
# the only question that was ever operationally load-bearing: is the thing on
# production the thing that is running?
#
# Desired, running and attempted are THREE different facts and are never
# collapsed. A failed deploy attempt must never read as a success: the box
# keeps serving the last good commit while last_attempt_commit moves on.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 - "$@" <<'PY'
import json
import os
import subprocess
import sys


def git(*args):
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


print("FrankensteinCentral deployment status")
print()

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
    running = rec.get("running_commit")
    attempt = rec.get("last_attempt_commit")
    result = rec.get("last_result", "?")
    # Only slice real SHAs — truncating placeholder text produced "— (none".
    short = lambda v, dash="—": v[:7] if v else dash  # noqa: E731
    print(f"  Running commit:        {short(running, '— (none confirmed)')}"
          f"   (last SUCCESSFUL deploy)")
    print(f"  Last attempted:        {short(attempt)}   at {rec.get('last_attempt_at','?')}")
    print(f"  Last deploy result:    {result}")
    # A null/absent running_commit is PENDING too: no successfully deployed SHA
    # is confirmed in the record. That says nothing about whether containers
    # happen to be up — only that no deployment has been confirmed.
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
print("  Ship with: bash scripts/test.sh && bash scripts/promote.sh <sha>")
PY
