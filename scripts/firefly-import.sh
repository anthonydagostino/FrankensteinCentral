#!/usr/bin/env bash
# Trigger the Firefly Data Importer without a browser, then check that the
# ledger actually moved. (SCRUM-142)
#
#   bash scripts/firefly-import.sh           trigger, verify, record
#   bash scripts/firefly-import.sh --check   print the last recorded run
#
# WHY. Nothing in this repository ever triggered an import. The FC firefly
# service is read-only, and the Data Importer only runs when someone opens it
# in a browser — so the ledger went stale until someone noticed the money card
# had paused. This is the thing to put on a timer (docs/SETUP-FIREFLY.md).
#
# THREE RESULTS, NOT TWO. "The importer returned 200" and "data entered the
# ledger" are different facts, and the gap between them is exactly the
# SCRUM-40 failure: an importer that runs happily and inserts nothing.
#
#   ok          the importer ran AND the ledger moved — rows were added
#   empty       the importer ran and the ledger did not move. A quiet banking
#               day looks like this. So does a connection that has silently
#               stopped delivering rows — the dashboard tells them apart by
#               how long the ledger has been still (data_safety.import_state)
#   failed      the importer could not be reached or refused
#   unverified  the importer ran but the FC firefly service could not answer,
#               so nothing can be said about the ledger
#
# The verdict is written through scripts/data-safety.sh into the same record
# the backup and drill use, so the assistant reads it through the mount it
# already has. `last_import_at` moves ONLY on `ok`.
#
# THE SECRET NEVER TOUCHES ARGV, STDOUT OR THE RECORD. It is written to a
# 0600 temp file and handed to curl via @file, so it is not in `ps`, not in
# this script's output, and not in any log line. The importer's response body
# is saved to the state directory (0600) rather than printed: it can echo the
# configuration it received.
set -uo pipefail
cd "$(dirname "$0")/.."
. scripts/data-safety.sh

STATE_DIR="$(ds_state_dir)"
mkdir -p "$STATE_DIR"

# ---- configuration: environment first, then .env, never echoed ------------
envget() {  # envget KEY -> value from the environment, else from .env, else ""
  local key="$1"
  if [ -n "${!key:-}" ]; then printf '%s' "${!key}"; return; fi
  [ -f .env ] || return 0
  python3 - "$key" <<'PYENV'
import sys
key = sys.argv[1]
for line in open(".env", encoding="utf-8", errors="replace"):
    line = line.strip()
    if line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    if k.strip() == key:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        print(v, end="")
        break
PYENV
}

IMPORTER_URL="$(envget FIREFLY_IMPORTER_INTERNAL_URL)"
[ -n "$IMPORTER_URL" ] || IMPORTER_URL="$(envget FIREFLY_IMPORTER_URL)"
IMPORTER_URL="${IMPORTER_URL%/}"
SECRET="$(envget FIREFLY_IMPORTER_SECRET)"
IMPORT_DIR="$(envget FIREFLY_IMPORT_DIR)"
IMPORT_CONFIG="$(envget FIREFLY_IMPORT_CONFIG)"
FIREFLY_SVC="${FIREFLY_SVC_URL:-http://localhost:${FIREFLY_SVC_PORT:-8097}}"
WAIT="${FIREFLY_IMPORT_WAIT:-120}"
INTERVAL="${FIREFLY_IMPORT_INTERVAL:-5}"
TRIGGER_TIMEOUT="${FIREFLY_IMPORT_TIMEOUT:-900}"

if [ "${1:-}" = "--check" ]; then
  python3 - "$(ds_record_path)" <<'PYCHK'
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    print("no import has ever been recorded here"); sys.exit(0)
for k in ("last_import_attempt_at", "last_import_result", "last_import_reason",
          "last_import_at", "import_rows", "import_kind"):
    if k in doc:
        print(f"  {k:26} {doc[k]}")
PYCHK
  exit 0
fi

fail() {  # fail <reason> <message>
  ds_record import failed "reason=$1"
  echo "IMPORT FAILED ($1): $2" >&2
  exit 1
}

# ---- refuse loudly when unconfigured, naming the key and never the value ---
[ -n "$IMPORTER_URL" ] || { ds_record import failed "reason=not_configured"
  echo "IMPORT NOT CONFIGURED: set FIREFLY_IMPORTER_URL in .env" >&2; exit 2; }
[ -n "$SECRET" ] || { ds_record import failed "reason=not_configured"
  echo "IMPORT NOT CONFIGURED: set FIREFLY_IMPORTER_SECRET in .env (the importer's AUTO_IMPORT_SECRET)" >&2; exit 2; }
if [ -n "$IMPORT_CONFIG" ]; then
  [ -f "$IMPORT_CONFIG" ] || { ds_record import failed "reason=not_configured"
    echo "IMPORT NOT CONFIGURED: FIREFLY_IMPORT_CONFIG names a file that does not exist" >&2; exit 2; }
  MODE="autoupload"
elif [ -n "$IMPORT_DIR" ]; then
  MODE="autoimport"
else
  ds_record import failed "reason=not_configured"
  echo "IMPORT NOT CONFIGURED: set FIREFLY_IMPORT_DIR (a directory the importer" >&2
  echo "  allows in IMPORT_DIR_ALLOWLIST) or FIREFLY_IMPORT_CONFIG (a config JSON)" >&2
  exit 2
fi

# ---- the secret goes to curl through a file, never through argv -----------
SECRET_FILE="$(mktemp "${TMPDIR:-/tmp}/fc-import.XXXXXX")"
BODY_FILE="$STATE_DIR/import-last-response.txt"
chmod 600 "$SECRET_FILE"
printf '%s' "$SECRET" > "$SECRET_FILE"
unset SECRET
trap 'rm -f "$SECRET_FILE"' EXIT

echo "Firefly import"
echo "  Importer:  $IMPORTER_URL  ($MODE)"
echo "  Verify via: $FIREFLY_SVC/freshness"

# ---- baseline: what the ledger holds before we touch anything -------------
probe() {  # -> "<count>|<stamp>" or "" when the firefly service cannot answer
  curl -sS --max-time 20 "$FIREFLY_SVC/freshness" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not d.get("connected"):
    sys.exit(0)
count = d.get("txn_total_90d")
stamp = d.get("ingest_latest_at") or ""
print(("" if count is None else str(count)) + "|" + str(stamp))
'
}
BEFORE="$(probe)"
[ -n "$BEFORE" ] || echo "  (the firefly service did not answer; the ledger cannot be checked afterwards)"

# ---- trigger ----------------------------------------------------------------
: > "$BODY_FILE"; chmod 600 "$BODY_FILE"
if [ "$MODE" = "autoimport" ]; then
  CODE="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' --max-time "$TRIGGER_TIMEOUT" \
           -X POST "$IMPORTER_URL/autoimport" \
           --data-urlencode "secret@$SECRET_FILE" \
           --data-urlencode "directory=$IMPORT_DIR" 2>/dev/null)" || CODE="000"
else
  CODE="$(curl -sS -o "$BODY_FILE" -w '%{http_code}' --max-time "$TRIGGER_TIMEOUT" \
           -X POST "$IMPORTER_URL/autoupload" \
           -F "secret=<$SECRET_FILE" \
           -F "json=@$IMPORT_CONFIG" 2>/dev/null)" || CODE="000"
fi
rm -f "$SECRET_FILE"

case "$CODE" in
  000) fail "importer_unreachable" "$IMPORTER_URL did not answer" ;;
  2*)  echo "  importer:  HTTP $CODE (response saved to $BODY_FILE)" ;;
  *)   fail "importer_http_$CODE" "the importer answered HTTP $CODE (response saved to $BODY_FILE)" ;;
esac

# ---- verify: did the ledger move? ------------------------------------------
if [ -z "$BEFORE" ]; then
  ds_record import unverified "kind=$MODE" "reason=firefly_unreachable"
  echo "  verdict:   UNVERIFIED — the importer ran, but the firefly service could not say whether anything entered"
  exit 1
fi
BEFORE_N="${BEFORE%%|*}"; BEFORE_T="${BEFORE#*|}"
DEADLINE=$(( $(date +%s) + WAIT ))
while :; do
  AFTER="$(probe)"
  AFTER_N="${AFTER%%|*}"; AFTER_T="${AFTER#*|}"
  MOVED=0
  if [ -n "$AFTER_N" ] && [ -n "$BEFORE_N" ] && [ "$AFTER_N" -gt "$BEFORE_N" ] 2>/dev/null; then MOVED=1; fi
  if [ -n "$AFTER_T" ] && [ "$AFTER_T" \> "$BEFORE_T" ]; then MOVED=1; fi
  if [ "$MOVED" = 1 ]; then
    ROWS=""
    [ -n "$AFTER_N" ] && [ -n "$BEFORE_N" ] && ROWS=$(( AFTER_N - BEFORE_N ))
    ds_record import ok "kind=$MODE" "rows=${ROWS:-unknown}" "ingest_at=${AFTER_T:-unknown}"
    echo "  verdict:   OK — the ledger moved (${ROWS:-?} new transaction(s) in the last 90 days)"
    exit 0
  fi
  [ "$(date +%s)" -ge "$DEADLINE" ] && break
  sleep "$INTERVAL"
done
ds_record import empty "kind=$MODE" "reason=nothing_entered"
echo "  verdict:   EMPTY — the importer ran, nothing entered the ledger within ${WAIT}s."
echo "             A quiet day looks like this. So does a connection that has stopped"
echo "             delivering rows; the dashboard reads the two apart by how long the"
echo "             ledger has been still."
exit 0
