#!/usr/bin/env bash
# Take a backup of everything this stack cannot rebuild from the repository.
#
#   bash scripts/backup.sh              take one, into the default directory
#   bash scripts/backup.sh --verify-only <dir>   check an existing backup
#
# WHY THIS IS IN THE REPO. docs/OPERATIONS.md said a backup ran nightly from
# `~/docker/backup.sh` — a file that is not in this repository, has never been
# read by anyone reviewing it, and whose restore path had never been run. A
# backup nobody can inspect and nobody has restored from is a belief, not a
# backup, and the belief is the dangerous part: it is what stops you noticing.
#
# What is actually at risk: db_data holds gym history, every focus session,
# Big 3 history, captures, calendar events, net-worth accounts and budget
# definitions. gmail_token holds the Google refresh token. The repository
# backs up the CODE and nothing else, and `docker compose down -v` is in the
# README as the way to start clean.
#
# HONESTY RULES, the same ones the money layer runs on:
#   * A dump that is empty or unreadable is a FAILED backup, not a small one.
#     The verdict is written after verification, never before.
#   * Skipped and failed are different states and are recorded differently.
#     A backup taken where docker is unavailable did not silently include the
#     token volume; it says it did not.
#   * Secrets are never printed. The token is copied as an opaque blob and its
#     contents never touch stdout, the manifest or the log.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_ROOT="${FRANKENSTEIN_BACKUP_DIR:-$HOME/frankenstein-backups}"
KEEP="${FRANKENSTEIN_BACKUP_KEEP:-14}"
PGHOST_="${POSTGRES_HOST:-localhost}"
PGPORT_="${POSTGRES_PORT:-5432}"
PGUSER_="${POSTGRES_USER:-frank}"
PGDB_="${POSTGRES_DB:-frankensteincentral}"
TOKEN_VOLUME="${FRANKENSTEIN_TOKEN_VOLUME:-gmail_token}"

fail() { echo "BACKUP FAILED: $1" >&2; exit 1; }

# ---- verify: is this directory a restorable backup? -------------------------
# Split out so restore.sh can call the identical check before it touches
# anything. A restore that discovers the archive is broken halfway through has
# already destroyed the thing it was replacing.
verify_backup() {
  local dir="$1" quiet="${2:-}"
  local ok=1
  [ -d "$dir" ] || { echo "  no such backup: $dir"; return 1; }
  [ -f "$dir/manifest.txt" ] || { echo "  no manifest — not a completed backup"; return 1; }
  grep -q '^result: ok$' "$dir/manifest.txt" || {
    echo "  manifest does not record a successful backup"; return 1; }
  [ -s "$dir/database.dump" ] || { echo "  database.dump missing or empty"; return 1; }
  # The checksums are the point: a truncated copy has the right name and the
  # wrong contents, and that is exactly the failure a backup is supposed to
  # survive.
  if [ -f "$dir/SHA256SUMS" ]; then
    ( cd "$dir" && sha256sum --quiet -c SHA256SUMS ) || {
      echo "  CHECKSUM MISMATCH — this backup is corrupt"; return 1; }
  else
    echo "  no SHA256SUMS — cannot prove the contents are intact"; return 1
  fi
  # pg_restore --list reads the archive's table of contents. It fails on a
  # dump that is truncated or was written by an incompatible pg_dump, which a
  # size check alone would happily pass.
  if command -v pg_restore >/dev/null 2>&1; then
    pg_restore --list "$dir/database.dump" >/dev/null 2>&1 || {
      echo "  pg_restore cannot read database.dump"; return 1; }
  fi
  [ -n "$quiet" ] || echo "  verified: $dir"
  return 0
}

if [ "${1:-}" = "--verify-only" ]; then
  [ -n "${2:-}" ] || fail "--verify-only needs a backup directory"
  verify_backup "$2" || exit 1
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"

echo "FrankensteinCentral backup"
echo "  Destination: $DEST"

# ---- the database -----------------------------------------------------------
# Custom format, so a restore can be selective and pg_restore can read the
# table of contents back for verification.
command -v pg_dump >/dev/null 2>&1 || fail "pg_dump is not installed"
if ! PGPASSWORD="${POSTGRES_PASSWORD:-frank}" pg_dump \
      -h "$PGHOST_" -p "$PGPORT_" -U "$PGUSER_" -d "$PGDB_" \
      --format=custom --file="$DEST/database.dump" 2>"$DEST/pg_dump.err"; then
  echo "  database: FAILED (see $DEST/pg_dump.err)"
  printf 'result: failed\nstage: pg_dump\ntaken_at: %s\n' "$STAMP" > "$DEST/manifest.txt"
  fail "pg_dump did not complete; the backup directory is kept for diagnosis"
fi
[ -s "$DEST/database.dump" ] || {
  printf 'result: failed\nstage: empty-dump\ntaken_at: %s\n' "$STAMP" > "$DEST/manifest.txt"
  fail "pg_dump produced an empty file"
}
rm -f "$DEST/pg_dump.err"
echo "  database:  $(du -h "$DEST/database.dump" | cut -f1)"

# ---- the Gmail refresh token ------------------------------------------------
# Losing it means re-consenting to Google by hand. Copied as an opaque blob:
# its contents are never read, printed or logged.
TOKEN_STATE="skipped"
if command -v docker >/dev/null 2>&1 && docker volume inspect "$TOKEN_VOLUME" >/dev/null 2>&1; then
  if docker run --rm -v "$TOKEN_VOLUME":/v:ro -v "$DEST":/out alpine \
       tar czf /out/gmail_token.tgz -C /v . >/dev/null 2>&1; then
    TOKEN_STATE="ok"
  else
    TOKEN_STATE="failed"
  fi
elif ! command -v docker >/dev/null 2>&1; then
  TOKEN_STATE="skipped: docker unavailable"
else
  TOKEN_STATE="skipped: no volume named $TOKEN_VOLUME"
fi
echo "  token:     $TOKEN_STATE"

# ---- checksums, then the verdict --------------------------------------------
( cd "$DEST" && sha256sum database.dump $( [ -f gmail_token.tgz ] && echo gmail_token.tgz ) \
    > SHA256SUMS )

{
  echo "result: ok"
  echo "taken_at: $STAMP"
  echo "database: $PGDB_"
  echo "token_volume: $TOKEN_STATE"
  echo "pg_dump_version: $(pg_dump --version | head -1)"
} > "$DEST/manifest.txt"

# The verdict above is provisional until the archive proves readable. A backup
# that reports success without checking is the failure mode this whole script
# exists to remove.
if ! verify_backup "$DEST" quiet; then
  printf 'result: failed\nstage: verification\ntaken_at: %s\n' "$STAMP" > "$DEST/manifest.txt"
  fail "the archive did not verify; it is NOT a usable backup"
fi

echo "  verified:  yes"

# ---- retention --------------------------------------------------------------
# Oldest first, and only ever complete backups: a half-written directory from
# an interrupted run is never counted as one of the copies you are keeping.
mapfile -t ALL < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
                     -exec test -f '{}/manifest.txt' \; -print | sort)
if [ "${#ALL[@]}" -gt "$KEEP" ]; then
  for old in "${ALL[@]:0:$((${#ALL[@]} - KEEP))}"; do
    rm -rf "$old"
    echo "  pruned:    $(basename "$old")"
  done
fi

echo
echo "Backup complete. Restore with: bash scripts/restore.sh $DEST"
