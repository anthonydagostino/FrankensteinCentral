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
        "running_commit": prod, "last_success_at": "2026-09-06T02:00:00+00:00",
        "test_gate": {"result": "passed", "commit": prod,
                      "at": "2026-09-06T02:00:00+00:00"},
        "running_state": "started", "last_success_commit": prod,
        "verification": {"result": "pass", "commit": prod,
                         "at": "2026-09-06T02:00:00+00:00"}}))
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
    assert doc["schema"] == 2
    assert doc["control"]["task_id"] == "FC-001"
    assert doc["control"]["status"] == "accepted"
    assert doc["control"]["deployment_authorization"] == "deploy-approved"
    assert doc["accepted"]["implementation_commit"] == world["prod"]
    assert doc["promotion"]["production_commit"] == world["prod"]
    assert doc["deployment"]["running_commit"] == world["prod"]
    assert doc["deployment"]["in_sync"] is True
    # A deploy started containers; it did not prove the app is healthy.
    assert doc["test_gate"]["result"] == "passed"
    assert doc["test_gate"]["commit"] == world["prod"]
    assert doc["verification"]["result"] == "pass"
    assert doc["verification"]["required"] is True
    assert doc["verification"]["confirms_deployment"] is True


def test_promotion_and_deployment_are_distinguished(world):
    """production moving is NOT the same fact as the code running."""
    (world["state"] / "deployed.json").write_text(json.dumps({
        "production_branch": "production", "last_attempt_commit": world["prod"],
        "last_attempt_at": "2026-09-06T02:00:00+00:00",
        "last_result": "tests_failed",
        "running_commit": "0" * 40,
        "last_success_at": "2026-09-05T00:00:00+00:00",
        "test_gate": {"result": "failed", "commit": world["prod"],
                      "at": "2026-09-06T02:00:00+00:00"}}))
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["promotion"]["production_commit"] == world["prod"]
    assert doc["deployment"]["running_commit"] == "0" * 40
    assert doc["deployment"]["in_sync"] is False
    assert doc["deployment"]["promoted_but_not_running"] is True
    assert doc["test_gate"]["result"] == "failed"
    assert doc["attention_required"] is True
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


# ══ absence of evidence must be actionable ════════════════════════════
#
# Codex review finding 2: a missing deployment record produced
# running_commit=null, failures=[], verification=unknown and
# promoted_but_not_running=false -- so every check in docs/CODEX-WAKEUP.md
# fell through and the Product Owner saw nothing wrong.


@pytest.mark.parametrize("state,expected", [
    ("missing", "deployment_evidence_missing"),
    ("malformed", "deployment_evidence_malformed"),
])
def test_unusable_deployment_evidence_demands_attention(world, state, expected):
    rec = world["state"] / "deployed.json"
    if state == "missing":
        rec.unlink()
    else:
        rec.write_text("{not json at all")
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["deployment_evidence"] == state
    assert doc["attention_required"] is True, \
        "unusable deployment evidence was reported as nothing to see"
    assert any(f["result"] == expected for f in doc["failures"]), doc["failures"]
    assert doc["verification"]["result"] == "unknown"
    assert doc["verification"]["source"] == "no usable deployment record"


def test_a_promoted_commit_with_no_confirmed_deployment_demands_attention(world):
    """An unknown running commit must never read as 'in sync'."""
    (world["state"] / "deployed.json").write_text(json.dumps({
        "production_branch": "production", "last_attempt_commit": None,
        "last_attempt_at": None, "last_result": None,
        "running_commit": None, "last_success_at": None}))
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["deployment"]["running_commit"] is None
    assert doc["deployment"]["in_sync"] is False
    assert doc["deployment"]["promoted_but_not_running"] is True, \
        "production holds a commit that nothing confirms is running"
    assert doc["attention_required"] is True
    assert any(f["result"] == "no_confirmed_deployment" for f in doc["failures"])


def test_a_healthy_deployment_needs_no_attention(world):
    """The flag must not be permanently on, or it means nothing.

    Healthy now means VERIFIED: the deployment succeeded and a readiness check
    passed on that same commit. That state, and only that state, is all-clear.
    """
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["deployment_evidence"] == "ok"
    assert doc["test_gate"]["result"] == "passed"
    assert doc["verification"]["result"] == "pass"
    assert doc["failures"] == []
    assert doc["attention_required"] is False


# ══ verification is about ONE commit ══════════════════════════════════
#
# Codex FC-002 review finding 4: the publisher accepted a pass/fail field
# without checking WHICH commit it was about, so a verification of the
# previous release could confirm the current one.


def with_deployment(world, **over):
    rec = {"production_branch": "production",
           "last_attempt_commit": world["prod"],
           "last_attempt_at": "2026-09-06T02:00:00+00:00",
           "last_result": "success", "running_commit": world["prod"],
           "last_success_at": "2026-09-06T02:00:00+00:00",
           "test_gate": {"result": "passed", "commit": world["prod"],
                         "at": "2026-09-06T02:00:00+00:00"}}
    rec.update(over)
    (world["state"] / "deployed.json").write_text(json.dumps(rec))


def test_a_verification_of_a_different_commit_is_stale_not_a_pass(world):
    """THE REGRESSION: an old pass must not confirm a new deployment."""
    with_deployment(world, verification={
        "result": "pass", "commit": "9" * 40, "at": "2026-09-05T00:00:00+00:00"})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "stale", \
        "a readiness result for another commit was accepted as a pass"
    assert doc["attention_required"] is True
    assert any(f["result"] == "verification_stale" for f in doc["failures"])


def test_a_verification_matching_the_running_commit_is_a_pass(world):
    with_deployment(world, verification={
        "result": "pass", "commit": world["prod"],
        "at": "2026-09-06T02:00:00+00:00", "degraded": ["gmail"]})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "pass"
    assert doc["verification"]["commit"] == world["prod"]
    # A degraded optional integration is reported, and is not a failure.
    assert doc["verification"]["degraded"] == ["gmail"]
    assert doc["attention_required"] is False


def test_a_failed_readiness_check_demands_attention(world):
    with_deployment(world, verification={
        "result": "fail", "commit": world["prod"],
        "at": "2026-09-06T02:00:00+00:00",
        "required_failed": ["app catalog"]})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "fail"
    assert doc["verification"]["required_failed"] == ["app catalog"]
    assert doc["attention_required"] is True
    assert any(f["result"] == "verification_fail" for f in doc["failures"])


def test_a_verification_without_a_commit_is_unknown_not_a_pass(world):
    with_deployment(world, verification={
        "result": "pass", "commit": None, "at": "2026-09-06T02:00:00+00:00"})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "unknown"


def test_no_recorded_verification_holds_attention(world):
    """THE REGRESSION Codex reproduced: a successful deployment with no
    verification record came out result=not_run, attention_required=false.

    An earlier revision of this test asserted the opposite, on the reasoning
    that not_run is honest and should not latch the flag on forever. Honest it
    is; all-clear it is not. The question the flag answers is "is this
    deployment confirmed working?", and for a deployment nobody checked, the
    answer is no. It does not latch forever because deploy.sh now moves a
    successful deploy through pending -> pass/fail, so a lasting not_run means
    the check genuinely never ran or crashed -- exactly the case worth seeing.
    """
    with_deployment(world)
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "not_run"
    assert doc["verification"]["required"] is True
    assert doc["verification"]["confirms_deployment"] is False
    assert doc["attention_required"] is True, \
        "an unverified deployment was published as needing no attention"
    assert any(f["result"] == "verification_not_run" for f in doc["failures"])


def test_a_readiness_crash_recorded_as_not_run_holds_attention(world):
    """deploy.sh records not_run when the check produced nothing usable."""
    with_deployment(world, verification={
        "result": "not_run", "commit": world["prod"],
        "at": "2026-09-06T02:00:00+00:00",
        "detail": "readiness check produced no usable result"})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "not_run"
    assert doc["attention_required"] is True


def test_a_pending_verification_is_not_ready(world):
    """deploy.sh marks a fresh success pending until readiness reports.

    Without this transition the record briefly said "deployed successfully"
    with last time's pass still attached -- a new deployment reading as
    verified before anything had looked at it.
    """
    with_deployment(world, verification={
        "result": "pending", "commit": world["prod"],
        "at": "2026-09-06T02:00:00+00:00",
        "detail": "readiness check has not reported yet"})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "pending"
    assert doc["verification"]["confirms_deployment"] is False
    assert doc["attention_required"] is True


@pytest.mark.parametrize("verification,why", [
    ("a string", "verification is not an object"),
    ([{"result": "pass"}], "verification is a list"),
    ({"result": "green", "commit": "b" * 40}, "result is not a verdict"),
    ({"result": True, "commit": "b" * 40}, "result is not even a string"),
])
def test_a_malformed_verification_is_never_a_pass(world, verification, why):
    with_deployment(world, verification=verification)
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["verification"]["result"] == "malformed", why
    assert doc["verification"]["confirms_deployment"] is False, why
    assert doc["attention_required"] is True, why


# ══ a partial compose failure is not a preserved old deployment ═══════
#
# Codex FC-002 correction 2, finding 3: compose can already have replaced some
# containers before it fails. Carrying the previous success forward as
# running_commit asserted a rollback that never happened.


def test_a_partial_compose_failure_does_not_claim_the_old_sha_is_running(world):
    with_deployment(world, last_result="compose_failed",
                    last_attempt_commit="c" * 40,
                    running_commit=None,
                    running_state="unknown_partial_start",
                    last_success_commit=world["prod"],
                    verification={"result": "unknown", "commit": None,
                                  "at": "2026-09-06T02:30:00+00:00"})
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["deployment"]["running_commit"] is None, \
        "a partial compose failure was published as the old SHA still running"
    assert doc["deployment"]["running_state"] == "unknown_partial_start"
    # The previous success is still a known fact -- just a different one.
    assert doc["deployment"]["last_success_commit"] == world["prod"]
    assert doc["verification"]["result"] == "unknown"
    assert doc["attention_required"] is True


# ══ the release service runs from a copy ══════════════════════════════
#
# Codex FC-002 correction 2, finding 6: a release-service checkout does not
# update when production moves, so the gate enforcing a release can be older
# code than the release. The installed source SHA and the candidate SHA are
# different facts and are published as different fields.


def test_the_release_source_commit_is_published_separately(world):
    (world["state"] / "release" / "releases.jsonl").write_text(
        json.dumps({"at": "2026-09-06T03:00:00Z", "mode": "run",
                    "result": "released", "released": world["prod"],
                    "detail": "", "source_commit": "d" * 40}) + "\n")
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["release_service"]["source_commit"] == "d" * 40
    assert doc["release_service"]["matches_production"] is False, \
        "a release service running older code than production read as current"
    assert doc["promotion"]["last_released_sha"] == world["prod"], \
        "the candidate SHA was conflated with the release service's own SHA"


def test_a_release_source_matching_production_is_reported_as_current(world):
    (world["state"] / "release" / "releases.jsonl").write_text(
        json.dumps({"at": "2026-09-06T03:00:00Z", "mode": "run",
                    "result": "released", "released": world["prod"],
                    "detail": "", "source_commit": world["prod"]}) + "\n")
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["release_service"]["matches_production"] is True


def test_an_absent_release_source_is_unknown_not_a_match(world):
    """Older records carry no source_commit. Missing must not read as equal."""
    (world["state"] / "release" / "releases.jsonl").write_text(
        json.dumps({"at": "2026-09-06T03:00:00Z", "mode": "run",
                    "result": "released", "released": world["prod"],
                    "detail": ""}) + "\n")
    assert run_publisher(world).returncode == 0
    doc = published(world)
    assert doc["release_service"]["source_commit"] is None
    assert doc["release_service"]["matches_production"] is None
