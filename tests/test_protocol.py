"""Tests for the Product Owner <-> Claude protocol files and helper.

This is development-process infrastructure, not product code. What matters:
STATE.json stays valid and machine-readable, the documented vocabularies match
what the helper enforces, and an inconsistent state is DETECTED rather than
silently tolerated (the protocol's rule is "do not guess — block").
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRANK = ROOT / ".frankenstein"
STATE = FRANK / "STATE.json"
PROTOCOL = FRANK / "PROTOCOL.md"
DIRECTIVE = FRANK / "PRODUCT_DIRECTIVE.md"
HANDOFF = FRANK / "IMPLEMENTATION_HANDOFF.md"
HELPER = ROOT / "scripts" / "frankenstein-status.sh"

TURNS = {"product_owner", "claude", "none"}
STATUSES = {"awaiting_directive", "ready_for_implementation", "implementing",
            "awaiting_review", "changes_requested", "accepted", "blocked"}


def helper(*args, state_text=None, tmp_path=None):
    """Run the helper, optionally against a temporary copy of the repo state."""
    if state_text is None:
        return subprocess.run(["bash", str(HELPER), *args], capture_output=True,
                              text=True, cwd=ROOT, timeout=60)
    # Swap STATE.json, run, restore — so a bad state is exercised for real.
    original = STATE.read_text()
    try:
        STATE.write_text(state_text)
        return subprocess.run(["bash", str(HELPER), *args], capture_output=True,
                              text=True, cwd=ROOT, timeout=60)
    finally:
        STATE.write_text(original)


# ---- the files exist and are well formed -------------------------------

def test_all_four_protocol_files_exist():
    for f in (PROTOCOL, DIRECTIVE, HANDOFF, STATE):
        assert f.exists(), f"missing {f.relative_to(ROOT)}"


def test_state_is_valid_json_with_the_documented_schema():
    state = json.loads(STATE.read_text())
    for key in ("protocol_version", "task_id", "turn", "status",
                "directive_commit", "implementation_commit", "last_actor",
                "updated_at"):
        assert key in state, f"STATE.json missing {key}"
    assert isinstance(state["protocol_version"], int)
    assert state["turn"] in TURNS
    assert state["status"] in STATUSES


def test_protocol_documents_every_allowed_value():
    """The vocabulary the helper enforces must be written down for humans."""
    text = PROTOCOL.read_text()
    for value in TURNS | STATUSES:
        assert value in text, f"{value} is not documented in PROTOCOL.md"


def test_protocol_states_the_core_rules():
    text = PROTOCOL.read_text().lower()
    for phrase in ("ready_for_implementation", "changes_requested",
                   "deployment authorization", "high-risk",
                   "no deviations", "authoritative"):
        assert phrase in text, f"PROTOCOL.md does not cover: {phrase}"


# ---- initialized neutral, with nothing fabricated ----------------------

AUTHORIZATIONS = {"none", "test-only", "deploy-approved"}


def test_state_and_directive_identify_the_same_task():
    """The committed protocol files must agree with each other.

    This replaces an assertion that the state was still the BOOTSTRAP state.
    The worker materializes the real control directive and STATE.json into
    every task branch, so a live task legitimately says turn=claude with a
    real authorization -- and scripts/test.sh is the deploy gate, so asserting
    the idle state here would block every real task from ever deploying.
    What must hold is that the two files tell the same story.
    """
    state = json.loads(STATE.read_text())
    text = DIRECTIVE.read_text()

    ids = re.findall(r"(?mi)^[ \t]*Task[ \t]*ID[ \t]*:[ \t]*(\S+)[ \t]*$", text)
    assert len(ids) == 1, f"expected exactly one Task ID line, found {ids}"
    assert ids[0] == state["task_id"], (
        f"PRODUCT_DIRECTIVE.md names {ids[0]} but STATE.json says "
        f"{state['task_id']}")


def test_the_directive_carries_exactly_one_known_authorization():
    """A missing or unrecognized value is treated as `none` by the protocol,
    so an ambiguous directive must never reach the branch in the first place.
    """
    text = DIRECTIVE.read_text()
    auth = re.findall(
        r"(?mi)^[ \t]*Deployment Authorization[ \t]*:[ \t]*(\S+)[ \t]*$", text)
    assert len(auth) == 1, (
        f"expected exactly one Deployment Authorization line, found {auth}")
    assert auth[0] in AUTHORIZATIONS, (
        f"{auth[0]!r} is not one of {sorted(AUTHORIZATIONS)}")


def test_an_unauthorized_state_cannot_start_product_work():
    """Whatever the committed state is, the helper's verdict must match it."""
    state = json.loads(STATE.read_text())
    claude_may_work = (state["turn"] == "claude" and
                       state["status"] in ("ready_for_implementation",
                                           "changes_requested"))
    out = helper().stdout
    if claude_may_work:
        assert "Claude MAY implement" in out, out
    else:
        assert "Claude MAY implement" not in out, (
            "the helper authorized work the state does not permit:\n" + out)


def test_no_fabricated_handoff():
    """A handoff must never describe work that did not happen.

    The invariant is CONSISTENCY, not emptiness. Once the loop is live the
    worker writes a real handoff into every task branch, so asserting the
    bootstrap sentinel is still present would block all real work. What must
    stay true is that the handoff and STATE.json tell the same story.
    """
    text = HANDOFF.read_text()
    state = json.loads(STATE.read_text())
    commit = state["implementation_commit"]

    if commit is None:
        # Nothing is recorded as implemented, so the handoff may not present
        # itself as a delivered FC task. Two forms are legitimate: the
        # untouched sentinel, or a handoff that explicitly says it is not one.
        low = text.lower()
        assert ("no implementation handoff has been written" in low
                or "not an fc task" in low), (
            "IMPLEMENTATION_HANDOFF.md describes delivered work, but "
            "STATE.json records implementation_commit=null")
    else:
        assert re.fullmatch(r"[0-9a-f]{40}", commit), (
            f"implementation_commit is not a full SHA: {commit!r}")
        assert "no implementation handoff has been written" not in text.lower(), (
            "STATE.json records an implementation, but the handoff is still "
            "the bootstrap placeholder")


def a_real_commit():
    """The helper verifies that a recorded SHA exists in this repository, so a
    made-up one is reported as a problem rather than exercising the state."""
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True,
                          timeout=60).stdout.strip()


REAL_SHA = a_real_commit()


@pytest.mark.parametrize("turn,status,extra,verdict", [
    ("claude", "ready_for_implementation", {}, "Claude MAY implement"),
    ("claude", "changes_requested", {"implementation_commit": REAL_SHA},
     "Claude MAY implement"),
    ("claude", "blocked", {}, "BLOCKED"),
    ("claude", "implementing", {}, "Not Claude's turn"),
    ("product_owner", "awaiting_review", {"implementation_commit": REAL_SHA},
     "Not Claude's turn"),
    ("product_owner", "awaiting_directive", {}, "Not Claude's turn"),
    ("none", "accepted", {"implementation_commit": REAL_SHA},
     "Accepted; no task in flight"),
])
def test_the_helper_verdict_matches_every_realistic_state(turn, status, extra,
                                                          verdict):
    """Exercise the real protocol states, not just the bootstrap one.

    These are the states a live task actually passes through, each supplied
    COHERENTLY -- an incoherent state is a separate case below, and the helper
    is required to block on it rather than render a verdict.
    """
    state = json.loads(STATE.read_text())
    state.update(turn=turn, status=status, **extra)
    r = helper(state_text=json.dumps(state, indent=2))
    assert r.returncode == 0, r.stdout + r.stderr
    assert verdict in r.stdout, f"{turn}/{status} gave:\n{r.stdout}"
    if verdict != "Claude MAY implement":
        assert "Claude MAY implement" not in r.stdout


@pytest.mark.parametrize("turn,status,extra,why", [
    ("product_owner", "awaiting_review", {"implementation_commit": None},
     "awaiting review with nothing implemented"),
    ("claude", "accepted", {"implementation_commit": REAL_SHA},
     "accepted while still Claude's turn"),
    ("claude", "ready_for_implementation", {"implementation_commit": "b" * 40},
     "a recorded SHA that is not a commit in this repository"),
])
def test_an_incoherent_state_blocks_instead_of_guessing(turn, status, extra, why):
    """The protocol's rule is "do not guess -- block"."""
    state = json.loads(STATE.read_text())
    state.update(turn=turn, status=status, **extra)
    r = helper(state_text=json.dumps(state, indent=2))
    assert r.returncode != 0, f"{why} was tolerated:\n{r.stdout}"


# ---- the helper ---------------------------------------------------------

def test_helper_reports_status_and_succeeds():
    r = helper()
    assert r.returncode == 0, r.stderr
    assert "Frankenstein Development Protocol" in r.stdout
    # The verdict depends on the committed state and is asserted against it in
    # test_an_unauthorized_state_cannot_start_product_work; what must always
    # hold here is that the helper renders a verdict at all.
    assert "  => " in r.stdout, r.stdout


def test_helper_check_passes_on_the_committed_state():
    r = helper("--check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_helper_proposes_a_sequential_next_id():
    r = helper("--next-id")
    assert r.returncode == 0
    assert "Next task id:       FC-" in r.stdout


# ---- inconsistency must be DETECTED, not tolerated ---------------------

@pytest.mark.parametrize("bad,why", [
    ('{"protocol_version":1,"task_id":"FC-001","turn":"wizard",'
     '"status":"accepted","directive_commit":null,"implementation_commit":null,'
     '"last_actor":null,"updated_at":"x"}', "invalid turn"),
    ('{"protocol_version":1,"task_id":"FC-001","turn":"claude",'
     '"status":"vibing","directive_commit":null,"implementation_commit":null,'
     '"last_actor":null,"updated_at":"x"}', "invalid status"),
    ('{"protocol_version":1,"task_id":"nope","turn":"none",'
     '"status":"accepted","directive_commit":null,"implementation_commit":null,'
     '"last_actor":null,"updated_at":"x"}', "malformed task id"),
    ('{"protocol_version":1,"task_id":"FC-001","turn":"product_owner",'
     '"status":"awaiting_review","directive_commit":null,'
     '"implementation_commit":null,"last_actor":"claude","updated_at":"x"}',
     "review pending on a nonexistent implementation"),
    ('{"protocol_version":1,"task_id":"FC-001","turn":"claude",'
     '"status":"accepted","directive_commit":null,"implementation_commit":null,'
     '"last_actor":null,"updated_at":"x"}', "accepted but turn=claude"),
    ('{"protocol_version":1,"task_id":"FC-001","turn":"product_owner",'
     '"status":"accepted","directive_commit":"deadbeefdeadbeef",'
     '"implementation_commit":null,"last_actor":null,"updated_at":"x"}',
     "commit that does not exist"),
    ('{"turn":"claude","status":"implementing"}', "missing required fields"),
    ('{not json at all', "unparseable"),
])
def test_helper_check_rejects_inconsistent_state(bad, why):
    r = helper("--check", state_text=bad)
    assert r.returncode != 0, f"helper accepted an invalid state: {why}\n{r.stdout}"


def test_task_id_mismatch_between_state_and_directive_is_caught():
    state = json.loads(STATE.read_text())
    state["task_id"] = "FC-999"          # directive still says FC-001
    r = helper("--check", state_text=json.dumps(state))
    assert r.returncode != 0
    assert "task id" in r.stdout.lower()


# ---- this is process infrastructure, not a product change --------------

def test_protocol_files_do_not_touch_product_code():
    """Everything this task adds lives in process-only paths."""
    allowed_prefixes = (".frankenstein/", "scripts/frankenstein-status.sh",
                        "tests/", "CLAUDE.md", "scripts/test.sh")
    for f in (PROTOCOL, DIRECTIVE, HANDOFF, STATE, HELPER):
        rel = str(f.relative_to(ROOT))
        assert rel.startswith(allowed_prefixes), rel


def test_helper_prints_no_env_values():
    r = helper()
    combined = r.stdout + r.stderr
    for secretish in ("TOKEN=", "SECRET=", "PASSWORD=", "Bearer "):
        assert secretish not in combined
