#!/usr/bin/env bash
# Dump every stateful thing on the box that the repository cannot rebuild, so a
# restic/Backrest snapshot has something worth archiving.  (SCRUM-33)
#
#   bash scripts/dump-all.sh                 dump everything into $FRANKENSTEIN_DUMP_DIR/current
#   bash scripts/dump-all.sh --verify-only <dir>
#
# Backrest runs this as a CONDITION_SNAPSHOT_START hook with onError FATAL, so a
# non-zero exit here cancels the snapshot. That is the whole contract: restic
# must never archive a half-written or empty dump with a green tick on it.
#
# WHAT IS DUMPED, and how each is known rather than assumed
#
#   dashboard    The hub's own Postgres + Gmail token. Delegated to
#                scripts/backup.sh — the shipped, restore-tested mechanism
#                (tests/test_backup_restore.py). Called, not copied: one restore
#                path, one set of tests.
#   firefly      Firefly III's database. Its ENGINE IS NOT DOCUMENTED ANYWHERE
#                in this repo, and the ticket says "MariaDB or Postgres". So it
#                is read from the firefly container's own environment
#                (DB_CONNECTION / DB_HOST / DB_DATABASE — Firefly's .env.example
#                names them) and the database container is resolved from that.
#                Nothing about the engine is guessed; if it cannot be resolved
#                to exactly one container this fails and says why.
#   vaultwarden  /data: the SQLite database through vaultwarden's own `backup`
#                subcommand (VACUUM INTO a db_<stamp>.sqlite3 beside the live
#                file — src/db/mod.rs), then everything else in /data (rsa_key*,
#                attachments/, sends/, config.json) as a tarball. The live
#                db.sqlite3 is never copied: WAL mode loses committed rows.
#   pihole       A Teleporter archive: `pihole-FTL --teleporter` (v6) writes a
#                zip into its cwd and prints the name.
#
# Container names default to the measured inventory in
# docs/DEPLOYMENT-BASELINE.md ("Neighbours on the same box") and are env-
# overridable. A container that is not running is a FAILURE, not a skip — a
# target is only "skipped" when DUMP_SKIP names it on purpose.
#
# HONESTY RULES — the same ones scripts/backup.sh and the money layer run on:
#   * Every target ends in exactly one of ok / failed: <why> / skipped: <why>,
#     and the three are never collapsed. The verdict is written last.
#   * A dump below DUMP_MIN_BYTES is a FAILED dump. Size is asserted per file.
#   * Any failure => exit 1, and `current` is left pointing at the previous
#     complete set. A new `current` appears only by atomic rename of a fully
#     verified staging directory.
#   * No secret ever reaches the host. Database passwords are read INSIDE the
#     database container from its own environment (MYSQL_PWD for MariaDB); they
#     are not passed on argv, not printed, and not written to the manifest.
set -euo pipefail
cd "$(dirname "$0")/.."

DUMP_ROOT="${FRANKENSTEIN_DUMP_DIR:-/srv/dumps}"
MIN_BYTES="${DUMP_MIN_BYTES:-1024}"
KEEP_FAILED="${DUMP_KEEP_FAILED:-3}"
SKIP=",${DUMP_SKIP:-},"                      # e.g. DUMP_SKIP=pihole,vaultwarden

FIREFLY_CTR="${FIREFLY_CONTAINER:-firefly}"
FIREFLY_DB_CTR="${FIREFLY_DB_CONTAINER:-}"    # empty => resolve from Firefly's DB_HOST
VW_CTR="${VAULTWARDEN_CONTAINER:-vaultwarden}"
PIHOLE_CTR="${PIHOLE_CONTAINER:-pihole}"

TARGETS=(dashboard firefly vaultwarden pihole)
declare -A STATE

fail() { echo "DUMP FAILED: $1" >&2; exit 1; }
record() { STATE["$1"]="$2"; echo "  $(printf '%-12s' "$1:") $2"; }
skipped() { case "$SKIP" in *",$1,"*) return 0;; esac; return 1; }
ctr_running() { docker ps --format '{{.Names}}' 2>/dev/null | grep -qx -- "$1"; }
assert_min() {  # file label -> 0 if large enough
  local size; size=$(stat -c %s "$1" 2>/dev/null || echo 0)
  [ "$size" -ge "$MIN_BYTES" ]
}
clip() { head -c 200 | tr '\n' ' '; }

# ---- verify: is this directory a complete dump set? -------------------------
verify_dump() {
  local dir="$1"
  [ -d "$dir" ] || { echo "  no such dump: $dir"; return 1; }
  [ -f "$dir/manifest.txt" ] || { echo "  no manifest — not a completed dump"; return 1; }
  grep -q '^result: ok$' "$dir/manifest.txt" || {
    echo "  manifest does not record a successful dump"; return 1; }
  [ -f "$dir/SHA256SUMS" ] || { echo "  no SHA256SUMS — cannot prove the contents are intact"; return 1; }
  ( cd "$dir" && sha256sum --quiet -c SHA256SUMS ) || {
    echo "  CHECKSUM MISMATCH — this dump set is corrupt"; return 1; }
  echo "  verified: $dir"
}

if [ "${1:-}" = "--verify-only" ]; then
  [ -n "${2:-}" ] || fail "--verify-only needs a dump directory"
  verify_dump "$2" || exit 1
  exit 0
fi

# ---- preflight --------------------------------------------------------------
command -v docker >/dev/null 2>&1 || fail "docker is not installed; nothing can be dumped"
docker ps >/dev/null 2>&1 || fail "docker daemon is not reachable; nothing can be dumped"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is not installed"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DUMP_ROOT"
STAGE="$DUMP_ROOT/.staging-$STAMP"
mkdir -p "$STAGE"
echo "FrankensteinCentral dump-all"
echo "  Staging:   $STAGE"

# ---- dashboard: the hub's own Postgres + Gmail token ------------------------
dump_dashboard() {
  local out="$STAGE/dashboard"; mkdir -p "$out"
  # backup.sh keeps its own retention; here it is one run into one directory.
  if FRANKENSTEIN_BACKUP_DIR="$out" FRANKENSTEIN_BACKUP_KEEP=1 \
       bash scripts/backup.sh > "$out/backup.log" 2>&1; then
    record dashboard ok
  else
    record dashboard "failed: scripts/backup.sh exited non-zero ($(grep -m1 'FAILED' "$out/backup.log" | clip))"
  fi
}

# ---- firefly: engine and container discovered, never assumed ----------------
resolve_firefly_db() {  # prints the db container name, or nothing
  [ -n "$FIREFLY_DB_CTR" ] && { echo "$FIREFLY_DB_CTR"; return; }
  local host="$1" project cands
  project=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' \
              "$FIREFLY_CTR" 2>/dev/null || true)
  if [ -n "$project" ]; then
    # DB_HOST is a compose SERVICE name, resolved by compose DNS — the container
    # is usually named <project>-<service>-1, so match on the labels, not the name.
    cands=$(docker ps --format '{{.Names}}' \
              --filter "label=com.docker.compose.project=$project" \
              --filter "label=com.docker.compose.service=$host" 2>/dev/null || true)
  fi
  [ -n "${cands:-}" ] || cands=$(docker ps --format '{{.Names}}' --filter "name=^${host}\$" 2>/dev/null | grep -x -- "$host" || true)
  echo "$cands"
}

dump_firefly() {
  local out="$STAGE/firefly"; mkdir -p "$out"
  ctr_running "$FIREFLY_CTR" || { record firefly "failed: container '$FIREFLY_CTR' is not running (set FIREFLY_CONTAINER)"; return; }
  local conn host db
  conn=$(docker exec "$FIREFLY_CTR" printenv DB_CONNECTION 2>/dev/null || true)
  host=$(docker exec "$FIREFLY_CTR" printenv DB_HOST 2>/dev/null || true)
  db=$(docker exec "$FIREFLY_CTR" printenv DB_DATABASE 2>/dev/null || true)
  [ -n "$conn" ] || { record firefly "failed: '$FIREFLY_CTR' has no DB_CONNECTION in its environment"; return; }

  local file
  case "$conn" in
    mysql|pgsql)
      local dbctr n
      dbctr=$(resolve_firefly_db "$host")
      n=$(printf '%s\n' "$dbctr" | grep -c . || true)
      if [ "$n" -ne 1 ]; then
        # One line: a manifest value must never carry a newline.
        record firefly "failed: DB_HOST='$host' resolves to $n containers ($(printf '%s' "${dbctr:-none}" | tr '\n' ' ')); set FIREFLY_DB_CONTAINER"
        return
      fi
      ctr_running "$dbctr" || { record firefly "failed: database container '$dbctr' is not running"; return; }
      echo "  firefly:     $conn on '$dbctr' (database: $db)"
      if [ "$conn" = mysql ]; then
        file="$out/firefly.sql"
        # The root password never leaves the container: read from ITS env, handed
        # to the client through MYSQL_PWD (not argv), inside the exec.
        docker exec "$dbctr" sh -c '
          PW="${MARIADB_ROOT_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}"
          [ -n "$PW" ] || { echo "no MARIADB_ROOT_PASSWORD / MYSQL_ROOT_PASSWORD in the db container" >&2; exit 3; }
          D=$(command -v mariadb-dump || command -v mysqldump) || { echo "neither mariadb-dump nor mysqldump in the db image" >&2; exit 4; }
          MYSQL_PWD="$PW" exec "$D" --single-transaction --quick --all-databases -uroot
        ' > "$file" 2>"$out/dump.err" || {
          record firefly "failed: mariadb dump ($(clip < "$out/dump.err"))"; return; }
      else
        file="$out/firefly.dump"
        docker exec -e FF_DB="$db" "$dbctr" sh -c \
          'exec pg_dump -U "${POSTGRES_USER:-postgres}" --format=custom "$FF_DB"' \
          > "$file" 2>"$out/dump.err" || {
          record firefly "failed: pg_dump ($(clip < "$out/dump.err"))"; return; }
      fi ;;
    sqlite)
      # The database is a file inside the APP container. Never cp it: WAL mode.
      file="$out/firefly.sqlite"
      echo "  firefly:     sqlite inside '$FIREFLY_CTR'"
      docker exec "$FIREFLY_CTR" sh -c '
        command -v sqlite3 >/dev/null 2>&1 || { echo "sqlite3 is not in the firefly image; a WAL-safe copy needs it" >&2; exit 4; }
        F="${DB_DATABASE:-/var/www/html/storage/database/database.sqlite}"
        sqlite3 "$F" ".backup /tmp/ff-dump.sqlite" && cat /tmp/ff-dump.sqlite; rc=$?; rm -f /tmp/ff-dump.sqlite; exit $rc
      ' > "$file" 2>"$out/dump.err" || {
        record firefly "failed: sqlite backup ($(clip < "$out/dump.err"))"; return; } ;;
    *)
      record firefly "failed: DB_CONNECTION='$conn' is not mysql/pgsql/sqlite"; return ;;
  esac
  rm -f "$out/dump.err"
  assert_min "$file" || { record firefly "failed: $(basename "$file") is under $MIN_BYTES bytes — an empty dump is not a dump"; return; }
  record firefly ok
}

# ---- vaultwarden: its own backup subcommand, then the rest of /data ---------
dump_vaultwarden() {
  local out="$STAGE/vaultwarden"; mkdir -p "$out"
  ctr_running "$VW_CTR" || { record vaultwarden "failed: container '$VW_CTR' is not running (set VAULTWARDEN_CONTAINER)"; return; }
  local msg path
  if ! msg=$(docker exec "$VW_CTR" /vaultwarden backup 2>&1); then
    record vaultwarden "failed: 'vaultwarden backup' ($(printf '%s' "$msg" | clip))"; return
  fi
  # "Backup to '/data/db_20260909_140000.sqlite3' was successful"
  path=$(printf '%s\n' "$msg" | sed -n "s/.*Backup to '\([^']*\)'.*/\1/p" | head -1)
  [ -n "$path" ] || { record vaultwarden "failed: could not read the backup path from: $(printf '%s' "$msg" | clip)"; return; }
  docker cp "$VW_CTR:$path" "$out/db.sqlite3" >/dev/null 2>&1 || {
    record vaultwarden "failed: docker cp of $path"; return; }
  # Do not let /data fill with VACUUM copies.
  docker exec "$VW_CTR" rm -f -- "$path" >/dev/null 2>&1 || true
  assert_min "$out/db.sqlite3" || { record vaultwarden "failed: db.sqlite3 is under $MIN_BYTES bytes"; return; }
  # Keys, attachments, sends, config — everything the database alone cannot restore.
  docker exec "$VW_CTR" sh -c \
    'cd /data && tar czf - --exclude="./db.sqlite3*" --exclude="./db_*.sqlite3" --exclude="./tmp" .' \
    > "$out/data-files.tgz" 2>"$out/tar.err" || {
    record vaultwarden "failed: tar of /data ($(clip < "$out/tar.err"))"; return; }
  rm -f "$out/tar.err"
  # Size is the wrong test for a gzip'd tarball: a real but small /data compresses
  # to a few hundred bytes. The right test is CONTENT — without the RSA key the
  # restored database cannot decrypt a single vault item.
  tar tzf "$out/data-files.tgz" 2>/dev/null | grep -q 'rsa_key' || {
    record vaultwarden "failed: data-files.tgz contains no rsa_key* — the database is unusable without it"; return; }
  record vaultwarden ok
}

# ---- pihole: a Teleporter archive ------------------------------------------
dump_pihole() {
  local out="$STAGE/pihole"; mkdir -p "$out"
  ctr_running "$PIHOLE_CTR" || { record pihole "failed: container '$PIHOLE_CTR' is not running (set PIHOLE_CONTAINER)"; return; }
  local msg name src
  if ! msg=$(docker exec -w /tmp "$PIHOLE_CTR" pihole-FTL --teleporter 2>&1); then
    record pihole "failed: pihole-FTL --teleporter ($(printf '%s' "$msg" | clip))"; return
  fi
  name=$(printf '%s\n' "$msg" | grep -o '[^[:space:]]*\.zip' | tail -1)
  [ -n "$name" ] || { record pihole "failed: teleporter printed no .zip name: $(printf '%s' "$msg" | clip)"; return; }
  case "$name" in /*) src="$name";; *) src="/tmp/$name";; esac
  docker cp "$PIHOLE_CTR:$src" "$out/teleporter.zip" >/dev/null 2>&1 || {
    record pihole "failed: docker cp of $src"; return; }
  docker exec "$PIHOLE_CTR" rm -f -- "$src" >/dev/null 2>&1 || true
  assert_min "$out/teleporter.zip" || { record pihole "failed: teleporter.zip is under $MIN_BYTES bytes"; return; }
  record pihole ok
}

# ---- run --------------------------------------------------------------------
for t in "${TARGETS[@]}"; do
  if skipped "$t"; then record "$t" "skipped: named in DUMP_SKIP"; continue; fi
  "dump_$t"
done

# ---- checksums, verdict, publish --------------------------------------------
( cd "$STAGE" && find . -type f ! -name SHA256SUMS ! -name manifest.txt -print0 \
    | sort -z | xargs -0 sha256sum > SHA256SUMS )

FAILED=0; TAKEN=0
for t in "${TARGETS[@]}"; do
  case "${STATE[$t]}" in ok) TAKEN=$((TAKEN+1));; failed*) FAILED=1;; esac
done
[ "$TAKEN" -gt 0 ] || { FAILED=1; echo "  (every target was skipped — a dump of nothing is not a dump)"; }

{
  echo "result: $([ "$FAILED" -eq 0 ] && echo ok || echo failed)"
  echo "taken_at: $STAMP"
  for t in "${TARGETS[@]}"; do echo "$t: ${STATE[$t]}"; done
} > "$STAGE/manifest.txt"

if [ "$FAILED" -ne 0 ]; then
  # Keep the evidence, keep the last good set, and make the snapshot NOT happen.
  mv "$STAGE" "$DUMP_ROOT/failed-$STAMP"
  mapfile -t OLD < <(find "$DUMP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'failed-*' | sort)
  if [ "${#OLD[@]}" -gt "$KEEP_FAILED" ]; then
    for d in "${OLD[@]:0:$((${#OLD[@]} - KEEP_FAILED))}"; do rm -rf "$d"; done
  fi
  fail "one or more targets failed — see $DUMP_ROOT/failed-$STAMP/manifest.txt; 'current' is unchanged"
fi

verify_dump "$STAGE" >/dev/null || { mv "$STAGE" "$DUMP_ROOT/failed-$STAMP"; fail "the staged set did not verify"; }

# Atomic publish: readers (restic) only ever see a complete, verified `current`.
rm -rf "$DUMP_ROOT/current.prev"
[ -d "$DUMP_ROOT/current" ] && mv "$DUMP_ROOT/current" "$DUMP_ROOT/current.prev"
mv "$STAGE" "$DUMP_ROOT/current"
rm -rf "$DUMP_ROOT/current.prev"

echo
echo "Dump complete: $DUMP_ROOT/current  ($TAKEN of ${#TARGETS[@]} targets)"
