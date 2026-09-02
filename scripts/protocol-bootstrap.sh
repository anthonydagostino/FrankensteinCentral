#!/usr/bin/env bash
# ONE-TIME protocol bootstrap migration. Run on the OptiPlex.
#
#   bash scripts/protocol-bootstrap.sh <TARGET_SHA>
#
# Migrates the box from branch-following deployment to production-only
# deployment, then PROVES both directions of the boundary on the real machine.
#
# WHAT THIS CHANGES
#   * writes a systemd drop-in pinning FRANKENSTEIN_BRANCH=production
#   * stops the deploy timer for the duration of the git work, and restarts it
#   * fast-forwards the production branch to TARGET_SHA
#   * pushes and then deletes one uniquely-named throwaway branch
#
# The script does not touch containers, volumes, .env or user data directly.
# It does, however, deliberately cause the EXISTING deployment pipeline to
# rebuild and restart containers: that is what promoting to production means,
# and proving it is the point of TEST B. No user data or volumes are removed by
# that pipeline (`docker compose up -d --build --remove-orphans`).
#
# It never force pushes and never rewrites history. Every unexpected state
# stops with "STOPPED: <reason>", and the exit code is non-zero whenever the
# boundary was not proven.
#
# Exit codes: 0 both tests passed · 1 stopped/failed · 3 TEST B not provable
set -uo pipefail

TARGET_SHA="${1:-}"                 # REQUIRED — no default; see below
PROD_BRANCH="${FRANKENSTEIN_BRANCH_OVERRIDE:-production}"
TASK_BRANCH="claude/personal-app-hub-vvpy4h"
DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
RECORD="${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}/deployed.json"
UNIT="frankenstein-deploy.service"
TIMER="frankenstein-deploy.timer"

# Cleanup state — the trap uses these to undo partial work on ANY exit path.
THROWAWAY=""          # set once the branch actually exists on the remote
TIMER_STOPPED=0       # set once we have quiesced the timer

die() { echo; echo "STOPPED: $*"; exit 1; }
hr()  { echo; echo "════════ $* ════════"; }

cleanup() {
  local rc=$?
  if [ -n "$THROWAWAY" ]; then
    echo
    echo "cleanup: deleting throwaway branch '$THROWAWAY'"
    git push origin --delete "$THROWAWAY" >/dev/null 2>&1 \
      && echo "cleanup: deleted" \
      || echo "cleanup: WARNING could not delete '$THROWAWAY' — remove it manually"
  fi
  if [ "$TIMER_STOPPED" = "1" ]; then
    echo "cleanup: restarting $TIMER"
    sudo systemctl start "$TIMER" >/dev/null 2>&1
    if systemctl is-active --quiet "$TIMER"; then
      echo "cleanup: $TIMER is active again"
    else
      echo "cleanup: !! $TIMER IS NOT ACTIVE — auto-deploy is disabled."
      echo "cleanup: !! restore it with:  sudo systemctl start $TIMER"
    fi
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

# ─────────────────────────────────────────────────────────────────────────
# Item 8: the target commit must be explicit. A stale default silently
# promotes the wrong thing months later.
[ -n "$TARGET_SHA" ] || {
  echo "usage: bash scripts/protocol-bootstrap.sh <TARGET_SHA>"
  echo
  echo "TARGET_SHA is required — the commit to promote to production."
  echo "There is deliberately no default: a hard-coded SHA goes stale and would"
  echo "promote the wrong commit."
  exit 1
}

cd "$DIR" 2>/dev/null || die "repo not found at $DIR"

# ─────────────────────────────────────────────────────────────────────────
hr "1. STATE BEFORE MIGRATION (read-only)"
echo "repo path            : $DIR"
echo "checked-out branch   : $(git rev-parse --abbrev-ref HEAD)"
echo "box HEAD             : $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-58)"

# Item 1: a dirty tree must hard-stop. deploy.sh does `git reset --hard`, so
# uncommitted work here would be destroyed by the very deploy we trigger.
# Tracked modifications are fatal; untracked files are listed but tolerated
# (.env is gitignored, and the box legitimately carries local artefacts).
DIRTY_TRACKED="$(git status --porcelain --untracked-files=no)"
UNTRACKED="$(git ls-files --others --exclude-standard | head -5)"
if [ -n "$DIRTY_TRACKED" ]; then
  echo "$DIRTY_TRACKED"
  die "working tree has uncommitted changes to tracked files. A deploy runs 'git reset --hard' and would destroy them. Commit or stash first."
fi
echo "working tree         : clean (no tracked modifications)"
[ -n "$UNTRACKED" ] && { echo "untracked (tolerated):"; echo "$UNTRACKED" | sed 's/^/  /'; }


git fetch --prune origin >/dev/null 2>&1 || die "git fetch failed — check network/credentials"
PROD_BEFORE_FULL="$(git rev-parse "origin/$PROD_BRANCH" 2>/dev/null || echo)"
PROD_BEFORE="${PROD_BEFORE_FULL:0:7}"
echo "origin/$PROD_BRANCH   : ${PROD_BEFORE:-MISSING}"
echo "origin/task branch   : $(git rev-parse --short "origin/$TASK_BRANCH" 2>/dev/null || echo MISSING)"

if grep -q 'FRANKENSTEIN_BRANCH:-production' scripts/autopull.sh 2>/dev/null; then
  echo "poller version       : NEW (watches production)"
elif grep -q 'rev-parse --abbrev-ref HEAD' scripts/autopull.sh 2>/dev/null; then
  echo "poller version       : OLD (follows checked-out branch — this is the bug)"
else
  echo "poller version       : UNKNOWN"
fi

echo
echo "--- systemd unit ---"
systemctl cat "$UNIT" 2>/dev/null | grep -vE '^\s*$' || die "$UNIT not found — is auto-deploy installed?"
echo
echo "--- systemd environment ---"
systemctl show "$UNIT" -p Environment
echo
echo "--- timer ---"
systemctl list-timers "$TIMER" --no-pager 2>/dev/null | head -3
echo
echo "--- deployed.json ---"
cat "$RECORD" 2>/dev/null || echo "(none — pre-migration deploys did not record one)"
echo
echo "--- containers ---"
docker compose ps --format '{{.Service}}\t{{.Status}}' 2>/dev/null | sort \
  || docker-compose ps 2>/dev/null || echo "(could not list containers)"

# ─────────────────────────────────────────────────────────────────────────
hr "PRE-FLIGHT CHECKS (still no mutation)"

git cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || die "$TARGET_SHA not found locally after fetch"
TARGET_FULL="$(git rev-parse "$TARGET_SHA")"
echo "target commit        : $(git rev-parse --short "$TARGET_FULL")  $(git log -1 --pretty=%s "$TARGET_FULL" | cut -c1-52)"

[ -n "$PROD_BEFORE_FULL" ] || die "origin/$PROD_BRANCH does not exist. Refusing to create it blindly."

# Item 10: already-at-target is honestly unprovable, not a pass.
ALREADY_AT_TARGET=0
if [ "$PROD_BEFORE_FULL" = "$TARGET_FULL" ]; then
  ALREADY_AT_TARGET=1
  echo "fast-forward         : production is ALREADY at the target"
elif git merge-base --is-ancestor "$PROD_BEFORE_FULL" "$TARGET_FULL"; then
  echo "fast-forward         : YES ($PROD_BEFORE -> $(git rev-parse --short "$TARGET_FULL"))"
else
  die "promoting $TARGET_SHA onto $PROD_BRANCH is NOT a fast-forward. Resolve deliberately; this script will not force."
fi

# Item 7: the whole proof depends on the poller actually running.
systemctl list-unit-files "$TIMER" >/dev/null 2>&1 \
  || die "$TIMER does not exist — the poll-cycle tests would prove nothing."
if systemctl is-active --quiet "$TIMER"; then
  echo "deploy timer         : active"
else
  die "$TIMER is not active. Start it (sudo systemctl start $TIMER) and re-run; without it no poll cycle occurs and neither test means anything."
fi

# ─────────────────────────────────────────────────────────────────────────
# Item 9: the timer fires every 60s in THIS repo and runs `git fetch`,
# `git checkout`, `git reset --hard`. Concurrent git operations race on
# index.lock, and a deploy could reset the tree mid-migration. Smallest
# deterministic fix: quiesce the timer for the git/systemd work, then restart
# it so the poller performs TEST B and TEST A normally. The EXIT trap restarts
# it on every failure path.
hr "2. QUIESCE THE DEPLOY TIMER (removes the poller/bootstrap race)"
sudo systemctl stop "$TIMER" || die "could not stop $TIMER"
TIMER_STOPPED=1
echo "stopped              : $TIMER"

echo -n "waiting for any in-flight deploy to finish"
for _ in $(seq 1 60); do
  systemctl is-active --quiet "$UNIT" || break
  echo -n "."
  sleep 10
done
echo
systemctl is-active --quiet "$UNIT" \
  && die "a deploy is still running after 10 minutes — investigate before migrating."
echo "in-flight deploy     : none"

# ─────────────────────────────────────────────────────────────────────────
hr "3. PIN FRANKENSTEIN_BRANCH=production IN SYSTEMD"
sudo mkdir -p "/etc/systemd/system/$UNIT.d" || die "could not create systemd drop-in dir"
sudo tee "/etc/systemd/system/$UNIT.d/branch.conf" >/dev/null <<'CONF'
[Service]
Environment=FRANKENSTEIN_BRANCH=production
CONF
sudo systemctl daemon-reload || die "systemctl daemon-reload failed"

ENV_NOW="$(systemctl show "$UNIT" -p Environment)"
echo "$ENV_NOW"
echo "$ENV_NOW" | grep -q "FRANKENSTEIN_BRANCH=production" \
  || die "systemd environment does NOT contain FRANKENSTEIN_BRANCH=production"
echo "verified             : systemd deploy branch is pinned to production"

# ─────────────────────────────────────────────────────────────────────────
hr "4. PROMOTE TO PRODUCTION  [TEST B]"
if [ "$ALREADY_AT_TARGET" = "1" ]; then
  echo "production is already at $(git rev-parse --short "$TARGET_FULL") — nothing to promote."
  echo "TEST B is therefore NOT PROVABLE by this run. No commit will be manufactured"
  echo "to create one; that would be fabricating evidence."
  TEST_B="N/A (production already at target — unproven)"
else
  # Fast-forward already verified. Identical to `promote.sh --bootstrap`,
  # inlined because the box may sit on a commit predating promote.sh.
  git push origin "$TARGET_FULL:refs/heads/$PROD_BRANCH" \
    || die "push to $PROD_BRANCH failed"
  echo "promoted             : $PROD_BEFORE -> $(git rev-parse --short "$TARGET_FULL")"
  TEST_B=PENDING
fi

# ─────────────────────────────────────────────────────────────────────────
hr "5. RESUME THE TIMER AND WAIT FOR CONVERGENCE"
sudo systemctl start "$TIMER" || die "could not restart $TIMER"
TIMER_STOPPED=0
systemctl is-active --quiet "$TIMER" || die "$TIMER did not come back active"
echo "restarted            : $TIMER"

running_commit() {
  python3 -c "
import json
try: print(json.load(open('$RECORD')).get('running_commit') or '')
except Exception: print('')" 2>/dev/null
}
last_success() {
  python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_success_at') or '')
except Exception: print('')" 2>/dev/null
}

if [ "$ALREADY_AT_TARGET" = "0" ]; then
  echo "waiting up to 12 min for the poller to deploy $(git rev-parse --short "$TARGET_FULL")"
  for i in $(seq 1 48); do
    sleep 15
    [ "$(running_commit)" = "$TARGET_FULL" ] && { echo "deployed after ~$((i*15))s"; break; }
    [ $((i % 4)) -eq 0 ] && echo "  ...waiting ($((i*15))s), running=$(running_commit | cut -c1-7)"
  done
fi

echo
echo "--- journal ---"
journalctl -u "$UNIT" --since '-15 min' --no-pager | tail -20
echo
echo "--- deployed.json ---"
cat "$RECORD" 2>/dev/null || echo "(still none)"
echo
echo "--- containers ---"
docker compose ps --format '{{.Service}}\t{{.Status}}' 2>/dev/null | sort

# Item 4: a failed TEST B must stop BEFORE TEST A. Running the isolation test
# on a box that did not converge would compare meaningless numbers.
if [ "$ALREADY_AT_TARGET" = "0" ]; then
  if [ "$(running_commit)" = "$TARGET_FULL" ]; then
    TEST_B=PASS
    echo
    echo "TEST B: PASS — production change deployed and running_commit matches."
  else
    TEST_B=FAIL
    echo
    echo "TEST B: FAIL — the box did not converge on $(git rev-parse --short "$TARGET_FULL")."
    echo "running_commit=$(running_commit | cut -c1-7)  last_result=$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_result') or 'unknown')
except Exception: print('unknown')" 2>/dev/null)"
    die "production deployment did not succeed. Not running TEST A: its comparison would be meaningless on a box in an unknown state. Check the journal above (the test gate may have failed, which leaves the previous build running)."
  fi
fi

# ─────────────────────────────────────────────────────────────────────────
hr "6. TEST A — TASK-BRANCH PUSH MUST NOT DEPLOY"

# Item 2: unique name, and prove it does not already exist remotely.
CANDIDATE="throwaway-boundary-test-$(date +%Y%m%d-%H%M%S)-$$"
if git ls-remote --heads origin "$CANDIDATE" | grep -q .; then
  die "throwaway branch '$CANDIDATE' unexpectedly already exists on the remote"
fi
echo "throwaway branch     : $CANDIDATE (verified not pre-existing)"

# Item 6: refresh production and capture FULL SHAs for equality.
git fetch --prune origin >/dev/null 2>&1 || die "fetch before TEST A failed"
PROD_A="$(git rev-parse "origin/$PROD_BRANCH")"
REC_A="$(cat "$RECORD" 2>/dev/null || echo none)"
RUN_A="$(running_commit)"
SUCCESS_A="$(last_success)"
echo "before: production=${PROD_A:0:7} running=${RUN_A:0:7} last_success_at=${SUCCESS_A:-none}"

git push origin "$TARGET_FULL:refs/heads/$CANDIDATE" >/dev/null 2>&1 \
  || die "could not push throwaway branch"
THROWAWAY="$CANDIDATE"          # from here the trap will clean it up
git ls-remote --heads origin "$CANDIDATE" | grep -q . \
  && echo "confirmed on GitHub  : yes"

echo "waiting 150s (two full poll cycles)..."
sleep 150

git fetch --prune origin >/dev/null 2>&1 || die "fetch after TEST A failed"
PROD_B="$(git rev-parse "origin/$PROD_BRANCH")"
REC_B="$(cat "$RECORD" 2>/dev/null || echo none)"
RUN_B="$(running_commit)"
SUCCESS_B="$(last_success)"
echo "after : production=${PROD_B:0:7} running=${RUN_B:0:7} last_success_at=${SUCCESS_B:-none}"
echo
echo "--- journal during the window ---"
journalctl -u "$UNIT" --since '-4 min' --no-pager | tail -10

TEST_A=FAIL
if [ "$PROD_A" = "$PROD_B" ] && [ "$RUN_A" = "$RUN_B" ] \
   && [ "$REC_A" = "$REC_B" ] && [ "$SUCCESS_A" = "$SUCCESS_B" ]; then
  TEST_A=PASS
fi
echo
echo "TEST A: $TEST_A  (production SHA, running_commit, deployed.json and last_success_at all unchanged)"

# ─────────────────────────────────────────────────────────────────────────
hr "PROTOCOL BOOTSTRAP RESULT"
FINAL_RESULT="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_result') or 'unknown')
except Exception: print('unknown')" 2>/dev/null)"

cat <<SUMMARY
Protocol Bootstrap Result

  Production branch:            $PROD_BRANCH
  Production SHA:               $(git rev-parse --short "origin/$PROD_BRANCH")   (was ${PROD_BEFORE:-none})
  Running SHA:                  $(running_commit | cut -c1-7)
  Systemd deploy branch:        $(systemctl show "$UNIT" -p Environment | grep -o 'FRANKENSTEIN_BRANCH=[^ "]*' || echo 'NOT PINNED')
  Deploy timer:                 $(systemctl is-active "$TIMER")
  Last deploy result:           $FINAL_RESULT
  Last successful deploy:       $(last_success)
  Task-branch isolation test:   $TEST_A
  Production deploy test:       $TEST_B
SUMMARY
echo
echo "Paste this whole output back for Product Owner review."

# Item 5: the exit code must reflect whether the boundary was actually proven.
[ "$TEST_A" = "PASS" ] || { echo; echo "EXIT: non-zero — TEST A did not pass."; exit 1; }
case "$TEST_B" in
  PASS) exit 0 ;;
  N/A*) echo; echo "EXIT: 3 — TEST B unproven (production was already at target)."; exit 3 ;;
  *)    echo; echo "EXIT: non-zero — TEST B did not pass."; exit 1 ;;
esac
