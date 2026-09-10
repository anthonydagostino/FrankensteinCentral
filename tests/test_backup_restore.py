"""The restore path, actually run (PRODUCT_IDEAS #37).

docs/OPERATIONS.md asserted a nightly backup from `~/docker/backup.sh` — a
file that is not in this repository, that nobody reviewing this code has ever
read, and whose restore had never once been performed. That is a belief, not a
backup, and the belief is the dangerous half: it is what stops you checking.

So this file does the thing that had never been done. It starts a REAL
PostgreSQL, writes real rows, runs the real scripts/backup.sh, DESTROYS the
database, runs the real scripts/restore.sh, and compares the contents row for
row. Nothing here is mocked; if the scripts cannot round-trip data, these
fail.

The second half is about the failure directions, which matter more than the
happy path:

  * a truncated or corrupt archive must be REFUSED, not restored halfway —
    a restore that discovers the problem mid-flight has already destroyed the
    thing it was replacing;
  * a backup whose dump came out empty must record `failed`, because a backup
    that reports success it has not verified is worse than no backup at all;
  * the Gmail refresh token must never appear in the manifest, the log, or
    anything else the script prints.
"""
import os
import shutil
import tempfile
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup.sh"
RESTORE = ROOT / "scripts" / "restore.sh"

# Postgres refuses to run as root, which is how CI and the dev container run.
NOBODY = 65534


def _pgbin():
    for base in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (base / "initdb").exists():
            return base
    return None


PGBIN = _pgbin()
needs_pg = pytest.mark.skipif(
    PGBIN is None or not shutil.which("pg_dump") or not shutil.which("pg_restore"),
    reason="no local PostgreSQL binaries to round-trip against",
)


def sh(*args, cwd=None, env=None, check=True, as_nobody=False):
    argv = list(args)
    if as_nobody and os.geteuid() == 0:
        argv = ["setpriv", "--reuid", str(NOBODY), "--regid", str(NOBODY),
                "--clear-groups"] + argv
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise AssertionError(f"{argv}\n{r.stdout}\n{r.stderr}")
    return r


class Cluster:
    """A throwaway PostgreSQL, on a socket short enough for the 107-byte cap."""

    def __init__(self, tmp):
        self.dir = Path(tmp)
        self.data = self.dir / "data"
        self.sock = self.dir / "s"
        self.port = "5599"
        self.db = "frankensteincentral"
        self.user = "frank"

    def start(self):
        self.sock.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0:
            for p in (self.dir, self.sock, self.data):
                os.chmod(p, 0o700)
                os.chown(p, NOBODY, NOBODY)
        sh(str(PGBIN / "initdb"), "-D", str(self.data), "-U", self.user,
           "--auth=trust", as_nobody=True)
        sh(str(PGBIN / "pg_ctl"), "-D", str(self.data),
           "-o", f"-k {self.sock} -h '' -p {self.port}",
           "-l", str(self.dir / "log"), "start", as_nobody=True)
        for _ in range(40):
            if self.psql("select 1", db="postgres", check=False).returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise AssertionError((self.dir / "log").read_text())
        self.psql(f"CREATE DATABASE {self.db}", db="postgres")

    def stop(self):
        sh(str(PGBIN / "pg_ctl"), "-D", str(self.data), "stop", "-m", "immediate",
           check=False, as_nobody=True)

    def psql(self, sql, db=None, check=True):
        return sh("psql", "-h", str(self.sock), "-p", self.port, "-U", self.user,
                  "-d", db or self.db, "-tAc", sql, check=check)

    @property
    def env(self):
        return {**os.environ,
                "POSTGRES_HOST": str(self.sock), "POSTGRES_PORT": self.port,
                "POSTGRES_USER": self.user, "POSTGRES_DB": self.db,
                "POSTGRES_PASSWORD": "unused-trust-auth"}


@pytest.fixture
def pg():
    """The cluster lives directly under /tmp, not under tmp_path.

    Two reasons, both learned the hard way. pytest's tmp_path sits below
    /tmp/pytest-of-root/, whose ancestors are root-only, so the unprivileged
    user postgres insists on running as cannot traverse down to the data
    directory at all. And the Unix socket path has a hard 107-byte limit that
    a pytest tmp_path plus a test name blows straight through.
    """
    d = tempfile.mkdtemp(prefix="fcpg.", dir="/tmp")
    c = Cluster(d)
    try:
        c.start()
        yield c
    finally:
        c.stop()
        shutil.rmtree(d, ignore_errors=True)


def seed(pg):
    """The kinds of thing the idea says are one disk away from zero."""
    pg.psql("""
        CREATE TABLE focus_sessions (id serial primary key, day date,
                                     label text, minutes int);
        CREATE TABLE big3 (id serial primary key, day date, text text, done bool);
        INSERT INTO focus_sessions (day, label, minutes) VALUES
          ('2026-09-01','Study',45),('2026-09-02','Study',90),
          ('2026-09-03','Reading',30);
        INSERT INTO big3 (day, text, done) VALUES
          ('2026-09-01','Ship the calendar', true),
          ('2026-09-01','Fix the money bug', false);
    """)


def contents(pg):
    return (pg.psql("SELECT count(*)||':'||coalesce(sum(minutes),0) FROM focus_sessions").stdout.strip(),
            pg.psql("SELECT string_agg(text, '|' ORDER BY id) FROM big3").stdout.strip())


# ---- the round trip that had never been run ---------------------------------

@needs_pg
def test_backup_then_destroy_then_restore_returns_every_row(pg, tmp_path):
    seed(pg)
    before = contents(pg)
    assert before[0] == "3:165", f"seed did not land: {before}"

    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups")}
    r = sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    assert "verified:  yes" in r.stdout, r.stdout
    made = sorted((tmp_path / "backups").iterdir())
    assert len(made) == 1, made

    # Destroy it. Not "modify" — drop the tables outright, the way losing the
    # volume would.
    pg.psql("DROP TABLE focus_sessions; DROP TABLE big3;")
    assert pg.psql("SELECT to_regclass('focus_sessions')").stdout.strip() == ""

    r = sh("bash", str(RESTORE), str(made[0]), cwd=str(ROOT), env=env)
    assert "Database restored" in r.stdout, r.stdout + r.stderr
    assert contents(pg) == before, "restored contents differ from what was backed up"


@needs_pg
def test_restoring_over_a_live_database_converges_rather_than_colliding(pg, tmp_path):
    """The realistic case: you are restoring onto a stack that is still up and
    already has the tables. --clean --if-exists has to make that work."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]

    pg.psql("INSERT INTO big3 (day, text, done) VALUES ('2026-09-04','Later thing',false)")
    assert "Later thing" in contents(pg)[1]

    sh("bash", str(RESTORE), str(d), cwd=str(ROOT), env=env)
    assert "Later thing" not in contents(pg)[1], "restore did not replace the table"
    assert contents(pg)[0] == "3:165"


@needs_pg
def test_a_dry_run_verifies_and_changes_nothing(pg, tmp_path):
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]
    pg.psql("INSERT INTO big3 (day, text, done) VALUES ('2026-09-04','Untouched',false)")
    before = contents(pg)
    r = sh("bash", str(RESTORE), "--dry-run", str(d), cwd=str(ROOT), env=env)
    assert "nothing was changed" in r.stdout
    assert contents(pg) == before


# ---- which way it fails ------------------------------------------------------

@needs_pg
def test_a_corrupt_archive_is_refused_before_anything_is_dropped(pg, tmp_path):
    """The one that matters most. A restore that fails halfway has destroyed
    the database it was replacing."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]
    before = contents(pg)

    dump = d / "database.dump"
    dump.write_bytes(dump.read_bytes()[: len(dump.read_bytes()) // 2])   # truncate

    r = sh("bash", str(RESTORE), str(d), cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0, "a truncated archive must not restore"
    assert "REFUSED" in r.stderr, r.stdout + r.stderr
    assert contents(pg) == before, "the live database was touched despite the refusal"


@needs_pg
def test_a_tampered_archive_fails_its_checksum(pg, tmp_path):
    """Same size, different bytes — the failure a size check sails past."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]
    raw = bytearray((d / "database.dump").read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    (d / "database.dump").write_bytes(bytes(raw))
    r = sh("bash", str(BACKUP), "--verify-only", str(d), cwd=str(ROOT),
           env=env, check=False)
    assert r.returncode != 0
    assert "CHECKSUM MISMATCH" in r.stdout


@needs_pg
def test_a_backup_with_no_manifest_is_not_a_backup(pg, tmp_path):
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]
    (d / "manifest.txt").unlink()
    r = sh("bash", str(RESTORE), str(d), cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0
    assert "REFUSED" in r.stderr


@needs_pg
def test_a_backup_against_an_unreachable_database_reports_failure(tmp_path):
    """It must not leave a directory that later looks like a usable backup."""
    env = {**os.environ, "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "1",
           "POSTGRES_USER": "frank", "POSTGRES_DB": "nope",
           "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    r = sh("bash", str(BACKUP), cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0
    assert "BACKUP FAILED" in r.stderr
    made = list((tmp_path / "b").iterdir())
    assert len(made) == 1
    assert "result: failed" in (made[0] / "manifest.txt").read_text()
    # And restore must refuse it rather than reading the word "manifest" as
    # meaning "restorable".
    r2 = sh("bash", str(RESTORE), str(made[0]), cwd=str(ROOT), env=env, check=False)
    assert r2.returncode != 0


@needs_pg
def test_the_listing_names_broken_backups_instead_of_hiding_them(pg, tmp_path):
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    good = sorted((tmp_path / "b").iterdir())[0]
    bad = tmp_path / "b" / "20200101T000000Z"
    bad.mkdir()
    (bad / "manifest.txt").write_text("result: ok\n")     # claims success, has no dump
    r = sh("bash", str(RESTORE), "--list", cwd=str(ROOT), env=env)
    assert "BROKEN   20200101T000000Z" in r.stdout, r.stdout
    assert f"ok       {good.name}" in r.stdout, r.stdout


@needs_pg
def test_an_empty_backup_directory_says_so_rather_than_looking_healthy(tmp_path):
    env = {**os.environ, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "empty")}
    r = sh("bash", str(RESTORE), "--list", cwd=str(ROOT), env=env)
    assert "nothing has ever been backed up here" in r.stdout


# ---- secrets ------------------------------------------------------------------

@needs_pg
def test_the_backup_never_prints_a_secret(pg, tmp_path):
    """scripts/verify.sh has this rule and it applies here for the same reason:
    the token is the one thing in the archive that is worth stealing."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b"),
           "POSTGRES_PASSWORD": "hunter2-do-not-print"}
    r = sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    blob = r.stdout + r.stderr
    assert "hunter2-do-not-print" not in blob
    d = sorted((tmp_path / "b").iterdir())[0]
    assert "hunter2-do-not-print" not in (d / "manifest.txt").read_text()


@needs_pg
def test_a_skipped_token_volume_says_skipped_and_never_ok(pg, tmp_path):
    """Skipped and included are different facts. A manifest that implied the
    token was in the archive when it was not is how you discover, during a
    restore, that you have to re-consent to Google."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b"),
           "FRANKENSTEIN_TOKEN_VOLUME": "definitely-not-a-real-volume"}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]
    manifest = (d / "manifest.txt").read_text()
    assert "token_volume: skipped" in manifest, manifest
    assert not (d / "gmail_token.tgz").exists()


# ---- retention ----------------------------------------------------------------

@needs_pg
def test_retention_keeps_the_newest_and_never_counts_a_half_written_one(pg, tmp_path):
    seed(pg)
    root = tmp_path / "b"
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(root),
           "FRANKENSTEIN_BACKUP_KEEP": "2"}
    # A directory from an interrupted run: no manifest, so not a backup.
    (root / "19990101T000000Z").mkdir(parents=True)
    for _ in range(3):
        sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
        time.sleep(1.1)          # the stamp is second-resolution
    kept = sorted(p.name for p in root.iterdir() if (p / "manifest.txt").exists())
    assert len(kept) == 2, kept
    assert (root / "19990101T000000Z").exists(), (
        "the partial directory was counted as a backup and pruned something real")


# ---- gaps found by mutating the scripts ---------------------------------------
#
# The first pass of this file passed against four reintroduced bugs. Two were
# redundant guards (proved below). These two were real holes: nothing drove a
# restore that pg_restore could READ but could not APPLY, and nothing checked
# an archive whose dump was present but empty.

@needs_pg
def test_a_restore_that_cannot_be_applied_fails_loudly(pg, tmp_path):
    """pg_restore without --exit-on-error prints its errors and exits 0.

    The archive here is perfectly valid — it verifies, pg_restore can list it.
    It just cannot be APPLIED, because a view in the target depends on a table
    the restore has to drop first, and DROP TABLE without CASCADE fails. That
    is the shape of a real partial restore: some objects land, some do not,
    and the exit code is the only thing standing between that and a database
    everyone believes was restored.
    """
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]

    # The archive itself is fine — this is not a corruption test.
    assert sh("bash", str(BACKUP), "--verify-only", str(d), cwd=str(ROOT),
              env=env, check=False).returncode == 0

    pg.psql("CREATE VIEW b3_open AS SELECT * FROM big3 WHERE NOT done")

    r = sh("bash", str(RESTORE), str(d), cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0, (
        "pg_restore hit an error it could not recover from and the script "
        "reported success:\n" + r.stdout + r.stderr)
    assert "REFUSED" in r.stderr or "partially restored" in r.stderr, r.stderr


@needs_pg
def test_an_archive_whose_dump_is_empty_does_not_verify(pg, tmp_path):
    """Present-but-empty is its own failure, distinct from missing. A restore
    from it would 'succeed' at restoring nothing at all."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "b")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    d = sorted((tmp_path / "b").iterdir())[0]
    (d / "database.dump").write_bytes(b"")
    r = sh("bash", str(BACKUP), "--verify-only", str(d), cwd=str(ROOT),
           env=env, check=False)
    assert r.returncode != 0, r.stdout
    r2 = sh("bash", str(RESTORE), str(d), cwd=str(ROOT), env=env, check=False)
    assert r2.returncode != 0
    assert contents(pg)[0] == "3:165", "an empty archive reached the database"


# --- the drill (SCRUM-67) ----------------------------------------------------
#
# "A backup you have never restored is a belief, not a backup." The dashboard
# is meant to state how many days since the last VERIFIED restore, and until
# `--drill` existed there was nothing that could ever make that number
# anything but "never": restore.sh recorded nothing, and the only other
# restore path overwrites your live database, which is not something you run
# on a timer.
#
# These run the real scripts against a real PostgreSQL. Nothing is mocked.

import json  # noqa: E402


def _record(state_dir):
    path = Path(state_dir) / "data-safety.json"
    return json.loads(path.read_text()) if path.exists() else None


@needs_pg
def test_a_drill_proves_the_backup_restores_without_touching_the_live_database(pg, tmp_path):
    """The whole feature. It must verify, and it must be safe to run on a
    timer against a live stack — those two together are what make the number
    on the dashboard maintainable rather than a one-off."""
    seed(pg)
    before = contents(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)

    r = sh("bash", str(RESTORE), "--drill", cwd=str(ROOT), env=env)
    assert "Drill passed" in r.stdout, r.stdout + r.stderr
    assert "matching the backup's record" in r.stdout, r.stdout

    # The live database is untouched — that is the difference between a drill
    # and a restore.
    assert contents(pg) == before

    rec = _record(tmp_path / "state")
    assert rec["last_restore_result"] == "ok"
    assert rec["restore_kind"] == "drill"
    assert rec["last_restore_at"]


@needs_pg
def test_the_scratch_database_is_always_dropped(pg, tmp_path):
    """A drill that leaves debris behind stops being something you run
    unattended."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    sh("bash", str(RESTORE), "--drill", cwd=str(ROOT), env=env)
    left = pg.psql("SELECT datname FROM pg_database WHERE datname LIKE '%_drill_%'")
    assert left.stdout.strip() == "", f"scratch databases left behind: {left.stdout}"


@needs_pg
def test_an_empty_dump_fails_the_drill_even_though_it_restores_cleanly(pg, tmp_path):
    """The reason the drill compares counts instead of trusting pg_restore's
    exit code. A dump of zero rows restores perfectly — right checksums, right
    table of contents, exit 0 — and is worthless. Only the comparison against
    what was recorded at dump time catches it."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    made = sorted((tmp_path / "backups").iterdir())[0]

    # Re-dump with the tables emptied, but keep the ORIGINAL rowcounts.txt —
    # exactly the shape of a backup that silently stopped capturing data.
    rows = (made / "rowcounts.txt").read_text()
    pg.psql("DELETE FROM focus_sessions; DELETE FROM big3; ANALYZE;")
    sh(str(PGBIN / "pg_dump"), "-h", str(pg.sock), "-p", pg.port, "-U", pg.user,
       "-d", pg.db, "--format=custom", "--file", str(made / "database.dump"))
    (made / "rowcounts.txt").write_text(rows)
    sh("bash", "-c", f"cd {made} && sha256sum database.dump gmail_token.tgz "
       f"rowcounts.txt 2>/dev/null || sha256sum database.dump rowcounts.txt "
       f"> SHA256SUMS", check=False)
    sh("bash", "-c", f"cd {made} && sha256sum database.dump rowcounts.txt > SHA256SUMS")

    r = sh("bash", str(RESTORE), "--drill", str(made), cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0, "an empty dump passed the drill"
    assert "does not match" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert _record(tmp_path / "state")["last_restore_result"] == "failed"


@needs_pg
def test_a_failed_drill_never_advances_the_last_success_date(pg, tmp_path):
    """The number on the dashboard is "days since the last VERIFIED restore".
    A run of failures must not make it look freshly safe."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    sh("bash", str(RESTORE), "--drill", cwd=str(ROOT), env=env)
    good = _record(tmp_path / "state")["last_restore_at"]

    # Now drill against a backup that does not exist.
    r = sh("bash", str(RESTORE), "--drill", str(tmp_path / "nope"),
           cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0
    rec = _record(tmp_path / "state")
    assert rec["last_restore_result"] == "failed"
    assert rec["last_restore_at"] == good, "a failure moved the last-success date"


@needs_pg
def test_with_no_backups_at_all_the_drill_refuses_rather_than_passing(tmp_path):
    env = {**os.environ, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    r = sh("bash", str(RESTORE), "--drill", cwd=str(ROOT), env=env, check=False)
    assert r.returncode != 0
    assert "no verified backup" in (r.stdout + r.stderr)


@needs_pg
def test_a_backup_records_itself_only_after_it_verifies(pg, tmp_path):
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    rec = _record(tmp_path / "state")
    assert rec["last_backup_result"] == "ok"
    assert rec["last_backup_at"]
    # ...and a backup must never claim a restore happened.
    assert "last_restore_at" not in rec


@needs_pg
def test_the_record_never_contains_a_secret(pg, tmp_path):
    """Same rule as the backup itself: counts, timestamps and verdicts only."""
    seed(pg)
    env = {**pg.env, "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "backups"),
           "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state")}
    sh("bash", str(BACKUP), cwd=str(ROOT), env=env)
    sh("bash", str(RESTORE), "--drill", cwd=str(ROOT), env=env)
    raw = (tmp_path / "state" / "data-safety.json").read_text().lower()
    for forbidden in ("password", "token", "secret", pg.env["POSTGRES_PASSWORD"].lower()):
        assert forbidden not in raw, f"{forbidden!r} appears in the record"
