#!/usr/bin/env bash
# Shared: record what happened to the data-safety record. SOURCED, not run.
#
#   . scripts/data-safety.sh
#   ds_record backup  ok    "taken_at=... dir=..."
#   ds_record restore ok    "kind=drill rows=1234 from=..."
#
# WHY ONE FILE. Three things have to agree on this record's shape: backup.sh
# writes half of it, restore.sh writes the other half, and the assistant reads
# all of it to answer "how many days since the last verified restore". Two of
# those are shell and one is Python, so the format cannot be enforced by a
# type — only by there being exactly one place that writes it.
#
# WHERE IT LIVES. The same state directory as the deploy record, deliberately:
# outside the repo, so `git reset --hard` during a deploy cannot erase it, and
# already mounted read-only into the assistant container. Adding a second
# mount for a second file would be a second thing to get wrong.
#
# WHAT IT NEVER HOLDS. No credential, no row contents, no path inside the
# token volume. Counts, timestamps and verdicts only.

ds_state_dir() { echo "${FRANKENSTEIN_STATE_DIR:-$HOME/.frankenstein}"; }
ds_record_path() { echo "$(ds_state_dir)/data-safety.json"; }

# ds_record <kind: backup|restore> <result: ok|failed> [key=value ...]
#
# Merges into the existing record rather than replacing it: a backup must not
# erase the last restore's date, which is the number this whole feature exists
# to show. A failed run updates `last_*_attempt_at` and `last_*_result` and
# deliberately leaves `last_*_at` — the last SUCCESS — alone, so a run of
# failures cannot make the dashboard look freshly safe.
ds_record() {
  local kind="$1" result="$2"; shift 2
  local path; path="$(ds_record_path)"
  mkdir -p "$(dirname "$path")" 2>/dev/null || return 0
  python3 - "$path" "$kind" "$result" "$@" <<'PY' 2>/dev/null || true
import json, sys, datetime
path, kind, result = sys.argv[1:4]
extra = {}
for pair in sys.argv[4:]:
    if "=" in pair:
        key, value = pair.split("=", 1)
        extra[key] = value
try:
    with open(path) as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        doc = {}
except Exception:
    doc = {}
now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
doc[f"last_{kind}_attempt_at"] = now
doc[f"last_{kind}_result"] = result
if result == "ok":
    doc[f"last_{kind}_at"] = now
    for key, value in extra.items():
        doc[f"{kind}_{key}"] = value
with open(path, "w") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
PY
}
