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


# ══ fail-safe inputs: bad repo dir, failed fetch, stale refs ════════════
#
# DESIRED must represent what is on GitHub RIGHT NOW. Anything less — a stale
# remote-tracking ref, an unreadable repo — must deploy nothing.

def test_invalid_repo_directory_deploys_nothing(box, tmp_path):
    """Without set -e an unchecked cd would leave the poller running in
    whatever directory systemd started it in."""
    env = dict(os.environ, FRANKENSTEIN_DIR=str(tmp_path / "does-not-exist"),
               FRANKENSTEIN_STATE_DIR=str(box["state"]))
    env.pop("FRANKENSTEIN_BRANCH", None)
    r = sh("bash", str(box["clone"] / "scripts" / "autopull.sh"), env=env, check=False)
    assert not box["marker"].exists(), "deployed from an invalid repo directory"
    assert "cannot be entered" in r.stdout
    assert r.returncode == 0, "should not spin as a failing unit"


def test_repo_dir_failure_never_reaches_fetch_or_deploy():
    src = "\n".join(l for l in AUTOPULL.read_text().splitlines()
                    if not l.lstrip().startswith("#"))
    guard = src.index("cannot be entered")
    assert guard < src.index("git fetch"), "cd must be checked before fetching"
    assert guard < src.index("running_commit"), "before reading deployment state"
    assert guard < src.index("deploy.sh"), "before invoking deploy.sh"


def test_failed_fetch_does_not_fall_back_to_a_stale_ref(box):
    """A: stale origin/production exists locally, fetch now fails.
    The poller must NOT decide from the stale ref."""
    converge(box)
    new = push_commit(box, "production", "newer\n")
    sh("git", "fetch", "-q", "origin", "production", cwd=box["clone"])
    stale = git("rev-parse", "origin/production", cwd=box["clone"])
    assert stale == new, "precondition: a remote-tracking ref exists locally"

    # break the remote so the next fetch fails, leaving the stale ref in place
    shutil.rmtree(box["remote"])
    r, deployed = poll(box)
    assert not deployed, (
        "poller acted on a stale remote-tracking ref after a failed fetch\n"
        f"{r.stdout}{r.stderr}")
    assert "UNKNOWN" in r.stdout and "stale" in r.stdout
    assert r.returncode == 0


def test_successful_fetch_uses_the_fresh_ref(box):
    """B: fetch succeeds -> normal desired-vs-running logic applies."""
    converge(box)
    push_commit(box, "production", "promoted\n")
    r, deployed = poll(box)
    assert deployed, f"fresh production change should deploy\n{r.stdout}"
    assert "!= running" in r.stdout


def test_unresolvable_desired_ref_deploys_nothing(box):
    """C: fetch succeeds but the desired ref cannot be resolved."""
    converge(box)
    r, deployed = poll(box, branch="branch-that-does-not-exist")
    assert not deployed
    assert "NOT deploying" in r.stdout
    assert r.returncode == 0


def test_fetch_is_a_gate_not_a_best_effort():
    src = "\n".join(l for l in AUTOPULL.read_text().splitlines()
                    if not l.lstrip().startswith("#"))
    fetch_line = next(l for l in src.splitlines() if "git fetch --prune origin" in l)
    assert "|| true" not in fetch_line, (
        "a best-effort fetch lets a stale remote-tracking ref decide the "
        "production boundary")
    assert "if ! git fetch" in src


# ---- status: null running_commit is PENDING ---------------------------

STATUS = ROOT / "scripts" / "frankenstein-status.sh"


def status_with(tmp_path, record: dict | None, branch=None):
    """Run the status helper against a controlled deployment record.

    `branch` pins which ref counts as production. Without it the helper
    resolves origin/production from whatever the ambient checkout happens to
    have -- which exists on the box and does NOT exist on a CI runner that
    checked out a single branch, so a test relying on it passes locally and
    fails in CI.
    """
    state = tmp_path / "st"
    state.mkdir(exist_ok=True)
    if record is not None:
        (state / "deployed.json").write_text(json.dumps(record))
    env = dict(os.environ, FRANKENSTEIN_STATE_DIR=str(state))
    if branch is not None:
        env["FRANKENSTEIN_BRANCH"] = branch
    return subprocess.run(["bash", str(STATUS)], capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=60).stdout


def test_null_running_commit_reports_deployment_pending(tmp_path):
    """The exact post-bootstrap OptiPlex state."""
    out = status_with(tmp_path, {
        "production_branch": "production", "running_commit": None,
        "last_attempt_commit": "b" * 40, "last_result": "tests_failed",
        "last_attempt_at": "2026-09-01T00:00:00+00:00"})
    assert "DEPLOYMENT PENDING" in out
    assert "no confirmed running deployment" in out
    assert "none confirmed" in out


def test_null_running_status_does_not_claim_containers_are_down(tmp_path):
    out = status_with(tmp_path, {
        "production_branch": "production", "running_commit": None,
        "last_attempt_commit": "b" * 40, "last_result": "tests_failed"})
    assert "does not mean containers are down" in out


def test_mismatched_running_commit_reports_pending(tmp_path):
    # HEAD always resolves, so the desired commit is known in every
    # environment and the MISMATCH is what is being exercised here.
    out = status_with(tmp_path, {
        "production_branch": "production", "running_commit": "c" * 40,
        "last_attempt_commit": "d" * 40, "last_result": "tests_failed"},
        branch="HEAD")
    assert "DEPLOYMENT PENDING" in out
    assert "is not the running commit" in out


def test_an_undeterminable_desired_commit_reports_pending(tmp_path):
    """Silence would read as health. It must not."""
    out = status_with(tmp_path, {
        "production_branch": "production", "running_commit": "c" * 40,
        "last_attempt_commit": "c" * 40, "last_result": "success"},
        branch="no-such-branch-anywhere")
    assert "DEPLOYMENT PENDING" in out
    assert "cannot be determined" in out


def test_status_does_not_truncate_placeholder_text(tmp_path):
    """Slicing [:7] on placeholder text produced '— (none' and 'an unco'."""
    out = status_with(tmp_path, {
        "production_branch": "production", "running_commit": None,
        "last_attempt_commit": None, "last_result": "tests_failed"})
    # Assert the full placeholders render, rather than guessing at mangled
    # forms — "— (none " is a legitimate prefix of "— (none confirmed)".
    assert "— (none confirmed)" in out, "running placeholder was truncated"
    assert "(last SUCCESSFUL deploy)" in out, "label was truncated"
    assert "still on no confirmed commit" in out, "fallback text was truncated"


# ══ rollback: untracked files must not veto it; collisions must ════════════
#
# WHY: the gate was `git status --porcelain`, which counts untracked files. A
# single stray file in the deployment checkout therefore made rollback refuse —
# the tool was unavailable in precisely the situation it exists for. But
# `read-tree -u --reset` silently overwrites untracked files (checkout would
# error; read-tree does not), so the gate cannot simply be dropped either. It
# has to separate two questions: is there tracked work to lose, and would the
# restored tree land on something untracked.
#
# These tests run the real scripts/rollback.sh against throwaway repos and
# assert what it actually does, not what its help text claims.

@pytest.fixture
def rollback_world(tmp_path):
    """A repo whose production tip (BAD) deleted paths the known-good commit
    (GOOD) still carries, leaving those paths free for a test to occupy with
    untracked files:

        GOOD  app.txt  legacy.txt  conf/site.ini  data  runtime.log
        BAD   app.txt (changed), the rest deleted
    """
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    sh("git", "init", "--bare", "-b", "production", str(remote))
    sh("git", "init", "-b", "production", str(work))
    sh("git", "config", "user.email", "t@t", cwd=work)
    sh("git", "config", "user.name", "t", cwd=work)

    (work / "scripts").mkdir()
    shutil.copy(ROLLBACK, work / "scripts" / "rollback.sh")
    (work / ".gitignore").write_text("*.log\n")
    (work / "app.txt").write_text("v1\n")
    (work / "legacy.txt").write_text("still needed\n")
    (work / "conf").mkdir()
    (work / "conf" / "site.ini").write_text("a=1\n")
    (work / "data").write_text("payload\n")
    # tracked despite matching .gitignore — ignore rules only govern untracked
    # paths, so this is how an ignored path can also be a restored path.
    (work / "runtime.log").write_text("kept\n")
    sh("git", "add", "-A", cwd=work)
    sh("git", "add", "-f", "runtime.log", cwd=work)  # -A skips ignored paths
    sh("git", "commit", "-qm", "good", cwd=work)
    good = git("rev-parse", "HEAD", cwd=work)

    (work / "app.txt").write_text("v2-broken\n")
    (work / "legacy.txt").unlink()
    (work / "data").unlink()
    (work / "runtime.log").unlink()
    shutil.rmtree(work / "conf")
    sh("git", "add", "-A", cwd=work)
    sh("git", "commit", "-qm", "bad", cwd=work)
    bad = git("rev-parse", "HEAD", cwd=work)

    sh("git", "remote", "add", "origin", str(remote), cwd=work)
    sh("git", "push", "-q", "origin", "production", cwd=work)
    return {"work": work, "remote": remote, "good": good, "bad": bad}


def roll(world, *args):
    return sh("bash", "scripts/rollback.sh", *args,
              cwd=world["work"], check=False)


def prod_tip(world):
    return git("rev-parse", "production", cwd=world["remote"])


def test_rollback_still_refuses_a_tracked_modification(rollback_world):
    """The original safety gate: uncommitted tracked work is destroyed by
    read-tree, so it must hard-stop."""
    (rollback_world["work"] / "app.txt").write_text("uncommitted edit\n")
    r = roll(rollback_world, rollback_world["good"])
    assert r.returncode != 0
    assert "working tree is dirty" in r.stdout + r.stderr
    assert prod_tip(rollback_world) == rollback_world["bad"], "production moved"


def test_an_unrelated_untracked_file_does_not_block_rollback(rollback_world):
    """The defect: this used to refuse. Nothing in the restored tree touches
    scratch.txt, so it is not the rollback's business."""
    scratch = rollback_world["work"] / "scratch.txt"
    scratch.write_text("notes\n")
    r = roll(rollback_world, rollback_world["good"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert prod_tip(rollback_world) != rollback_world["bad"], "rollback did not push"
    assert scratch.read_text() == "notes\n", "an unrelated file was destroyed"


def test_rollback_restores_the_good_tree_and_stays_append_only(rollback_world):
    (rollback_world["work"] / "scratch.txt").write_text("notes\n")
    assert roll(rollback_world, rollback_world["good"]).returncode == 0
    new = prod_tip(rollback_world)
    w = rollback_world["work"]
    assert git("rev-parse", new + "^{tree}", cwd=w) == \
        git("rev-parse", rollback_world["good"] + "^{tree}", cwd=w), \
        "the restored tree is not the good tree"
    assert git("rev-parse", new + "^", cwd=w) == rollback_world["bad"], \
        "the rollback commit must sit ON TOP of the bad one"
    # the bad deploy stays in the audit trail rather than being erased
    assert sh("git", "merge-base", "--is-ancestor", rollback_world["bad"], new,
              cwd=w, check=False).returncode == 0


def test_untracked_file_on_a_restored_path_is_refused(rollback_world):
    """legacy.txt exists in the good tree; read-tree would overwrite the
    untracked copy without a word."""
    (rollback_world["work"] / "legacy.txt").write_text("unsaved work\n")
    r = roll(rollback_world, rollback_world["good"])
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "legacy.txt" in out
    assert prod_tip(rollback_world) == rollback_world["bad"]
    assert (rollback_world["work"] / "legacy.txt").read_text() == "unsaved work\n"


def test_untracked_file_blocking_a_restored_directory_is_refused(rollback_world):
    """Untracked FILE `conf`, good tree carries `conf/site.ini`: git cannot
    create the directory, so exact-path equality would have missed this."""
    (rollback_world["work"] / "conf").write_text("i am a file\n")
    r = roll(rollback_world, rollback_world["good"])
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "conf" in out
    assert prod_tip(rollback_world) == rollback_world["bad"]


def test_untracked_directory_blocking_a_restored_file_is_refused(rollback_world):
    """The mirror image: untracked `data/keep.txt` makes `data` a directory,
    while the good tree carries `data` as a file."""
    d = rollback_world["work"] / "data"
    d.mkdir()
    (d / "keep.txt").write_text("mine\n")
    r = roll(rollback_world, rollback_world["good"])
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "data" in out
    assert prod_tip(rollback_world) == rollback_world["bad"]
    assert (d / "keep.txt").read_text() == "mine\n"


def test_an_ignored_file_that_collides_with_nothing_permits_rollback(rollback_world):
    """Ignored files are not dirt. stray.log is matched by .gitignore and sits
    on no restored path, so it neither blocks the rollback nor is disturbed."""
    stray = rollback_world["work"] / "stray.log"
    stray.write_text("logs\n")
    r = roll(rollback_world, rollback_world["good"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert stray.read_text() == "logs\n"


def test_an_ignored_file_on_a_restored_path_is_refused(rollback_world):
    """.env is ignored and irreplaceable. Being ignored must not mean being
    silently overwritten when the restored tree carries that same path."""
    (rollback_world["work"] / "runtime.log").write_text("live state\n")
    r = roll(rollback_world, rollback_world["good"])
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "runtime.log" in out
    assert prod_tip(rollback_world) == rollback_world["bad"]
    assert (rollback_world["work"] / "runtime.log").read_text() == "live state\n"


def test_the_collision_gate_applies_to_the_dry_run_too(rollback_world):
    """The operator must learn about the collision before committing to it."""
    (rollback_world["work"] / "legacy.txt").write_text("unsaved\n")
    r = roll(rollback_world, "--dry-run", rollback_world["good"])
    assert r.returncode != 0
    assert "legacy.txt" in r.stdout + r.stderr


# ══ there is exactly ONE deployer ══════════════════════════════════════════
#
# WHY: .github/workflows/deploy.yml deployed on every push to the task branch
# and to main, handing GITHUB_REF_NAME straight to deploy.sh — the boundary
# reopened, in a second mechanism nobody was watching. It was inert only
# because no self-hosted runner happened to be registered, which is a fact
# about the host, not a safety property. An autonomous worker pushes task
# branches on its own, so a dormant deployer is a live risk.

WORKFLOWS = ROOT / ".github" / "workflows"


def test_no_github_workflow_can_deploy():
    """Any workflow that invokes deploy.sh, or registers a self-hosted runner,
    is a second deployment path. There must not be one."""
    for wf in WORKFLOWS.glob("*.yml"):
        text = wf.read_text()
        assert "deploy.sh" not in text, f"{wf.name} invokes the deployer"
        assert "self-hosted" not in text, f"{wf.name} targets a self-hosted runner"


def test_the_actions_deploy_workflow_is_gone_and_tests_remain():
    assert not (WORKFLOWS / "deploy.yml").exists(), \
        "the GitHub Actions deploy path must not come back"
    assert (WORKFLOWS / "tests.yml").exists(), \
        "the test workflow is not a deployer and must stay"


def test_setup_doc_names_the_poller_as_the_only_deploy_path():
    text = DEPLOY_DOC.read_text().replace("*", "")   # ignore markdown emphasis
    assert "only supported deployment mechanism" in text
    assert "git checkout production" in text, \
        "the deployment checkout must be pointed at production, not a task branch"
    # the removed option must not read as an available choice
    assert "Option A" not in text and "Option B" not in text, \
        "the two-deployer choice is gone; do not present it"
    assert "sudo ./svc.sh install" not in text, \
        "self-hosted runner install must not be an instruction any more"


# ---- test-gate truthfulness (Codex FC-002 review, finding 3) ------------
#
# DEPLOY_SKIP_TESTS=1 skips the suite entirely, but the record inferred
# test_gate from the DEPLOY result -- so a skipped gate followed by a
# successful compose was recorded as "passed". The deploy disposition and the
# test disposition are different facts and must be recorded independently.


def run_record(tmp_path, calls, disposition="skipped"):
    """Run deploy.sh's real record() with a controlled test disposition."""
    src = DEPLOY.read_text()
    fn = src[src.index("record() {"):src.index('echo "==> Deploying')]
    record_json = tmp_path / "deployed.json"
    harness = tmp_path / "h.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -uo pipefail\n"
        f'RECORD="{record_json}"\nBRANCH="production"\n'
        f'TEST_DISPOSITION="{disposition}"\n' + fn + "\n" + "\n".join(calls) + "\n")
    r = subprocess.run(["bash", str(harness)], capture_output=True, text=True,
                       timeout=60)
    assert record_json.exists(), r.stderr
    return json.loads(record_json.read_text())


def test_skipped_tests_are_never_recorded_as_a_passed_gate(tmp_path):
    doc = run_record(tmp_path, ['record "success" "' + "a" * 40 + '"'],
                     disposition="skipped")
    assert doc["last_result"] == "success"
    assert doc["test_gate"]["result"] == "skipped", \
        "a deploy that skipped the suite claimed a passing test gate"
    assert doc["test_gate"]["commit"] == "a" * 40


def test_skipped_tests_with_a_failed_compose_still_report_skipped(tmp_path):
    doc = run_record(tmp_path, ['record "compose_failed" "' + "c" * 40 + '"'],
                     disposition="skipped")
    assert doc["last_result"] == "compose_failed"
    assert doc["test_gate"]["result"] == "skipped"


def test_a_passing_gate_is_recorded_independently_of_the_deploy(tmp_path):
    doc = run_record(tmp_path, ['record "compose_failed" "' + "d" * 40 + '"'],
                     disposition="passed")
    assert doc["last_result"] == "compose_failed"
    assert doc["test_gate"]["result"] == "passed", \
        "tests passed; only the deploy failed. Both facts must survive."


def test_a_failed_gate_is_recorded_as_failed(tmp_path):
    doc = run_record(tmp_path, ['record "tests_failed" "' + "e" * 40 + '" "failed"'],
                     disposition="passed")
    assert doc["test_gate"]["result"] == "failed"


def test_compose_failure_is_recorded_rather_than_aborting_silently():
    """set -euo pipefail aborted before record(), so the record kept showing
    the PREVIOUS success and a failed deploy looked healthy."""
    src = DEPLOY.read_text()
    assert "compose_failed" in src, "a Compose failure is not recorded at all"
    up = src.index("up -d --build")
    assert src.index("compose_failed") > up, \
        "the Compose failure must be recorded after the attempt"
    assert re.search(r"if\s*!\s*\$DC up -d --build", src), \
        "the Compose result must be captured, not left to set -e"


def test_deploy_never_rolls_back_automatically():
    """A failed readiness check reports; reverting unattended is its own
    hazard and belongs to the Product Owner's rollback authorization."""
    src = DEPLOY.read_text()
    for forbidden in ("rollback.sh", "git revert", "promote.sh"):
        assert forbidden not in src, f"deploy.sh references {forbidden}"


def test_readiness_runs_after_compose_and_is_recorded():
    src = DEPLOY.read_text()
    up = src.index("up -d --build")
    ready = src.index("readiness.sh")
    assert ready > up, "readiness must be checked after the stack is started"
    assert "record_verification" in src
