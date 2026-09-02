"""The review/production boundary: pushing code must not deploy it.

WHY: the poller used to track `git rev-parse --abbrev-ref HEAD` — whatever was
checked out on the box — which was the same branch implementation was pushed
to. Pushing for review therefore deployed to production, so the Product Owner
could not inspect a change before it went live.

These tests run the REAL scripts/autopull.sh against throwaway git repos with a
stub deploy.sh, and assert whether a deploy would actually have been triggered.
They test the decision, not a description of it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUTOPULL = ROOT / "scripts" / "autopull.sh"
PROMOTE = ROOT / "scripts" / "promote.sh"
PROTOCOL = ROOT / ".frankenstein" / "PROTOCOL.md"
DIRECTIVE = ROOT / ".frankenstein" / "PRODUCT_DIRECTIVE.md"


def sh(*args, cwd=None, env=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       timeout=90, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r


def git(*args, cwd):
    return sh("git", *args, cwd=cwd).stdout.strip()


@pytest.fixture
def box(tmp_path):
    """A fake OptiPlex: a bare 'GitHub' remote, a clone, and a stub deploy.sh
    that records when it is invoked instead of touching containers."""
    remote = tmp_path / "remote.git"
    clone = tmp_path / "box"
    sh("git", "init", "--bare", "-b", "production", str(remote))

    seed = tmp_path / "seed"
    seed.mkdir()
    sh("git", "init", "-b", "production", str(seed))
    sh("git", "config", "user.email", "t@t", cwd=seed)
    sh("git", "config", "user.name", "t", cwd=seed)
    (seed / "app.txt").write_text("v1\n")
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", "v1", cwd=seed)
    sh("git", "remote", "add", "origin", str(remote), cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)

    sh("git", "clone", "-q", str(remote), str(clone))
    sh("git", "config", "user.email", "t@t", cwd=clone)
    sh("git", "config", "user.name", "t", cwd=clone)

    # stub deploy.sh: writes a marker so we can prove whether it ran
    (clone / "scripts").mkdir(exist_ok=True)
    marker = clone / "DEPLOY_RAN"
    (clone / "scripts" / "deploy.sh").write_text(
        f'#!/usr/bin/env bash\necho "$1" > "{marker}"\n')
    shutil.copy(AUTOPULL, clone / "scripts" / "autopull.sh")

    state = tmp_path / "state"
    state.mkdir()
    return {"remote": remote, "clone": clone, "seed": seed, "marker": marker,
            "state": state, "record": state / "deployed.json"}


def set_running(box, sha, result="success"):
    """Write deployed.json as deploy.sh would: running_commit is the last
    SUCCESSFUL deploy, which is the poller's notion of 'what is running'."""
    box["record"].write_text(json.dumps({
        "production_branch": "production", "running_commit": sha,
        "last_attempt_commit": sha, "last_result": result,
        "last_attempt_at": "2026-09-01T00:00:00+00:00"}))


def converge(box):
    """Mark the box as having successfully deployed the current production tip."""
    sha = git("rev-parse", "origin/production", cwd=box["clone"])
    set_running(box, sha)
    return sha


def poll(box, branch=None):
    """Run one poller cycle exactly as the systemd unit does."""
    env = dict(os.environ, FRANKENSTEIN_DIR=str(box["clone"]),
               FRANKENSTEIN_STATE_DIR=str(box["state"]))
    env.pop("FRANKENSTEIN_BRANCH", None)
    if branch:
        env["FRANKENSTEIN_BRANCH"] = branch
    r = sh("bash", str(box["clone"] / "scripts" / "autopull.sh"),
           env=env, check=False)
    return r, box["marker"].exists()


def push_commit(box, branch, text):
    seed = box["seed"]
    sh("git", "checkout", "-q", "-B", branch, cwd=seed)
    (seed / "app.txt").write_text(text)
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", text, cwd=seed)
    sh("git", "push", "-q", "origin", branch, cwd=seed)
    return git("rev-parse", "HEAD", cwd=seed)


# ---- the core guarantee -------------------------------------------------

def test_pushing_a_task_branch_does_not_deploy(box):
    """Claude pushes FC work for review; production must not move."""
    converge(box)          # box has successfully deployed production
    push_commit(box, "claude/FC-002-some-task", "task work\n")
    r, deployed = poll(box)
    assert not deployed, (
        "pushing a task branch triggered a deploy — review before production "
        f"is impossible.\n{r.stdout}{r.stderr}")


@pytest.mark.parametrize("branch", [
    "claude/FC-003-money", "claude/personal-app-hub-vvpy4h",
    "experiment", "throwaway-test",
])
def test_no_non_production_branch_deploys(box, branch):
    converge(box)
    push_commit(box, branch, f"work on {branch}\n")
    _, deployed = poll(box)
    assert not deployed, f"{branch} triggered a deploy"


def test_changing_the_production_branch_does_deploy(box):
    """The other half: promotion MUST reach the box."""
    converge(box)
    sha = push_commit(box, "production", "promoted\n")
    r, deployed = poll(box)
    assert deployed, f"production moved but no deploy ran\n{r.stdout}{r.stderr}"
    assert box["marker"].read_text().strip() == "production"
    assert sha


def test_task_branch_then_promotion(box):
    """The full loop: push for review (no deploy), then promote (deploy)."""
    converge(box)
    sha = push_commit(box, "claude/FC-004-thing", "reviewable\n")
    _, deployed = poll(box)
    assert not deployed, "review push deployed"

    # Product Owner accepts; promotion fast-forwards production to that commit.
    sh("git", "push", "-q", box["remote"].as_posix(),
       f"{sha}:refs/heads/production", cwd=box["seed"])
    _, deployed = poll(box)
    assert deployed, "promotion to production did not deploy"


def test_missing_production_branch_fails_safe(box):
    """No production branch must mean NO deploy — never fall back to whatever
    is checked out (that fallback was the original bug)."""
    converge(box)
    push_commit(box, "claude/FC-005-x", "work\n")
    r, deployed = poll(box, branch="does-not-exist")
    assert not deployed
    assert r.returncode == 0, "should not spin as a failing unit"
    assert "NOT deploying" in r.stdout


def test_poller_never_falls_back_to_the_checked_out_branch():
    # Strip comments: the file explains the old bug in prose, and prose is not
    # behavior. Only executable lines are asserted on.
    src = "\n".join(l for l in AUTOPULL.read_text().splitlines()
                    if not l.lstrip().startswith("#"))
    assert "rev-parse --abbrev-ref HEAD" not in src, (
        "autopull.sh still defaults to the checked-out branch — that is the "
        "coupling that made every review push a production deploy")
    assert 'FRANKENSTEIN_BRANCH:-production' in src


# ---- deployment authorization semantics --------------------------------

def test_protocol_documents_push_is_allowed_under_every_auth_level():
    text = PROTOCOL.read_text().lower()
    for phrase in ("none", "test-only", "deploy-approved"):
        assert phrase in text
    assert "push" in text and "task branch" in text
    # the crucial sentence: pushing is not deploying
    assert ("pushing a task branch" in text or "task-branch push" in text)


def test_acceptance_and_deployment_are_documented_as_separate_gates():
    text = PROTOCOL.read_text().lower()
    assert "accept" in text and "deployment authorization" in text
    assert "does not" in text  # e.g. "accepted does not mean deploy now"


def test_state_does_not_imply_deployment(tmp_path):
    """An implementation_commit existing means code was pushed for REVIEW.
    It must never be read as 'this is running in production'."""
    state = json.loads((ROOT / ".frankenstein" / "STATE.json").read_text())
    assert "implementation_commit" in state
    assert "deployed_commit" not in state, (
        "deployment state must not live in STATE.json — it is recorded by "
        "deploy.sh on the box, since only the box knows what actually ran")


# ---- promotion guards ---------------------------------------------------

def test_promote_refuses_without_acceptance_and_authorization():
    r = sh("bash", str(PROMOTE), "--dry-run", cwd=ROOT, check=False)
    assert r.returncode != 0
    assert "REFUSED" in r.stdout


def test_promote_is_fast_forward_only():
    src = PROMOTE.read_text()
    assert "merge-base --is-ancestor" in src
    assert "not a fast-forward" in src


def test_promote_treats_missing_authorization_as_none():
    src = PROMOTE.read_text()
    assert 'auth="none"' in src


def test_directive_carries_the_authorization_field():
    assert "Deployment Authorization:" in DIRECTIVE.read_text()


# ---- rollback policy: production is append-only -------------------------

ROLLBACK = ROOT / "scripts" / "rollback.sh"
DEPLOY_DOC = ROOT / "docs" / "SETUP-DEPLOY.md"


def test_rollback_helper_exists_and_moves_forward():
    src = ROLLBACK.read_text()
    # builds a NEW commit carrying the good tree, on top of production
    assert "read-tree -u --reset" in src
    assert "refs/heads/$PROD_BRANCH" in src
    # assert on code, not prose — the header explains why force-pushing is wrong
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "--force" not in code, "rollback must never rewrite the branch"


def test_rollback_refuses_on_a_dirty_tree():
    assert "working tree is dirty" in ROLLBACK.read_text()


def test_force_with_lease_is_documented_as_emergency_only():
    """It may be documented, but never as the normal path."""
    for doc in (DEPLOY_DOC, PROTOCOL):
        text = doc.read_text().lower()
        if "force-with-lease" not in text:
            continue
        idx = text.index("force-with-lease")
        window = text[max(0, idx - 600):idx + 600]
        assert "emergency" in window, f"{doc.name}: force-with-lease lacks an emergency-only qualifier"
        assert ("high-risk" in window or "approval" in window), \
            f"{doc.name}: force-with-lease not marked as requiring approval"


def test_protocol_lists_branch_rewriting_as_high_risk():
    text = PROTOCOL.read_text().lower()
    assert "rewriting the production branch" in text


def test_normal_rollback_is_documented_before_the_emergency_one():
    text = DEPLOY_DOC.read_text().lower()
    assert "production moves forward" in text
    assert text.index("rollback.sh") < text.index("force-with-lease"), \
        "the emergency path must not be presented first"


def test_promote_bootstrap_flag_keeps_fast_forward_safety():
    src = PROMOTE.read_text()
    assert "--bootstrap" in src
    assert "PROTOCOL GATES SKIPPED" in src
    # the fast-forward guard must sit OUTSIDE the gate-skipping branch
    ff = src.index("merge-base --is-ancestor")
    gate = src.index('if [ "$FORCE" != "1" ]; then')
    gate_end = src.index("git fetch --prune origin")
    assert not (gate < ff < gate_end), "fast-forward check must apply even with --bootstrap"


# ══ deployment STATE MODEL: desired vs running, not local HEAD ══════════
#
# The live bootstrap wedged the poller. deploy.sh checks out and resets the
# repo to production BEFORE the test gate, so a commit whose tests fail leaves
# local HEAD == origin/production while the containers still run the previous
# build. The old poller compared HEAD and concluded "converged", permanently
# suppressing the retry.

def head_equals_production(box):
    """Reproduce deploy.sh's checkout: local HEAD moved to production."""
    sh("git", "fetch", "-q", "origin", "production", cwd=box["clone"])
    sh("git", "checkout", "-q", "-B", "production", "origin/production",
       cwd=box["clone"])
    return git("rev-parse", "HEAD", cwd=box["clone"])


def test_failed_test_gate_still_retries_deployment(box):
    """THE EXACT LIVE FAILURE.

    origin/production = NEW, local HEAD = NEW (deploy.sh reset it),
    last_result = tests_failed, running_commit = OLD.
    The poller MUST attempt to deploy NEW.
    """
    old = converge(box)
    new = push_commit(box, "production", "new code\n")
    head = head_equals_production(box)
    assert head == new, "precondition: HEAD was moved to production by the checkout"
    set_running(box, old, result="tests_failed")   # containers still on OLD

    r, deployed = poll(box)
    assert deployed, (
        "poller treated a failed deploy as converged because local HEAD "
        f"matched production — the live wedge.\n{r.stdout}{r.stderr}")
    assert "!= running" in r.stdout


def test_failed_deploy_retries_on_every_subsequent_cycle(box):
    """Retries must not be suppressed by local checkout state."""
    old = converge(box)
    push_commit(box, "production", "new code\n")
    head_equals_production(box)
    set_running(box, old, result="tests_failed")
    for cycle in range(3):
        box["marker"].unlink(missing_ok=True)
        _, deployed = poll(box)
        assert deployed, f"retry suppressed on cycle {cycle + 1}"


def test_null_running_commit_attempts_deployment(box):
    """origin/production = NEW, HEAD = NEW, running_commit = null.
    Never infer success from HEAD; attempt the normal test-gated deploy."""
    push_commit(box, "production", "new code\n")
    head_equals_production(box)
    box["record"].write_text(json.dumps({
        "production_branch": "production", "running_commit": None,
        "last_attempt_commit": None, "last_result": "unknown"}))
    r, deployed = poll(box)
    assert deployed, "null running_commit must be treated as unknown, not converged"
    assert "no confirmed running commit" in r.stdout


@pytest.mark.parametrize("content,label", [
    ("", "empty file"),
    ("{ not json", "unparseable"),
    ('{"production_branch": "production"}', "key absent"),
    ('{"running_commit": ""}', "empty string"),
])
def test_unreadable_record_fails_safe_by_deploying(box, content, label):
    push_commit(box, "production", "new code\n")
    head_equals_production(box)
    box["record"].write_text(content)
    _, deployed = poll(box)
    assert deployed, f"{label}: unknown running state must attempt a deploy"


def test_missing_record_fails_safe_by_deploying(box):
    push_commit(box, "production", "new code\n")
    head_equals_production(box)
    assert not box["record"].exists()
    _, deployed = poll(box)
    assert deployed, "a missing record must be treated as unknown, not converged"


def test_running_equals_desired_does_not_deploy_whatever_head_is(box):
    """The converse: running_commit == desired means no deploy, even with
    local HEAD pointing somewhere else entirely."""
    sha = converge(box)
    sh("git", "checkout", "-q", "-B", "some-other-branch", cwd=box["clone"])
    (box["clone"] / "scratch.txt").write_text("local mess\n")
    sh("git", "add", "-A", cwd=box["clone"])
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "local divergence", cwd=box["clone"])
    assert git("rev-parse", "HEAD", cwd=box["clone"]) != sha
    _, deployed = poll(box)
    assert not deployed, "local HEAD must not trigger a deploy; running == desired"


def test_poller_never_reads_local_head_for_convergence():
    src = "\n".join(l for l in AUTOPULL.read_text().splitlines()
                    if not l.lstrip().startswith("#"))
    assert "rev-parse HEAD" not in src, (
        "local HEAD is not proof that a commit is running — it is moved by "
        "deploy.sh before the test gate")
    assert "running_commit" in src, "the poller must consult the deployment record"


# ---- deploy.sh record invariant ----------------------------------------

DEPLOY = ROOT / "scripts" / "deploy.sh"


def test_failed_deploy_never_advances_running_commit():
    """Invariant: running_commit = last SUCCESSFULLY deployed commit."""
    src = DEPLOY.read_text()
    record_fn = src[src.index("record() {"):src.index("echo \"==> Deploying")]
    assert 'local running="$prev"' in record_fn
    assert '[ "$result" = "success" ] && running="$sha"' in record_fn, \
        "running_commit may only advance on success"


def test_failed_deploy_records_the_attempt():
    src = DEPLOY.read_text()
    assert 'record "tests_failed"' in src, "a failed gate must be recorded"
    assert "last_attempt_commit" in src


def test_running_commit_survives_a_failed_deploy(tmp_path):
    """Behavioral: run deploy.sh's record() twice — success then failure —
    and confirm running_commit stays on the successful SHA."""
    src = DEPLOY.read_text()
    fn = src[src.index("record() {"):src.index('echo "==> Deploying')]
    record_json = tmp_path / "deployed.json"
    harness = tmp_path / "h.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        f'RECORD="{record_json}"\nBRANCH="production"\n' + fn +
        '\nrecord "success" "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        'record "tests_failed" "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\n')
    subprocess.run(["bash", str(harness)], capture_output=True, timeout=60)
    doc = json.loads(record_json.read_text())
    assert doc["running_commit"] == "a" * 40, "a failed deploy advanced running_commit"
    assert doc["last_attempt_commit"] == "b" * 40, "the attempt should be recorded"
    assert doc["last_result"] == "tests_failed"


# ---- test-host tooling --------------------------------------------------

TEST_SH = ROOT / "scripts" / "test.sh"


def test_test_sh_never_invokes_a_bare_pip():
    """The OptiPlex had python3 with no `pip` in PATH; that failed a deploy."""
    for line in TEST_SH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("echo"):
            continue
        # remove the legitimate module form first, then look for a bare binary
        without_module = stripped.replace("python3 -m pip", "")
        assert not re.search(r'(^|[;&|(\s])pip\s+install', without_module), \
            f"bare pip invocation: {stripped}"


def test_test_sh_uses_python3_pip_module():
    assert "python3 -m pip install" in TEST_SH.read_text()


def test_test_sh_names_the_host_prerequisite():
    text = TEST_SH.read_text()
    assert "python3-pip" in text, "must name the exact host package"
    assert "does not run apt or sudo itself" in text


def test_test_sh_does_not_run_apt_or_sudo():
    code_lines = [l for l in TEST_SH.read_text().splitlines()
                  if not l.strip().startswith("#") and not l.strip().startswith("echo")]
    joined = "\n".join(code_lines)
    assert "apt-get install" not in joined and "sudo " not in joined
