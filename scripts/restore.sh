#!/usr/bin/env bash
# Restore from a backup taken by scripts/backup.sh.
#
#   bash scripts/restore.sh <backup-dir>            restore the database
#   bash scripts/restore.sh --dry-run <backup-dir>  verify, change nothing
#   bash scripts/restore.sh --list                  what backups exist
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

BACKUP_ROOT="${FRANKENSTEIN_BACKUP_DIR:-$HOME/frankenstein-backups}"
PGHOST_="${POSTGRES_HOST:-localhost}"
PGPORT_="${POSTGRES_PORT:-5432}"
PGUSER_="${POSTGRES_USER:-frank}"
PGDB_="${POSTGRES_DB:-frankensteincentral}"

fail() { echo "RESTORE REFUSED: $1" >&2; exit 1; }

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
else
  fail "pg_restore reported errors — the database may be partially restored"
fi

if [ -f "$DIR/gmail_token.tgz" ]; then
  echo
  echo "This backup also contains the Gmail token volume. It is NOT restored"
  echo "automatically — putting a credential back is a decision, not a step."
  echo "  docker run --rm -v gmail_token:/v -v $DIR:/in alpine \\"
  echo "    tar xzf /in/gmail_token.tgz -C /v"
fi
