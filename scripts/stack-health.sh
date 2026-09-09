#!/usr/bin/env bash
# Is the stack actually SERVING? (SCRUM-107)
#
#   bash scripts/stack-health.sh            wait up to 60s for the stack to be up
#   bash scripts/stack-health.sh --once     one look, no waiting
#   FRANKENSTEIN_HEALTH_TIMEOUT=90 bash scripts/stack-health.sh
#
# Exit 0 = every container is up and nothing is crash-looping. Exit 1 = it is
# not, and the names are printed.
#
# WHY THIS EXISTS. `docker compose up -d` returns when containers have been
# STARTED, not when they are serving. A container that starts and immediately
# crash-loops satisfies it completely. deploy.sh recorded `success` straight
# after that call, so `deployed.json` claimed `running_commit: <sha>` for a
# stack that might be entirely down — and autopull.sh treats that field as
# ground truth, concludes DESIRED == RUNNING, and STOPS RETRYING. The box then
# sits on a broken deploy while the deployment system believes it converged.
#
# That is the same wedge autopull.sh's own docstring records having been burned
# by once already: "Comparing HEAD (the earlier behavior) made that state look
# converged and permanently suppressed the retry." The comparison was fixed;
# the thing being compared was still never verified. This verifies it.
#
# WHAT COUNTS AS UP, and what deliberately does not:
#   * `restarting` is NOT up. It is what a crash-loop looks like from outside,
#     and it is the exact state this ticket is about.
#   * `exited` / `dead` are not up, whatever the exit code. A one-shot that has
#     finished cleanly would read as down here; there are none in this stack,
#     and inventing an exception for a service that does not exist would only
#     make room for a broken one to hide.
#   * a container reporting health `unhealthy` is not up even while `running`.
#   * `starting` is not yet up — it is why this polls rather than looking once.
#   * NO CONTAINERS AT ALL is not up. An empty list means nothing is serving;
#     reading it as "nothing is broken" is how a stack that never started
#     reports success.
set -uo pipefail
cd "$(dirname "$0")/.."

TIMEOUT="${FRANKENSTEIN_HEALTH_TIMEOUT:-60}"
INTERVAL="${FRANKENSTEIN_HEALTH_INTERVAL:-3}"
[ "${1:-}" = "--once" ] && TIMEOUT=0

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "stack-health: no docker compose available" >&2
  exit 1
fi

# One look. Prints the not-up services, one per line; empty output means up.
snapshot() {
  $DC ps --format json 2>/dev/null | python3 -c '
import json, sys

raw = sys.stdin.read().strip()
if not raw:
    # No containers is not health. See the header.
    print("(no containers)")
    sys.exit(0)

# compose v2 emits one JSON object per line; older builds emit a single array.
rows = []
try:
    parsed = json.loads(raw)
    rows = parsed if isinstance(parsed, list) else [parsed]
except ValueError:
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            # An unparseable line is not evidence of health.
            print("(unreadable compose output)")
            sys.exit(0)

if not rows:
    print("(no containers)")
    sys.exit(0)

for r in rows:
    if not isinstance(r, dict):
        print("(unreadable compose output)")
        continue
    name = r.get("Service") or r.get("Name") or "?"
    state = (r.get("State") or "").lower()
    health = (r.get("Health") or "").lower()
    if state != "running":
        shown = state or "unknown"
        print(f"{name} ({shown})")
    elif health in ("unhealthy", "starting"):
        print(f"{name} ({health})")
'
}

DEADLINE=$(( $(date +%s) + TIMEOUT ))
while :; do
  DOWN="$(snapshot)"
  if [ -z "$DOWN" ]; then
    echo "stack-health: all services up"
    exit 0
  fi
  [ "$(date +%s)" -ge "$DEADLINE" ] && break
  sleep "$INTERVAL"
done

echo "stack-health: NOT serving after ${TIMEOUT}s —"
echo "$DOWN" | sed 's/^/  /'
exit 1
