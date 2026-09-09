"""deploy.sh upgrades itself before it touches the working tree.

WHY THIS FILE EXISTS: deploy.sh runs `git reset --hard` on the repository it
lives in, which includes deploy.sh. Bash does not load a script into memory —
it executes by byte offset and re-reads the file as it goes — so rewriting the
script mid-run makes the interpreter resume at a stale offset inside new
content. It can skip a block, run half a line, or execute something that was
never a statement, and nothing reports an error.

The quieter half of the same bug is that any change to deploy.sh takes effect
only on the deploy AFTER the one that pulls it, because the old copy is what
performs the pull. That is not hypothetical: the FRANKENSTEIN_STATE_DIR export
shipped and then did nothing for a cycle for exactly this reason.

These run the REAL script. A grep for "exec" would pass just as happily on a
handover that never fires, and the failure being guarded against is invisible
except by running it.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"


def sh(*args, cwd=None, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env=env, check=check,
                          capture_output=True, text=True)


def git(*args, cwd):
    return sh("git", *args, cwd=cwd).stdout.strip()


@pytest.fixture
def box(tmp_path):
    """A fake OptiPlex carrying the real deploy.sh, with docker stubbed out."""
    remote, seed, clone = tmp_path / "remote.git", tmp_path / "seed", tmp_path / "box"
    sh("git", "init", "--bare", "-b", "production", str(remote))
    seed.mkdir()
    sh("git", "init", "-b", "production", str(seed))
    sh("git", "config", "user.email", "t@t", cwd=seed)
    sh("git", "config", "user.name", "t", cwd=seed)
    (seed / "scripts").mkdir()
    shutil.copy(DEPLOY, seed / "scripts" / "deploy.sh")
    (seed / "app.txt").write_text("v1\n")
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", "v1", cwd=seed)
    sh("git", "remote", "add", "origin", str(remote), cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)
    sh("git", "clone", "-q", str(remote), str(clone))

    # docker, stubbed: `compose version` must succeed for deploy.sh to pick a
    # command, and everything else is recorded rather than run.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    dockerlog = tmp_path / "docker.log"
    docker = bindir / "docker"
    docker.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{dockerlog}"\nexit 0\n')
    docker.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()
    return {"remote": remote, "seed": seed, "clone": clone, "state": state,
            "bindir": bindir, "dockerlog": dockerlog, "tmp": tmp_path,
            "record": state / "deployed.json"}


def run_deploy(box, check=True):
    """Invoke the clone's CHECKED-OUT deploy.sh, exactly as the poller does."""
    env = dict(os.environ,
               FRANKENSTEIN_DIR=str(box["clone"]),
               FRANKENSTEIN_STATE_DIR=str(box["state"]),
               DEPLOY_SKIP_TESTS="1",
               MARKER=str(box["tmp"] / "marker"),
               PATH=f"{box['bindir']}:{os.environ['PATH']}")
    env.pop("FRANKENSTEIN_DEPLOY_REEXEC", None)
    return sh("bash", str(box["clone"] / "scripts" / "deploy.sh"), "production",
              env=env, check=check)


def publish_new_deploy_script(box, marker_line):
    """Push a deploy.sh that announces itself, so we can tell which one ran."""
    seed = box["seed"]
    text = DEPLOY.read_text() + f'\n{marker_line}\n'
    (seed / "scripts" / "deploy.sh").write_text(text)
    sh("git", "add", "-A", cwd=seed)
    sh("git", "commit", "-qm", "new deploy.sh", cwd=seed)
    sh("git", "push", "-q", "origin", "production", cwd=seed)


# --- the handover ----------------------------------------------------------

def test_a_changed_deploy_script_runs_on_the_very_same_deploy(box):
    """The whole point. The incoming deploy.sh must do the work, not sit on
    disk waiting for a second deploy to notice it."""
    publish_new_deploy_script(box, 'echo NEW >> "$MARKER"')
    r = run_deploy(box)
    marker = box["tmp"] / "marker"
    assert marker.exists(), (
        f"the new deploy.sh never ran.\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    assert marker.read_text().strip() == "NEW"
    assert "running the new one" in r.stdout


def test_the_new_script_is_interpreted_from_outside_the_repo(box):
    """The property the whole design rests on, asserted from inside the script
    that actually runs: `$0` must not be a path `git reset --hard` can rewrite.

    Handing over to a copy placed back in the repo would look identical from
    the outside and still be wrong — bash would be re-reading a file the reset
    is about to overwrite, which is the original bug wearing the fix's clothes.
    An earlier version of this test asserted the absence of one filename and
    passed on exactly that mutation.
    """
    # Resolved BY the running script, against its own working directory.
    # Recording a bare `$0` records a relative path, which the test would then
    # resolve against its own cwd — an absolute-looking answer about the wrong
    # directory, and a test that passes on the mutation it exists to catch.
    publish_new_deploy_script(
        box, 'echo "$(cd "$(dirname "$0")" && pwd)/$(basename "$0")" >> "$MARKER"')
    r = run_deploy(box)
    ran_from = Path((box["tmp"] / "marker").read_text().strip()).resolve()
    clone = box["clone"].resolve()
    assert clone not in ran_from.parents, (
        f"the new deploy.sh ran from {ran_from}, inside the repo it resets")
    assert "running the new one" in r.stdout


def test_the_handover_copy_is_the_incoming_version(box):
    publish_new_deploy_script(box, 'echo NEW >> "$MARKER"')
    run_deploy(box)
    upgrade = box["state"] / ".deploy-upgrade.sh"
    assert upgrade.exists(), "the handover copy should live outside the repo"
    assert 'echo NEW >> "$MARKER"' in upgrade.read_text()


def test_the_deploy_still_completes_after_handing_over(box):
    """Handing over is not an escape hatch: the new script must go on to do
    the deploy, or this trades a subtle bug for a silent no-op."""
    publish_new_deploy_script(box, 'echo NEW >> "$MARKER"')
    run_deploy(box)
    head = git("rev-parse", "origin/production", cwd=box["clone"])
    rec = json.loads(box["record"].read_text())
    assert rec["last_result"] == "success"
    assert rec["running_commit"] == head
    assert "up -d --build --remove-orphans" in box["dockerlog"].read_text()


# --- and does not fire when it should not ----------------------------------

def test_an_unchanged_script_hands_over_to_nobody(box):
    """The common case is that deploy.sh did not change. Re-execing anyway
    would double every deploy's work for no reason."""
    sh("git", "commit", "-q", "--allow-empty", "-m", "unrelated", cwd=box["seed"])
    sh("git", "push", "-q", "origin", "production", cwd=box["seed"])
    r = run_deploy(box)
    assert "running the new one" not in r.stdout
    assert not (box["tmp"] / "marker").exists()
    assert json.loads(box["record"].read_text())["last_result"] == "success"


def test_the_handover_is_one_shot(box):
    """The guard has to survive into the child, or two scripts that each
    considered the other newer would ping-pong forever."""
    publish_new_deploy_script(box, 'echo NEW >> "$MARKER"')
    run_deploy(box)
    # One handover, therefore exactly one line from the new script.
    assert (box["tmp"] / "marker").read_text().count("NEW") == 1


def test_a_second_deploy_of_the_same_code_hands_over_again_to_nobody(box):
    """After the first deploy the clone carries the new script, so the second
    run must find nothing to upgrade to."""
    publish_new_deploy_script(box, 'echo NEW >> "$MARKER"')
    run_deploy(box)
    r = run_deploy(box)
    assert "running the new one" not in r.stdout
    assert (box["tmp"] / "marker").read_text().count("NEW") == 2  # ran directly


# --- degrading safely ------------------------------------------------------

def test_an_unreachable_remote_does_not_block_the_deploy_at_the_check(box):
    """The upgrade check must never be the thing that stops a deploy. With the
    remote gone the fetch fails; the real pull below is what reports it, under
    `set -e`, exactly as before this check existed."""
    shutil.rmtree(box["remote"])
    r = run_deploy(box, check=False)
    assert "running the new one" not in r.stdout
    assert r.returncode != 0                      # the real pull still fails
    assert "Traceback" not in r.stderr


def test_the_leftover_temp_file_is_not_left_behind(box):
    """A deploy that finds nothing to upgrade to should leave no litter in the
    state directory, which is also where the deploy record lives."""
    sh("git", "commit", "-q", "--allow-empty", "-m", "unrelated", cwd=box["seed"])
    sh("git", "push", "-q", "origin", "production", cwd=box["seed"])
    run_deploy(box)
    assert not (box["state"] / ".deploy-upgrade.sh.tmp").exists()


def test_a_relative_invocation_does_not_hand_over_on_every_run(box):
    """deploy.sh resolves its own path before it cd's away. Comparing against
    a bare `$0` would compare against a path that no longer exists once the cd
    has happened — which reads as "the script changed" and would hand over on
    every single deploy, unchanged or not."""
    sh("git", "commit", "-q", "--allow-empty", "-m", "unrelated", cwd=box["seed"])
    sh("git", "push", "-q", "origin", "production", cwd=box["seed"])
    env = dict(os.environ,
               FRANKENSTEIN_DIR=str(box["clone"]),
               FRANKENSTEIN_STATE_DIR=str(box["state"]),
               DEPLOY_SKIP_TESTS="1",
               MARKER=str(box["tmp"] / "marker"),
               PATH=f"{box['bindir']}:{os.environ['PATH']}")
    env.pop("FRANKENSTEIN_DEPLOY_REEXEC", None)
    # Invoked by a RELATIVE path, from the repo's parent — not from inside it.
    r = sh("bash", "box/scripts/deploy.sh", "production",
           cwd=box["tmp"], env=env)
    assert "running the new one" not in r.stdout
    assert json.loads(box["record"].read_text())["last_result"] == "success"
