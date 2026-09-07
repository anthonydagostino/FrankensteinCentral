#!/usr/bin/env bash
# Post-deploy readiness check: is the CORE dashboard actually usable?
#
#   bash scripts/readiness.sh                 check, print JSON, exit 0/1
#   bash scripts/readiness.sh --json-only     no human summary on stderr
#
# Started containers are NOT a working application. deploy.sh records a
# successful compose start as `success`, which says the images started — it
# says nothing about whether the gateway serves the dashboard or the catalog
# every page renders from is valid. This closes that gap.
#
# A 200 IS NOT THE HUB. "<html" plus the substring "app" matches
# `<html><body>Application unavailable</body></html>` — an error page passed
# this check. So the entry page must now carry stable dashboard-specific
# structure, AND the same-origin JS/CSS it actually declares must load with a
# plausible type and body. A hub whose scripts 404, or whose scripts come back
# as an HTML fallback page, is a blank screen for the user however green the
# containers look.
#
# BOUNDED AND READ-ONLY, deliberately:
#   * only GET, and only the gateway's own read endpoints plus the assets the
#     entry page itself names. Asset URLs that are not same-origin relative
#     paths are never fetched — a compromised or misconfigured page cannot
#     point this check at an arbitrary host.
#   * one overall time budget shared by every retry; it cannot hang a deploy
#   * it never restarts, reverts or changes anything. It reports.
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

# Structure the hub has and an error page does not. Kept in one place, and
# pinned to the real gateway/static/index.html by a test, so a marker that
# drifts out of the page fails in CI rather than on the box after a deploy.
STRUCTURE_MARKERS="${FRANKENSTEIN_READINESS_MARKERS:-cc-grid cc-donext cc-money}"

# The assets the dashboard cannot render without. Checking "whatever the page
# happens to declare" is not enough: a page that simply stops declaring its
# script still passes, because there is nothing left to fail on. Each of these
# must be BOTH declared by the entry page AND fetched successfully. Pinned to
# gateway/static/index.html by a test.
ESSENTIAL_ASSETS="${FRANKENSTEIN_READINESS_ASSETS:-/app.js /home.js /styles.css /home.css}"

JSON_ONLY=0
[ "${1:-}" = "--json-only" ] && JSON_ONLY=1

COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

python3 - "$BASE_URL" "$RETRIES" "$DELAY" "$TIMEOUT" "$COMMIT" \
         "$REQUIRED_SERVICES" "$OPTIONAL_SERVICES" "$JSON_ONLY" \
         "$STRUCTURE_MARKERS" "$ESSENTIAL_ASSETS" <<'PY'
import json, sys, time, urllib.request, urllib.error, datetime, re

(base, retries, delay, timeout, commit, required, optional, json_only,
 markers, essential) = sys.argv[1:11]
retries, delay, timeout = int(retries), float(delay), float(timeout)
required = required.split()
optional = optional.split()
markers = markers.split()
essential = essential.split()
json_only = json_only == "1"

# ONE budget for the whole check, shared by every retry loop. Retrying the
# entry page and then retrying service health must not be able to add up to
# more wall clock than the deploy was promised.
BUDGET = retries * (delay + timeout) + timeout
DEADLINE = time.monotonic() + BUDGET
MAX_ASSETS = 12          # bounded: the entry page cannot enlarge this check

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
        ctype = (r.headers.get("Content-Type") or "").lower()
        return r.status, r.read(2_000_000), ctype


def attempt(fn):
    """Retry fn until it reports success, attempts run out, or the shared
    budget is spent. fn returns (ok, value, detail).

    The LAST value is kept, not discarded, so a failing check can still report
    per-service detail from the response it did get rather than collapsing to
    "no usable /api/health" when the endpoint answered perfectly well.
    """
    last, last_value = "no attempt was made", None
    for i in range(max(1, retries)):
        try:
            ok, value, detail = fn()
            if ok:
                return True, value, detail
            last, last_value = detail, value
        except Exception as exc:              # noqa: BLE001 - reported, not raised
            last = clean(exc)
        if i + 1 >= retries or time.monotonic() + delay >= DEADLINE:
            break
        time.sleep(delay)
    return False, last_value, last


checks = []


def add(name, req, status, detail):
    checks.append({"name": name, "required": req, "status": status,
                   "detail": clean(detail)})


# ── 1. the gateway serves the dashboard itself ──────────────────────────
def fetch_index():
    status, body, ctype = get("/")
    if status != 200:
        return False, None, "HTTP %s" % status
    if not body.strip():
        return False, None, "200 OK with an empty body"
    if "html" not in ctype and ctype:
        return False, None, "200 OK but Content-Type is %s" % clean(ctype, 60)
    return True, body.decode("utf-8", "replace"), "index.html served"


ok, html, detail = attempt(fetch_index)
if not ok:
    add("gateway serves the dashboard", True, "fail",
        "no usable response from %s/ after %s attempts: %s"
        % (base, retries, detail))
else:
    # Substring "app" is not evidence: "Application unavailable" contains it.
    # Require the hub's own structure.
    missing = [m for m in markers if m not in html]
    if "<html" not in html.lower():
        add("gateway serves the dashboard", True, "fail",
            "200 OK but the body is not an HTML document")
    elif missing:
        add("gateway serves the dashboard", True, "fail",
            "200 OK but the dashboard structure is absent (missing: %s)"
            % ", ".join(missing))
    else:
        add("gateway serves the dashboard", True, "pass",
            "index.html served with %d structure markers" % len(markers))

# ── 1b. the entry page's own JS and CSS actually load ───────────────────
# A hub whose scripts 404, or whose scripts are answered with an HTML
# fallback page, renders as a blank screen. Only same-origin relative paths
# the page itself declares are fetched; anything absolute, protocol-relative
# or scheme-bearing is recorded and skipped, never requested.
def same_origin_assets(doc):
    found, skipped = [], []
    raw = [(m.group(1), "js") for m in
           re.finditer(r'<script\b[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']', doc, re.I)]
    for tag in re.finditer(r'<link\b[^>]*>', doc, re.I):
        t = tag.group(0)
        if not re.search(r'\brel\s*=\s*["\']?[^"\'>]*stylesheet', t, re.I):
            continue
        href = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', t, re.I)
        if href:
            raw.append((href.group(1), "css"))
    for url, kind in raw:
        u = url.strip()
        if (not u or u.startswith('//') or '://' in u
                or re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', u)):
            skipped.append(u)
            continue
        path = u if u.startswith('/') else '/' + u
        if (path, kind) not in found:
            found.append((path, kind))
    return found[:MAX_ASSETS], skipped


TYPE_OK = {"js": ("javascript", "ecmascript"), "css": ("css",)}

if html is not None:
    assets, external = same_origin_assets(html)
    if external:
        add("entry page assets are same-origin", True, "pass",
            "%d non-same-origin asset reference(s) not requested" % len(external))
    # An asset that is not declared cannot fail a fetch, so absence has to be
    # its own check. Compared on the path alone, so a cache-busting query
    # string does not read as a different file.
    declared = {p.split('?')[0].split('#')[0] for p, _ in assets}
    absent = [e for e in essential if e not in declared]
    add("entry page declares its essential assets", True,
        "fail" if absent else "pass",
        "the entry page does not reference: %s" % ", ".join(absent) if absent
        else "all %d essential assets are referenced" % len(essential))

    if not assets:
        add("entry page assets", True, "fail",
            "the entry page declares no same-origin JS or CSS to verify")
    else:
        for path, kind in assets:
            def fetch_asset(path=path, kind=kind):
                status, body, ctype = get(path)
                if status != 200:
                    return False, None, "HTTP %s" % status
                if not body.strip():
                    return False, None, "200 OK with an empty body"
                head = body.lstrip()[:200].lower()
                if head.startswith(b"<!doctype") or head.startswith(b"<html"):
                    return False, None, ("200 OK but an HTML document was served "
                                         "as %s" % kind)
                if ctype and not any(t in ctype for t in TYPE_OK[kind]):
                    return False, None, ("200 OK but Content-Type is %s, not %s"
                                         % (clean(ctype, 60), kind))
                return True, None, "%d bytes, %s" % (len(body), clean(ctype, 40))
            ok, _, detail = attempt(fetch_asset)
            add("asset %s" % path, True, "pass" if ok else "fail", detail)

# ── 2. the app catalog every page renders from is valid ─────────────────
def fetch_catalog():
    status, body, _ = get("/api/apps")
    if status != 200:
        return False, None, "HTTP %s" % status
    apps = json.loads(body)
    if not isinstance(apps, list) or not apps:
        return False, None, "catalog is empty or not a list"
    bad = [a for a in apps
           if not isinstance(a, dict) or not a.get("key") or not a.get("name")]
    if bad:
        return False, None, "%d malformed entries" % len(bad)
    return True, apps, "%d apps, all with key and name" % len(apps)


ok, _, detail = attempt(fetch_catalog)
add("app catalog", True, "pass" if ok else "fail", detail)


# ── 3. service readiness, required vs optional ──────────────────────────
# Retried within the same budget: a required service that is still starting
# when the HTML is already served is the ordinary case, not a failure.
def entry_status(entry):
    """A health entry can be anything the service felt like emitting. Malformed
    shapes are reported as a structured result, never raised."""
    if isinstance(entry, dict):
        status = entry.get("status")
        detail = entry.get("detail")
        if not isinstance(detail, str):
            detail = None
        if isinstance(status, str):
            return status, detail or status
        return None, "malformed health entry: status is %s" % type(status).__name__
    return None, "malformed health entry: %s, expected an object" % type(entry).__name__


def fetch_health():
    status, body, _ = get("/api/health")
    if status != 200:
        return False, None, "HTTP %s" % status
    health = json.loads(body)
    if not isinstance(health, dict):
        return False, None, "/api/health is not an object"
    down = []
    for key in required:
        entry = health.get(key)
        if entry is None:
            down.append(key)
            continue
        state, _ = entry_status(entry)
        if state != "up":
            down.append(key)
    if down:
        return False, health, "required service(s) not up: %s" % ", ".join(down)
    return True, health, "%d services reported" % len(health)


ok, health, detail = attempt(fetch_health)

if health is None:
    add("service health endpoint", True, "fail", "no usable /api/health: %s" % detail)
else:
    add("service health endpoint", True, "pass", "%d services reported" % len(health))
    for key in required:
        entry = health.get(key)
        if entry is None:
            add("service %s" % key, True, "fail", "not present in /api/health")
            continue
        state, why = entry_status(entry)
        add("service %s" % key, True, "pass" if state == "up" else "fail",
            "up" if state == "up" else why)
    for key in optional:
        entry = health.get(key)
        if entry is None:
            add("service %s" % key, False, "degraded", "not present in /api/health")
            continue
        state, why = entry_status(entry)
        # An unconfigured third-party integration is a DEGRADED hub, not a
        # failed deployment. A malformed entry from one is degraded too.
        add("service %s" % key, False, "pass" if state == "up" else "degraded",
            "up" if state == "up" else why)

failed = [c for c in checks if c["required"] and c["status"] == "fail"]
degraded = [c["name"] for c in checks if c["status"] == "degraded"]

doc = {
    "result": "fail" if failed else "pass",
    "checked_commit": commit,
    "at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
    "base_url": base,
    "budget_seconds": round(BUDGET, 1),
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
