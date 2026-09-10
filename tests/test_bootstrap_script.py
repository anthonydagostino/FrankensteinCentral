"""Regression tests for scripts/protocol-bootstrap.sh.

This script runs with sudo on the production box, stops the deploy timer, and
moves the production branch. The failure modes below were found in Product
Owner review of the first version; each test exists so they cannot come back.

Behavioral tests execute the real script against throwaway git repos. Where a
check can only be reached on a machine with systemd and a live poller, the test
asserts on the script's CODE (comments stripped — prose is not behavior).
"""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "protocol-bootstrap.sh"


def code() -> str:
    """The script with comments stripped: assert on behavior, not prose."""
    return "\n".join(l for l in SCRIPT.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


def main_flow() -> str:
    """Code with the cleanup() definition removed.

    cleanup() is defined near the top but executes last (via trap), so its
    position in the file says nothing about execution order.
    """
    c = code()
    return c[:c.index("cleanup() {")] + c[c.index("trap cleanup"):]


def run(*args, cwd=None, env=None):
    e = {"HOME": "/tmp", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    if env:
        e.update(env)
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                          text=True, timeout=120, cwd=cwd, env=e)


@pytest.fixture
def repo(tmp_path):
    """A minimal git repo standing in for the box's clone."""
    d = tmp_path / "box"
    d.mkdir()
    sh = lambda *a: subprocess.run(a, cwd=d, capture_output=True, timeout=30)
    sh("git", "init", "-q", "-b", "production")
    sh("git", "config", "user.email", "t@t")
    sh("git", "config", "user.name", "t")
    (d / "README.md").write_text("v1\n")
    (d / "scripts").mkdir()
    (d / "scripts" / "autopull.sh").write_text('FRANKENSTEIN_BRANCH:-production\n')
    sh("git", "add", "-A")
    sh("git", "commit", "-qm", "seed")
    return d


# ---- item 8: explicit target, no stale default -------------------------

def test_target_sha_is_required():
    r = run()
    assert r.returncode != 0
    assert "TARGET_SHA is required" in r.stdout


def test_no_stale_hardcoded_target_default():
    assert "a65d272" not in SCRIPT.read_text(), "stale SHA default returned"
    assert re.search(r'TARGET_SHA="\$\{1:-\}"', code()), "target must default to empty"


# ---- item 1: dirty working tree hard-stops -----------------------------

def test_dirty_tracked_tree_hard_stops(repo):
    (repo / "README.md").write_text("uncommitted edit\n")
    r = run("HEAD", env={"FRANKENSTEIN_DIR": str(repo)})
    assert r.returncode != 0
    assert "uncommitted changes to tracked files" in r.stdout
    assert "reset --hard" in r.stdout, "must explain WHY a dirty tree is fatal"


def test_clean_tree_passes_the_dirty_gate(repo):
    r = run("HEAD", env={"FRANKENSTEIN_DIR": str(repo)})
    assert "working tree         : clean" in r.stdout


def test_untracked_files_are_tolerated_not_fatal(repo):
    (repo / "local-artifact.log").write_text("noise\n")
    r = run("HEAD", env={"FRANKENSTEIN_DIR": str(repo)})
    assert "working tree         : clean" in r.stdout
    assert "uncommitted changes to tracked files" not in r.stdout


def test_dirty_check_runs_before_any_mutation():
    c = main_flow()
    gate = c.index("uncommitted changes to tracked files")
    assert gate < c.index("systemctl stop"), "timer stopped before the dirty check"
    assert gate < c.index("git push origin"), "pushed before the dirty check"
    assert gate < c.index("sudo tee"), "systemd drop-in written before the dirty check"


# ---- item 2: unique throwaway branch, proven not to pre-exist ----------

def test_throwaway_branch_name_is_unique():
    c = code()
    assert "throwaway-boundary-test-$(date" in c and "$$" in c, \
        "throwaway branch name must be unique per run"


def test_throwaway_pre_existence_is_checked():
    c = code()
    assert 'git ls-remote --heads origin "$CANDIDATE"' in c
    assert "unexpectedly already exists" in c


# ---- item 3: cleanup on interruption or failure ------------------------

def test_trap_covers_interruption_and_failure():
    c = code()
    assert "trap cleanup EXIT" in c
    assert "'exit 130' INT" in c and "'exit 143' TERM" in c


def test_cleanup_deletes_the_throwaway_branch_and_restores_the_timer():
    c = code()
    cleanup = c[c.index("cleanup() {"):c.index("trap cleanup")]
    assert "git push origin --delete" in cleanup
    assert "systemctl start" in cleanup
    assert "IS NOT ACTIVE" in cleanup, "must warn loudly if the timer cannot be restored"


def test_throwaway_is_registered_for_cleanup_only_after_it_exists():
    c = code()
    push = c.index('git push origin "$TARGET_FULL:refs/heads/$CANDIDATE"')
    register = c.index('THROWAWAY="$CANDIDATE"')
    assert push < register, "must not schedule deletion of a branch that was never created"


# ---- items 4 and 5: failure stops early and exits non-zero -------------

def test_failed_test_b_stops_before_test_a():
    c = code()
    fail_b = c.index("production deployment did not succeed")
    test_a = c.index("6. TEST A")
    assert fail_b < test_a, "a failed TEST B must stop before TEST A runs"
    assert "Not running TEST A" in c


def test_failed_boundary_tests_exit_non_zero():
    c = code()
    tail = c[c.index("PROTOCOL BOOTSTRAP RESULT"):]
    assert 'TEST A did not pass' in tail and "exit 1" in tail
    assert 'TEST B did not pass' in tail


def test_success_path_exits_zero():
    assert "PASS) exit 0 ;;" in code()


# ---- item 6: fresh fetch and full-SHA equality -------------------------

def test_production_is_refreshed_before_test_a():
    c = code()
    test_a = c.index("6. TEST A")
    assert "git fetch --prune origin" in c[test_a:c.index('PROD_A=')], \
        "origin/production must be refreshed immediately before the comparison"


def test_equality_checks_use_full_shas():
    c = code()
    for var in ("PROD_A=", "PROD_B=", "TARGET_FULL=", "PROD_BEFORE_FULL="):
        line = next(l for l in c.splitlines() if l.strip().startswith(var))
        assert "--short" not in line, f"{var} must hold a full SHA for comparison"


# ---- item 7: the timer must exist and be active ------------------------

def test_timer_existence_and_activity_are_verified():
    c = code()
    assert "systemctl list-unit-files" in c
    assert "systemctl is-active --quiet" in c
    assert "would prove nothing" in c


# ---- item 9: poller/bootstrap race ------------------------------------

def test_timer_is_quiesced_before_git_and_systemd_work():
    c = code()
    stop = c.index("systemctl stop")
    assert stop < c.index("git push origin \"$TARGET_FULL:refs/heads/$PROD_BRANCH\"")
    assert stop < c.index("systemctl daemon-reload")


def test_in_flight_deploy_is_waited_out():
    c = code()
    assert "in-flight deploy" in c
    assert "still running after 10 minutes" in c


def test_timer_is_restarted_before_the_poll_cycle_tests():
    c = code()
    restart = c.index('sudo systemctl start "$TIMER"')
    assert restart < c.index("6. TEST A"), "the poller must be running for the tests"
    assert 'TIMER_STOPPED=0' in c


# ---- item 10: already-at-target stays honestly unproven ----------------

def test_already_at_target_does_not_manufacture_a_commit():
    c = code()
    assert "NOT PROVABLE" in c
    assert "fabricating evidence" in c
    assert "exit 3" in c, "unproven must be distinguishable from proven"


def test_no_marker_commit_is_ever_created():
    c = code()
    assert "git commit" not in c, "the script must never create a commit"


# ---- item 11: honest wording about containers --------------------------

def test_container_wording_is_accurate():
    header = SCRIPT.read_text()[:2000]
    assert "does not touch containers" in header
    assert "rebuild and restart containers" in header, \
        "must state that promotion causes the pipeline to restart containers"


def test_never_claims_to_avoid_all_container_effects():
    assert "never touches containers, volumes" not in SCRIPT.read_text(), \
        "the old wording was misleading — promotion does restart containers"


# ---- safety invariants -------------------------------------------------

def test_never_force_pushes():
    c = code()
    assert "--force" not in c and "force-with-lease" not in c


def test_no_destructive_docker_or_data_commands():
    """Look at commands actually invoked, not text quoted inside messages.
    The script mentions 'git reset --hard' when explaining why a dirty tree is
    fatal; quoting it is not running it."""
    invoked = []
    for line in code().splitlines():
        stripped = line.strip()
        if stripped.startswith(("echo ", "die ", "printf ", "cat <<")):
            continue
        invoked.append(re.sub(r'"[^"]*"', "", stripped))  # drop quoted strings
    joined = "\n".join(invoked)
    for danger in ("docker compose down", "docker volume rm", "docker rm",
                   "rm -rf", "git reset --hard", "git clean"):
        assert danger not in joined, f"destructive command present: {danger}"


# ══ Product Owner second review — four implementation-level fixes ═══════

# ---- issue 1: recovery flag cleared only after PROVEN active -----------

def test_timer_stopped_flag_is_cleared_only_after_proving_active():
    """TIMER_STOPPED means "cleanup must still restore this timer". Clearing it
    on the start command's exit status would disarm recovery while the timer
    was actually down."""
    c = code()
    resume = c[c.index("RESUME THE TIMER"):c.index("running_commit()")]
    start = resume.index('systemctl start "$TIMER"')
    verify = resume.index("is-active --quiet")
    clear = resume.index("TIMER_STOPPED=0")
    assert start < verify < clear, (
        "TIMER_STOPPED must be cleared AFTER is-active verification, not before")


def test_timer_verification_failure_still_has_recovery_armed():
    c = code()
    resume = c[c.index("RESUME THE TIMER"):c.index("running_commit()")]
    die_line = resume.index("did not come back active")
    clear = resume.index("TIMER_STOPPED=0")
    assert die_line < clear, "the die() on inactive timer must fire while recovery is armed"


# ---- issue 2: cleanup is not reentrant --------------------------------

def test_signals_funnel_through_a_single_exit_trap():
    c = code()
    assert "trap cleanup EXIT\n" in c, "cleanup must be trapped on EXIT only"
    assert "trap 'exit 130' INT" in c, "INT must set status, not invoke cleanup directly"
    assert "trap 'exit 143' TERM" in c, "TERM must set status, not invoke cleanup directly"
    assert "trap cleanup EXIT INT TERM" not in c, "reentrant trap pattern returned"


def test_cleanup_has_an_idempotency_guard():
    c = code()
    body = c[c.index("cleanup() {"):c.index("trap cleanup EXIT")]
    assert 'CLEANUP_RAN' in body and "return" in body


def test_cleanup_runs_exactly_once_when_invoked_twice(tmp_path):
    """Behavioral: extract the guard + a side effect and call it twice."""
    marker = tmp_path / "ran"
    harness = tmp_path / "h.sh"
    src = SCRIPT.read_text()
    guard = src[src.index("CLEANUP_RAN=0"):src.index("trap cleanup EXIT")]
    # replace the privileged body with a countable side effect
    guard = re.sub(r'(?s)(CLEANUP_RAN=1).*', r'\1\n  echo x >> "' + str(marker) + '"\n}\n', guard)
    harness.write_text("#!/usr/bin/env bash\n" + guard + "\ncleanup\ncleanup\ncleanup\n")
    subprocess.run(["bash", str(harness)], capture_output=True, timeout=30)
    assert marker.read_text().count("x") == 1, "cleanup body ran more than once"


# ---- issue 3: already-at-target stops before TEST A --------------------

def test_already_at_target_exits_before_test_a():
    c = code()
    early = c.index('if [ "$ALREADY_AT_TARGET" = "1" ]; then')
    test_a = c.index("6. TEST A")
    assert early < test_a
    block = c[early:test_a]
    assert "exit 3" in block, "the N/A path must exit 3 before TEST A"
    assert "print_summary" in block, "it must still print a result block"
    assert "not run" in block, "TEST A must be reported as not run, not as passed"


def test_already_at_target_creates_no_throwaway_branch():
    c = code()
    # locate the EARLY-EXIT block specifically; the promote stage's else-branch
    # legitimately contains the promotion push
    early = c.index('TEST_A="not run')
    block = c[early:c.index("6. TEST A")]
    assert "CANDIDATE" not in block, "no throwaway branch may be created on the N/A path"
    assert "git push" not in block


def test_na_path_states_acceptance_was_not_obtained():
    c = code()
    assert "two-direction proof required for protocol acceptance was NOT obtained" in c


def test_summary_function_is_defined_before_it_is_used():
    """The N/A path calls print_summary earlier than the normal path; bash
    needs the definition first."""
    src = SCRIPT.read_text()
    definition = src.index("print_summary() {")
    first_call = min(i for i in range(len(src))
                     if src.startswith("print_summary", i)
                     and not src.startswith("print_summary() {", i))
    assert definition < first_call


# ---- issue 4: remote confirmation is mandatory ------------------------

def test_throwaway_remote_confirmation_is_mandatory():
    c = code()
    confirm = c[c.index('THROWAWAY="$CANDIDATE"'):c.index("waiting 150s")]
    assert "|| die" in confirm, (
        "remote confirmation must stop the script; without set -e a bare "
        "&& echo silently continues into an invalid isolation test")
    assert "cannot be observed on the remote" in confirm


def test_confirmation_failure_message_explains_the_risk():
    c = code()
    assert "proves nothing" in c[c.index('THROWAWAY="$CANDIDATE"'):]
