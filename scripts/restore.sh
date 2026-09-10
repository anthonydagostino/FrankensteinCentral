#!/usr/bin/env bash
# Restore from a backup taken by scripts/backup.sh.
#
#   bash scripts/restore.sh <backup-dir>            restore the database
#   bash scripts/restore.sh --dry-run <backup-dir>  verify, change nothing
#   bash scripts/restore.sh --list                  what backups exist
#   bash scripts/restore.sh --drill [dir]           PROVE the newest backup
#                                                   restores. Never touches
#                                                   the live database.
#
# THE RESTORE PATH IS THE BACKUP. An untested restore is a folder of files you
# hope are useful; docs/PRODUCT_IDEAS.md #37 is blunt about this and it is
# right. tests/test_backup_restore.py runs this script for real against a real
# PostgreSQL: it writes rows, dumps, DESTROYS the database, restores, and
# compares row for row. That test is the only reason this file may be trusted.
#
# ORDER MATTERS. Verification happens BEFORE anything is dropped. A restore
# that discovers halfway through that the archive is corrupt has already
# destroyed the thing it was replacing, which is strictly worse than never
# having tried.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/data-safety.sh

BACKUP_ROOT="${FRANKENSTEIN_BACKUP_DIR:-$HOME/frankenstein-backups}"
PGHOST_="${POSTGRES_HOST:-localhost}"
PGPORT_="${POSTGRES_PORT:-5432}"
PGUSER_="${POSTGRES_USER:-frank}"
PGDB_="${POSTGRES_DB:-frankensteincentral}"

fail() { echo "RESTORE REFUSED: $1" >&2; exit 1; }

# ---- the drill --------------------------------------------------------------
# "A backup you have never restored is a belief, not a backup" (SCRUM-67). The
# only way that stops being true is to restore one, and the only way to do that
# routinely is into somewhere that is not your live database.
#
# So: restore the newest verified backup into a scratch database, compare the
# row counts against what was recorded at dump time, drop the scratch, and
# write the date down. It is safe to run on a timer, which is the point — a
# restore proven once is a fact with an expiry date on it.
#
# The comparison is what makes this more than "pg_restore exited 0". An empty
# dump restores perfectly: right checksums, right table of contents, zero rows.
# Global, not `local`: the EXIT trap below runs after drill() has returned, so
# a local would be out of scope by then and `set -u` would kill the script
# AFTER a successful drill — reporting failure for a run that passed. Caught by
# the tests, which is the only reason this comment exists rather than a bug.
SCRATCH_DB=""

drill() {
  local dir="$1"
  SCRATCH_DB="${PGDB_}_drill_$$"
  local scratch="$SCRATCH_DB"
  echo "Restore drill"
  echo "  From:    $dir"
  echo "  Into:    scratch database $scratch (the live $PGDB_ is not touched)"
  echo

  bash scripts/backup.sh --verify-only "$dir" || {
    ds_record restore failed
    fail "the archive did not verify — nothing was restored"
  }
  command -v pg_restore >/dev/null 2>&1 || { ds_record restore failed
    fail "pg_restore is not installed"; }

  local pg=(-h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_")
  export PGPASSWORD="${POSTGRES_PASSWORD:-frank}"

  # Always drop the scratch database, including on failure — a drill that
  # leaves debris behind stops being something you run on a timer.
  trap 'if [ -n "${SCRATCH_DB:-}" ]; then
          PGPASSWORD="${POSTGRES_PASSWORD:-frank}" psql -h "$PGHOST_" -p "$PGPORT_" \
            -U "$PGUSER_" -d postgres -q \
            -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\"" >/dev/null 2>&1 || true
        fi' EXIT

  psql "${pg[@]}" -d postgres -q -c "CREATE DATABASE \"$scratch\"" >/dev/null 2>&1 || {
    ds_record restore failed
    fail "could not create the scratch database"
  }

  if ! pg_restore "${pg[@]}" -d "$scratch" --no-owner --no-privileges \
        --exit-on-error "$dir/database.dump" >/dev/null 2>&1; then
    ds_record restore failed
    fail "pg_restore reported errors restoring into the scratch database"
  fi

  # What came back.
  local got total tables
  got="$(psql "${pg[@]}" -d "$scratch" -At -F $'\t' -q -c "
      SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname" 2>/dev/null || true)"
  tables="$(printf '%s\n' "$got" | grep -c . || true)"
  total="$(printf '%s\n' "$got" | awk -F'\t' '{s+=$2} END {print s+0}')"

  if [ -f "$dir/rowcounts.txt" ]; then
    # The strong check: same tables, same counts as at dump time.
    if ! diff <(sort "$dir/rowcounts.txt") <(printf '%s\n' "$got" | sort) >/dev/null; then
      echo
      echo "  Row counts do NOT match what was recorded when this backup was taken:"
      diff <(sort "$dir/rowcounts.txt") <(printf '%s\n' "$got" | sort) | head -20 || true
      ds_record restore failed
      fail "the restored data does not match the backup's own record of it"
    fi
    echo "  Verified: $tables table(s), $total row(s) — matching the backup's record."
  else
    # An older backup, taken before rowcounts were written. Say which check ran.
    [ "$tables" -gt 0 ] || { ds_record restore failed
      fail "the restore produced no tables at all"; }
    echo "  Verified: $tables table(s), $total row(s)."
    echo "  NOTE: this backup predates rowcounts.txt, so the counts could not be"
    echo "        compared against dump time — only that something came back."
  fi

  ds_record restore ok "kind=drill" "rows=$total" "tables=$tables" \
    "from=$(basename "$dir")"
  echo
  echo "Drill passed. The scratch database has been dropped."
}

newest_verified_backup() {
  while IFS= read -r d; do
    if bash scripts/backup.sh --verify-only "$d" >/dev/null 2>&1; then echo "$d"; return 0; fi
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r)
  return 1
}

if [ "${1:-}" = "--drill" ]; then
  shift
  DRILL_DIR="${1:-}"
  if [ -z "$DRILL_DIR" ]; then
    DRILL_DIR="$(newest_verified_backup)" || {
      ds_record restore failed
      fail "there is no verified backup to drill against"
    }
  fi
  drill "$DRILL_DIR"
  exit 0
fi

if [ "${1:-}" = "--list" ]; then
  echo "Backups in $BACKUP_ROOT"
  found=0
  while IFS= read -r d; do
    found=1
    if bash scripts/backup.sh --verify-only "$d" >/dev/null 2>&1; then
      echo "  ok       $(basename "$d")  $(du -sh "$d" | cut -f1)"
    else
      # Named, not hidden. A backup you cannot restore from is the single most
      # important thing on this list.
      echo "  BROKEN   $(basename "$d")"
    fi
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r)
  [ "$found" = 1 ] || echo "  (none — nothing has ever been backed up here)"
  exit 0
fi

DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi
DIR="${1:-}"
[ -n "$DIR" ] || fail "name the backup directory (or --list to see them)"

echo "Restore"
echo "  From:     $DIR"
echo "  Into:     $PGDB_ on $PGHOST_:$PGPORT_"
echo

echo "Verifying the archive before touching anything..."
bash scripts/backup.sh --verify-only "$DIR" || fail "the archive did not verify"

if [ "$DRY" = 1 ]; then
  echo
  echo "(dry run — the archive is restorable and nothing was changed.)"
  exit 0
fi

command -v pg_restore >/dev/null 2>&1 || fail "pg_restore is not installed"

echo
echo "Restoring. Existing objects in $PGDB_ will be replaced."
# --clean --if-exists drops each object before recreating it, so restoring
# over a live database converges rather than colliding.
#
# What actually protects you here is the `if` around this command: pg_restore
# exits non-zero whenever it ignored any error, and this script treats that as
# a failed restore rather than printing "restored" over a half-populated
# database. That is the covered guarantee — ignoring the exit status fails 3
# tests.
#
# --exit-on-error is damage limitation on top, NOT the thing under test: it
# stops at the first error instead of plowing through the rest of the archive.
# It does not change the exit code (PG16 returns 1 either way once errors were
# ignored), so no test here distinguishes it. Said plainly rather than left
# looking load-bearing.
if PGPASSWORD="${POSTGRES_PASSWORD:-frank}" pg_restore \
     -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$PGDB_" \
     --clean --if-exists --no-owner --no-privileges --exit-on-error \
     "$DIR/database.dump"; then
  echo
  echo "Database restored from $(basename "$DIR")."
  ds_record restore ok "kind=real" "from=$(basename "$DIR")"
else
  ds_record restore failed
  fail "pg_restore reported errors — the database may be partially restored"
fi

if [ -f "$DIR/gmail_token.tgz" ]; then
  echo
  echo "This backup also contains the Gmail token volume. It is NOT restored"
  echo "automatically — putting a credential back is a decision, not a step."
  echo "  docker run --rm -v gmail_token:/v -v $DIR:/in alpine \\"
  echo "    tar xzf /in/gmail_token.tgz -C /v"
fi
