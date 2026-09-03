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
import shlex
import subprocess
import threading
import time
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
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(
        f"# Product Directive\n\nTask ID: {task_id}\n"
        f"Deployment Authorization: none\n\n## Objective\ndo the work\n")
    (ctl / ".frankenstein" / "IMPLEMENTATION_HANDOFF.md").write_text("# handoff\n")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "commit", "-qm", f"control {turn}/{status}", cwd=ctl)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/control", cwd=ctl)
    return git("rev-parse", "HEAD", cwd=ctl)


def child_tmp(world):
    """TMPDIR inside the child == $CHILD_HOME/tmp on the host.

    The worker no longer forwards any test-only channel into the sandbox, so a
    mock records evidence in its own scratch temp directory and the test reads
    it from outside. It is wiped at the start of every run.
    """
    return world["agent_dir"] / "child-home" / "tmp"


def agent_clone(world):
    return world["tmp"] / "agenthome" / "worktrees" / "agent-repo"


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


# A mock that RECORDS what it was actually handed before doing its work.
# The previous mock wrote STATE.json/PRODUCT_DIRECTIVE.md itself, which is how
# the stale-directive bug escaped detection.
MOCK_INSPECT = (
    'mkdir -p "$TMPDIR" && '
    'cp .frankenstein/PRODUCT_DIRECTIVE.md "$TMPDIR/directive.txt" && '
    'cp .frankenstein/STATE.json "$TMPDIR/state.json" && '
    'cp .frankenstein/AUTHORIZING_CONTROL_COMMIT "$TMPDIR/authorizing.txt" && '
    'printf "seen\n" >> app.txt && '
    'printf "# handoff\n\n## Deviations From Directive\nNo deviations\n" '
    '> .frankenstein/IMPLEMENTATION_HANDOFF.md && '
    'python3 -c "import json;p=\'.frankenstein/STATE.json\';'
    'd=json.load(open(p));d.update(turn=\'product_owner\','
    'status=\'awaiting_review\',last_actor=\'claude\');'
    'json.dump(d,open(p,\'w\'))" && '
    'git add -A && '
    'git -c user.email=c@c -c user.name=claude commit -qm "[CLAUDE] inspected"'
)


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

def seed_task_branch(world, task_id="FC-001"):
    """Publish an existing implementation branch for the task.

    changes_requested continues work already under review; without a prior
    branch the worker correctly refuses, so tests about that status must
    provide one.
    """
    wt = world["tmp"] / f"seedbranch-{task_id}"
    sh("git", "clone", "-q", "--branch", "production", str(world["remote"]), str(wt))
    sh("git", "checkout", "-q", "-b", f"claude/{task_id}-work", cwd=wt)
    (wt / "prior.txt").write_text("prior pass\n")
    sh("git", "add", "-A", cwd=wt)
    sh("git", "-c", "user.email=c@c", "-c", "user.name=claude",
       "commit", "-qm", f"[CLAUDE] {task_id} prior pass", cwd=wt)
    sh("git", "push", "-q", "origin", f"HEAD:refs/heads/claude/{task_id}-work", cwd=wt)
    return git("rev-parse", "HEAD", cwd=wt)


@pytest.mark.parametrize("status", ["ready_for_implementation", "changes_requested"])
def test_authorized_state_invokes_claude(world, status):
    if status == "changes_requested":
        seed_task_branch(world)
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

def hijack_control_mid_run(world, task_id="FC-002", status="changes_requested"):
    """Move control from OUTSIDE while a run is in flight.

    The child is now fully isolated — it cannot reach the remote, so it can no
    longer stage this itself. The mock signals through its own scratch temp
    directory and waits; this watcher, running on the host, moves control and
    releases it.
    """
    ready = child_tmp(world) / "HIJACK_NOW"
    done = child_tmp(world) / "HIJACK_DONE"

    def watch():
        deadline = time.time() + 120
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        if not ready.exists():
            return
        set_control_directive(world, turn="claude", status=status,
                              task_id=task_id, objective="superseding directive")
        done.write_text("")

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    return t


HIJACK_SIGNAL = (
    'mkdir -p "$TMPDIR" && : > "$TMPDIR/HIJACK_NOW" && '
    'for _ in $(seq 1 1200); do [ -e "$TMPDIR/HIJACK_DONE" ] && break; sleep 0.1; done && '
)


def test_control_change_during_run_blocks_the_handoff(world):
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    watcher = hijack_control_mid_run(world)
    r = run_worker(world, mock=HIJACK_SIGNAL + MOCK_GOOD)
    watcher.join(timeout=10)
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
    src = WORKER.read_text()
    start = src.index('PROMPT="')
    prompt = src[start:src.index('directive."', start)]
    for named in ("promote.sh", "rollback.sh", "deploy.sh", "systemd", "sudo"):
        assert named in prompt, f"prompt does not forbid {named}"
    assert "You may NOT" in prompt


def test_worker_installs_a_pre_push_hook_rejecting_production():
    src = WORKER.read_text()
    assert "hooks/pre-push" in src
    hook = src[src.index('cat > "$hook"'):src.index('chmod +x "$hook"')]
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
        "refs/heads/claude/FC-001-work aaa refs/heads/claude/FC-001-work "
        + "0" * 40 + "\n"),
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
    assert 'show "$CONTROL_COMMIT:.frankenstein/$1"' in c, \
        "protocol files must be read out of the control commit itself"
    assert 'STATE_JSON="$(ctl_file STATE.json)"' in c
    assert 'DIRECTIVE_TEXT="$(ctl_file PRODUCT_DIRECTIVE.md)"' in c
    # nothing may be read from a working tree instead
    assert "cat .frankenstein/STATE.json" not in c


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


# ══ 1. the AUTHORITATIVE control snapshot must reach Claude ════════════
#
# The task branch descends from production, whose .frankenstein/ copies may be
# stale placeholders. The run must see the directive and state that authorized
# it, not production's.

def make_production_stale(world):
    """Put a DIFFERENT directive and state on production."""
    seed = world["seed"]
    (seed / ".frankenstein").mkdir(exist_ok=True)
    (seed / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(
        "# Product Directive\n\nTask ID: FC-999\n\n## Objective\n"
        "STALE PRODUCTION PLACEHOLDER — must never be acted on.\n")
    (seed / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": "FC-999", "turn": "product_owner",
        "status": "awaiting_directive", "directive_commit": None,
        "implementation_commit": None, "last_actor": None,
        "updated_at": "2020-01-01T00:00:00Z"}))
    sh("git", "add", "-A", cwd=seed)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "stale protocol files on production", cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)


def set_control_directive(world, *, turn, status, task_id, objective):
    ctl = world["tmp"] / f"ctldir-{task_id}-{status}"
    if ctl.exists():
        sh("rm", "-rf", str(ctl))
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "config", "user.email", "t@t", cwd=ctl)
    sh("git", "config", "user.name", "t", cwd=ctl)
    if sh("git", "ls-remote", "--heads", "origin", "control", cwd=ctl).stdout.strip():
        sh("git", "checkout", "-q", "-B", "control", "origin/control", cwd=ctl)
    else:
        sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
        sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(
        f"# Product Directive\n\nTask ID: {task_id}\n"
        f"Deployment Authorization: none\n\n## Objective\n{objective}\n")
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": task_id, "turn": turn,
        "status": status, "directive_commit": None,
        "implementation_commit": None, "last_actor": None,
        "updated_at": "2026-09-01T00:00:00Z"}, indent=2))
    (ctl / ".frankenstein" / "IMPLEMENTATION_HANDOFF.md").write_text("# handoff\n")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "commit", "-qm", f"directive {task_id}", cwd=ctl)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/control", cwd=ctl)
    return git("rev-parse", "HEAD", cwd=ctl)


def test_claude_receives_the_control_directive_not_productions(world):
    """THE BUG: production carried FC-999 placeholders while control authorized
    FC-001. The run must see control's versions."""
    make_production_stale(world)
    control = set_control_directive(
        world, turn="claude", status="ready_for_implementation",
        task_id="FC-001", objective="AUTHORITATIVE OBJECTIVE FROM CONTROL")
    r = run_worker(world, mock=MOCK_INSPECT)
    assert r.returncode == 0, r.stderr
    seen = child_tmp(world)

    directive = (seen / "directive.txt").read_text()
    assert "AUTHORITATIVE OBJECTIVE FROM CONTROL" in directive
    assert "STALE PRODUCTION PLACEHOLDER" not in directive, \
        "the run was handed production's stale directive"
    assert "FC-001" in directive and "FC-999" not in directive

    state = json.loads((seen / "state.json").read_text())
    assert state["task_id"] == "FC-001", "the run saw production's stale state"
    assert state["status"] == "ready_for_implementation"

    assert (seen / "authorizing.txt").read_text().strip() == control, \
        "the immutable authorizing control SHA was not preserved for the run"


def test_authorizing_control_commit_is_recorded_on_the_branch(world):
    make_production_stale(world)
    control = set_control_directive(
        world, turn="claude", status="ready_for_implementation",
        task_id="FC-001", objective="do the thing")
    run_worker(world, mock=MOCK_INSPECT)
    clone = agent_clone(world)
    recorded = git("show", f"claude/FC-001-work:.frankenstein/AUTHORIZING_CONTROL_COMMIT",
                   cwd=clone)
    assert recorded.strip() == control


# ══ 2. changes_requested continues, never restarts ═════════════════════

def test_changes_requested_continues_the_existing_implementation(world):
    """A. first run -> commit A. PO requests changes. B. second run -> commit B
    descending from A, with A's product work still present."""
    make_production_stale(world)
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="first pass")
    first = run_worker(world, mock=(
        'printf "FIRST-PASS-WORK\\n" > first.txt && '
        'printf "# handoff\\n" > .frankenstein/IMPLEMENTATION_HANDOFF.md && '
        'git add -A && git -c user.email=c@c -c user.name=claude '
        'commit -qm "[CLAUDE] FC-001 first"'))
    assert first.returncode == 0, first.stderr
    remote_after_first = sh("git", "ls-remote", str(world["remote"]),
                            "refs/heads/claude/FC-001-work").stdout.split()[0]

    # Product Owner requests changes
    set_control_directive(world, turn="claude", status="changes_requested",
                          task_id="FC-001", objective="now also do the second bit")

    second = run_worker(world, mock=(
        'printf "SECOND-PASS-WORK\\n" > second.txt && '
        'git add -A && git -c user.email=c@c -c user.name=claude '
        'commit -qm "[CLAUDE] FC-001 corrections"'))
    assert second.returncode == 0, second.stderr

    verify = world["tmp"] / "verify-continuation"
    sh("git", "clone", "-q", "--branch", "claude/FC-001-work",
       str(world["remote"]), str(verify))
    assert (verify / "first.txt").exists(), "the first pass's work was discarded"
    assert (verify / "second.txt").exists(), "the corrections are missing"
    ancestry = sh("git", "merge-base", "--is-ancestor", remote_after_first, "HEAD",
                  cwd=verify, check=False)
    assert ancestry.returncode == 0, "commit B does not descend from commit A"


def test_changes_requested_without_prior_work_refuses(world):
    set_control_directive(world, turn="claude", status="changes_requested",
                          task_id="FC-007", objective="continue something absent")
    r = run_worker(world, mock=MOCK_GOOD)
    assert r.returncode != 0
    assert "nothing to continue" in r.stderr


# ══ 3. the second-stage control publication race ═══════════════════════

POST_RECEIVE_HIJACK = r'''#!/bin/sh
# Fires on the worker's own task-branch push, which happens strictly between
# the two control token checks.
while read -r old new ref; do
  case "$ref" in
    refs/heads/claude/*)
      [ -e "__FIRED__" ] && continue
      : > "__FIRED__"
      d=$(mktemp -d)
      (
        unset GIT_DIR GIT_QUARANTINE_PATH GIT_OBJECT_DIRECTORY \
              GIT_ALTERNATE_OBJECT_DIRECTORIES
        git clone -q --branch control "__REMOTE__" "$d" || exit 0
        cd "$d" || exit 0
        python3 -c "import json;p='.frankenstein/STATE.json';d=json.load(open(p));d.update(task_id='FC-002',status='changes_requested',last_actor='product_owner');json.dump(d,open(p,'w'),indent=2)"
        git add -A
        git -c user.email=p@p -c user.name=po commit -qm "[PO-CHANGES] FC-002"
        git push -q origin HEAD:control
      ) >/dev/null 2>&1
      rm -rf "$d"
      ;;
  esac
done
exit 0
'''


def test_control_moving_between_the_two_token_checks_blocks_the_handoff(world):
    """The genuine second-stage race.

    Moving control from inside the mock is caught by the FIRST token check, so
    that test does not exercise stage 2 at all. This one moves control from a
    post-receive hook on the remote, fired by the worker's own task-branch
    push — which happens strictly between the two checks. Without the late
    CONTROL_AT_PUBLISH check (and with a reset to origin/control rather than
    to the authorizing commit) the stale handoff rebases cleanly onto newer
    Product Owner state and then fast-forwards over it.
    """
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    fired = world["tmp"] / "HOOK_FIRED"
    hook = world["remote"] / "hooks" / "post-receive"
    hook.write_text(POST_RECEIVE_HIJACK
                    .replace("__FIRED__", str(fired))
                    .replace("__REMOTE__", str(world["remote"])))
    hook.chmod(0o755)

    r = run_worker(world, mock=MOCK_GOOD)
    assert fired.exists(), "the race never triggered — the test would prove nothing"
    assert r.returncode != 0, "stale handoff published over newer control state"
    assert "before publication" in r.stderr

    ctl = world["tmp"] / "verify-stage2"
    sh("git", "clone", "-q", "--branch", "control", str(world["remote"]), str(ctl))
    state = json.loads((ctl / ".frankenstein" / "STATE.json").read_text())
    assert state["task_id"] == "FC-002", "newer Product Owner state was clobbered"
    assert state["status"] == "changes_requested"
    assert state["last_actor"] == "product_owner"
    assert state["implementation_commit"] is None, \
        "a stale implementation SHA was stamped onto newer state"


def test_control_clone_resets_to_the_authorizing_commit_not_origin():
    c = code(WORKER)
    assert 'reset --hard --quiet "$CONTROL_COMMIT"' in c, \
        "resetting to origin/control would rebase stale state onto newer PO state"
    assert "CONTROL_AT_PUBLISH" in c, "the late token check is missing"


def test_control_fetch_and_reset_failures_stop_publication():
    c = code(WORKER)
    assert "control_fetch_failed" in c and "control_reset_failed" in c, \
        "without set -e these need explicit failure paths"


# ══ 4. the child-process boundary (behavioral) ═════════════════════════

SANDBOX_OK = subprocess.run(
    ["unshare", "--user", "--map-root-user", "--mount", "true"],
    capture_output=True).returncode == 0
needs_sandbox = pytest.mark.skipif(
    not SANDBOX_OK, reason="user namespaces unavailable in this environment")


@needs_sandbox
def test_child_cannot_write_the_production_checkout(world):
    """A. the child tries to write a marker into the production checkout."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    victim = world["prod"] / "PWNED"
    attack = (
        f'(echo pwned > "{victim}") 2>/dev/null; '
        'printf "work\\n" >> app.txt && '
        'printf "# handoff\\n" > .frankenstein/IMPLEMENTATION_HANDOFF.md && '
        'git add -A && git -c user.email=c@c -c user.name=claude '
        'commit -qm "[CLAUDE] work"')
    run_worker(world, mock=attack)
    assert not victim.exists(), "child wrote into the production checkout"


@needs_sandbox
def test_child_has_no_remote_to_push_production_through(world):
    """B. the child attempts a production push by any git invocation."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    before = sh("git", "ls-remote", str(world["remote"]),
                "refs/heads/production").stdout.split()[0]
    attack = (
        'mkdir -p "$TMPDIR"; '
        'git push origin HEAD:production 2>/dev/null; '
        f'git push "{world["remote"]}" HEAD:production 2>/dev/null; '
        'git remote -v > "$TMPDIR/remotes.txt" 2>&1; '
        f'ls "{world["remote"]}" > "$TMPDIR/remote-dir.txt" 2>&1; '
        'printf "work\\n" >> app.txt && '
        'printf "# handoff\\n" > .frankenstein/IMPLEMENTATION_HANDOFF.md && '
        'git add -A && git -c user.email=c@c -c user.name=claude '
        'commit -qm "[CLAUDE] work"')
    run_worker(world, mock=attack)
    seen = child_tmp(world)
    after = sh("git", "ls-remote", str(world["remote"]),
               "refs/heads/production").stdout.split()[0]
    assert before == after, "production was moved by the child"
    assert (seen / "remotes.txt").read_text().strip() == "", \
        "the child's clone still had a remote configured"
    assert "No such file" in (seen / "remote-dir.txt").read_text(), \
        "the child could still see the bare remote on disk"


@needs_sandbox
def test_child_environment_carries_no_reusable_credentials(world):
    """C. the child cannot obtain the credential the publisher uses."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    probe = (
        'mkdir -p "$TMPDIR"; '
        'env > "$TMPDIR/env.txt"; '
        'git config --get credential.helper >> "$TMPDIR/env.txt" 2>&1; '
        'printf "work\\n" >> app.txt && '
        'printf "# handoff\\n" > .frankenstein/IMPLEMENTATION_HANDOFF.md && '
        'git add -A && git -c user.email=c@c -c user.name=claude '
        'commit -qm "[CLAUDE] work"')
    run_worker(world, mock=probe,
               extra_env={"GITHUB_TOKEN": "ghp_SECRET_SHOULD_NOT_LEAK",
                          "GH_TOKEN": "gh_SECRET_SHOULD_NOT_LEAK"})
    env_seen = (child_tmp(world) / "env.txt").read_text()
    assert "SECRET_SHOULD_NOT_LEAK" not in env_seen, \
        "a GitHub credential reached the child environment"
    assert "GIT_ASKPASS=/bin/false" in env_seen
    assert "GIT_TERMINAL_PROMPT=0" in env_seen


@needs_sandbox
def test_child_can_still_do_normal_work(world):
    """D. ordinary edits, git operations and tests still work in the clone."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    r = run_worker(world, mock=(
        'bash scripts/test.sh >/dev/null && '
        'printf "normal\\n" >> app.txt && '
        'printf "# handoff\\n" > .frankenstein/IMPLEMENTATION_HANDOFF.md && '
        'git add -A && git -c user.email=c@c -c user.name=claude '
        'commit -qm "[CLAUDE] normal work"'))
    assert r.returncode == 0, r.stderr
    verify = world["tmp"] / "verify-normal"
    sh("git", "clone", "-q", "--branch", "claude/FC-001-work",
       str(world["remote"]), str(verify))
    assert "normal" in (verify / "app.txt").read_text()


def test_worker_refuses_to_run_unconfined_by_default():
    c = code(WORKER)
    assert "refusing to run" in c and "unconfined" in c
    assert "ALLOW_UNSANDBOXED" in c
    svc = (ROOT / "scripts" / "agent" / "frankenstein-agent.service").read_text()
    assert "ALLOW_UNSANDBOXED" not in svc, \
        "the systemd template must not disable the sandbox"


def test_pre_push_hook_detects_real_force_pushes(tmp_path):
    """The earlier hook only blocked ref names and claimed force protection it
    did not have. This asserts genuine non-fast-forward detection."""
    m = re.search(r"<<'HOOKEOF'\n(.*?)\nHOOKEOF\n", WORKER.read_text(), re.S)
    hook = tmp_path / "pre-push"; hook.write_text(m.group(1)); hook.chmod(0o755)
    repo = tmp_path / "r"; repo.mkdir()
    sh("git", "init", "-q", str(repo))
    shas = []
    for msg in ("a", "b"):
        (repo / "f").write_text(msg)
        sh("git", "add", "-A", cwd=repo)
        sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
           "commit", "-qm", msg, cwd=repo)
        shas.append(git("rev-parse", "HEAD", cwd=repo))
    old, new = shas
    zero = "0" * 40

    def hook_run(line):
        return subprocess.run(["bash", str(hook)], input=line, cwd=repo,
                              capture_output=True, text=True, timeout=30)

    assert hook_run(f"r {new} refs/heads/production {old}\n").returncode != 0
    rewind = hook_run(f"r {old} refs/heads/claude/FC-001-work {new}\n")
    assert rewind.returncode != 0, "hook does not detect force pushes"
    assert "non-fast-forward" in rewind.stderr
    assert hook_run(f"r {new} refs/heads/claude/FC-001-work {old}\n").returncode == 0
    assert hook_run(f"r {new} refs/heads/claude/FC-002-work {zero}\n").returncode == 0


# ══ 5. --dry-run must not mutate anything remote ═══════════════════════

def remote_refs(world):
    out = sh("git", "ls-remote", str(world["remote"])).stdout
    return sorted(l.strip() for l in out.splitlines() if l.strip())


def test_dry_run_changes_no_remote_refs(world):
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    before = remote_refs(world)
    r = run_worker(world, "--dry-run", mock=MOCK_GOOD)
    after = remote_refs(world)
    assert before == after, (
        "--dry-run changed remote refs:\n"
        f"before: {before}\nafter:  {after}")
    assert r.returncode == 0, r.stderr
    assert "DRY RUN" in r.stderr and "nothing pushed" in r.stderr


def test_dry_run_reports_what_would_be_published(world):
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    r = run_worker(world, "--dry-run", mock=MOCK_GOOD)
    assert "task branch:" in r.stderr
    assert "implementation SHA:" in r.stderr
    assert "control transition:" in r.stderr


def test_dry_run_is_recorded_as_such(world):
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    run_worker(world, "--dry-run", mock=MOCK_GOOD)
    rec = json.loads((world["agent_dir"] / "runs.jsonl").read_text().strip().splitlines()[-1])
    assert rec["result"] == "dry_run" and rec["mode"] == "dry-run"


# ══ 6. exact task-id and state validation ══════════════════════════════

@pytest.mark.parametrize("task_id", [
    "FC-001junk", "FC-01", "fc-001", "FC-001 ", "FC-", "XX-001", "FC-001-extra",
])
def test_malformed_task_ids_are_rejected(world, task_id):
    ctl = world["tmp"] / f"tid-{task_id.strip().replace(' ', '_')}"
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": task_id, "turn": "claude",
        "status": "ready_for_implementation"}))
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text("# d\n")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "bad id", cwd=ctl)
    sh("git", "push", "-qf", "origin", "HEAD:refs/heads/control", cwd=ctl)
    marker = world["tmp"] / "TID"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists(), f"invoked on malformed task_id {task_id!r}"
    assert r.returncode == 0


def test_wrong_protocol_version_is_rejected(world):
    ctl = world["tmp"] / "pv"
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 99, "task_id": "FC-001", "turn": "claude",
        "status": "ready_for_implementation"}))
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text("# d\n")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "pv", cwd=ctl)
    sh("git", "push", "-qf", "origin", "HEAD:refs/heads/control", cwd=ctl)
    marker = world["tmp"] / "PV"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists()
    assert "protocol_version" in r.stderr


def test_missing_directive_blocks_invocation(world):
    ctl = world["tmp"] / "nodir"
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": "FC-001", "turn": "claude",
        "status": "ready_for_implementation"}))
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "no directive", cwd=ctl)
    sh("git", "push", "-qf", "origin", "HEAD:refs/heads/control", cwd=ctl)
    marker = world["tmp"] / "ND"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists()
    assert "no PRODUCT_DIRECTIVE" in r.stderr


def test_directive_naming_a_different_task_blocks_invocation(world):
    ctl = world["tmp"] / "mismatch"
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": "FC-001", "turn": "claude",
        "status": "ready_for_implementation"}))
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(
        "# Product Directive\n\nTask ID: FC-042\n")
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "mismatch", cwd=ctl)
    sh("git", "push", "-qf", "origin", "HEAD:refs/heads/control", cwd=ctl)
    marker = world["tmp"] / "MM"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists(), "invoked despite directive/state task mismatch"
    assert "inconsistent" in r.stderr
# ══ 7. filesystem isolation: the real home is not merely un-inherited ══
#
# env -i hides environment variables. It does NOT hide the filesystem. These
# assert the child cannot reach the user's actual home — gh credentials, ssh
# keys, ~/.frankenstein, shell configuration — by absolute path.

WORK_AND_COMMIT = (
    'printf "work\\n" >> app.txt && '
    'printf "# handoff\\n" > .frankenstein/IMPLEMENTATION_HANDOFF.md && '
    'git add -A && git -c user.email=c@c -c user.name=claude '
    'commit -qm "[CLAUDE] work"')


def plant_host_home_secrets(world):
    """The worker runs with HOME=world['tmp'], so that IS the 'real home'."""
    home = world["tmp"]
    (home / ".config" / "gh").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "gh" / "hosts.yml").write_text(
        "github.com:\n    oauth_token: GH_HOST_SECRET_TOKEN\n")
    (home / ".ssh").mkdir(exist_ok=True)
    (home / ".ssh" / "id_test").write_text("SSH_HOST_PRIVATE_KEY_MATERIAL\n")
    (home / ".gitconfig").write_text("[user]\n\tname = anthony\n")
    return home


@needs_sandbox
def test_child_cannot_read_host_github_credentials(world):
    """A. ~/.config/gh/hosts.yml must not be reachable from the child."""
    plant_host_home_secrets(world)
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    probe = (
        'mkdir -p "$TMPDIR"; '
        '{ cat "$HOME/.config/gh/hosts.yml" || echo UNAVAILABLE; } '
        '> "$TMPDIR/gh.txt" 2>&1; '
        + WORK_AND_COMMIT)
    r = run_worker(world, mock=probe)
    assert r.returncode == 0, r.stderr
    seen = (child_tmp(world) / "gh.txt").read_text()
    assert "GH_HOST_SECRET_TOKEN" not in seen, \
        "the child read the host's GitHub credential"
    assert "UNAVAILABLE" in seen or "No such file" in seen


@needs_sandbox
def test_child_cannot_read_host_ssh_material(world):
    """B. ~/.ssh must not be reachable from the child."""
    plant_host_home_secrets(world)
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    probe = (
        'mkdir -p "$TMPDIR"; '
        '{ cat "$HOME/.ssh/id_test" || echo UNAVAILABLE; } > "$TMPDIR/ssh.txt" 2>&1; '
        'ls -A "$HOME" > "$TMPDIR/homelist.txt" 2>&1; '
        + WORK_AND_COMMIT)
    r = run_worker(world, mock=probe)
    assert r.returncode == 0, r.stderr
    assert "SSH_HOST_PRIVATE_KEY_MATERIAL" not in (child_tmp(world) / "ssh.txt").read_text()
    listing = (child_tmp(world) / "homelist.txt").read_text().split()
    for leaked in (".ssh", ".config", ".gitconfig", "remote.git", "seed",
                   "FrankensteinCentral", "agenthome"):
        assert leaked not in listing, f"the child can see {leaked} in the home directory"


@needs_sandbox
def test_child_cannot_write_the_host_home(world):
    """C. a write to an absolute host-home path must not land there."""
    plant_host_home_secrets(world)
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    home = world["tmp"]
    probe = (
        f'echo pwned > "{home}/PWNED_HOME" 2>/dev/null; '
        'echo pwned > "$HOME/PWNED_HOME_RELATIVE" 2>/dev/null; '
        f'echo pwned > "{home}/.ssh/authorized_keys" 2>/dev/null; '
        + WORK_AND_COMMIT)
    r = run_worker(world, mock=probe)
    assert r.returncode == 0, r.stderr
    assert not (home / "PWNED_HOME").exists(), "child wrote into the host home"
    assert not (home / "PWNED_HOME_RELATIVE").exists()
    assert not (home / ".ssh" / "authorized_keys").exists(), \
        "child wrote into the host's ~/.ssh"


@needs_sandbox
def test_shared_temp_locations_are_masked(world):
    """/var/tmp is not an ancestor of anything the child needs, so it is
    replaced by a private tmpfs."""
    marker = Path("/var/tmp") / f"fc-mask-probe-{os.getpid()}"
    marker.write_text("HOST_VAR_TMP_CONTENT\n")
    try:
        set_control_directive(world, turn="claude",
                              status="ready_for_implementation",
                              task_id="FC-001", objective="work")
        probe = (
            'mkdir -p "$TMPDIR"; '
            f'{{ cat "{marker}" || echo UNAVAILABLE; }} > "$TMPDIR/vartmp.txt" 2>&1; '
            + WORK_AND_COMMIT)
        r = run_worker(world, mock=probe)
        assert r.returncode == 0, r.stderr
        assert "HOST_VAR_TMP_CONTENT" not in (child_tmp(world) / "vartmp.txt").read_text()
    finally:
        marker.unlink(missing_ok=True)


# ══ 8. the independent test run must not be a sandbox escape ═══════════
#
# scripts/test.sh is a file Claude may edit. Running it on the host after the
# child exits hands a compromised run arbitrary code execution as the deploying
# user. This is the escape that existed in 759cea0.

@needs_sandbox
def test_verification_runs_inside_containment_not_on_the_host(world):
    plant_host_home_secrets(world)
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    home = world["tmp"]
    prod = world["prod"]
    attack = (
        'printf "%s\\n" '
        '"#!/usr/bin/env bash" '
        f'"echo pwned > \\"{home}/PWNED_OUTSIDE\\" 2>/dev/null" '
        f'"echo pwned > \\"{prod}/PWNED_OUTSIDE\\" 2>/dev/null" '
        '"exit 1" > scripts/test.sh && chmod +x scripts/test.sh && '
        + WORK_AND_COMMIT)
    r = run_worker(world, mock=attack)
    assert not (home / "PWNED_OUTSIDE").exists(), \
        "the modified test.sh executed outside containment"
    assert not (prod / "PWNED_OUTSIDE").exists(), \
        "the modified test.sh wrote into the production checkout"
    assert r.returncode != 0, "a failing verification must not publish"
    assert "tests fail" in r.stderr
    branches = sh("git", "ls-remote", "--heads", str(world["remote"])).stdout
    assert "claude/FC-001-work" not in branches, "a failed run published anyway"


@needs_sandbox
def test_verification_still_passes_honest_work(world):
    """The containment must not break the legitimate case."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    r = run_worker(world, mock=WORK_AND_COMMIT)
    assert r.returncode == 0, r.stderr
    assert "re-running the test suite independently, inside the sandbox" in r.stderr


# ══ 9. the publisher never executes the child's git control plane ══════

@needs_sandbox
def test_planted_pre_push_hook_never_runs(world):
    """The child owns .git/hooks in its workspace. The publisher is a clone the
    child never had access to, so a planted hook is never executed."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    home = world["tmp"]
    attack = (
        'mkdir -p .git/hooks && printf "%s\\n" '
        '"#!/usr/bin/env bash" '
        f'"echo pwned > \\"{home}/HOOK_RAN\\" 2>/dev/null" '
        '"exit 0" > .git/hooks/pre-push && chmod +x .git/hooks/pre-push && '
        + WORK_AND_COMMIT)
    r = run_worker(world, mock=attack)
    assert r.returncode == 0, r.stderr
    assert not (home / "HOOK_RAN").exists(), \
        "the publisher executed a hook planted by the child"
    branches = sh("git", "ls-remote", "--heads", str(world["remote"])).stdout
    assert "claude/FC-001-work" in branches, "publication came from the child's clone"
    hooks = agent_clone(world) / ".git" / "hooks"
    assert not any(hooks.iterdir()), "the child's hooks survived into the publisher zone"


@needs_sandbox
def test_planted_git_config_never_influences_the_publisher(world):
    """uploadpack.packObjectsHook is honoured when another repository fetches
    from this one — exactly what the publisher does."""
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    home = world["tmp"]
    pack_hook = f'sh -c \'echo pwned > "{home}/PACKHOOK_RAN"; exec git "$@"\' --'
    attack = (
        'git config uploadpack.packObjectsHook ' + shlex.quote(pack_hook) + ' && '
        'git config core.hooksPath /nonexistent-hooks && '
        + WORK_AND_COMMIT)
    r = run_worker(world, mock=attack)
    assert r.returncode == 0, r.stderr
    assert not (home / "PACKHOOK_RAN").exists(), \
        "the publisher honoured git config written by the child"
    config = (agent_clone(world) / ".git" / "config").read_text()
    assert "packObjectsHook" not in config, "the child's git config was not restored"
    assert "hooksPath" not in config
    branches = sh("git", "ls-remote", "--heads", str(world["remote"])).stdout
    assert "claude/FC-001-work" in branches


def test_publisher_is_a_separate_clone_from_the_child_workspace():
    c = code(WORKER)
    assert 'PUB_DIR="$AGENT_DIR/publisher"' in c
    assert 'rm -rf "$PUB_DIR"' in c, "the publisher clone must be fresh each run"
    assert 'git clone --quiet --no-checkout "$REPO_URL" "$PUB_DIR"' in c, \
        "the publisher must be cloned from origin, never from the child's clone"
    assert 'rm -rf "$CONTROL_DIR"' in c, "the control clone must be fresh each run"
    # every push comes from a clone the child never saw
    pushes = [l.strip() for l in c.splitlines()
              if re.search(r'(^|\s)git\s+[^|;]*\spush\s', l)]
    assert pushes
    for line in pushes:
        assert '"$PUB_DIR"' in line or '"$CONTROL_DIR"' in line, \
            f"push from an untrusted clone: {line}"


def test_git_in_the_child_clone_is_defused():
    c = code(WORKER)
    assert "core.hooksPath=/dev/null" in c and "core.fsmonitor=" in c, \
        "git in the child's clone must not honour hooks or fsmonitor"
    assert "uploadpack.packObjectsHook=" in c, \
        "the fetch out of the child's clone must not honour its pack hook"
    assert 'cp -f "$TRUSTED_CONFIG" "$AGENT_CLONE/.git/config"' in c, \
        "the child's git config must be restored before it is used again"
    # the restore must happen before any verification or publication
    assert c.index("resanitize_clone\n") < c.index("IMPL_COMMIT=")


def test_verification_is_sandboxed_in_source():
    c = executable(WORKER)
    idx = c.index("bash scripts/test.sh")
    window = c[max(0, idx - 400):idx]
    assert "run_sandboxed" in window, \
        "the independent test run must be inside the sandbox"


# ══ 10. the directive must actually name the task ══════════════════════

def publish_control(world, *, state, directive):
    ctl = world["tmp"] / f"ctl-raw-{abs(hash((state, directive))) % 10**8}"
    if ctl.exists():
        sh("rm", "-rf", str(ctl))
    sh("git", "clone", "-q", str(world["remote"]), str(ctl))
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", cwd=ctl)
    (ctl / ".frankenstein").mkdir(exist_ok=True)
    (ctl / ".frankenstein" / "STATE.json").write_text(state)
    if directive is not None:
        (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(directive)
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-qm", "raw control", cwd=ctl)
    sh("git", "push", "-qf", "origin", "HEAD:refs/heads/control", cwd=ctl)


AUTHORIZED_STATE = json.dumps({
    "protocol_version": 1, "task_id": "FC-001", "turn": "claude",
    "status": "ready_for_implementation", "directive_commit": None,
    "implementation_commit": None, "last_actor": None,
    "updated_at": "2026-09-01T00:00:00Z"})


@pytest.mark.parametrize("directive,why", [
    ("# Product Directive\n\n## Objective\nDo something.\n", "no Task ID line"),
    ("# Product Directive\n\nTask ID: FC-01\n", "malformed Task ID"),
    ("# Product Directive\n\nTask ID: FC-001-extra\n", "malformed Task ID"),
    ("# Product Directive\n\nTask ID: whatever\n", "non-FC Task ID"),
    ("# Product Directive\n\nTask ID: FC-042\n", "different Task ID"),
    ("# Product Directive\n\nTask ID: FC-001\nTask ID: FC-042\n",
     "two conflicting Task IDs"),
])
def test_directive_must_name_exactly_this_task(world, directive, why):
    publish_control(world, state=AUTHORIZED_STATE, directive=directive)
    marker = world["tmp"] / "DIRINVOKED"
    r = run_worker(world, mock=f'touch "{marker}"')
    assert not marker.exists(), f"invoked despite a directive with {why}"
    assert r.returncode == 0
    assert "NO-OP" in r.stderr


def test_a_correct_directive_still_authorizes(world):
    """The strict check must not block the legitimate case."""
    publish_control(world, state=AUTHORIZED_STATE,
                    directive="# Product Directive\n\nTask ID: FC-001\n"
                              "Deployment Authorization: none\n\n## Objective\nWork.\n")
    r = run_worker(world, mock=MOCK_GOOD)
    assert r.returncode == 0, r.stderr
    assert "AUTHORIZED" in r.stderr


# ══ 11. the probe is inert ═════════════════════════════════════════════

def test_probe_touches_no_refs_and_needs_no_enable(world):
    set_control_directive(world, turn="claude", status="ready_for_implementation",
                          task_id="FC-001", objective="work")
    (world["agent_dir"] / "ENABLED").unlink()
    before = remote_refs(world)
    r = run_worker(world, "--probe")
    assert before == remote_refs(world), "--probe changed remote refs"
    assert "containment probe" in r.stdout
    assert "Nothing was fetched, pushed, deployed or changed" in r.stdout
    assert not (world["agent_dir"] / "runs.jsonl").exists(), \
        "--probe recorded a run"


@needs_sandbox
def test_probe_reports_containment_and_does_not_leak(world):
    plant_host_home_secrets(world)
    r = run_worker(world, "--probe",
                   extra_env={"GITHUB_TOKEN": "ghp_SECRET_SHOULD_NOT_LEAK",
                              "GH_TOKEN": "gh_SECRET_SHOULD_NOT_LEAK"})
    out = r.stdout + r.stderr
    assert "LEAK:" not in out, out
    assert "SECRET_SHOULD_NOT_LEAK" not in out, "the probe printed a credential"
    assert "sandbox:            available" in out
    assert "scratch home is writable" in out
