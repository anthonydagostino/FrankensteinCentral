"""A deploy that skipped the suite must still say so tomorrow.

WHY THIS FILE EXISTS (SCRUM-108): `DEPLOY_SKIP_TESTS=1` forces a deploy past
the test gate. The hatch itself is reasonable — a gate with no override tends
to get removed rather than used carefully. What was wrong is that it was
INVISIBLE afterwards: `record success` wrote a byte-identical entry either way,
so neither `frankenstein-status.sh`, nor the deploy card, nor a person could
tell a tested deploy from an untested one an hour later, let alone a month.

An override you can see is a safety net. One you cannot is a hole.

WHY IT IS TESTED HERE RATHER THAN BY READING THE SOURCE: `record()` is shell
that shells out to python and writes JSON, and the property that matters is
about a SEQUENCE of deploys — a skipped one, then a failed one, then a tested
one — which no amount of reading a single function proves. These drive the real
function out of the real script.
"""
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"


def run_record(record_path, calls):
    """Source deploy.sh's record() and drive it, without running a deploy.

    deploy.sh does `git reset --hard` and restarts containers, so it cannot be
    executed here. The function is extracted verbatim from the real file — if
    it is renamed or its signature changes, this fails rather than silently
    testing a copy that no longer exists.
    """
    src = DEPLOY.read_text()
    start = src.index("record() {")
    end = src.index("\n}\n", start) + 3
    body = src[start:end]
    script = textwrap.dedent(f"""
        set -euo pipefail
        RECORD="{record_path}"
        BRANCH="production"
        {body}
        {chr(10).join(calls)}
    """)
    subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True)
    return json.loads(Path(record_path).read_text())


def test_the_extracted_function_is_the_real_one():
    """A test that silently drifts onto a stale copy passes forever."""
    src = DEPLOY.read_text()
    assert src.count("record() {") == 1
    assert 'record "success" "$SHA" "$TESTS_STATUS"' in src, (
        "deploy.sh no longer passes the test verdict on success")
    assert 'record "tests_failed" "$(git rev-parse HEAD)" "failed"' in src


def test_a_tested_deploy_records_that_it_was_tested(tmp_path):
    doc = run_record(tmp_path / "d.json", ['record "success" "aaa111" "passed"'])
    assert doc["running_commit"] == "aaa111"
    assert doc["running_tests"] == "passed"
    assert doc["last_attempt_tests"] == "passed"
    assert "last_skipped_tests_at" not in doc


def test_a_skipped_deploy_is_marked_and_says_which_commit(tmp_path):
    doc = run_record(tmp_path / "d.json", ['record "success" "bbb222" "skipped"'])
    assert doc["running_commit"] == "bbb222"
    assert doc["running_tests"] == "skipped"
    assert doc["last_skipped_tests_commit"] == "bbb222"
    assert doc["last_skipped_tests_at"]


def test_the_mark_survives_a_later_failed_attempt(tmp_path):
    """`running_tests` describes what is SERVING. A failed attempt does not
    change what is serving, so it must not launder the mark off it — the same
    rule `running_commit` already follows."""
    p = tmp_path / "d.json"
    run_record(p, ['record "success" "bbb222" "skipped"'])
    doc = run_record(p, ['record "tests_failed" "ccc333" "failed"'])
    assert doc["running_commit"] == "bbb222", "a failed deploy changed what is serving"
    assert doc["running_tests"] == "skipped", "the untested build is still the one serving"
    assert doc["last_attempt_tests"] == "failed"


def test_only_a_tested_deploy_clears_it(tmp_path):
    """The mark persists until it is replaced by a build that earned it."""
    p = tmp_path / "d.json"
    run_record(p, ['record "success" "bbb222" "skipped"'])
    assert json.loads(p.read_text())["running_tests"] == "skipped"
    doc = run_record(p, ['record "success" "ddd444" "passed"'])
    assert doc["running_tests"] == "passed"
    assert doc["running_commit"] == "ddd444"


def test_an_unhealthy_start_does_not_claim_the_gate_either(tmp_path):
    """`started_unhealthy` leaves running_commit on the previous build, so the
    verdict must stay with it too."""
    p = tmp_path / "d.json"
    run_record(p, ['record "success" "bbb222" "skipped"'])
    doc = run_record(p, ['record "started_unhealthy" "eee555" "passed"'])
    assert doc["running_commit"] == "bbb222"
    assert doc["running_tests"] == "skipped"


def test_a_record_from_before_this_existed_reads_as_unknown(tmp_path):
    """Not "passed". The box has such a record right now, and inventing a pass
    from a missing key is the failure docs/BUDGETS.md bans in the money layer,
    for the same reason."""
    p = tmp_path / "d.json"
    p.write_text(json.dumps({
        "production_branch": "production",
        "last_attempt_commit": "old111", "last_result": "success",
        "running_commit": "old111"}))
    doc = json.loads(p.read_text())
    assert doc.get("running_tests") is None

    # And the next real deploy fills it in rather than guessing backwards.
    doc = run_record(p, ['record "success" "new222" "passed"'])
    assert doc["running_tests"] == "passed"


# ── the gate itself, not just the bookkeeping ───────────────────────────────

def test_the_skip_branch_sets_the_status_and_says_so_loudly():
    src = DEPLOY.read_text()
    assert 'TESTS_STATUS="skipped"' in src
    assert 'TESTS_STATUS="passed"' in src
    assert "THE TEST GATE IS OFF FOR THIS DEPLOY" in src, (
        "skipping the suite must be loud in the deploy output as well as "
        "recorded — the person running it should not have to read JSON")


def test_status_reports_the_gate_and_never_invents_a_pass():
    src = (ROOT / "scripts" / "frankenstein-status.sh").read_text()
    assert "running_tests" in src
    assert "THE RUNNING BUILD WAS DEPLOYED WITH THE TEST GATE OFF" in src
    assert "unknown (record predates tracking)" in src, (
        "an absent verdict must read as unknown, not as passed")
