"""scripts/dump-all.sh, actually run (SCRUM-33).

Every target is reached through `docker exec` / `docker cp`, so the seam is a
fake `docker` placed first on PATH. It emulates ONLY the subcommands the script
uses and otherwise fails loudly (exit 99), so a new docker call in the script
shows up here as a failure rather than silently passing.

The fake is faithful where it matters: `docker exec CTR sh -c SCRIPT` really
runs SCRIPT under `sh` with a fake "container environment" (a root password
canary, DB_CONNECTION/DB_HOST/DB_DATABASE as Firefly sets them) and fake
in-container binaries (mariadb-dump / pg_dump) that refuse unless the password
reached them through MYSQL_PWD. So the inline shell the real script sends into
the container is what gets exercised — not a stub of it.

The failure directions are the point, as with backup.sh:

  * an EMPTY dump is a failed dump, the run exits non-zero, and the previous
    `current` set is left exactly as it was;
  * a container that is not running is a failure, never a quiet skip;
  * a DB_HOST that resolves to zero or two containers is refused with the fix
    named, rather than guessed;
  * the root password appears nowhere on the host side — not in any argv the
    fake docker saw, not in stdout/stderr, not in the manifest.
"""
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dump-all.sh"
CANARY = "s3cr3t-r00t-pw-CANARY-9f8e7d"

FAKE_DOCKER = r'''#!/usr/bin/env bash
# Fake docker for tests: only what dump-all.sh uses. Anything else -> exit 99.
set -u
printf '%s\n' "docker $*" >> "$FAKE_LOG"
running() { for c in $FAKE_CONTAINERS; do [ "$c" = "$1" ] && return 0; done; return 1; }
# /tmp FIRST: the fixture dirs themselves live under /tmp, so mapping /data
# before /tmp would rewrite the mapped result a second time.
map() { local p="$1"; p="${p//\/tmp/$FAKE_PIHOLE_TMP}"; p="${p//\/data/$FAKE_VW_DATA}"; printf '%s' "$p"; }
case "$1" in
  ps)
    shift; svc=""; name=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --format) shift ;;
        --filter) shift
          case "$1" in
            label=com.docker.compose.service=*) svc="${1#label=com.docker.compose.service=}" ;;
            name=*) name="${1#name=}" ;;
          esac ;;
      esac; shift
    done
    if [ -n "$svc" ]; then [ "$svc" = "$FAKE_FF_HOST" ] && printf '%s\n' $FAKE_DB_CONTAINERS; exit 0; fi
    if [ -n "$name" ]; then n="${name#^}"; n="${n%\$}"; running "$n" && echo "$n"; exit 0; fi
    printf '%s\n' $FAKE_CONTAINERS; exit 0 ;;
  inspect) echo "${FAKE_FF_PROJECT:-}"; exit 0 ;;
  exec)
    shift; envs=()
    while [ $# -gt 0 ]; do case "$1" in -w) shift 2 ;; -e) envs+=("$2"); shift 2 ;; *) break ;; esac; done
    ctr="$1"; shift
    running "$ctr" || { echo "Error response from daemon: No such container: $ctr" >&2; exit 1; }
    # --- the "container environment" ---
    export MARIADB_ROOT_PASSWORD="$FAKE_CANARY" POSTGRES_USER=ff DB_HOST="$FAKE_FF_HOST" DB_DATABASE=firefly
    if [ "$FAKE_FF_CONN" = "UNSET" ]; then unset DB_CONNECTION; else export DB_CONNECTION="$FAKE_FF_CONN"; fi
    for e in ${envs[@]+"${envs[@]}"}; do export "$e"; done
    export PATH="$FAKE_BIN:$PATH"
    case "$1" in
      printenv) shift; exec printenv "$1" ;;
      sh) shift; [ "$1" = -c ] && shift; exec sh -c "$(map "$1")" ;;
      /vaultwarden) f="$FAKE_VW_DATA/db_$(date +%Y%m%d_%H%M%S).sqlite3"; head -c 4096 /dev/zero > "$f"
                    echo "Backup to '/data/$(basename "$f")' was successful" ;;
      pihole-FTL) f="pi-hole_TESTBOX_teleporter_$(date +%s).zip"; head -c 4096 /dev/urandom > "$FAKE_PIHOLE_TMP/$f"; echo "$f" ;;
      rm) shift; [ "$1" = -f ] && shift; [ "$1" = -- ] && shift; rm -f "$(map "$1")" ;;
      *) echo "fake docker: unhandled exec: $*" >&2; exit 99 ;;
    esac ;;
  cp) cp "$(map "${2#*:}")" "$3" ;;
  *) echo "fake docker: unhandled: $*" >&2; exit 99 ;;
esac
'''

FAKE_MARIADB_DUMP = r'''#!/usr/bin/env bash
# Refuses unless the password arrived via MYSQL_PWD — the way the real client
# reads it — so a script that put it on argv or forgot it would fail here.
[ "${MYSQL_PWD:-}" = "$FAKE_CANARY" ] || { echo "Access denied for user 'root'@'localhost'" >&2; exit 1; }
[ -n "${FAKE_EMPTY_FIREFLY:-}" ] && exit 0
for i in $(seq 1 120); do echo "INSERT INTO accounts VALUES ($i, 'row $i', 'padding padding padding');"; done
'''

FAKE_PG_DUMP = r'''#!/usr/bin/env bash
[ -n "${FAKE_EMPTY_FIREFLY:-}" ] && exit 0
head -c 4096 /dev/urandom
'''


def _write_exec(path: Path, body: str):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def box(tmp_path):
    """A fake box: fake docker first on PATH, fake container filesystems."""
    hostbin = tmp_path / "hostbin"; hostbin.mkdir()
    ctrbin = tmp_path / "ctrbin"; ctrbin.mkdir()
    _write_exec(hostbin / "docker", FAKE_DOCKER)
    _write_exec(ctrbin / "mariadb-dump", FAKE_MARIADB_DUMP)
    _write_exec(ctrbin / "pg_dump", FAKE_PG_DUMP)
    vw = tmp_path / "vw-data"; vw.mkdir()
    (vw / "db.sqlite3").write_bytes(b"live-db-never-copied" * 100)
    # Random, like a real key: repetitive bytes gzip to nothing and would make a
    # size check meaningless — which is exactly why the script checks content.
    (vw / "rsa_key.pem").write_bytes(b"-----BEGIN RSA PRIVATE KEY-----\n" + os.urandom(1700))
    (vw / "config.json").write_text("{}")
    (vw / "attachments").mkdir()
    pt = tmp_path / "pihole-tmp"; pt.mkdir()
    dumps = tmp_path / "dumps"
    log = tmp_path / "docker.log"; log.write_text("")

    def run(*, conn="mysql", containers="firefly vaultwarden pihole ff-db-1",
            db_containers="ff-db-1", project="ff", skip="dashboard", extra=None):
        env = {
            **os.environ,
            "PATH": f"{hostbin}:{os.environ['PATH']}",
            "FAKE_LOG": str(log), "FAKE_CANARY": CANARY, "FAKE_BIN": str(ctrbin),
            "FAKE_CONTAINERS": containers, "FAKE_DB_CONTAINERS": db_containers,
            "FAKE_FF_CONN": conn, "FAKE_FF_HOST": "db", "FAKE_FF_PROJECT": project,
            "FAKE_VW_DATA": str(vw), "FAKE_PIHOLE_TMP": str(pt),
            "FRANKENSTEIN_DUMP_DIR": str(dumps), "DUMP_SKIP": skip,
            # backup.sh (run for real in one test) and data-safety.sh write to
            # $HOME by default; neither may touch the machine running the tests.
            "FRANKENSTEIN_BACKUP_DIR": str(tmp_path / "pg-backups"),
            "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state"),
        }
        env.update(extra or {})
        r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
        return r, dumps, log

    return run


def manifest(d: Path) -> dict:
    out = {}
    for line in (d / "manifest.txt").read_text().splitlines():
        k, _, v = line.partition(": ")
        out[k] = v
    return out


# ---- happy path --------------------------------------------------------------

def test_every_target_dumps_and_current_verifies(box):
    r, dumps, _ = box()
    assert r.returncode == 0, r.stdout + r.stderr
    cur = dumps / "current"
    m = manifest(cur)
    assert m["result"] == "ok"
    assert m["firefly"] == "ok" and m["vaultwarden"] == "ok" and m["pihole"] == "ok"
    assert m["dashboard"].startswith("skipped:")
    for f in ("firefly/firefly.sql", "vaultwarden/db.sqlite3",
              "vaultwarden/data-files.tgz", "pihole/teleporter.zip"):
        assert (cur / f).stat().st_size >= 1024, f
    # the staged set was published atomically: no staging dir survives
    assert not list(dumps.glob(".staging-*"))
    v = subprocess.run(["bash", str(SCRIPT), "--verify-only", str(cur)],
                       capture_output=True, text=True)
    assert v.returncode == 0, v.stdout + v.stderr


def test_postgres_firefly_takes_the_pg_dump_path(box):
    r, dumps, log = box(conn="pgsql")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dumps / "current" / "firefly" / "firefly.dump").stat().st_size >= 1024
    assert "pg_dump" in log.read_text()
    assert "mariadb-dump" not in log.read_text()


def test_the_live_vaultwarden_database_is_never_copied_raw(box):
    """The tarball must exclude db.sqlite3 (WAL mode loses committed rows);
    the database arrives only via vaultwarden's own VACUUM INTO copy."""
    r, dumps, _ = box()
    assert r.returncode == 0
    listing = subprocess.run(["tar", "tzf", str(dumps / "current/vaultwarden/data-files.tgz")],
                             capture_output=True, text=True).stdout
    assert "rsa_key.pem" in listing
    assert "db.sqlite3" not in listing


# ---- the failure directions --------------------------------------------------

def test_an_empty_dump_fails_the_run_and_leaves_current_untouched(box):
    _, dumps, _ = box()                    # a good `current` exists first
    before = (dumps / "current" / "manifest.txt").read_text()
    r, dumps, _ = box(extra={"FAKE_EMPTY_FIREFLY": "1"})
    assert r.returncode != 0
    assert "DUMP FAILED" in r.stderr
    assert (dumps / "current" / "manifest.txt").read_text() == before, \
        "a failed run replaced the last good set"
    failed = list(dumps.glob("failed-*"))
    assert len(failed) == 1
    m = manifest(failed[0])
    assert m["result"] == "failed"
    assert m["firefly"].startswith("failed:") and "under 1024 bytes" in m["firefly"]
    # the other targets still record their OWN outcome — one failure does not
    # blank the rest of the manifest
    assert m["vaultwarden"] == "ok" and m["pihole"] == "ok"


def test_a_container_that_is_not_running_is_a_failure_not_a_skip(box):
    r, dumps, _ = box(containers="firefly pihole ff-db-1")   # no vaultwarden
    assert r.returncode != 0
    m = manifest(next(dumps.glob("failed-*")))
    assert m["vaultwarden"].startswith("failed:")
    assert "not running" in m["vaultwarden"]
    assert "VAULTWARDEN_CONTAINER" in m["vaultwarden"], "the fix must be named"


def test_an_ambiguous_db_host_is_refused_rather_than_guessed(box):
    r, dumps, _ = box(containers="firefly vaultwarden pihole ff-db-1 ff-db-2",
                      db_containers="ff-db-1 ff-db-2")
    assert r.returncode != 0
    m = manifest(next(dumps.glob("failed-*")))
    assert "resolves to 2 containers" in m["firefly"]
    assert "FIREFLY_DB_CONTAINER" in m["firefly"]


def test_an_unresolvable_db_host_is_refused_with_the_fix_named(box):
    r, dumps, _ = box(db_containers="", project="")
    assert r.returncode != 0
    m = manifest(next(dumps.glob("failed-*")))
    assert "resolves to 0 containers" in m["firefly"]
    assert "FIREFLY_DB_CONTAINER" in m["firefly"]


def test_the_db_container_override_bypasses_discovery(box):
    r, dumps, log = box(db_containers="", project="",
                        extra={"FIREFLY_DB_CONTAINER": "ff-db-1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert manifest(dumps / "current")["firefly"] == "ok"


def test_a_firefly_without_db_connection_in_its_env_fails_plainly(box):
    r, dumps, _ = box(conn="UNSET")
    assert r.returncode != 0
    m = manifest(next(dumps.glob("failed-*")))
    assert "no DB_CONNECTION" in m["firefly"]


def test_skipping_everything_is_not_a_successful_dump(box):
    r, dumps, _ = box(skip="dashboard,firefly,vaultwarden,pihole")
    assert r.returncode != 0
    assert "dump of nothing" in r.stdout + r.stderr
    assert not (dumps / "current").exists()


def test_a_tampered_set_fails_verification(box):
    r, dumps, _ = box()
    assert r.returncode == 0
    (dumps / "current" / "firefly" / "firefly.sql").write_bytes(b"x" * 2048)
    v = subprocess.run(["bash", str(SCRIPT), "--verify-only", str(dumps / "current")],
                       capture_output=True, text=True)
    assert v.returncode != 0
    assert "CHECKSUM MISMATCH" in v.stdout + v.stderr


# ---- secrets stay inside the container ---------------------------------------

def test_the_root_password_never_reaches_the_host_side(box):
    """The fake mariadb-dump only succeeds if the password arrived via
    MYSQL_PWD inside the container, so a passing dump PROVES the password was
    used. It must then be absent from everything on the host side."""
    r, dumps, log = box()
    assert r.returncode == 0 and manifest(dumps / "current")["firefly"] == "ok"
    assert CANARY not in log.read_text(), "the password was on a docker argv"
    assert CANARY not in r.stdout + r.stderr
    assert CANARY not in (dumps / "current" / "manifest.txt").read_text()
    assert CANARY not in (dumps / "current" / "SHA256SUMS").read_text()


# ---- the dashboard target really delegates ------------------------------------

@pytest.mark.skipif(not shutil.which("pg_dump"), reason="backup.sh needs pg_dump")
def test_a_failing_backup_sh_propagates_as_a_failed_dashboard_target(box):
    """No fake here: the real scripts/backup.sh is run against a port nothing
    listens on, and its non-zero exit must surface as the dashboard target
    failing — delegation that swallowed the exit code would be worse than a
    copy."""
    r, dumps, _ = box(skip="firefly,vaultwarden,pihole",
                      extra={"POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "1"})
    assert r.returncode != 0
    m = manifest(next(dumps.glob("failed-*")))
    assert m["dashboard"].startswith("failed: scripts/backup.sh exited non-zero")
