#!/usr/bin/env bash
# ONE-TIME protocol bootstrap migration. Run on the OptiPlex.
#
# Migrates the box from branch-following deployment to production-only
# deployment, then PROVES both directions of the boundary on the real machine.
#
# Stages:
#   1. print every relevant fact BEFORE mutating anything
#   2. pin FRANKENSTEIN_BRANCH=production in systemd, verify the environment
#   3. promote the protocol chain to production (fast-forward only)
#   4. wait for the poller to deploy it, print the result        [TEST B]
#   5. push a throwaway branch, wait a full poll cycle, prove nothing deployed
#      and delete it                                             [TEST A]
#   6. print a summary block to paste back
#
# Safety: stops on unexpected state rather than forcing through. Never force
# pushes, never touches containers, volumes, .env or user data, never changes
# product functionality. Re-runnable.
set -uo pipefail

TARGET_SHA="${1:-a65d272}"          # final protocol state to promote
PROD_BRANCH="production"
TASK_BRANCH="claude/personal-app-hub-vvpy4h"
DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
RECORD="${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}/deployed.json"
UNIT="frankenstein-deploy.service"
THROWAWAY="throwaway-boundary-test"

die() { echo; echo "STOPPED: $*"; echo "Nothing further was changed."; exit 1; }
hr()  { echo; echo "════════ $* ════════"; }

cd "$DIR" 2>/dev/null || die "repo not found at $DIR"

# ─────────────────────────────────────────────────────────────────────────
hr "1. STATE BEFORE MIGRATION (no changes yet)"
echo "repo path            : $DIR"
echo "checked-out branch   : $(git rev-parse --abbrev-ref HEAD)"
echo "box HEAD             : $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-58)"

git fetch --prune origin >/dev/null 2>&1 || die "git fetch failed — check network/credentials"
PROD_BEFORE="$(git rev-parse --short "origin/$PROD_BRANCH" 2>/dev/null || echo MISSING)"
TASK_SHA="$(git rev-parse --short "origin/$TASK_BRANCH" 2>/dev/null || echo MISSING)"
echo "origin/$PROD_BRANCH   : $PROD_BEFORE"
echo "origin/task branch   : $TASK_SHA"

if grep -q 'FRANKENSTEIN_BRANCH:-production' scripts/autopull.sh 2>/dev/null; then
  echo "poller version       : NEW (watches production)"
elif grep -q 'rev-parse --abbrev-ref HEAD' scripts/autopull.sh 2>/dev/null; then
  echo "poller version       : OLD (follows checked-out branch — this is the bug)"
else
  echo "poller version       : UNKNOWN"
fi

echo "dirty working tree   : $(if [ -n "$(git status --porcelain)" ]; then echo YES; else echo no; fi)"
echo
echo "--- systemd unit ---"
systemctl cat "$UNIT" 2>/dev/null | grep -vE '^\s*$' || die "$UNIT not found — is auto-deploy installed?"
echo
echo "--- systemd environment ---"
systemctl show "$UNIT" -p Environment
echo
echo "--- deployed.json ---"
cat "$RECORD" 2>/dev/null || echo "(none — pre-migration deploys did not record one)"
echo
echo "--- containers ---"
docker compose ps --format '{{.Service}}\t{{.Status}}' 2>/dev/null | sort || docker-compose ps

# ─────────────────────────────────────────────────────────────────────────
hr "PRE-FLIGHT CHECKS"
git cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || die "$TARGET_SHA not found locally after fetch"
echo "target commit        : $(git rev-parse --short "$TARGET_SHA")  $(git log -1 --pretty=%s "$TARGET_SHA" | cut -c1-52)"

if [ "$PROD_BEFORE" = "MISSING" ]; then
  die "origin/$PROD_BRANCH does not exist. Expected it at a known baseline; refusing to create it blindly."
fi
if git merge-base --is-ancestor "origin/$PROD_BRANCH" "$TARGET_SHA"; then
  echo "fast-forward         : YES ($PROD_BEFORE -> $(git rev-parse --short "$TARGET_SHA"))"
elif [ "$(git rev-parse "origin/$PROD_BRANCH")" = "$(git rev-parse "$TARGET_SHA")" ]; then
  echo "fast-forward         : already at target"
else
  die "promoting $TARGET_SHA onto $PROD_BRANCH is NOT a fast-forward. Resolve deliberately; this script will not force."
fi

# ─────────────────────────────────────────────────────────────────────────
hr "2. PIN FRANKENSTEIN_BRANCH=production IN SYSTEMD"
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
hr "3. PROMOTE PROTOCOL CHAIN TO PRODUCTION  [TEST B begins]"
if [ "$(git rev-parse "origin/$PROD_BRANCH")" = "$(git rev-parse "$TARGET_SHA")" ]; then
  echo "production is already at $TARGET_SHA — no promotion needed."
  echo "TEST B cannot be demonstrated by this promotion; see the note at the end."
  PROMOTED=0
else
  # Fast-forward already verified above. This is exactly what
  # `promote.sh --bootstrap` does, inlined so it works even when the box is on
  # an older commit that predates promote.sh.
  git push origin "$TARGET_SHA:refs/heads/$PROD_BRANCH" || die "push to $PROD_BRANCH failed"
  echo "promoted             : $PROD_BEFORE -> $(git rev-parse --short "$TARGET_SHA")"
  PROMOTED=1
fi

# ─────────────────────────────────────────────────────────────────────────
hr "4. WAIT FOR THE POLLER TO DEPLOY (up to 10 min)"
WANT="$(git rev-parse "$TARGET_SHA")"
for i in $(seq 1 40); do
  sleep 15
  RUNNING="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('running_commit') or '')
except Exception: print('')" 2>/dev/null)"
  if [ "$RUNNING" = "$WANT" ]; then
    echo "deployed after ~$((i*15))s"
    break
  fi
  [ $((i % 4)) -eq 0 ] && echo "  ...waiting ($((i*15))s), running=${RUNNING:0:7}"
done
echo
echo "--- journal ---"
journalctl -u "$UNIT" --since '-12 min' --no-pager | tail -20
echo
echo "--- deployed.json ---"
cat "$RECORD" 2>/dev/null || echo "(still none)"
echo
echo "--- containers ---"
docker compose ps --format '{{.Service}}\t{{.Status}}' 2>/dev/null | sort

DEPLOY_OK=no
[ "$RUNNING" = "$WANT" ] && DEPLOY_OK=yes

# ─────────────────────────────────────────────────────────────────────────
hr "5. TEST A — TASK-BRANCH PUSH MUST NOT DEPLOY"
PROD_A="$(git rev-parse --short "origin/$PROD_BRANCH")"
REC_A="$(cat "$RECORD" 2>/dev/null || echo none)"
SUCCESS_A="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_success_at') or '')
except Exception: print('')" 2>/dev/null)"
echo "before: production=$PROD_A last_success_at=${SUCCESS_A:-none}"

git push origin "$TARGET_SHA:refs/heads/$THROWAWAY" >/dev/null 2>&1 \
  && echo "pushed throwaway branch '$THROWAWAY'" || die "could not push throwaway branch"
git ls-remote --heads origin "$THROWAWAY" | grep -q . \
  && echo "confirmed on GitHub  : yes"

echo "waiting 150s (two poll cycles)..."
sleep 150

git fetch --prune origin >/dev/null 2>&1
PROD_B="$(git rev-parse --short "origin/$PROD_BRANCH")"
REC_B="$(cat "$RECORD" 2>/dev/null || echo none)"
SUCCESS_B="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_success_at') or '')
except Exception: print('')" 2>/dev/null)"
echo "after : production=$PROD_B last_success_at=${SUCCESS_B:-none}"
echo
echo "--- journal during the window ---"
journalctl -u "$UNIT" --since '-3 min' --no-pager | tail -10

TEST_A=FAIL
if [ "$PROD_A" = "$PROD_B" ] && [ "$REC_A" = "$REC_B" ] && [ "$SUCCESS_A" = "$SUCCESS_B" ]; then
  TEST_A=PASS
fi
echo
echo "TEST A: $TEST_A  (production SHA, deployed.json and last_success_at all unchanged)"

echo
echo "deleting throwaway branch..."
git push origin --delete "$THROWAWAY" >/dev/null 2>&1 && echo "deleted '$THROWAWAY'" \
  || echo "WARNING: could not delete '$THROWAWAY' — remove it manually"

# ─────────────────────────────────────────────────────────────────────────
hr "PROTOCOL BOOTSTRAP RESULT"
FINAL_RUNNING="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('running_commit') or 'unknown')
except Exception: print('unknown')" 2>/dev/null)"
FINAL_RESULT="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_result') or 'unknown')
except Exception: print('unknown')" 2>/dev/null)"
FINAL_SUCCESS="$(python3 -c "
import json
try: print(json.load(open('$RECORD')).get('last_success_at') or 'unknown')
except Exception: print('unknown')" 2>/dev/null)"

cat <<SUMMARY
Protocol Bootstrap Result

  Production branch:            $PROD_BRANCH
  Production SHA:               $(git rev-parse --short "origin/$PROD_BRANCH")   (was $PROD_BEFORE)
  Running SHA:                  ${FINAL_RUNNING:0:7}
  Systemd deploy branch:        $(systemctl show "$UNIT" -p Environment | grep -o 'FRANKENSTEIN_BRANCH=[^ "]*' || echo 'NOT PINNED')
  Last deploy result:           $FINAL_RESULT
  Last successful deploy:       $FINAL_SUCCESS
  Task-branch isolation test:   $TEST_A
  Production deploy test:       $(if [ "$PROMOTED" = "1" ] && [ "$DEPLOY_OK" = "yes" ]; then echo PASS; elif [ "$PROMOTED" = "0" ]; then echo "N/A (production was already at target)"; else echo FAIL; fi)
SUMMARY
echo
echo "Paste this whole output back for Product Owner review."
