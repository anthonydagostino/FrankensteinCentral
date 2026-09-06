"""Tests for the deterministic release service (scripts/release-service.sh).

This script holds the production credential, so every test here is about what
it REFUSES to do. Each runs the real script against throwaway repositories;
none can touch a real production branch.

The property under test throughout: production moves ONLY to a commit the
Product Owner accepted, that descends from production, and that carries the
binding proving it was built from the accepted directive.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "scripts" / "release-service.sh"


def sh(*args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, args))}\n{r.stdout}\n{r.stderr}")
    return r


def git(*args, cwd):
    return sh("git", *args, cwd=cwd).stdout.strip()


def code() -> str:
    return "\n".join(l for l in SERVICE.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


@pytest.fixture
def world(tmp_path):
    """A bare remote with production, and a release home."""
    remote = tmp_path / "remote.git"
    sh("git", "init", "--bare", "-q", "-b", "production", str(remote))

    seed = tmp_path / "seed"
    seed.mkdir()
    sh("git", "init", "-q", "-b", "production", str(seed))
    sh("git", "config", "user.email", "t@t", cwd=seed)
    sh("git", "config", "user.name", "t", cwd=seed)
    (seed / "app.txt").write_text("v1\n")
    (seed / ".frankenstein").mkdir()
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", "seed", cwd=seed)
    sh("git", "remote", "add", "origin", str(remote), cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)

    rel = tmp_path / "releasehome"
    rel.mkdir()
    (rel / "ENABLED").write_text("")
    return {"remote": remote, "seed": seed, "release_dir": rel, "tmp": tmp_path}


def run_release(world, *args, enabled=True):
    if not enabled and (world["release_dir"] / "ENABLED").exists():
        (world["release_dir"] / "ENABLED").unlink()
    env = dict(FRANKENSTEIN_RELEASE_DIR=str(world["release_dir"]),
               FRANKENSTEIN_REPO_URL=str(world["remote"]),
               HOME=str(world["tmp"]), PATH="/usr/bin:/bin:/usr/local/bin")
    return subprocess.run(["bash", str(SERVICE), *args], capture_output=True,
                          text=True, timeout=180, env=env)


def prod_tip(world):
    return sh("git", "ls-remote", str(world["remote"]),
              "refs/heads/production").stdout.split()[0]


def all_refs(world):
    return dict(
        (line.split()[1], line.split()[0])
        for line in sh("git", "ls-remote", str(world["remote"])).stdout.splitlines()
        if line.strip())


def set_control(world, *, status="accepted", task_id="FC-001", impl=None,
                rollback_to=None, authorization="deploy-approved",
                last_actor="product_owner", turn="none", directive_commit=None,
                protocol_version=1, directive_task=None):
    """Publish a control commit carrying Product Owner state."""
    ctl = world["tmp"] / "ctl"
    if not ctl.exists():
        sh("git", "clone", "-q", str(world["remote"]), str(ctl))
        sh("git", "config", "user.email", "po@po", cwd=ctl)
        sh("git", "config", "user.name", "po", cwd=ctl)
        if sh("git", "ls-remote", "--heads", "origin", "control",
              cwd=ctl).stdout.strip():
            sh("git", "checkout", "-q", "-B", "control", "origin/control", cwd=ctl)
        else:
            sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
            sh("git", "reset", "-q", "--hard", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(
        f"# Product Directive\n\nTask ID: {directive_task or task_id}\n"
        f"Deployment Authorization: {authorization}\n\n## Objective\nwork\n")
    state = {"protocol_version": protocol_version, "task_id": task_id,
             "turn": turn, "status": status,
             "directive_commit": directive_commit,
             "implementation_commit": impl, "last_actor": last_actor,
             "updated_at": "2026-09-06T00:00:00Z"}
    if rollback_to:
        state["rollback_to"] = rollback_to
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps(state, indent=2))
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "commit", "-qm", f"[PO] {task_id} {status}", cwd=ctl)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/control", cwd=ctl)
    return git("rev-parse", "HEAD", cwd=ctl)


def make_implementation(world, control_commit, *, task_id="FC-001",
                        branch=None, base="origin/production", content="work\n",
                        snapshot=True):
    """A task branch that looks like one the worker produced."""
    branch = branch or f"claude/{task_id}-work"
    wt = world["tmp"] / f"impl-{branch.replace('/', '-')}"
    if not wt.exists():
        sh("git", "clone", "-q", str(world["remote"]), str(wt))
        sh("git", "config", "user.email", "c@c", cwd=wt)
        sh("git", "config", "user.name", "claude", cwd=wt)
        sh("git", "checkout", "-q", "-B", branch, base, cwd=wt)
    (wt / ".frankenstein").mkdir(exist_ok=True)
    if snapshot:
        (wt / ".frankenstein" / "AUTHORIZING_CONTROL_COMMIT").write_text(
            control_commit + "\n")
    (wt / "app.txt").write_text(content)
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-qm", f"[CLAUDE] {task_id} work", cwd=wt)
    sh("git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=wt)
    return git("rev-parse", "HEAD", cwd=wt)


def accepted_release(world, **kw):
    """The happy path, as a reusable arrangement: directive -> work -> accept."""
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude", last_actor="product_owner")
    impl = make_implementation(world, authorizing)
    accepting = set_control(world, status="accepted", impl=impl, **kw)
    return authorizing, impl, accepting


# ══ 1. the fail-closed default ═════════════════════════════════════════

def test_nothing_happens_without_an_enable_flag(world):
    accepted_release(world)
    before = prod_tip(world)
    r = run_release(world, enabled=False)
    assert r.returncode == 0
    assert "NO-OP" in r.stderr and "not enabled" in r.stderr
    assert prod_tip(world) == before


def test_disabled_flag_overrides_enabled(world):
    accepted_release(world)
    (world["release_dir"] / "DISABLED").write_text("")
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0 and "DISABLED" in r.stderr
    assert prod_tip(world) == before


def test_awaiting_directive_releases_nothing(world):
    set_control(world, status="awaiting_directive", turn="product_owner",
                last_actor=None)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0, r.stderr
    assert "NO-OP" in r.stderr
    assert prod_tip(world) == before


def test_no_control_branch_releases_nothing(world):
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0
    assert "no control branch" in r.stderr
    assert prod_tip(world) == before


@pytest.mark.parametrize("status", ["awaiting_review", "changes_requested",
                                    "ready_for_implementation", "blocked"])
def test_only_accepted_state_releases(world, status):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status=status, impl=impl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0, r.stderr
    assert "NO-OP" in r.stderr
    assert prod_tip(world) == before


def test_unsupported_protocol_version_releases_nothing(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", impl=impl, protocol_version=99)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0 and "NO-OP" in r.stderr
    assert prod_tip(world) == before


# ══ 2. the happy path ══════════════════════════════════════════════════

def test_accepted_work_is_promoted_exactly(world):
    _, impl, _ = accepted_release(world)
    r = run_release(world)
    assert r.returncode == 0, r.stderr
    assert prod_tip(world) == impl, "production is not the accepted commit"
    assert "RELEASED" in r.stderr


def test_only_the_production_ref_changes(world):
    _, impl, _ = accepted_release(world)
    before = all_refs(world)
    assert run_release(world).returncode == 0
    after = all_refs(world)
    changed = {k for k in set(before) | set(after)
               if before.get(k) != after.get(k)}
    # HEAD is the bare repo's symref to production and follows it by definition
    assert changed <= {"refs/heads/production", "HEAD"}, \
        f"other refs moved: {changed}"
    assert "refs/heads/production" in changed


def test_a_release_is_recorded(world):
    _, impl, _ = accepted_release(world)
    run_release(world)
    rec = json.loads((world["release_dir"] / "releases.jsonl")
                     .read_text().strip().splitlines()[-1])
    assert rec["result"] == "released" and rec["released"] == impl


def test_releasing_the_same_commit_twice_is_a_no_op(world):
    _, impl, _ = accepted_release(world)
    assert run_release(world).returncode == 0
    r = run_release(world)
    assert r.returncode == 0
    assert "already what production runs" in r.stderr
    assert prod_tip(world) == impl


def test_dry_run_pushes_nothing(world):
    accepted_release(world)
    before = all_refs(world)
    r = run_release(world, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "DRY RUN" in r.stderr
    assert all_refs(world) == before


def test_status_mode_pushes_nothing(world):
    accepted_release(world)
    before = all_refs(world)
    r = run_release(world, "--status")
    assert r.returncode == 0, r.stderr
    assert "would promote" in r.stdout
    assert all_refs(world) == before


# ══ 3. deployment authorization is separate from acceptance ════════════

@pytest.mark.parametrize("authorization", ["none", "pending", "deploy-denied"])
def test_acceptance_without_deploy_approval_releases_nothing(world, authorization):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", impl=impl, authorization=authorization)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0 and "NO-OP" in r.stderr
    assert prod_tip(world) == before


def test_directive_naming_a_different_task_blocks_the_release(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", impl=impl, directive_task="FC-999")
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0, "released despite an inconsistent directive"
    assert prod_tip(world) == before


# ══ 4. only the Product Owner can accept ═══════════════════════════════

@pytest.mark.parametrize("last_actor", ["claude", None, "worker"])
def test_acceptance_not_by_the_product_owner_is_refused(world, last_actor):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", impl=impl, last_actor=last_actor)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0, "released work the Product Owner did not accept"
    assert "product_owner" in r.stderr
    assert prod_tip(world) == before


def test_accepted_while_still_claudes_turn_is_refused(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", impl=impl, turn="claude")
    before = prod_tip(world)
    assert run_release(world).returncode != 0
    assert prod_tip(world) == before


# ══ 5. the implementation must be the accepted one ═════════════════════

def test_an_unknown_implementation_commit_is_refused(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    make_implementation(world, authorizing)
    set_control(world, status="accepted", impl="0" * 40)
    before = prod_tip(world)
    assert run_release(world).returncode != 0
    assert prod_tip(world) == before


def test_a_commit_on_no_task_branch_is_refused(world):
    """Reachability matters: an object that exists is not an accepted one."""
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing, branch="rogue/FC-001-work")
    set_control(world, status="accepted", impl=impl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0, "released a commit on no claude/* branch"
    # Either check may catch it: the service fetches only claude/* task refs,
    # so such a commit is usually not even present to evaluate.
    assert ("not reachable" in r.stderr
            or "not a commit in this repository" in r.stderr), r.stderr
    assert prod_tip(world) == before


def test_an_implementation_not_descending_from_production_is_refused(world):
    """Fast-forward only: production is never rewound."""
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    # move production forward underneath the accepted work
    other = world["tmp"] / "mover"
    sh("git", "clone", "-q", "--branch", "production", str(world["remote"]), str(other))
    sh("git", "config", "user.email", "t@t", cwd=other)
    sh("git", "config", "user.name", "t", cwd=other)
    (other / "hotfix.txt").write_text("x\n")
    sh("git", "add", "-A", cwd=other)
    sh("git", "commit", "-qm", "hotfix", cwd=other)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/production", cwd=other)

    set_control(world, status="accepted", impl=impl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0, "released a commit that would rewind production"
    assert "does not descend" in r.stderr
    assert prod_tip(world) == before


# ══ 6. the directive -> implementation -> acceptance binding ═══════════

def test_work_without_the_authorizing_snapshot_is_refused(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing, snapshot=False)
    set_control(world, status="accepted", impl=impl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert "AUTHORIZING_CONTROL_COMMIT" in r.stderr
    assert prod_tip(world) == before


def test_work_authorized_by_a_foreign_control_lineage_is_refused(world):
    """The anti-substitution check: a commit that descends from production and
    carries a snapshot, but not one on this control branch."""
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, "d" * 40)
    set_control(world, status="accepted", impl=impl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert prod_tip(world) == before


def test_a_directive_identity_change_between_authorization_and_acceptance_is_refused(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude", directive_commit="a" * 40)
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", impl=impl, directive_commit="b" * 40)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0, "released work built from a different directive"
    assert "directive_commit changed" in r.stderr
    assert prod_tip(world) == before


def test_work_authorized_for_a_different_task_is_refused(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude", task_id="FC-001")
    impl = make_implementation(world, authorizing, task_id="FC-001")
    # acceptance claims a different task, whose directive agrees with itself
    set_control(world, status="accepted", impl=impl, task_id="FC-002")
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert prod_tip(world) == before


# ══ 7. rollback: authorized, and append-only ═══════════════════════════

def test_rollback_moves_production_forward_to_an_older_tree(world):
    _, impl, _ = accepted_release(world)
    good = prod_tip(world)
    assert run_release(world).returncode == 0
    released = prod_tip(world)
    assert released == impl

    set_control(world, status="accepted", rollback_to=good, impl=None)
    r = run_release(world)
    assert r.returncode == 0, r.stderr
    now = prod_tip(world)

    probe = world["tmp"] / "rollback-probe"
    sh("git", "clone", "-q", "--branch", "production", str(world["remote"]), str(probe))
    assert git("rev-parse", "HEAD^{tree}", cwd=probe) == \
        git("rev-parse", f"{good}^{{tree}}", cwd=probe), \
        "the rollback did not restore the good tree"
    assert sh("git", "merge-base", "--is-ancestor", released, now,
              cwd=probe, check=False).returncode == 0, \
        "history was rewound; production must stay append-only"


def test_rollback_to_something_never_released_is_refused(world):
    authorizing = set_control(world, status="ready_for_implementation",
                              turn="claude")
    impl = make_implementation(world, authorizing)
    set_control(world, status="accepted", rollback_to=impl, impl=None)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert "not an ancestor of production" in r.stderr
    assert prod_tip(world) == before


def test_rollback_without_deploy_approval_is_refused(world):
    _, impl, _ = accepted_release(world)
    good = prod_tip(world)
    run_release(world)
    set_control(world, status="accepted", rollback_to=good, impl=None,
                authorization="none")
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode == 0 and "NO-OP" in r.stderr
    assert prod_tip(world) == before


def test_a_release_and_a_rollback_together_are_refused(world):
    _, impl, _ = accepted_release(world)
    good = prod_tip(world)
    set_control(world, status="accepted", impl=impl, rollback_to=good)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert "mutually exclusive" in r.stderr
    assert prod_tip(world) == before


def test_accepted_with_nothing_to_release_is_refused(world):
    set_control(world, status="accepted", impl=None)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert "nothing identifies what to release" in r.stderr
    assert prod_tip(world) == before


# ══ 8. malformed state ═════════════════════════════════════════════════

@pytest.mark.parametrize("impl,label", [
    ("abc", "short sha"),
    ("z" * 40, "not hex"),
])
def test_malformed_implementation_shas_are_refused(world, impl, label):
    set_control(world, status="accepted", impl=impl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0, f"released on {label}"
    assert prod_tip(world) == before


def test_unparseable_control_state_releases_nothing(world):
    ctl = world["tmp"] / "badctl"
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "config", "user.email", "po@po", cwd=ctl)
    sh("git", "config", "user.name", "po", cwd=ctl)
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", "--hard", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text("{not json")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "commit", "-qm", "broken", cwd=ctl)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/control", cwd=ctl)
    before = prod_tip(world)
    r = run_release(world)
    assert r.returncode != 0
    assert prod_tip(world) == before


# ══ 9. what the script is, structurally ════════════════════════════════

def test_service_invokes_no_model_and_no_agent():
    """No LLM, no Claude, no Codex — the whole point of the actor."""
    c = code()
    for forbidden in ("claude-worker.sh", "codex", "api.anthropic.com",
                      "anthropic_api_key", "openai", "prompt"):
        assert forbidden not in c.lower(), f"the release service references {forbidden}"
    assert not re.search(r'(^|[;&|]\s*)claude\s', c), "the release service invokes claude"


def test_service_never_invokes_deployment_tooling():
    c = code()
    for forbidden in ("promote.sh", "deploy.sh", "rollback.sh", "systemctl",
                      "docker", "sudo"):
        assert forbidden not in c, f"the release service invokes {forbidden}"


def test_service_never_force_pushes_or_deletes():
    c = code()
    for forbidden in ("--force", "push --delete", "--delete", "+refs/heads",
                      "--force-with-lease"):
        assert forbidden not in c, f"the release service can {forbidden}"
    assert '"$TARGET:refs/heads/$PROD_BRANCH"' in c, \
        "the push must name one fully-qualified destination ref"


def test_every_push_targets_production_only():
    c = code()
    pushes = [l.strip() for l in c.splitlines()
              if re.search(r'(^|\s)git\s+[^|;]*\spush\s', l)]
    assert pushes, "expected a push"
    for line in pushes:
        assert "$PROD_BRANCH" in line, f"unexpected push target: {line}"


def test_service_never_writes_control():
    c = code()
    for line in c.splitlines():
        if re.search(r'(^|\s)git\s+[^|;]*\spush\s', line):
            assert "CONTROL" not in line, f"control write: {line}"


@pytest.mark.parametrize("ref,extra", [
    ("refs/heads/control", "bbb"),
    ("refs/heads/main", "bbb"),
    ("refs/heads/claude/FC-001-work", "bbb"),
])
def test_pre_push_hook_allows_only_production(tmp_path, ref, extra):
    m = re.search(r"<<'HOOKEOF'\n(.*?)\nHOOKEOF\n", SERVICE.read_text(), re.S)
    assert m, "could not locate the pre-push hook heredoc"
    hook = tmp_path / "pre-push"
    hook.write_text(m.group(1))
    hook.chmod(0o755)
    r = subprocess.run(["bash", str(hook)], input=f"refs/heads/x aaa {ref} {extra}\n",
                       capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "REFUSED" in r.stderr


def test_pre_push_hook_refuses_to_delete_production(tmp_path):
    m = re.search(r"<<'HOOKEOF'\n(.*?)\nHOOKEOF\n", SERVICE.read_text(), re.S)
    hook = tmp_path / "pre-push"
    hook.write_text(m.group(1))
    hook.chmod(0o755)
    r = subprocess.run(
        ["bash", str(hook)],
        input=f"(delete) {'0' * 40} refs/heads/production bbb\n",
        capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "deleting production" in r.stderr


def test_systemd_templates_exist_but_are_not_enabled():
    for name in ("frankenstein-release.service", "frankenstein-release.timer"):
        t = ROOT / "scripts" / "agent" / name
        assert t.exists(), f"{name} missing"
        assert "NOT INSTALLED" in t.read_text()
    unit = (ROOT / "scripts" / "agent" / "frankenstein-release.service").read_text()
    assert "User=fcrelease" in unit, "the release service must not run as the human"
