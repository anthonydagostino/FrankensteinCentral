#!/usr/bin/env bash
# Deterministic release service — the ONLY actor that moves `production`.
#
#   bash scripts/release-service.sh             one poll cycle
#   bash scripts/release-service.sh --status    decision only, no action
#   bash scripts/release-service.sh --dry-run   full evaluation, NO push
#
# ── WHAT THIS IS ─────────────────────────────────────────────────────────
# A fixed script with no model, no prompt, no product judgement and no
# branching on anything but the protocol state it reads. It never invokes
# Claude, never invokes Codex, never chooses work, never edits a directive and
# never decides what "accepted" ought to mean. It reads the Product Owner's
# accepted state and either performs exactly one fast-forward push, or nothing.
#
# It exists because the alternative is an agent holding the production
# credential. Acceptance is judgement and belongs to the Product Owner;
# release is mechanism and belongs to something that cannot form an intention.
#
# ── ITS ENTIRE EFFECT ON THE WORLD ───────────────────────────────────────
#   git push origin <accepted SHA>:refs/heads/production      (fast-forward)
# or nothing. It cannot force push, cannot delete, cannot push any other ref,
# cannot write `control`, and cannot run deploy.sh, promote.sh or rollback.sh.
# The OptiPlex deploy poller does the deploying, as it always has.
#
# ── WHY IT CAN TRUST `control` ───────────────────────────────────────────
# Because a GitHub ruleset restricts `control` to the Product Owner actor. If
# that ruleset is ever removed, this service keeps trusting control — that
# dependency is stated in docs/RELEASE-SERVICE.md rather than hidden here.
set -uo pipefail

RELEASE_DIR="${FRANKENSTEIN_RELEASE_DIR:-$HOME/.frankenstein/release}"
PROD_DIR="${FRANKENSTEIN_DIR:-$HOME/FrankensteinCentral}"
CONTROL_BRANCH="${FRANKENSTEIN_CONTROL_BRANCH:-control}"
PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
SUPPORTED_PROTOCOL_VERSION="${FRANKENSTEIN_PROTOCOL_VERSION:-1}"
TASK_REF_GLOB="${FRANKENSTEIN_TASK_REF_GLOB:-refs/heads/claude/*}"

MODE="run"
case "${1:-}" in
  --status)  MODE="status" ;;
  --dry-run) MODE="dry-run" ;;
  "")        ;;
  *) echo "usage: $0 [--status|--dry-run]" >&2; exit 2 ;;
esac

mkdir -p "$RELEASE_DIR"
LOG="$RELEASE_DIR/release.log"
log()  { echo "$(date -Is)  $*" | tee -a "$LOG" >&2; }
noop() { log "NO-OP: $*"; exit 0; }
fail() { log "REFUSED: $*"; exit 1; }

# ── kill switches, independent of the worker and of the deploy poller ────
[ -e "$RELEASE_DIR/DISABLED" ] && noop "DISABLED flag present"
[ -e "$RELEASE_DIR/ENABLED" ] || [ "$MODE" != "run" ] \
  || noop "not enabled ($RELEASE_DIR/ENABLED absent) — releasing nothing"

# ── single flight ────────────────────────────────────────────────────────
LOCK="$RELEASE_DIR/release.lock"
exec 9>"$LOCK"
flock -n 9 || noop "another release cycle is in flight"

REPO_URL="${FRANKENSTEIN_REPO_URL:-$(git -C "$PROD_DIR" remote get-url origin 2>/dev/null)}"
[ -n "$REPO_URL" ] || fail "no repository URL: set FRANKENSTEIN_REPO_URL"

# THE CODE THIS SERVICE IS ITSELF RUNNING. A release-service checkout is a
# COPY; it does not move when production does, so the service can be enforcing
# an older revision of its own nine-point check than the one under review. That
# is a different fact from the commit being released and is recorded as one.
SOURCE_COMMIT="$(git -C "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" \
                   rev-parse HEAD 2>/dev/null || echo unknown)"

record() {  # result, released_sha, detail
  python3 - "$RELEASE_DIR/releases.jsonl" "$1" "$2" "$3" "$MODE" "$SOURCE_COMMIT" <<'PY' 2>/dev/null
import json, sys, datetime
path, result, sha, detail, mode, source = sys.argv[1:7]
rec = {"at": datetime.datetime.now(datetime.timezone.utc)
              .isoformat(timespec="seconds").replace("+00:00", "Z"),
       "mode": mode, "result": result, "released": sha or None, "detail": detail,
       "source_commit": source or None}
with open(path, "a") as fh:
    fh.write(json.dumps(rec) + "\n")
PY
}

# ══ a fresh, trusted working clone, recreated every cycle ════════════════
WORK="$RELEASE_DIR/work"
rm -rf "$WORK"
git init --quiet "$WORK"        || fail "could not create the release clone"
git -C "$WORK" remote add origin "$REPO_URL" || fail "could not configure the release clone"

# The hook is defence in depth. The GitHub ruleset is the real barrier; this
# one makes a mistake in THIS script impossible to express as a bad push.
HOOK="$WORK/.git/hooks/pre-push"
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<'HOOKEOF'
#!/usr/bin/env bash
# Installed by release-service.sh. This clone may push exactly one ref, only
# forward, and may never delete it.
ZERO=0000000000000000000000000000000000000000
status=0
while read -r _local_ref local_sha remote_ref remote_sha; do
  if [ "$remote_ref" != "refs/heads/production" ]; then
    echo "pre-push: REFUSED — the release service may only push refs/heads/production, not $remote_ref" >&2
    status=1; continue
  fi
  if [ "$local_sha" = "$ZERO" ]; then
    echo "pre-push: REFUSED — deleting production is never permitted" >&2
    status=1; continue
  fi
  if [ -n "$remote_sha" ] && [ "$remote_sha" != "$ZERO" ]; then
    if ! git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
      echo "pre-push: REFUSED — non-fast-forward push to production would discard history" >&2
      status=1
    fi
  fi
done
exit $status
HOOKEOF
chmod +x "$HOOK"

git -C "$WORK" fetch --quiet --no-tags origin \
      "refs/heads/$PROD_BRANCH:refs/remotes/origin/$PROD_BRANCH" \
  || { record "fetch_failed" "" "production"; fail "could not fetch $PROD_BRANCH"; }
# Control may legitimately not exist yet — that is "nothing authorizes a
# release", not an operational failure, so it must not be fatal here.
git -C "$WORK" fetch --quiet --no-tags origin \
      "refs/heads/$CONTROL_BRANCH:refs/remotes/origin/$CONTROL_BRANCH" 2>/dev/null
# Task branches carry the implementation. Missing ones are not fatal here; the
# reachability check below is what actually decides.
git -C "$WORK" fetch --quiet --no-tags origin \
      "+$TASK_REF_GLOB:refs/remotes/task/*" 2>/dev/null

CONTROL_TIP="$(git -C "$WORK" rev-parse --verify --quiet "refs/remotes/origin/$CONTROL_BRANCH^{commit}")"
[ -n "$CONTROL_TIP" ] || noop "no control branch — nothing authorizes a release"

# ── the authorization epoch, NOT the control tip ─────────────────────────
# The Product Owner writes content first and flips STATE.json last. Between
# those two commits the tip has moved but nothing has been authorized, and
# reading a directive from the tip while reading state from an older commit is
# exactly how a half-written authorization gets acted on. Read everything at
# the last commit that touched STATE.json, so a content-only commit is inert.
CONTROL_COMMIT="$(git -C "$WORK" rev-list -1 "$CONTROL_TIP" -- .frankenstein/STATE.json 2>/dev/null)"
[ -n "$CONTROL_COMMIT" ] \
  || noop "no commit on control has ever written .frankenstein/STATE.json"
PROD_COMMIT="$(git -C "$WORK" rev-parse --verify --quiet "refs/remotes/origin/$PROD_BRANCH^{commit}")"
[ -n "$PROD_COMMIT" ] || fail "no $PROD_BRANCH branch on the remote"

cfile() { git -C "$WORK" show "$1:.frankenstein/$2" 2>/dev/null; }

STATE_JSON="$(cfile "$CONTROL_COMMIT" STATE.json)"
[ -n "$STATE_JSON" ] || noop "control commit carries no .frankenstein/STATE.json"

# ── condition 1-4: the state itself ──────────────────────────────────────
VERDICT="$(printf '%s' "$STATE_JSON" | python3 -c "
import json, re, sys
version = int(sys.argv[1])
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('INVALID|STATE.json is unparseable: %s' % e); raise SystemExit
if not isinstance(d, dict):
    print('INVALID|STATE.json is not an object'); raise SystemExit
if d.get('protocol_version') != version:
    print('HOLD|protocol_version %r is not the supported %r'
          % (d.get('protocol_version'), version)); raise SystemExit
if d.get('status') != 'accepted':
    print('HOLD|status is %r, not accepted' % (d.get('status'),)); raise SystemExit
if d.get('last_actor') != 'product_owner':
    print('INVALID|status is accepted but last_actor is %r, not product_owner — '
          'only the Product Owner may accept' % (d.get('last_actor'),)); raise SystemExit
if d.get('turn') == 'claude':
    print('INVALID|status is accepted while turn is still claude'); raise SystemExit
task = d.get('task_id')
if not isinstance(task, str) or not re.fullmatch(r'FC-[0-9]{3,}', task):
    print('INVALID|task_id %r is malformed' % (task,)); raise SystemExit
impl = d.get('implementation_commit') or ''
back = d.get('rollback_to') or ''
for name, val in (('implementation_commit', impl), ('rollback_to', back)):
    if val and not re.fullmatch(r'[0-9a-f]{40}', str(val)):
        print('INVALID|%s %r is not a full 40-character SHA' % (name, val))
        raise SystemExit
if impl and back:
    print('INVALID|both implementation_commit and rollback_to are set — '
          'a release and a rollback are mutually exclusive'); raise SystemExit
if not impl and not back:
    print('INVALID|status is accepted but neither implementation_commit nor '
          'rollback_to is set — nothing identifies what to release'); raise SystemExit
print('OK|%s|%s|%s|%s' % (task, impl, back, d.get('directive_commit') or ''))
" "$SUPPORTED_PROTOCOL_VERSION" 2>/dev/null)"

case "$VERDICT" in
  OK\|*)      ;;
  HOLD\|*)    noop "${VERDICT#HOLD|}" ;;
  INVALID\|*) record "state_invalid" "" "${VERDICT#INVALID|}"
              fail "${VERDICT#INVALID|}" ;;
  *)          record "state_invalid" "" "unvalidatable"
              fail "control state could not be validated — releasing nothing" ;;
esac
IFS='|' read -r _ TASK_ID IMPL ROLLBACK_TO DIRECTIVE_COMMIT <<<"$VERDICT"

# ── condition 3: explicit deployment authorization, same control commit ──
DIRECTIVE="$(cfile "$CONTROL_COMMIT" PRODUCT_DIRECTIVE.md)"
[ -n "$DIRECTIVE" ] || { record "no_directive" "" ""
  fail "control carries no PRODUCT_DIRECTIVE.md — no deployment authorization to read"; }
AUTH_CHECK="$(printf '%s' "$DIRECTIVE" | python3 -c "
import re, sys
text = sys.stdin.read()
want = sys.argv[1]
auth = re.findall(r'(?mi)^\s*Deployment Authorization:\s*(\S+)\s*$', text)
if len(auth) != 1:
    print('INVALID|PRODUCT_DIRECTIVE.md carries %d Deployment Authorization lines, '
          'expected exactly 1' % len(auth)); raise SystemExit
if auth[0] != 'deploy-approved':
    print('HOLD|Deployment Authorization is %r, not deploy-approved' % auth[0])
    raise SystemExit
ids = re.findall(r'(?m)^\s*Task ID:\s*(\S+)\s*$', text)
if len(ids) != 1 or ids[0] != want:
    print('INVALID|PRODUCT_DIRECTIVE.md must name Task ID %s exactly once; found %r'
          % (want, ids)); raise SystemExit
print('OK')
" "$TASK_ID" 2>/dev/null)"
case "$AUTH_CHECK" in
  OK)         ;;
  HOLD\|*)    noop "${AUTH_CHECK#HOLD|}" ;;
  INVALID\|*) record "authorization_invalid" "" "${AUTH_CHECK#INVALID|}"
              fail "${AUTH_CHECK#INVALID|}" ;;
  *)          record "authorization_invalid" "" "unvalidatable"
              fail "deployment authorization could not be validated" ;;
esac

# ── revocation: the epoch authorizes, but the TIP must not have withdrawn it ──
# State is read at the epoch so a half-written cycle cannot be acted on. That
# alone would make a content-only revocation invisible, because withdrawing
# `deploy-approved` edits the directive without touching STATE.json. So the
# authorization must ALSO still stand at the tip. Disagreement is never
# resolved in favour of releasing.
if [ "$CONTROL_TIP" != "$CONTROL_COMMIT" ]; then
  TIP_DIRECTIVE="$(cfile "$CONTROL_TIP" PRODUCT_DIRECTIVE.md)"
  TIP_AUTH="$(printf '%s' "$TIP_DIRECTIVE" | python3 -c "
import re, sys
text = sys.stdin.read()
auth = re.findall(r'(?mi)^\s*Deployment Authorization:\s*(\S+)\s*\$', text)
ids  = re.findall(r'(?m)^\s*Task ID:\s*(\S+)\s*\$', text)
print('%s|%s' % (auth[0] if len(auth) == 1 else '<none>',
                 ids[0] if len(ids) == 1 else '<none>'))
" 2>/dev/null)"
  case "$TIP_AUTH" in
    "deploy-approved|$TASK_ID") ;;
    *) noop "authorization no longer stands at the control tip ${CONTROL_TIP:0:7} (found '${TIP_AUTH}') — releasing nothing" ;;
  esac
fi

# ══ ROLLBACK — append-only, and authorized by the same gate ══════════════
if [ -n "$ROLLBACK_TO" ]; then
  git -C "$WORK" rev-parse --verify --quiet "$ROLLBACK_TO^{commit}" >/dev/null \
    || { record "rollback_unknown" "" "$ROLLBACK_TO"
         fail "rollback_to ${ROLLBACK_TO:0:7} is not a commit in this repository"; }
  # ── idempotence ────────────────────────────────────────────────────────
  # A rollback moves production FORWARD to a new commit carrying an OLDER
  # tree, so production never becomes equal to rollback_to. Comparing commits
  # would therefore be true on every subsequent poll and build a fresh
  # rollback commit every cycle, forever -- and a service restart would not
  # clear it, because the authorization on control has not changed.
  #
  # What a rollback actually asserts is "production should be running this
  # tree". That IS satisfied after the first one, so compare trees. This is
  # derived entirely from the repository, so it holds across restarts and
  # across a rebuilt work clone.
  WANT_TREE="$(git -C "$WORK" rev-parse --verify --quiet "$ROLLBACK_TO^{tree}")"
  HAVE_TREE="$(git -C "$WORK" rev-parse --verify --quiet "$PROD_COMMIT^{tree}")"
  [ -n "$WANT_TREE" ] && [ -n "$HAVE_TREE" ] \
    || { record "rollback_tree_unreadable" "" "$ROLLBACK_TO"
         fail "could not read the trees for the rollback comparison"; }
  [ "$WANT_TREE" != "$HAVE_TREE" ] \
    || noop "production already carries the tree of ${ROLLBACK_TO:0:7} — rollback already applied, doing nothing"
  git -C "$WORK" merge-base --is-ancestor "$ROLLBACK_TO" "$PROD_COMMIT" \
    || { record "rollback_not_released" "" "$ROLLBACK_TO"
         fail "rollback_to ${ROLLBACK_TO:0:7} is not an ancestor of production — you can only roll back to something that was actually released"; }

  # Production moves FORWARD to an older tree. Nothing is rewound, so the bad
  # deploy stays in the audit trail and the ruleset can keep force pushes and
  # deletions blocked with no exception.
  TREE="$(git -C "$WORK" rev-parse "$ROLLBACK_TO^{tree}")"
  NEW="$(git -C "$WORK" \
    -c user.name="Frankenstein Release" -c user.email="release@frankenstein.local" \
    commit-tree "$TREE" -p "$PROD_COMMIT" -m "[RELEASE] rollback production to ${ROLLBACK_TO:0:7}

Authorized by control $CONTROL_COMMIT ($TASK_ID).
Tree restored from $ROLLBACK_TO. History is preserved: production moves
forward to an earlier tree rather than being rewound.")" \
    || { record "rollback_commit_failed" "" "$ROLLBACK_TO"; fail "could not build the rollback commit"; }
  TARGET="$NEW"; KIND="rollback"; DESCRIBE="rollback to ${ROLLBACK_TO:0:7} as ${NEW:0:7}"
else
  # ══ PROMOTION ═════════════════════════════════════════════════════════
  # condition 4: the implementation exists
  git -C "$WORK" rev-parse --verify --quiet "$IMPL^{commit}" >/dev/null \
    || { record "impl_unknown" "" "$IMPL"
         fail "implementation_commit ${IMPL:0:7} is not a commit in this repository"; }

  # condition 5: reachable from a task branch, not an arbitrary loose object
  REACHABLE=""
  for ref in $(git -C "$WORK" for-each-ref --format='%(refname)' 'refs/remotes/task/*'); do
    if git -C "$WORK" merge-base --is-ancestor "$IMPL" "$ref" 2>/dev/null; then
      REACHABLE="$ref"; break
    fi
  done
  [ -n "$REACHABLE" ] || { record "impl_unreachable" "" "$IMPL"
    fail "implementation_commit ${IMPL:0:7} is not reachable from any $TASK_REF_GLOB branch"; }

  # condition 6: fast-forward only, and it must actually change something
  [ "$IMPL" != "$PROD_COMMIT" ] \
    || noop "the accepted implementation is already what production runs"
  git -C "$WORK" merge-base --is-ancestor "$PROD_COMMIT" "$IMPL" \
    || { record "impl_not_descendant" "" "$IMPL"
         fail "implementation_commit ${IMPL:0:7} does not descend from production ${PROD_COMMIT:0:7} — refusing anything but a fast-forward"; }

  # conditions 7-8: directive -> implementation -> acceptance, bound together.
  # Without this, an accepted SHA could be swapped for a different commit that
  # merely happens to descend from production.
  SNAPSHOT="$(git -C "$WORK" show "$IMPL:.frankenstein/AUTHORIZING_CONTROL_COMMIT" 2>/dev/null | tr -d ' \n\r')"
  [ -n "$SNAPSHOT" ] || { record "snapshot_missing" "" "$IMPL"
    fail "the implementation carries no .frankenstein/AUTHORIZING_CONTROL_COMMIT — it cannot be bound to a directive"; }
  git -C "$WORK" rev-parse --verify --quiet "$SNAPSHOT^{commit}" >/dev/null \
    || { record "snapshot_unknown" "" "$SNAPSHOT"
         fail "the implementation names authorizing control commit ${SNAPSHOT:0:7}, which is not a commit here"; }
  git -C "$WORK" merge-base --is-ancestor "$SNAPSHOT" "$CONTROL_COMMIT" \
    || { record "snapshot_not_on_control" "" "$SNAPSHOT"
         fail "authorizing control commit ${SNAPSHOT:0:7} is not an ancestor of the accepting control commit ${CONTROL_COMMIT:0:7} — the work was authorized by a different control lineage"; }

  BIND="$(python3 -c "
import json, sys
snap, ctl, want_task, want_directive = sys.argv[1:5]
try:
    s = json.loads(snap)
except Exception as e:
    print('INVALID|the authorizing control commit has an unreadable STATE.json: %s' % e)
    raise SystemExit
if s.get('task_id') != want_task:
    print('INVALID|the authorizing control commit is for task %r but acceptance is for %r'
          % (s.get('task_id'), want_task)); raise SystemExit
sd = s.get('directive_commit') or ''
if sd != want_directive:
    print('INVALID|directive_commit changed between authorization (%r) and acceptance (%r) — '
          'the implementation was not built from the accepted directive' % (sd, want_directive))
    raise SystemExit
print('OK')
" "$(cfile "$SNAPSHOT" STATE.json)" "$CONTROL_COMMIT" "$TASK_ID" "$DIRECTIVE_COMMIT" 2>/dev/null)"
  case "$BIND" in
    OK) ;;
    INVALID\|*) record "binding_invalid" "" "${BIND#INVALID|}"; fail "${BIND#INVALID|}" ;;
    *)  record "binding_invalid" "" "unvalidatable"
        fail "the directive-to-implementation binding could not be validated" ;;
  esac

  TARGET="$IMPL"; KIND="promotion"; DESCRIBE="promote ${IMPL:0:7} ($TASK_ID)"
fi

log "AUTHORIZED $KIND: $DESCRIBE  control=${CONTROL_COMMIT:0:7} production=${PROD_COMMIT:0:7}"

if [ "$MODE" = "status" ]; then
  echo "would $DESCRIBE"
  exit 0
fi
if [ "$MODE" = "dry-run" ]; then
  record "dry_run" "$TARGET" "$DESCRIBE"
  log "DRY RUN — nothing pushed. WOULD push $TARGET to $PROD_BRANCH"
  exit 0
fi

# ── condition 9: production has not moved since it was evaluated ─────────
PROD_NOW="$(git -C "$WORK" ls-remote origin "refs/heads/$PROD_BRANCH" 2>/dev/null | awk 'NR==1{print $1}')"
[ -n "$PROD_NOW" ] || { record "fetch_failed" "" "production re-read"
  fail "could not re-read production immediately before pushing"; }
[ "$PROD_NOW" = "$PROD_COMMIT" ] || { record "production_moved" "" "$PROD_NOW"
  fail "production moved ${PROD_COMMIT:0:7} -> ${PROD_NOW:0:7} during validation — releasing nothing"; }

# ── the single effect ────────────────────────────────────────────────────
git -C "$WORK" push --quiet origin "$TARGET:refs/heads/$PROD_BRANCH" \
  || { record "push_failed" "" "$TARGET"; fail "pushing $PROD_BRANCH failed — production unchanged"; }

record "released" "$TARGET" "$DESCRIBE"
log "RELEASED $KIND ${TARGET:0:7} to $PROD_BRANCH — the deploy poller takes it from here"
