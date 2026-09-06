"""Tests for the status publisher (scripts/status-publisher.sh).

This is the Product Owner's only remote window onto what actually happened on
the box. Two properties matter most:

  * it PUBLISHES enough to review a release without shell access, and
  * it publishes NOTHING else -- no credentials, no command output, no
    personal data, and no ref other than `status`.

It is deliberately a different actor from the release service, whose entire
guarantee is that its only effect on the world is one push to production.
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLISHER = ROOT / "scripts" / "status-publisher.sh"


def sh(*args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=180)
    if check and r.returncode != 0:
        raise AssertionError(f"{' '.join(map(str, args))}\n{r.stdout}\n{r.stderr}")
    return r


def git(*args, cwd):
    return sh("git", *args, cwd=cwd).stdout.strip()


def code() -> str:
    return "\n".join(l for l in PUBLISHER.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


@pytest.fixture
def world(tmp_path):
    remote = tmp_path / "remote.git"
    sh("git", "init", "--bare", "-q", "-b", "production", str(remote))

    seed = tmp_path / "seed"
    seed.mkdir()
    sh("git", "init", "-q", "-b", "production", str(seed))
    sh("git", "config", "user.email", "t@t", cwd=seed)
    sh("git", "config", "user.name", "t", cwd=seed)
    (seed / "app.txt").write_text("v1\n")
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", "seed", cwd=seed)
    sh("git", "remote", "add", "origin", str(remote), cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)
    prod = git("rev-parse", "HEAD", cwd=seed)

    ctl = tmp_path / "ctl"
    sh("git", "clone", "-q", str(remote), str(ctl))
    sh("git", "config", "user.email", "po@po", cwd=ctl)
    sh("git", "config", "user.name", "po", cwd=ctl)
    sh("git", "checkout", "-q", "--orphan", "control", cwd=ctl)
    sh("git", "reset", "-q", "--hard", cwd=ctl)
    (ctl / ".frankenstein").mkdir()
    (ctl / ".frankenstein" / "PRODUCT_DIRECTIVE.md").write_text(
        "# Product Directive\n\nTask ID: FC-001\n"
        "Deployment Authorization: deploy-approved\n")
    (ctl / ".frankenstein" / "STATE.json").write_text(json.dumps({
        "protocol_version": 1, "task_id": "FC-001", "turn": "none",
        "status": "accepted", "directive_commit": None,
        "implementation_commit": prod, "last_actor": "product_owner",
        "updated_at": "2026-09-06T00:00:00Z"}, indent=2))
    sh("git", "add", "-f", ".frankenstein", cwd=ctl)
    sh("git", "commit", "-qm", "accept", cwd=ctl)
    sh("git", "push", "-q", "origin", "HEAD:refs/heads/control", cwd=ctl)

    state_dir = tmp_path / "state"
    (state_dir / "release").mkdir(parents=True)
    (state_dir / "deployed.json").write_text(json.dumps({
        "production_branch": "production", "last_attempt_commit": prod,
        "last_attempt_at": "2026-09-06T02:00:00+00:00", "last_result": "success",
        "running_commit": prod, "last_success_at": "2026-09-06T02:00:00+00:00"}))
    return {"remote": remote, "tmp": tmp_path, "state": state_dir,
            "prod": prod, "ctl": ctl}


def run_publisher(world, *args):
    env = dict(FRANKENSTEIN_REPO_URL=str(world["remote"]),
               FRANKENSTEIN_STATE_DIR=str(world["state"]),
               FRANKENSTEIN_STATUS_WORK=str(world["tmp"] / "statuswork"),
               HOME=str(world["tmp"]), PATH="/usr/bin:/bin:/usr/local/bin")
    return subprocess.run(["bash", str(PUBLISHER), *args], capture_output=True,
                          text=True, timeout=180, env=env)


def published(world):
    out = world["tmp"] / f"read-{len(list(world['tmp'].glob('read-*')))}"
    sh("git", "clone", "-q", "--branch", "status", str(world["remote"]), str(out))
    return json.loads((out / ".frankenstein" / "RELEASE_STATUS.json").read_text())


# ══ it publishes what the Product Owner needs ═════════════════════════

def test_it_publishes_the_record_to_the_status_branch(world):
    r = run_publisher(world)
    assert r.returncode == 0, r.stderr
    doc = published(world)
    assert doc["schema"] == 1
    assert doc["control"]["task_id"] == "FC-001"
    assert doc["control"]["status"] == "accepted"
    assert doc["control"]["deployment_authorization"] == "deploy-approved"
    assert doc["accepted"]["implementation_commit"] == world["prod"]
    assert doc["promotion"]["production_commit"] == world["prod"]
    assert doc["deployment"]["running_commit"] == world["prod"]
    assert doc["deployment"]["in_sync"] is True
    assert doc["verification"]["result"] == "pass"


def test_promotion_and_deployment_are_distinguished(world):
    """production moving is NOT the same fact as the code running."""
    (world["state"] / "deployed.json").write_text(json.dumps({
        "production_branch": "production", "last_attempt_commit": world["prod"],
        "last_attempt_at": "2026-09-06T02:00:00+00:00",
        "last_result": "tests_failed",
        "running_commit": "0" * 40,
        "last_success_at": "2026-09-05T00:00:00+00:00"}))
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["promotion"]["production_commit"] == world["prod"]
    assert doc["deployment"]["running_commit"] == "0" * 40
    assert doc["deployment"]["in_sync"] is False
    assert doc["deployment"]["promoted_but_not_running"] is True
    assert doc["verification"]["result"] == "fail"
    assert any("deploy" in (f["result"] or "") for f in doc["failures"])


def test_failures_are_reported(world):
    (world["state"] / "release" / "releases.jsonl").write_text(
        json.dumps({"at": "2026-09-06T03:00:00Z", "mode": "run",
                    "result": "impl_unreachable",
                    "released": None, "detail": "not on a task branch"}) + "\n")
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert any(f["result"] == "impl_unreachable" for f in doc["failures"])


# ══ it publishes nothing else ═════════════════════════════════════════

def test_credential_shaped_strings_are_redacted(world):
    leak = ("ghp_" + "A" * 36)
    (world["state"] / "release" / "releases.jsonl").write_text(
        json.dumps({"at": "2026-09-06T03:00:00Z", "mode": "run",
                    "result": "push_failed",
                    "released": None,
                    "detail": f"remote rejected: token {leak} and "
                              f"https://user:hunter2@github.com/x"}) + "\n")
    assert run_publisher(world).returncode == 0
    blob = json.dumps(published(world))
    assert leak not in blob, "a token-shaped string was published"
    assert "hunter2" not in blob, "an embedded URL credential was published"
    assert "[REDACTED]" in blob


def test_it_publishes_only_the_status_ref(world):
    before = sh("git", "ls-remote", str(world["remote"])).stdout
    assert run_publisher(world).returncode == 0
    after = sh("git", "ls-remote", str(world["remote"])).stdout

    def refs(text):
        return {l.split()[1]: l.split()[0] for l in text.splitlines() if l.strip()}

    b, a = refs(before), refs(after)
    changed = {k for k in set(b) | set(a) if b.get(k) != a.get(k)}
    assert changed == {"refs/heads/status"}, f"unexpected refs changed: {changed}"


def test_the_hook_refuses_any_other_ref(world):
    """The hook must be exercised with a real change.

    A push that git considers "up to date" is short-circuited before any hook
    runs, so testing with the SHA a ref already holds proves nothing.
    """
    assert run_publisher(world).returncode == 0
    work = world["tmp"] / "statuswork" / "work"
    # A commit that matches NO existing ref, so no push below can be
    # short-circuited as "up to date" before the hook runs.
    empty = git("hash-object", "-w", "-t", "tree", "/dev/null", cwd=work)
    other = sh("git", "-c", "user.name=probe", "-c", "user.email=p@p",
               "commit-tree", empty, "-m", "hook probe", cwd=work).stdout.strip()
    assert other not in (world["prod"],)

    # --force so git's own fast-forward check cannot be what refuses: the
    # HOOK must be the thing that stops it.
    for ref in ("refs/heads/production", "refs/heads/control",
                "refs/heads/handoff"):
        r = sh("git", "push", "--dry-run", "--force", "origin", f"{other}:{ref}",
               cwd=work, check=False)
        assert r.returncode != 0, f"the publisher could push {ref}"
        assert "REFUSED" in r.stderr, r.stderr

    r = sh("git", "push", "--dry-run", "--force", "origin", ":refs/heads/status",
           cwd=work, check=False)
    assert r.returncode != 0 and "REFUSED" in r.stderr, \
        "the publisher could delete its own branch"


def test_dry_run_publishes_nothing(world):
    before = sh("git", "ls-remote", str(world["remote"])).stdout
    r = run_publisher(world, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "DRY RUN" in r.stderr
    assert sh("git", "ls-remote", str(world["remote"])).stdout == before


def test_unchanged_status_is_not_republished(world):
    """A 2-minute timer must not append a commit every 2 minutes."""
    assert run_publisher(world).returncode == 0
    first = sh("git", "ls-remote", str(world["remote"]),
               "refs/heads/status").stdout.split()[0]
    r = run_publisher(world)
    assert r.returncode == 0, r.stderr
    assert "unchanged" in r.stderr
    second = sh("git", "ls-remote", str(world["remote"]),
                "refs/heads/status").stdout.split()[0]
    assert first == second, "an unchanged status was republished"


def test_it_never_promotes_or_deploys():
    """It reports on deployment; it must never perform one.

    Naming deploy.sh inside a published description string is fine -- what
    must not exist is any invocation of the deployment or promotion tooling,
    or any push to production.
    """
    src = code()
    for forbidden in ("bash scripts/promote.sh", "bash scripts/deploy.sh",
                      "bash scripts/rollback.sh", "docker compose",
                      "refs/heads/production:", ":refs/heads/production"):
        assert forbidden not in src, f"the status publisher invokes {forbidden}"


def test_it_reads_control_at_the_authorization_epoch():
    assert "rev-list -1" in code() and ".frankenstein/STATE.json" in code()
