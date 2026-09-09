"""A deploy may not claim success it has not verified (SCRUM-107).

`docker compose up -d` returns when containers have been STARTED, not when
they are serving. A container that starts and immediately crash-loops
satisfies it completely. deploy.sh recorded `success` on the next line, so
`deployed.json` claimed `running_commit: <sha>` for a stack that might be
entirely down.

The damage is not a wrong label. autopull.sh reads that field as ground truth:
DESIRED == RUNNING means converged, so the poller STOPS RETRYING and the box
sits on a broken deploy forever while the deployment system believes it
worked. It is the same wedge autopull.sh's docstring records being burned by
once already — the comparison was fixed, the thing compared was never checked.

These tests drive the real scripts/stack-health.sh with a fake `docker` on
PATH, so the parsing, the polling and the verdict are all exercised. The rule
throughout is that anything short of proof-of-serving reads as NOT serving:
no containers, unreadable output and a crash-loop all fail, because a check
that resolves ambiguity in favour of "up" is the bug it is replacing.
"""
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "scripts" / "stack-health.sh"
DEPLOY = ROOT / "scripts" / "deploy.sh"


def fake_docker(tmp_path, payloads):
    """A `docker` that answers `compose ps --format json` from a script.

    `payloads` is a list of strings, one per invocation; the last is reused
    once exhausted, so a test can make the stack recover on the Nth look.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    spool = tmp_path / "spool"
    spool.mkdir(exist_ok=True)
    for i, p in enumerate(payloads):
        (spool / f"{i}").write_text(p)
    (bin_dir / "docker").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # `docker compose version` must succeed so the script picks "docker compose".
        if [ "$1" = "compose" ] && [ "$2" = "version" ]; then exit 0; fi
        if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then
          N=$(cat "{spool}/.n" 2>/dev/null || echo 0)
          F="{spool}/$N"
          [ -f "$F" ] || F="{spool}/{len(payloads) - 1}"
          echo $((N + 1)) > "{spool}/.n"
          cat "$F"
          exit 0
        fi
        exit 0
        """))
    (bin_dir / "docker").chmod(0o755)
    return bin_dir


def run_health(tmp_path, payloads, *args, timeout="0", interval="0"):
    bin_dir = fake_docker(tmp_path, payloads)
    env = {**os.environ,
           "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "FRANKENSTEIN_HEALTH_TIMEOUT": timeout,
           "FRANKENSTEIN_HEALTH_INTERVAL": interval}
    return subprocess.run(["bash", str(HEALTH), *args], cwd=str(ROOT),
                          env=env, capture_output=True, text=True)


def svc(name, state="running", health=""):
    return {"Service": name, "Name": f"fc-{name}", "State": state, "Health": health}


def ndjson(rows):
    return "\n".join(json.dumps(r) for r in rows) + "\n"


# ---- what counts as serving --------------------------------------------------

def test_a_fully_running_stack_is_up(tmp_path):
    r = run_health(tmp_path, [ndjson([svc("gateway"), svc("core"), svc("db", health="healthy")])])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all services up" in r.stdout


def test_a_crash_looping_container_is_not_up(tmp_path):
    """`restarting` IS the crash-loop, and it is the exact state this ticket
    is about. Reading it as "started, therefore fine" is the bug."""
    r = run_health(tmp_path, [ndjson([svc("gateway"), svc("gmail", state="restarting")])])
    assert r.returncode == 1
    assert "gmail (restarting)" in r.stdout
    assert "NOT serving" in r.stdout


@pytest.mark.parametrize("state", ["exited", "dead", "created", "paused", ""])
def test_no_non_running_state_passes(tmp_path, state):
    r = run_health(tmp_path, [ndjson([svc("gateway"), svc("budget", state=state)])])
    assert r.returncode == 1, f"{state!r} was accepted as serving"
    assert "budget" in r.stdout


def test_an_unhealthy_container_is_not_up_even_while_running(tmp_path):
    r = run_health(tmp_path, [ndjson([svc("db", state="running", health="unhealthy")])])
    assert r.returncode == 1
    assert "db (unhealthy)" in r.stdout


def test_a_still_starting_container_is_not_yet_up(tmp_path):
    r = run_health(tmp_path, [ndjson([svc("db", state="running", health="starting")])])
    assert r.returncode == 1


def test_a_healthy_or_blank_health_is_fine(tmp_path):
    """Most services declare no healthcheck at all; a blank Health must not be
    read as unhealthy or nothing would ever pass."""
    r = run_health(tmp_path, [ndjson([svc("a", health=""), svc("b", health="healthy")])])
    assert r.returncode == 0, r.stdout


# ---- ambiguity resolves to NOT serving ---------------------------------------

def test_no_containers_at_all_is_not_health(tmp_path):
    """An empty list means nothing is serving. Reading it as "nothing is
    broken" is exactly how a stack that never started reports success."""
    r = run_health(tmp_path, [""])
    assert r.returncode == 1
    assert "no containers" in r.stdout


def test_an_empty_json_array_is_not_health(tmp_path):
    r = run_health(tmp_path, ["[]"])
    assert r.returncode == 1


@pytest.mark.parametrize("junk", ["not json at all", "{oops", '{"State":', "<html>err</html>"])
def test_unreadable_compose_output_is_not_health(tmp_path, junk):
    r = run_health(tmp_path, [junk])
    assert r.returncode == 1, f"{junk!r} was read as a healthy stack"


def test_a_json_scalar_where_objects_belong_is_not_health(tmp_path):
    r = run_health(tmp_path, ["[1, 2, 3]"])
    assert r.returncode == 1


# ---- both compose output shapes ----------------------------------------------

def test_a_single_json_array_is_understood(tmp_path):
    """Older compose builds emit one array rather than a line per container."""
    r = run_health(tmp_path, [json.dumps([svc("gateway"), svc("core")])])
    assert r.returncode == 0, r.stdout


def test_a_single_object_is_understood(tmp_path):
    r = run_health(tmp_path, [json.dumps(svc("gateway"))])
    assert r.returncode == 0, r.stdout


def test_an_array_still_catches_a_down_service(tmp_path):
    r = run_health(tmp_path, [json.dumps([svc("gateway"), svc("core", state="exited")])])
    assert r.returncode == 1
    assert "core (exited)" in r.stdout


# ---- the waiting ---------------------------------------------------------------

def test_it_waits_for_a_slow_stack_and_then_succeeds(tmp_path):
    """A service still starting on the first look must not fail the deploy —
    that is the whole reason this polls instead of looking once."""
    slow = ndjson([svc("gateway"), svc("db", state="running", health="starting")])
    good = ndjson([svc("gateway"), svc("db", state="running", health="healthy")])
    r = run_health(tmp_path, [slow, slow, good], timeout="30", interval="0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all services up" in r.stdout


def test_it_gives_up_on_a_stack_that_never_comes_up(tmp_path):
    bad = ndjson([svc("gateway"), svc("gmail", state="restarting")])
    r = run_health(tmp_path, [bad], timeout="1", interval="0")
    assert r.returncode == 1
    assert "gmail (restarting)" in r.stdout


def test_once_does_not_wait(tmp_path):
    bad = ndjson([svc("gmail", state="restarting")])
    good = ndjson([svc("gmail")])
    r = run_health(tmp_path, [bad, good], "--once", timeout="60", interval="0")
    assert r.returncode == 1, "--once looked more than once"


# ---- deploy.sh actually uses it ------------------------------------------------

def test_deploy_records_success_only_behind_the_health_check():
    """A source assertion, deliberately labelled as one: running the whole of
    deploy.sh would mean letting it git-reset and redeploy the repo it is
    running in. What is checked is the ORDER — that no `record "success"` can
    be reached without the health check passing first."""
    src = DEPLOY.read_text()
    assert "stack-health.sh" in src, "deploy.sh no longer verifies the stack"
    gate = src.index("stack-health.sh")
    success = src.index('record "success"')
    assert gate < success, (
        'record "success" is reachable before the health check — that is the bug')
    assert 'record "started_unhealthy"' in src, (
        "an unhealthy deploy must get its own result, not be silently skipped")


def test_the_unhealthy_result_is_not_the_word_success():
    """`record` advances running_commit only on exactly "success". If the
    unhealthy branch recorded any string containing it, the poller would
    conclude it had converged and stop retrying — the bug, restored."""
    src = DEPLOY.read_text()
    i = src.index('record "started_unhealthy"')
    line = src[i:src.index("\n", i)]
    assert '"started_unhealthy"' in line
    assert line.count("success") == 0


def test_only_an_exact_success_advances_the_running_commit(tmp_path):
    """The record writer's rule, which the fix depends on: an unhealthy deploy
    must leave running_commit naming the last commit that DID serve."""
    src = DEPLOY.read_text()
    assert '[ "$result" = "success" ] && running="$sha"' in src, (
        "record no longer gates running_commit on an exact success")
