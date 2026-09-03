"""Tests for the autonomous Claude worker (scripts/claude-worker.sh).

The worker can run Claude unattended against a real repository, so the
properties that matter are mostly about what it REFUSES to do. Every test here
runs the real script against throwaway git repositories with a mocked Claude;
none of them can touch a real production branch.

Covers the Product Owner's required cases A–J.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "claude-worker.sh"
CONTROL_BOOTSTRAP = ROOT / "scripts" / "control-bootstrap.sh"


def code(path: Path) -> str:
    """Script source with comments stripped — prose is not behavior."""
    return "\n".join(l for l in path.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


def executable(path: Path) -> str:
    """code() minus the instruction prompt.

    The PROMPT string tells Claude what it may NOT do, so it names promote.sh,
    deploy.sh and systemd on purpose. Naming them there is the opposite of
    invoking them; assertions about invocation must not read that text.
    """
    c = code(path)
    if 'PROMPT="' in c:
        start = c.index('PROMPT="')
        end = c.index('directive."', start) + len('directive."')
        c = c[:start] + c[end:]
    return c


def sh(*args, cwd=None, env=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       timeout=180, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, args))}\n{r.stdout}\n{r.stderr}")
    return r


def git(*args, cwd):
    return sh("git", *args, cwd=cwd).stdout.strip()


@pytest.fixture
def world(tmp_path):
    """A miniature universe: bare remote with production + control, a fake
    production checkout, and an agent home."""
    remote = tmp_path / "remote.git"
    sh("git", "init", "--bare", "-q", "-b", "production", str(remote))

    seed = tmp_path / "seed"
    seed.mkdir()
    sh("git", "init", "-q", "-b", "production", str(seed))
    sh("git", "config", "user.email", "t@t", cwd=seed)
    sh("git", "config", "user.name", "t", cwd=seed)
    (seed / "scripts").mkdir()
    # a test suite the worker will re-run independently
    (seed / "scripts" / "test.sh").write_text(
        '#!/usr/bin/env bash\n[ -f FAIL_TESTS ] && exit 1\necho "ALL TESTS PASSED"\n')
    (seed / "app.txt").write_text("v1\n")
    (seed / ".frankenstein").mkdir()
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", "seed", cwd=seed)
    sh("git", "remote", "add", "origin", str(remote), cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)

    prod_checkout = tmp_path / "FrankensteinCentral"
    sh("git", "clone", "-q", str(remote), str(prod_checkout))

    agent = tmp_path / "agenthome"
    (agent / "agent").mkdir(parents=True)
    (agent / "agent" / "ENABLED").write_text("")
    return {"remote": remote, "seed": seed, "prod": prod_checkout,
            "agent_dir": agent / "agent", "worktrees": agent / "worktrees",
            "tmp": tmp_path}


def set_control(world, *, turn, status, task_id="FC-001"):
    """Publish a control branch carrying the given protocol state."""
    ctl = world["tmp"] / f"ctl-{turn}-{status}"
    if ctl.exists():
        sh("rm", "-rf", str(ctl))
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "config", "user.email", "t@t", cwd=ctl)
    sh("git", "config", "user.name", "t", cwd=ctl)
    exists = sh("git", "ls-remote", "--heads", "origin", "control",
                cwd=ctl).stdout.strip()
    if exists:
        sh("git", "checkout", "-q", "-B", "control", "origin/control", cwd=ctl)
    else:
        sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
        sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": task_id, "turn": turn,
        "status": status, "directive_commit": None,
        "implementation_commit": None, "last_actor": None,
        "updated_at": "2026-09-01T00:00:00Z"}, indent=2))
    (ctl / ".frankenstein" / "IMPLEMENTATION_HANDOFF.md").write_text("# handoff\n")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "commit", "-qm", f"control {turn}/{status}", cwd=ctl)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/control", cwd=ctl)
    return git("rev-parse", "HEAD", cwd=ctl)


def run_worker(world, *args, mock=None, extra_env=None):
    env = dict(os.environ,
               HOME=str(world["tmp"]),
               FRANKENSTEIN_DIR=str(world["prod"]),
               FRANKENSTEIN_AGENT_DIR=str(world["agent_dir"]),
               FRANKENSTEIN_WORKTREE_ROOT=str(world["worktrees"]),
               FRANKENSTEIN_REPO_URL=str(world["remote"]))
    if mock:
        env["FRANKENSTEIN_MOCK_CLAUDE"] = mock
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", str(WORKER), *args], capture_output=True,
                          text=True, timeout=300, env=env)


# A mock "Claude" that does what the real one is asked to do.
MOCK_GOOD = (
    'echo "mock claude ran" && '
    'printf "work\\n" >> app.txt && '
    'mkdir -p .frankenstein && '
    'printf "# handoff\\n\\n## Deviations From Directive\\nNo deviations\\n" '
    '> .frankenstein/IMPLEMENTATION_HANDOFF.md && '
    'printf \'{"protocol_version":1,"task_id":"FC-001","turn":"product_owner",'
    '"status":"awaiting_review","directive_commit":null,'
    '"implementation_commit":null,"last_actor":"claude",'
    '"updated_at":"2026-09-01T01:00:00Z"}\' > .frankenstein/STATE.json && '
    'git add -A && '
    'git -c user.email=c@c -c user.name=claude commit -qm "[CLAUDE] FC-001 work"'
)


# ══ A. product_owner turn -> Claude NOT invoked ═════════════════════════

@pytest.mark.parametrize("turn,status", [
    ("product_owner", "awaiting_directive"),
    ("product_owner", "awaiting_review"),
    ("none", "accepted"),
    ("product_owner", "blocked"),
    ("claude", "implementing"),
    ("claude", "accepted"),
])
def test_unauthorized_state_never_invokes_claude(world, turn, status):
    set_control(world, turn=turn, status=status)
    marker = world["tmp"] / "INVOKED"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists(), f"Claude invoked on {turn}/{status}"
    assert "NO-OP" in r.stderr
    assert r.returncode == 0


# ══ B. claude + authorized status -> invoked once ═══════════════════════

@pytest.mark.parametrize("status", ["ready_for_implementation", "changes_requested"])
def test_authorized_state_invokes_claude(world, status):
    set_control(world, turn="claude", status=status)
    r = run_worker(world, mock=MOCK_GOOD)
    assert "AUTHORIZED" in r.stderr, r.stderr
    assert r.returncode == 0, r.stderr
    assert "DONE" in r.stderr


def test_successful_run_publishes_branch_and_handoff(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    r = run_worker(world, mock=MOCK_GOOD)
    assert r.returncode == 0, r.stderr
    branches = sh("git", "ls-remote", "--heads", str(world["remote"])).stdout
    assert "claude/FC-001-work" in branches, "task branch was not published"
    ctl = world["tmp"] / "verify"
    sh("git", "clone", "-q", "--branch", "control", str(world["remote"]), str(ctl))
    state = json.loads((ctl / ".frankenstein" / "STATE.json").read_text())
    assert state["status"] == "awaiting_review"
    assert state["implementation_commit"], "handoff must name the commit to review"


# ══ C. second poll while locked -> not invoked again ════════════════════

def test_lock_prevents_overlapping_runs(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    lock = world["agent_dir"] / "worker.lock"
    lock.touch()
    holder = subprocess.Popen(["flock", str(lock), "sleep", "20"])
    try:
        marker = world["tmp"] / "SECOND"
        r = run_worker(world, mock=f'touch "{marker}"')
        assert not marker.exists(), "a second run started while the lock was held"
        assert "another worker run holds the lock" in r.stderr
        assert r.returncode == 0
    finally:
        holder.terminate()
        holder.wait(timeout=10)


# ══ D. task-branch push leaves production untouched ════════════════════

def test_production_is_untouched_by_a_successful_run(world):
    before = git("rev-parse", "production", cwd=world["seed"])
    set_control(world, turn="claude", status="ready_for_implementation")
    r = run_worker(world, mock=MOCK_GOOD)
    assert r.returncode == 0, r.stderr
    after = sh("git", "ls-remote", str(world["remote"]),
               "refs/heads/production").stdout.split()[0]
    assert before == after, "production moved during an autonomous run"


# ══ E. the worker refuses the live production worktree ═════════════════

def test_worker_refuses_to_operate_inside_the_production_checkout(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    r = run_worker(world, mock=MOCK_GOOD,
                   extra_env={"FRANKENSTEIN_WORKTREE_ROOT": str(world["prod"] / "nested")})
    assert r.returncode != 0
    assert "isolation violation" in r.stderr


def test_worker_refuses_when_clone_equals_production_checkout(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    # make the computed clone path resolve to the production checkout itself
    parent = world["prod"].parent
    r = run_worker(world, mock=MOCK_GOOD,
                   extra_env={"FRANKENSTEIN_WORKTREE_ROOT": str(parent),
                              "FRANKENSTEIN_DIR": str(parent / "agent-repo")})
    # either isolation refusal or a clean stop — never a run inside production
    assert "isolation violation" in r.stderr or r.returncode != 0


# ══ F. control moved during the run -> no overwrite ════════════════════

def test_control_change_during_run_blocks_the_handoff(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    # the mock does its work, then the Product Owner moves control underneath
    hijack = (MOCK_GOOD + ' && ' + 'true')
    r = run_worker(world, mock=hijack + ' && ' + (
        f'cd "{world["tmp"]}" && rm -rf hijack && '
        f'git clone -q --branch control "{world["remote"]}" hijack && '
        'cd hijack && printf \'{"protocol_version":1,"task_id":"FC-002",'
        '"turn":"claude","status":"changes_requested","directive_commit":null,'
        '"implementation_commit":null,"last_actor":"product_owner",'
        '"updated_at":"2026-09-01T02:00:00Z"}\' > .frankenstein/STATE.json && '
        'git add -A && git -c user.email=p@p -c user.name=po '
        'commit -qm "[PO-CHANGES] FC-002" && git push -q origin HEAD:control'))
    assert r.returncode != 0, "stale worker published over newer control state"
    assert "control moved" in r.stderr
    assert "NOT overwriting" in r.stderr

    ctl = world["tmp"] / "check-f"
    sh("git", "clone", "-q", "--branch", "control", str(world["remote"]), str(ctl))
    state = json.loads((ctl / ".frankenstein" / "STATE.json").read_text())
    assert state["task_id"] == "FC-002", "newer Product Owner state was clobbered"
    assert state["status"] == "changes_requested"


# ══ G. Claude exits non-zero -> no fabricated handoff ══════════════════

def test_claude_failure_produces_no_handoff(world):
    control_before = set_control(world, turn="claude",
                                 status="ready_for_implementation")
    r = run_worker(world, mock='echo "boom" >&2; exit 3')
    assert r.returncode != 0
    assert "Claude exited 3" in r.stderr
    ctl = world["tmp"] / "check-g"
    sh("git", "clone", "-q", "--branch", "control", str(world["remote"]), str(ctl))
    assert git("rev-parse", "HEAD", cwd=ctl) == control_before, \
        "control changed despite a failed run"
    state = json.loads((ctl / ".frankenstein" / "STATE.json").read_text())
    assert state["status"] == "ready_for_implementation", "status was advanced"


def test_run_producing_no_commits_publishes_nothing(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    r = run_worker(world, mock='echo "did nothing"')
    assert r.returncode != 0
    assert "no commits" in r.stderr


# ══ H. tests fail -> no fabricated handoff ═════════════════════════════

def test_failing_tests_block_the_handoff(world):
    control_before = set_control(world, turn="claude",
                                 status="ready_for_implementation")
    bad = MOCK_GOOD.replace('printf "work\\n" >> app.txt',
                            'printf "work\\n" >> app.txt && touch FAIL_TESTS')
    r = run_worker(world, mock=bad)
    assert r.returncode != 0
    assert "tests fail" in r.stderr
    ctl = world["tmp"] / "check-h"
    sh("git", "clone", "-q", "--branch", "control", str(world["remote"]), str(ctl))
    assert git("rev-parse", "HEAD", cwd=ctl) == control_before


def test_worker_reruns_tests_independently():
    """The worker must not trust the run's own claim that tests passed."""
    assert "re-running the test suite independently" in WORKER.read_text()
    c = executable(WORKER)          # exclude the prompt, which also says it
    assert "bash scripts/test.sh" in c
    assert c.index("bash scripts/test.sh") > c.index("CLAUDE_RC"), \
        "the independent test run must happen AFTER Claude finishes"


# ══ I. production push/promotion unavailable to the worker path ════════

def test_worker_never_invokes_promotion_or_deployment_tooling():
    c = executable(WORKER)
    for forbidden in ("promote.sh", "rollback.sh", "deploy.sh", "systemctl",
                      "sudo ", "--force", "force-with-lease"):
        assert forbidden not in c, f"worker invokes forbidden operation: {forbidden}"


def test_prompt_explicitly_forbids_those_operations():
    """The same names must appear in the prompt — as prohibitions."""
    prompt = WORKER.read_text()
    prompt = prompt[prompt.index('PROMPT="'):]
    for named in ("promote.sh", "rollback.sh", "deploy.sh", "systemd", "sudo"):
        assert named in prompt, f"prompt does not forbid {named}"
    assert "You may NOT" in prompt


def test_worker_installs_a_pre_push_hook_rejecting_production():
    src = WORKER.read_text()
    assert "hooks/pre-push" in src
    hook = src[src.index("cat > \"$HOOK\""):src.index("chmod +x \"$HOOK\"")]
    assert "refs/heads/production" in hook
    assert "REFUSED" in hook


def test_pre_push_hook_actually_rejects_production(world, tmp_path):
    """Behavioral: extract the hook the worker installs and feed it a
    production push."""
    # capture the heredoc body between <<'HOOKEOF' and its terminator line
    m = re.search(r"<<'HOOKEOF'\n(.*?)\nHOOKEOF\n", WORKER.read_text(), re.S)
    assert m, "could not locate the pre-push hook heredoc"
    body = m.group(1)
    hook = tmp_path / "pre-push"
    hook.write_text(body)
    hook.chmod(0o755)
    r = subprocess.run(["bash", str(hook)], input=(
        "refs/heads/claude/FC-001-work aaa refs/heads/production bbb\n"),
        capture_output=True, text=True, timeout=30)
    assert r.returncode != 0, "hook allowed a production push"
    assert "REFUSED" in r.stderr

    ok = subprocess.run(["bash", str(hook)], input=(
        "refs/heads/claude/FC-001-work aaa refs/heads/claude/FC-001-work bbb\n"),
        capture_output=True, text=True, timeout=30)
    assert ok.returncode == 0, "hook blocked a legitimate task-branch push"


def test_worker_pushes_only_task_and_control_refs():
    """Match actual `git push` invocations, not any line containing 'push'
    (the pre-push hook path contains the word)."""
    c = executable(WORKER)
    # `git` must be a command word (not part of ".git/hooks/pre-push") and
    # `push` a separate argument (not the "-push" in "pre-push").
    pushes = [l.strip() for l in c.splitlines()
              if re.search(r'(^|\s)git\s+[^|;]*\spush\s', l)]
    assert pushes, "expected at least one push"
    for line in pushes:
        assert ("$TASK_BRANCH" in line or "$CONTROL_BRANCH" in line), \
            f"unexpected push target: {line}"


# ══ J. malformed control state -> no invocation ════════════════════════

@pytest.mark.parametrize("payload,label", [
    ("{ not json", "unparseable"),
    ('{"turn": "claude"}', "status missing"),
    ('{"turn":"claude","status":"ready_for_implementation","task_id":"nope"}',
     "bad task id"),
    ('{"turn":"wizard","status":"ready_for_implementation","task_id":"FC-001"}',
     "invalid turn"),
])
def test_malformed_control_state_invokes_nothing(world, payload, label):
    ctl = world["tmp"] / f"bad-{label.replace(' ', '-')}"
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(payload)
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "bad state", cwd=ctl)
    sh("git", "push", "-q", "-f", "origin", "HEAD:refs/heads/control", cwd=ctl)

    marker = world["tmp"] / f"INVOKED-{label.replace(' ', '-')}"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists(), f"Claude invoked on malformed state: {label}"
    assert r.returncode == 0
    assert "NO-OP" in r.stderr


def test_missing_control_branch_invokes_nothing(world):
    marker = world["tmp"] / "NOCTL"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists()
    assert "control branch" in r.stderr


# ══ kill switch and enablement ═════════════════════════════════════════

def test_disabled_flag_stops_everything(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    (world["agent_dir"] / "DISABLED").write_text("")
    marker = world["tmp"] / "KILLED"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists()
    assert "kill switch" in r.stderr
    assert r.returncode == 0


def test_worker_refuses_without_an_explicit_enable(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    (world["agent_dir"] / "ENABLED").unlink()
    marker = world["tmp"] / "NOTENABLED"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists(), "ran without being explicitly enabled"
    assert "not enabled" in r.stderr


def test_kill_switch_is_independent_of_the_production_deployer():
    """Disabling Claude must not disable deployment, and vice versa."""
    worker = code(WORKER)
    assert "frankenstein-deploy" not in worker, \
        "the worker must not touch the production deploy units"
    autopull = code(ROOT / "scripts" / "autopull.sh")
    assert "agent" not in autopull and "claude-worker" not in autopull, \
        "the deployer must not depend on the Claude worker"


# ══ logging, timeout, systemd templates ════════════════════════════════

def test_run_is_recorded_with_diagnostic_fields(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    run_worker(world, mock=MOCK_GOOD)
    runs = (world["agent_dir"] / "runs.jsonl").read_text().strip().splitlines()
    rec = json.loads(runs[-1])
    for field in ("task_id", "control_commit", "task_branch", "started",
                  "ended", "result", "claude_exit", "handoff_commit", "log"):
        assert field in rec, f"run record missing {field}"


def test_failures_are_recorded_too(world):
    set_control(world, turn="claude", status="ready_for_implementation")
    run_worker(world, mock='exit 4')
    rec = json.loads((world["agent_dir"] / "runs.jsonl").read_text().strip().splitlines()[-1])
    assert rec["result"] == "claude_failed" and rec["claude_exit"] == 4


def test_logs_live_outside_the_repository():
    c = code(WORKER)
    assert "FRANKENSTEIN_AGENT_DIR" in c
    assert ".frankenstein/agent" in WORKER.read_text()


def test_claude_run_is_bounded_by_a_timeout():
    c = code(WORKER)
    assert "timeout" in c and "MAX_RUNTIME" in c


def test_systemd_templates_exist_but_are_not_enabled():
    svc = ROOT / "scripts" / "agent" / "frankenstein-agent.service"
    timer = ROOT / "scripts" / "agent" / "frankenstein-agent.timer"
    assert svc.exists() and timer.exists()
    assert "NOT INSTALLED, NOT ENABLED" in svc.read_text()
    assert "NoNewPrivileges=yes" in svc.read_text()
    # separate units from the production deployer
    assert "frankenstein-deploy" not in timer.read_text()


# ══ control branch design ══════════════════════════════════════════════

def test_control_bootstrap_never_touches_production():
    c = code(CONTROL_BOOTSTRAP)
    assert "will NOT be touched" in CONTROL_BOOTSTRAP.read_text()
    for forbidden in ("--force", "push --quiet -u origin production",
                      "refs/heads/production"):
        assert forbidden not in c


def test_control_carries_only_protocol_files():
    c = code(CONTROL_BOOTSTRAP)
    assert "refusing: something outside .frankenstein/ was staged" in c
    assert "--orphan" in c, "control should share no history with production"


def test_worker_reads_state_from_control_not_the_working_tree():
    c = code(WORKER)
    assert 'show "$CONTROL_COMMIT:.frankenstein/STATE.json"' in c, \
        "authoritative state must come from the control commit itself"


def test_control_bootstrap_never_switches_the_users_checkout():
    """An earlier version created the orphan branch in place. Switching back
    then failed — the orphan index left every product file untracked and git
    refused to overwrite them — stranding the working checkout on a temp
    branch with a 4-file index. It must work in a throwaway clone instead."""
    c = code(CONTROL_BOOTSTRAP)
    assert "mktemp -d" in c, "must operate in a throwaway clone"
    assert "git clone" in c
    # the dangerous in-place pattern must not return
    assert "checkout --quiet --orphan" in c
    orphan_line = next(l for l in c.splitlines() if "--orphan" in l)
    clone_idx = c.index("git clone")
    assert c.index(orphan_line) > clone_idx, \
        "the orphan branch must be created inside the temp clone, not in place"
    assert 'checkout --quiet "$START_BRANCH"' not in c, \
        "restoring the user's branch is no longer needed and used to fail silently"


def test_control_bootstrap_is_idempotent_when_control_exists(tmp_path):
    """Running it again must be a clean no-op, not a second orphan commit."""
    c = code(CONTROL_BOOTSTRAP)
    assert "control already exists — nothing to bootstrap." in c
    exists_check = c.index("EXISTS=1")
    assert exists_check < c.index("mktemp -d"), \
        "existence must be checked before any temp clone or mutation"
