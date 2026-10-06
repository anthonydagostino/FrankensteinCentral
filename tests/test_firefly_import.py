"""scripts/firefly-import.sh: the trigger, and the verdict it is allowed to give.

The script's whole value is the gap it refuses to paper over: "the importer
returned 200" and "data entered the ledger" are different facts. These tests
drive the real script with a fake `curl` on PATH that plays both the Data
Importer and the FC firefly service, so the trigger, the before/after probe,
the waiting, the record and — above all — the secret hygiene are all exercised.
"""
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "firefly-import.sh"
SECRET = "correct-horse-battery-staple-2026"


def fake_curl(tmp_path, freshness, importer_code="200", importer_dead=False):
    """A curl that answers /freshness from a sequence and the importer with a
    fixed status, and logs every argv it was given."""
    bin_dir = tmp_path / "bin"; bin_dir.mkdir(exist_ok=True)
    spool = tmp_path / "spool"; spool.mkdir(exist_ok=True)
    for i, body in enumerate(freshness):
        (spool / f"{i}").write_text(json.dumps(body))
    (bin_dir / "curl").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> "{tmp_path}/curl.log"
        OUT=""; URL=""
        args=("$@")
        for ((i=0; i<${{#args[@]}}; i++)); do
          [ "${{args[$i]}}" = "-o" ] && OUT="${{args[$((i+1))]}}"
          case "${{args[$i]}}" in http*) URL="${{args[$i]}}";; esac
        done
        case "$URL" in
          */freshness)
            N=$(cat "{spool}/.n" 2>/dev/null || echo 0)
            F="{spool}/$N"; [ -f "$F" ] || F="{spool}/{max(len(freshness) - 1, 0)}"
            echo $((N + 1)) > "{spool}/.n"
            [ -f "$F" ] && cat "$F"
            exit 0 ;;
          */autoimport|*/autoupload)
            if [ "{'1' if importer_dead else '0'}" = "1" ]; then exit 7; fi
            [ -n "$OUT" ] && echo '{{"message":"import started","config":"REDACTED-IN-TEST"}}' > "$OUT"
            printf '%s' "{importer_code}"
            exit 0 ;;
        esac
        exit 0
        """))
    (bin_dir / "curl").chmod(0o755)
    return bin_dir


def run(tmp_path, freshness, *args, env_extra=None, importer_code="200", importer_dead=False):
    bin_dir = fake_curl(tmp_path, freshness, importer_code, importer_dead)
    env = {k: v for k, v in os.environ.items() if not k.startswith("FIREFLY_")}
    env.update({
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FRANKENSTEIN_STATE_DIR": str(tmp_path / "state"),
        "FIREFLY_IMPORTER_URL": "http://importer.test:8094",
        "FIREFLY_IMPORTER_SECRET": SECRET,
        "FIREFLY_IMPORT_DIR": "/import",
        "FIREFLY_IMPORT_WAIT": "1",
        "FIREFLY_IMPORT_INTERVAL": "0",
    })
    env.update(env_extra or {})
    # Run from a scratch cwd copy of nothing: the script cd's to the repo
    # itself, and must not read the developer's own .env when the environment
    # already answers.
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=str(ROOT), env=env,
                          capture_output=True, text=True)


def record(tmp_path):
    p = tmp_path / "state" / "data-safety.json"
    return json.loads(p.read_text()) if p.exists() else {}


FRESH = {"connected": True, "txn_total_90d": 100, "ingest_latest_at": "2026-09-28T06:00:00-04:00"}
MOVED = {"connected": True, "txn_total_90d": 103, "ingest_latest_at": "2026-09-28T09:41:12-04:00"}


# ---- the three verdicts -----------------------------------------------------

def test_the_ledger_moving_is_ok_and_counts_the_rows(tmp_path):
    r = run(tmp_path, [FRESH, MOVED])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout and "3 new transaction" in r.stdout
    rec = record(tmp_path)
    assert rec["last_import_result"] == "ok"
    assert rec["import_rows"] == "3"
    assert rec["import_kind"] == "autoimport"
    assert rec["last_import_at"]


def test_an_import_that_lands_nothing_is_empty_not_ok(tmp_path):
    """The SCRUM-40 shape: the importer runs, returns 200, inserts nothing.
    That must not be recorded as a successful import."""
    r = run(tmp_path, [FRESH, FRESH, FRESH])
    assert r.returncode == 0, "a quiet day is not an error for cron"
    assert "EMPTY" in r.stdout
    rec = record(tmp_path)
    assert rec["last_import_result"] == "empty"
    assert rec["last_import_reason"] == "nothing_entered"
    assert "last_import_at" not in rec


def test_a_timestamp_advancing_alone_is_enough(tmp_path):
    """Two imports in one day: the count may not change if the second
    re-imports and dedupes, but created_at moving still proves ingestion."""
    later = {**FRESH, "ingest_latest_at": "2026-09-28T18:00:00-04:00"}
    r = run(tmp_path, [FRESH, later])
    assert r.returncode == 0
    assert record(tmp_path)["last_import_result"] == "ok"


def test_a_count_rising_alone_is_enough(tmp_path):
    """The other half: rows backfilled with an OLD created_at (a bank whose
    feed stamps the posting date) move the count and not the newest stamp.
    Either signal proves ingestion; requiring both would miss real imports."""
    more = {**FRESH, "txn_total_90d": FRESH["txn_total_90d"] + 2}
    r = run(tmp_path, [FRESH, more])
    assert r.returncode == 0, r.stdout + r.stderr
    rec = record(tmp_path)
    assert rec["last_import_result"] == "ok"
    assert rec["import_rows"] == "2"


def test_an_importer_error_is_failed_with_the_status_as_the_reason(tmp_path):
    r = run(tmp_path, [FRESH], importer_code="500")
    assert r.returncode == 1
    assert "HTTP 500" in r.stderr
    rec = record(tmp_path)
    assert rec["last_import_result"] == "failed"
    assert rec["last_import_reason"] == "importer_http_500"
    assert "last_import_at" not in rec


def test_an_unreachable_importer_is_failed_not_empty(tmp_path):
    r = run(tmp_path, [FRESH], importer_dead=True)
    assert r.returncode == 1
    assert record(tmp_path)["last_import_reason"] == "importer_unreachable"


def test_a_silent_firefly_service_makes_the_run_unverified(tmp_path):
    """The importer ran; nobody can say whether anything entered. That is
    its own state — not ok, and not the same as 'nothing entered'."""
    r = run(tmp_path, [{"connected": False}])
    assert r.returncode == 1
    assert "UNVERIFIED" in r.stdout
    rec = record(tmp_path)
    assert rec["last_import_result"] == "unverified"
    assert rec["last_import_reason"] == "firefly_unreachable"
    assert "last_import_at" not in rec


def test_a_failure_after_a_success_keeps_the_last_success(tmp_path):
    run(tmp_path, [FRESH, MOVED])
    good = record(tmp_path)["last_import_at"]
    run(tmp_path, [MOVED], importer_code="503")
    rec = record(tmp_path)
    assert rec["last_import_result"] == "failed"
    assert rec["last_import_at"] == good


# ---- configuration ------------------------------------------------------------

def test_missing_configuration_refuses_and_names_the_key_not_the_value(tmp_path):
    r = run(tmp_path, [FRESH], env_extra={"FIREFLY_IMPORTER_SECRET": ""})
    assert r.returncode == 2
    assert "FIREFLY_IMPORTER_SECRET" in r.stderr
    assert record(tmp_path)["last_import_reason"] == "not_configured"
    assert not (tmp_path / "curl.log").exists(), "nothing should have been called"


def test_neither_a_directory_nor_a_config_is_refused(tmp_path):
    r = run(tmp_path, [FRESH], env_extra={"FIREFLY_IMPORT_DIR": ""})
    assert r.returncode == 2
    assert "FIREFLY_IMPORT_DIR" in r.stderr and "FIREFLY_IMPORT_CONFIG" in r.stderr


def test_a_config_file_selects_autoupload(tmp_path):
    cfg = tmp_path / "discover.json"
    cfg.write_text('{"flow": "simplefin"}')
    r = run(tmp_path, [FRESH, MOVED], env_extra={"FIREFLY_IMPORT_CONFIG": str(cfg),
                                                 "FIREFLY_IMPORT_DIR": ""})
    assert r.returncode == 0, r.stdout + r.stderr
    log = (tmp_path / "curl.log").read_text()
    assert "/autoupload" in log and f"json=@{cfg}" in log
    assert record(tmp_path)["import_kind"] == "autoupload"


def test_a_directory_selects_autoimport_and_names_it(tmp_path):
    run(tmp_path, [FRESH, MOVED])
    log = (tmp_path / "curl.log").read_text()
    assert "/autoimport" in log and "directory=/import" in log


def test_check_reports_the_last_run_without_triggering_one(tmp_path):
    run(tmp_path, [FRESH, MOVED])
    (tmp_path / "curl.log").unlink()
    r = run(tmp_path, [FRESH], "--check")
    assert r.returncode == 0
    assert "last_import_result" in r.stdout and "ok" in r.stdout
    assert not (tmp_path / "curl.log").exists()


# ---- the secret ---------------------------------------------------------------

def test_the_secret_never_reaches_argv_stdout_stderr_or_the_record(tmp_path):
    """It is handed to curl through a 0600 file. If it were in argv it would
    be in `ps`; if it were in output it would be in the cron log."""
    r = run(tmp_path, [FRESH, MOVED])
    log = (tmp_path / "curl.log").read_text()
    assert SECRET not in log, "the secret is in curl's argv"
    assert "secret@" in log or "secret=<" in log, "curl was not told to read it from a file"
    assert SECRET not in r.stdout and SECRET not in r.stderr
    raw = (tmp_path / "state" / "data-safety.json").read_text()
    assert SECRET not in raw
    body = tmp_path / "state" / "import-last-response.txt"
    assert body.exists() and (body.stat().st_mode & 0o777) == 0o600


def test_the_secret_file_is_gone_afterwards(tmp_path):
    run(tmp_path, [FRESH, MOVED], env_extra={"TMPDIR": str(tmp_path / "tmp")})
    (tmp_path / "tmp").mkdir(exist_ok=True)
    assert not list((tmp_path / "tmp").glob("fc-import.*"))


# ---- --daily: the hourly timer asks the bank once a day --------------------
#
# scripts/import/frankenstein-import.timer fires hourly so a box that slept
# through 6am still imports when it wakes and a failure is retried soon. The
# guard below is what makes that one import a day rather than sixteen.

def _recorded(tmp_path, result, when):
    state = tmp_path / "state"; state.mkdir(exist_ok=True)
    (state / "data-safety.json").write_text(json.dumps(
        {"last_import_attempt_at": when, "last_import_result": result}))


def _today_iso():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def test_daily_skips_a_day_that_already_landed(tmp_path):
    _recorded(tmp_path, "ok", _today_iso())
    r = run(tmp_path, [FRESH, MOVED], "--daily")
    assert r.returncode == 0
    assert "already ran today" in r.stdout
    assert not (tmp_path / "curl.log").exists(), "the importer was asked anyway"


def test_daily_skips_a_day_that_ran_and_found_nothing(tmp_path):
    """`empty` is a run, not a failure: a quiet banking day must not be
    re-asked every hour."""
    _recorded(tmp_path, "empty", _today_iso())
    r = run(tmp_path, [FRESH, MOVED], "--daily")
    assert r.returncode == 0 and "already ran today" in r.stdout


def test_daily_retries_a_failed_run(tmp_path):
    _recorded(tmp_path, "failed", _today_iso())
    r = run(tmp_path, [FRESH, MOVED], "--daily")
    assert "already ran today" not in r.stdout
    assert record(tmp_path)["last_import_result"] == "ok"


def test_daily_runs_when_the_last_run_was_yesterday(tmp_path):
    _recorded(tmp_path, "ok", "2026-01-01T06:07:00+00:00")
    r = run(tmp_path, [FRESH, MOVED], "--daily")
    assert record(tmp_path)["last_import_result"] == "ok"
    assert "already ran today" not in r.stdout


def test_daily_runs_when_nothing_was_ever_recorded(tmp_path):
    r = run(tmp_path, [FRESH, MOVED], "--daily")
    assert record(tmp_path)["last_import_result"] == "ok"


# ---- the shipped timer ---------------------------------------------------

UNIT_DIR = ROOT / "scripts" / "import"


def test_the_timer_is_hourly_persistent_and_uses_the_daily_guard():
    timer = (UNIT_DIR / "frankenstein-import.timer").read_text()
    service = (UNIT_DIR / "frankenstein-import.service").read_text()
    assert "Persistent=true" in timer, "a box that was off at 6am would skip the day"
    assert "OnCalendar=*-*-* 06..22:07:00" in timer
    assert "firefly-import.sh --daily" in service, \
        "an hourly timer without the guard asks the bank sixteen times a day"
    assert "Type=oneshot" in service
    assert "TimeoutStartSec=" in service


def test_the_docs_install_the_timer_not_a_cron_line():
    for doc in ("docs/SETUP-FIREFLY.md", "docs/DO-THIS-FIREFLY-IMPORT.md"):
        text = (ROOT / doc).read_text()
        assert "frankenstein-import.timer" in text, f"{doc} does not install the timer"
        assert "23 6 * * *" not in text, f"{doc} still tells the reader to type a cron line"
