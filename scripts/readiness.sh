#!/usr/bin/env bash
# Post-deploy readiness check: is the CORE dashboard actually usable?
#
#   bash scripts/readiness.sh                 check, print JSON, exit 0/1
#   bash scripts/readiness.sh --json-only     no human summary on stderr
#
# Started containers are NOT a working application. deploy.sh records a
# successful `docker compose up` as `success`, which says the images started —
# it says nothing about whether the gateway serves the dashboard or the
# catalog every page renders from is valid. This closes that gap.
#
# BOUNDED AND READ-ONLY, deliberately:
#   * only GET, only the gateway's own read endpoints and static assets
#   * never sync, email, calendar, financial or any other mutation endpoint
#   * finite retries and timeouts; it cannot hang a deploy
#   * it never restarts, rolls back or changes anything. It reports.
#
# REQUIRED vs OPTIONAL. A service that needs third-party credentials must not
# be able to fail a core deployment: Anthony's Gmail token expiring is not a
# broken dashboard. Required services are the ones that run entirely locally.
# Optional ones are reported as degraded and are never fatal.
set -uo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${FRANKENSTEIN_READINESS_URL:-http://localhost:8080}"
RETRIES="${FRANKENSTEIN_READINESS_RETRIES:-10}"
DELAY="${FRANKENSTEIN_READINESS_DELAY:-3}"
TIMEOUT="${FRANKENSTEIN_READINESS_TIMEOUT:-5}"

# Explicit, reviewable lists. Required = no third-party credential needed.
REQUIRED_SERVICES="${FRANKENSTEIN_REQUIRED_SERVICES:-core tasks fitness deals finance budget networth assistant}"
# Optional = needs an external account, token or server to be meaningful.
OPTIONAL_SERVICES="${FRANKENSTEIN_OPTIONAL_SERVICES:-gmail firefly plex powerbuy vault schedule stocks}"

JSON_ONLY=0
[ "${1:-}" = "--json-only" ] && JSON_ONLY=1

COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

python3 - "$BASE_URL" "$RETRIES" "$DELAY" "$TIMEOUT" "$COMMIT" \
         "$REQUIRED_SERVICES" "$OPTIONAL_SERVICES" "$JSON_ONLY" <<'PY'
import json, sys, time, urllib.request, urllib.error, datetime, re

base, retries, delay, timeout, commit, required, optional, json_only = sys.argv[1:9]
retries, delay, timeout = int(retries), float(delay), float(timeout)
required = required.split()
optional = optional.split()
json_only = json_only == "1"

SECRET = re.compile(r'(gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_\-]{16,}'
                    r'|//[^/@\s]*:[^/@\s]*@)')


def clean(text, cap=160):
    """Error text is reported, so it is scrubbed and capped like all free text."""
    t = SECRET.sub('[REDACTED]', str(text))
    return re.sub(r'\s+', ' ', t).strip()[:cap]


def get(path):
    """GET only. This function is the entire network surface of the check."""
    url = base.rstrip('/') + path
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "frankenstein-readiness"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(2_000_000)


checks = []


def add(name, req, status, detail):
    checks.append({"name": name, "required": req, "status": status,
                   "detail": clean(detail)})


# ── 1. the gateway serves the dashboard itself ──────────────────────────
html = None
last = ""
for attempt in range(retries):
    try:
        status, body = get("/")
        if status == 200 and body.strip():
            html = body.decode("utf-8", "replace")
            break
        last = f"HTTP {status}"
    except Exception as exc:
        last = clean(exc)
    time.sleep(delay)

if html is None:
    add("gateway serves the dashboard", True, "fail",
        f"no usable response from {base}/ after {retries} attempts: {last}")
else:
    # The page must actually be the hub, not a proxy error page that happens
    # to return 200.
    looks_right = "<html" in html.lower() and ("app" in html.lower())
    add("gateway serves the dashboard", True,
        "pass" if looks_right else "fail",
        "index.html served" if looks_right
        else "200 OK but the body does not look like the dashboard")

# ── 2. the app catalog every page renders from is valid ─────────────────
try:
    status, body = get("/api/apps")
    apps = json.loads(body)
    if status != 200:
        add("app catalog", True, "fail", f"HTTP {status}")
    elif not isinstance(apps, list) or not apps:
        add("app catalog", True, "fail", "catalog is empty or not a list")
    else:
        bad = [a for a in apps
               if not isinstance(a, dict) or not a.get("key") or not a.get("name")]
        add("app catalog", True, "fail" if bad else "pass",
            f"{len(bad)} malformed entries" if bad
            else f"{len(apps)} apps, all with key and name")
except Exception as exc:
    add("app catalog", True, "fail", exc)

# ── 3. service readiness, required vs optional ──────────────────────────
health = None
try:
    status, body = get("/api/health")
    if status == 200:
        health = json.loads(body)
except Exception as exc:
    add("service health endpoint", True, "fail", exc)

if health is None:
    if not any(c["name"] == "service health endpoint" for c in checks):
        add("service health endpoint", True, "fail", "no usable /api/health")
elif not isinstance(health, dict):
    add("service health endpoint", True, "fail", "/api/health is not an object")
else:
    add("service health endpoint", True, "pass", f"{len(health)} services reported")
    for key in required:
        entry = health.get(key)
        if entry is None:
            add(f"service {key}", True, "fail", "not present in /api/health")
        elif entry.get("status") == "up":
            add(f"service {key}", True, "pass", "up")
        else:
            add(f"service {key}", True, "fail", entry.get("detail", "not up"))
    for key in optional:
        entry = health.get(key)
        if entry is None:
            add(f"service {key}", False, "degraded", "not present in /api/health")
        elif entry.get("status") == "up":
            add(f"service {key}", False, "pass", "up")
        else:
            # An unconfigured third-party integration is a DEGRADED hub, not a
            # failed deployment.
            add(f"service {key}", False, "degraded",
                entry.get("detail", "not up"))

failed = [c for c in checks if c["required"] and c["status"] == "fail"]
degraded = [c["name"] for c in checks if c["status"] == "degraded"]

doc = {
    "result": "fail" if failed else "pass",
    "checked_commit": commit,
    "at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
    "base_url": base,
    "required_failed": [c["name"] for c in failed],
    "degraded": degraded,
    "checks": checks,
}
print(json.dumps(doc, indent=2, sort_keys=True))

if not json_only:
    for c in checks:
        mark = {"pass": "  ok ", "fail": "FAIL", "degraded": "deg "}[c["status"]]
        print(f"  {mark}  {c['name']}: {c['detail']}", file=sys.stderr)
    print(f"  => readiness {doc['result'].upper()} for {commit[:7]}"
          f"{' (degraded: ' + ', '.join(degraded) + ')' if degraded else ''}",
          file=sys.stderr)

sys.exit(1 if failed else 0)
PY
