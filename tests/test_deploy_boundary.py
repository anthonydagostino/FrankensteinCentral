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

    return {"remote": remote, "clone": clone, "seed": seed, "marker": marker}


def poll(box, branch=None):
    """Run one poller cycle exactly as the systemd unit does."""
    env = dict(os.environ, FRANKENSTEIN_DIR=str(box["clone"]))
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
    push_commit(box, branch, f"work on {branch}\n")
    _, deployed = poll(box)
    assert not deployed, f"{branch} triggered a deploy"


def test_changing_the_production_branch_does_deploy(box):
    """The other half: promotion MUST reach the box."""
    sha = push_commit(box, "production", "promoted\n")
    r, deployed = poll(box)
    assert deployed, f"production moved but no deploy ran\n{r.stdout}{r.stderr}"
    assert box["marker"].read_text().strip() == "production"
    assert sha


def test_task_branch_then_promotion(box):
    """The full loop: push for review (no deploy), then promote (deploy)."""
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
