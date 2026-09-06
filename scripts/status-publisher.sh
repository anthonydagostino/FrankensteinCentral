#!/usr/bin/env bash
# Publish the release/deployment record to the remotely readable `status`
# branch, so the Product Owner can see what happened without shell access to
# the box and without Anthony relaying anything.
#
#   bash scripts/status-publisher.sh --dry-run   build and print, push nothing
#   bash scripts/status-publisher.sh             build and push if changed
#
# It is DELIBERATELY a different actor from the release service. That service's
# entire guarantee is that its only effect on the world is one fast-forward
# push to production; giving it a second ref to write would destroy that. This
# script therefore holds no production credential and its own pre-push hook
# permits exactly one ref: refs/heads/status.
#
# It reports. It never decides anything, never promotes, and never deploys.
#
# PRIVACY: the published record contains SHAs, protocol state, timestamps and
# result codes only. It never includes credentials, tokens, environment,
# command output, file contents, email or financial data. Free-text detail from
# local records is scrubbed and length-capped before publication.
set -euo pipefail
cd "$(dirname "$0")/.."

STATUS_BRANCH="${FRANKENSTEIN_STATUS_BRANCH:-status}"
CONTROL_BRANCH="${FRANKENSTEIN_CONTROL_BRANCH:-control}"
PROD_BRANCH="${FRANKENSTEIN_BRANCH:-production}"
STATE_DIR="${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}"
RELEASE_DIR="${FRANKENSTEIN_RELEASE_DIR:-$STATE_DIR/release}"
DEPLOYED="${FRANKENSTEIN_DEPLOYED:-$STATE_DIR/deployed.json}"
WORKROOT="${FRANKENSTEIN_STATUS_WORK:-$STATE_DIR/status}"

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry-run" ;;
  "") ;;
  *) echo "unknown argument: $1" >&2; exit 2 ;;
esac

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

REPO_URL="${FRANKENSTEIN_REPO_URL:-$(git remote get-url origin 2>/dev/null || true)}"
[ -n "$REPO_URL" ] || fail "no repository URL: set FRANKENSTEIN_REPO_URL"

mkdir -p "$WORKROOT"
WORK="$WORKROOT/work"
rm -rf "$WORK"
git init --quiet "$WORK" || fail "could not create the status clone"
git -C "$WORK" remote add origin "$REPO_URL" || fail "could not configure the status clone"

# One ref, forward-only in spirit; status is an orphan branch that is replaced
# wholesale, so the hook enforces only WHICH ref may be written.
HOOK="$WORK/.git/hooks/pre-push"
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<HOOKEOF
#!/usr/bin/env bash
# Installed by status-publisher.sh. This clone may push exactly one ref.
ZERO=0000000000000000000000000000000000000000
status=0
while read -r _l local_sha remote_ref _r; do
  if [ "\$remote_ref" != "refs/heads/$STATUS_BRANCH" ]; then
    echo "pre-push: REFUSED — the status publisher may only push refs/heads/$STATUS_BRANCH, not \$remote_ref" >&2
    status=1; continue
  fi
  if [ "\$local_sha" = "\$ZERO" ]; then
    echo "pre-push: REFUSED — deleting $STATUS_BRANCH is never permitted" >&2
    status=1
  fi
done
exit \$status
HOOKEOF
chmod +x "$HOOK"

git -C "$WORK" fetch --quiet --no-tags origin \
  "refs/heads/$PROD_BRANCH:refs/remotes/origin/$PROD_BRANCH" 2>/dev/null || true
git -C "$WORK" fetch --quiet --no-tags origin \
  "refs/heads/$CONTROL_BRANCH:refs/remotes/origin/$CONTROL_BRANCH" 2>/dev/null || true
git -C "$WORK" fetch --quiet --no-tags origin \
  "refs/heads/$STATUS_BRANCH:refs/remotes/origin/$STATUS_BRANCH" 2>/dev/null || true

PROD_COMMIT="$(git -C "$WORK" rev-parse --verify --quiet "refs/remotes/origin/$PROD_BRANCH^{commit}" || true)"
CONTROL_TIP="$(git -C "$WORK" rev-parse --verify --quiet "refs/remotes/origin/$CONTROL_BRANCH^{commit}" || true)"
CONTROL_EPOCH=""
if [ -n "$CONTROL_TIP" ]; then
  CONTROL_EPOCH="$(git -C "$WORK" rev-list -1 "$CONTROL_TIP" -- .frankenstein/STATE.json 2>/dev/null || true)"
fi
CONTROL_STATE=""
if [ -n "$CONTROL_EPOCH" ]; then
  CONTROL_STATE="$(git -C "$WORK" show "$CONTROL_EPOCH:.frankenstein/STATE.json" 2>/dev/null || true)"
fi
CONTROL_DIRECTIVE=""
if [ -n "$CONTROL_EPOCH" ]; then
  CONTROL_DIRECTIVE="$(git -C "$WORK" show "$CONTROL_EPOCH:.frankenstein/PRODUCT_DIRECTIVE.md" 2>/dev/null || true)"
fi

RECORD="$WORK/.frankenstein/RELEASE_STATUS.json"
mkdir -p "$(dirname "$RECORD")"

python3 - "$RECORD" "$PROD_COMMIT" "$CONTROL_TIP" "$CONTROL_EPOCH" "$DEPLOYED" \
         "$RELEASE_DIR/releases.jsonl" "$CONTROL_STATE" "$CONTROL_DIRECTIVE" <<'PY'
import json, re, sys, datetime, os

(out, prod, ctl_tip, ctl_epoch, deployed_path, releases_path,
 ctl_state_raw, ctl_directive) = sys.argv[1:9]

SECRET = re.compile(
    r'(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}'
    r'|sk-[A-Za-z0-9_\-]{16,}|//[^/@\s]*:[^/@\s]*@)')

def scrub(text, cap=200):
    """Never publish free text verbatim: redact credential shapes, cap length."""
    if not isinstance(text, str):
        return None
    t = SECRET.sub('[REDACTED]', text)
    t = re.sub(r'\s+', ' ', t).strip()
    return t[:cap]

def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}

def sha_or_none(v):
    return v if isinstance(v, str) and re.fullmatch(r'[0-9a-f]{40}', v) else None

state = {}
if ctl_state_raw:
    try:
        state = json.loads(ctl_state_raw)
    except Exception:
        state = {}
if not isinstance(state, dict):
    state = {}

auth = None
if ctl_directive:
    found = re.findall(r'(?mi)^\s*Deployment Authorization:\s*(\S+)\s*$', ctl_directive)
    auth = found[0] if len(found) == 1 else None

deployed = load_json(deployed_path)

releases = []
try:
    with open(releases_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    releases.append(json.loads(line))
                except Exception:
                    pass
except Exception:
    pass
real = [r for r in releases if r.get("mode") == "run"]
last_release = real[-1] if real else None

accepted = sha_or_none(state.get("implementation_commit"))
rollback = sha_or_none(state.get("rollback_to"))
running = sha_or_none(deployed.get("running_commit"))
attempted = sha_or_none(deployed.get("last_attempt_commit"))
prod = sha_or_none(prod)

# PROMOTION and DEPLOYMENT are different facts and are never collapsed:
# production may hold a commit that has not deployed, or that failed to deploy.
promoted_not_deployed = bool(prod and running and prod != running)
deploy_result = deployed.get("last_result")

failures = []
for r in real[-10:]:
    if r.get("result") not in ("released", "dry_run", None):
        failures.append({"at": r.get("at"), "result": scrub(r.get("result"), 60),
                         "detail": scrub(r.get("detail"))})
if deploy_result and deploy_result != "success":
    failures.append({"at": deployed.get("last_attempt_at"),
                     "result": "deploy_" + scrub(str(deploy_result), 60),
                     "detail": "deploy of %s did not succeed" % ((attempted or "?")[:7])})

doc = {
  "schema": 1,
  "generated_at": datetime.datetime.now(datetime.timezone.utc)
                    .isoformat(timespec="seconds").replace("+00:00", "Z"),
  "control": {
    "tip": sha_or_none(ctl_tip),
    "authorization_epoch": sha_or_none(ctl_epoch),
    "task_id": state.get("task_id") if isinstance(state.get("task_id"), str) else None,
    "turn": state.get("turn") if isinstance(state.get("turn"), str) else None,
    "status": state.get("status") if isinstance(state.get("status"), str) else None,
    "deployment_authorization": auth,
  },
  "accepted": {
    "implementation_commit": accepted,
    "rollback_to": rollback,
  },
  "promotion": {
    "production_commit": prod,
    "accepted_is_promoted": bool(accepted and prod and accepted == prod),
    "last_release_result": scrub(last_release.get("result"), 60) if last_release else None,
    "last_release_at": last_release.get("at") if last_release else None,
    "last_released_sha": sha_or_none(last_release.get("released")) if last_release else None,
  },
  "deployment": {
    "running_commit": running,
    "desired_commit": prod,
    "in_sync": bool(prod and running and prod == running),
    "promoted_but_not_running": promoted_not_deployed,
    "last_attempt_commit": attempted,
    "last_attempt_at": deployed.get("last_attempt_at"),
    "last_result": scrub(str(deploy_result), 60) if deploy_result else None,
    "last_success_at": deployed.get("last_success_at"),
  },
  "verification": {
    # scripts/test.sh runs inside deploy.sh and ABORTS the deploy on failure,
    # so a successful deployment is itself the verification signal. A separate
    # verdict is only meaningful once a post-deploy check publishes one.
    "result": ("pass" if deploy_result == "success"
               else ("fail" if deploy_result in ("tests_failed", "compose_failed")
                     else "unknown")),
    "source": "deploy gate (scripts/test.sh inside deploy.sh)",
    "at": deployed.get("last_attempt_at"),
  },
  "failures": failures[-10:],
}

with open(out, "w") as fh:
    fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print(json.dumps(doc, indent=2, sort_keys=True))
PY

if [ "$MODE" = "dry-run" ]; then
  log "DRY RUN — nothing pushed."
  exit 0
fi

# Idempotence: publish only when the content actually changed, so a 2-minute
# timer does not produce a commit every 2 minutes forever.
PREV=""
if git -C "$WORK" rev-parse --verify --quiet "refs/remotes/origin/$STATUS_BRANCH^{commit}" >/dev/null; then
  PREV="$(git -C "$WORK" show "refs/remotes/origin/$STATUS_BRANCH:.frankenstein/RELEASE_STATUS.json" 2>/dev/null || true)"
fi
NEXT="$(cat "$RECORD")"
# generated_at always differs, so compare everything else.
strip_ts() { python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('x'); raise SystemExit
d.pop('generated_at', None)
print(json.dumps(d, sort_keys=True))"; }
if [ -n "$PREV" ] \
   && [ "$(printf '%s' "$PREV" | strip_ts)" = "$(printf '%s' "$NEXT" | strip_ts)" ]; then
  log "status unchanged — nothing published"
  exit 0
fi

git -C "$WORK" checkout --quiet --orphan "$STATUS_BRANCH" 2>/dev/null || true
git -C "$WORK" reset --quiet
git -C "$WORK" add -f .frankenstein/RELEASE_STATUS.json
git -C "$WORK" \
  -c user.name="Frankenstein Status" -c user.email="status@frankenstein.local" \
  commit --quiet -m "[STATUS] production=${PROD_COMMIT:0:7} running=$(python3 -c "
import json,sys
try: print((json.load(open(sys.argv[1])).get('running_commit') or '')[:7] or 'none')
except Exception: print('unknown')" "$DEPLOYED")" \
  || fail "could not commit the status record"

git -C "$WORK" push --quiet --force origin "HEAD:refs/heads/$STATUS_BRANCH" \
  || fail "publishing $STATUS_BRANCH failed"
log "published $STATUS_BRANCH"
