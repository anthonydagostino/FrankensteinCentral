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
#   * only GET, only the gateway's own read endpoints and its OWN static
#     assets; never a sync, email, calendar, financial or mutation endpoint
#   * one overall time budget covers every retry, so it cannot hang a deploy
#   * same-origin only: asset URLs carrying a scheme or host are refused, so a
#     compromised or misconfigured page cannot make this fetch the internet
#   * it never restarts, rolls back or changes anything. It reports.
#
# REQUIRED vs OPTIONAL. A service that needs third-party credentials must not
# be able to fail a core deployment: an expired Gmail token is not a broken
# dashboard. Required services run entirely locally and are database-backed,
# so their health endpoints are also the DB evidence. Optional ones are
# reported as degraded and are never fatal.
set -uo pipefail
cd "$(dirname "$0")/.."

BASE_URL="${FRANKENSTEIN_READINESS_URL:-http://localhost:8080}"
BUDGET="${FRANKENSTEIN_READINESS_BUDGET:-90}"
DELAY="${FRANKENSTEIN_READINESS_DELAY:-3}"
TIMEOUT="${FRANKENSTEIN_READINESS_TIMEOUT:-5}"

# Explicit, reviewable lists. Required = no third-party credential needed.
REQUIRED_SERVICES="${FRANKENSTEIN_REQUIRED_SERVICES:-core tasks fitness deals finance budget networth assistant}"
# Optional = needs an external account, token or server to be meaningful.
OPTIONAL_SERVICES="${FRANKENSTEIN_OPTIONAL_SERVICES:-gmail firefly plex powerbuy vault schedule stocks}"
# Structure that identifies THIS dashboard, not merely "some HTML".
REQUIRED_MARKERS="${FRANKENSTEIN_READINESS_MARKERS:-cc-grid cc-money cc-donext cc-today}"

JSON_ONLY=0
[ "${1:-}" = "--json-only" ] && JSON_ONLY=1

COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

python3 - "$BASE_URL" "$BUDGET" "$DELAY" "$TIMEOUT" "$COMMIT" \
         "$REQUIRED_SERVICES" "$OPTIONAL_SERVICES" "$REQUIRED_MARKERS" \
         "$JSON_ONLY" <<'PY'
import json, sys, time, urllib.request, urllib.parse, datetime, re

(base, budget, delay, timeout, commit,
 required, optional, markers, json_only) = sys.argv[1:10]
budget, delay, timeout = float(budget), float(delay), float(timeout)
required, optional, markers = required.split(), optional.split(), markers.split()
json_only = json_only == "1"

DEADLINE = time.monotonic() + budget
MAX_ASSETS = 8

SECRET = re.compile(r'(gh[pousr]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_\-]{16,}'
                    r'|//[^/@\s]*:[^/@\s]*@)')


def clean(text, cap=160):
    t = SECRET.sub('[REDACTED]', str(text))
    return re.sub(r'\s+', ' ', t).strip()[:cap]


def left():
    return DEADLINE - time.monotonic()


def get(path):
    """GET only. This function is the entire network surface of the check."""
    url = base.rstrip('/') + path
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "frankenstein-readiness"})
    with urllib.request.urlopen(req, timeout=min(timeout, max(left(), 0.1))) as r:
        return r.status, r.read(2_000_000), r.headers.get("Content-Type", "")


checks = []


def add(name, req, status, detail):
    checks.append({"name": name, "required": req, "status": status,
                   "detail": clean(detail)})


def retry(fn):
    """Run fn() until it succeeds or the shared budget is spent."""
    last = "not attempted"
    while left() > 0:
        try:
            ok, info = fn()
            if ok:
                return True, info
            last = info
        except Exception as exc:
            last = clean(exc)
        if left() <= delay:
            break
        time.sleep(delay)
    return False, last


# -- 1. the gateway serves THIS dashboard --------------------------------
def fetch_home():
    status, body, _ = get("/")
    if status != 200:
        return False, "HTTP %s" % status
    html = body.decode("utf-8", "replace")
    missing = [m for m in markers if m not in html]
    if missing:
        # A proxy or error page can return 200 and contain the word
        # "Application". Only this dashboard's own structure counts.
        return False, ("200 OK but the page is missing dashboard structure: "
                       + ", ".join(missing[:4]))
    return True, html


ok, home = retry(fetch_home)
html = home if ok else None
add("gateway serves the dashboard", True, "pass" if ok else "fail",
    "all %d dashboard markers present" % len(markers) if ok else home)

# -- 2. the entry page's own JS and CSS actually load ---------------------
# A gateway that serves index.html while its static assets 404 looks fine to
# an HTML-only check, and the page renders nothing.
if html:
    refs = (re.findall(r'<script[^>]+src="([^"]+)"', html)
            + re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html))
    assets, skipped = [], []
    for ref in refs:
        parsed = urllib.parse.urlparse(ref)
        if parsed.scheme or parsed.netloc:
            skipped.append(ref)          # never follow off-origin URLs
            continue
        if not ref.startswith("/"):
            ref = "/" + ref
        if ref not in assets:
            assets.append(ref)
    if skipped:
        add("static assets are same-origin", False, "degraded",
            "%d off-origin asset reference(s) not fetched" % len(skipped))
    if not assets:
        add("dashboard assets", True, "fail",
            "the page references no same-origin script or stylesheet")
    for ref in assets[:MAX_ASSETS]:
        expect_js = ref.endswith(".js")
        try:
            status, body, ctype = get(ref)
        except Exception as exc:
            add("asset %s" % ref, True, "fail", exc)
            continue
        text = body.decode("utf-8", "replace").lstrip()
        if status != 200:
            add("asset %s" % ref, True, "fail", "HTTP %s" % status)
        elif not body.strip():
            add("asset %s" % ref, True, "fail", "empty response")
        elif text[:1] == "<" or "<!doctype html" in text[:200].lower():
            # An SPA fallback or error page served in place of the asset.
            add("asset %s" % ref, True, "fail",
                "HTML was served where JavaScript/CSS was expected")
        elif expect_js and "html" in ctype.lower():
            add("asset %s" % ref, True, "fail",
                "served as %s rather than JavaScript" % clean(ctype, 40))
        else:
            add("asset %s" % ref, True, "pass", "%d bytes" % len(body))

# -- 3. the app catalog every page renders from is valid -----------------
def fetch_catalog():
    status, body, _ = get("/api/apps")
    if status != 200:
        return False, "HTTP %s" % status
    apps = json.loads(body)
    if not isinstance(apps, list) or not apps:
        return False, "catalog is empty or not a list"
    bad = [a for a in apps
           if not isinstance(a, dict) or not a.get("key") or not a.get("name")]
    if bad:
        return False, "%d malformed entries" % len(bad)
    return True, "%d apps, all with key and name" % len(apps)


ok, info = retry(fetch_catalog)
add("app catalog", True, "pass" if ok else "fail", info)


# -- 4. service readiness, required vs optional --------------------------
def entry_status(entry):
    """A malformed entry is a structured failure, never an exception."""
    if not isinstance(entry, dict):
        return None, "malformed health entry: %s" % type(entry).__name__
    return entry.get("status"), entry.get("detail", "")


last_health = {}


def fetch_health():
    status, body, _ = get("/api/health")
    if status != 200:
        return False, "HTTP %s" % status
    health = json.loads(body)
    if not isinstance(health, dict):
        return False, "/api/health is not an object"
    # Keep the payload even when the required check below fails, so optional
    # services are still classified. Reporting "core is down" while saying
    # nothing about the other seven hides most of the picture.
    last_health.clear()
    last_health.update(health)
    missing = []
    for key in required:
        if key not in health:
            missing.append("%s absent" % key)
            continue
        st, detail = entry_status(health[key])
        if st != "up":
            missing.append("%s %s" % (key, clean(detail or st, 40)))
    if missing:
        # Required services get the SAME retry budget as the page itself:
        # they may still be starting while the gateway already answers.
        return False, "; ".join(missing[:4])
    return True, health


ok, info = retry(fetch_health)
if ok:
    add("required services", True, "pass", "all %d up" % len(required))
else:
    add("required services", True, "fail", info)
health = last_health if last_health else None

if isinstance(health, dict):
    for key in optional:
        if key not in health:
            add("service %s" % key, False, "degraded", "not present in /api/health")
            continue
        st, detail = entry_status(health[key])
        if st == "up":
            add("service %s" % key, False, "pass", "up")
        else:
            # An unconfigured third-party integration is a DEGRADED hub, not a
            # failed deployment.
            add("service %s" % key, False, "degraded", detail or st or "not up")

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
        print("  %s  %s: %s" % (mark, c["name"], c["detail"]), file=sys.stderr)
    print("  => readiness %s for %s%s"
          % (doc["result"].upper(), commit[:7],
             " (degraded: " + ", ".join(degraded) + ")" if degraded else ""),
          file=sys.stderr)

sys.exit(1 if failed else 0)
PY
