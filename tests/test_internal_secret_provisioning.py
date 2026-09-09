"""The box gives itself the inter-service secret, once, and never says it out loud.

WHY THIS FILE EXISTS: gmail's /internal/token now requires `FC_INTERNAL_SECRET`
and fails closed without it (SCRUM-114) — correct, and it shipped with nothing
that sets the value. `docker-compose.yml` defaults it to empty, so on a real
deployment the schedule service could not borrow the Google credential at all
and Google Calendar sync stopped, waiting on somebody to notice a `.env` line
nobody had been told about.

There is nothing for a person to decide here. The value is random, it only has
to match between two containers on the same box, and it is never shown to
anyone — so `deploy.sh` generates it. Asking an operator to paste
`openssl rand -hex 32` buys no security and costs a working calendar every time
the step is missed.

Four properties, and all four matter:

  * it appears when it is missing, or the feature stays dead;
  * it is never rewritten when it is already there, because rotating it
    mid-deploy breaks the running pair until both containers restart —
    rotation is a deliberate act, not a side effect of deploying;
  * it is never printed, because deploy output is pasted into tickets;
  * it is written before the containers start, or it reaches them a whole
    deploy late.

These run the REAL deploy.sh against a throwaway box, like its siblings: a grep
for the generating line would pass just as happily on a block that never
executes.
"""
import os
import re
import shutil
import subprocess

import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"
KEY = "FC_INTERNAL_SECRET"


def sh(*args, cwd=None, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env=env, check=check,
                          capture_output=True, text=True)


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

    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text('#!/usr/bin/env bash\nexit 0\n')
    docker.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()
    return {"clone": clone, "state": state, "bindir": bindir, "tmp": tmp_path,
            "env_file": clone / ".env"}


def deploy(box):
    env = dict(os.environ,
               FRANKENSTEIN_DIR=str(box["clone"]),
               FRANKENSTEIN_STATE_DIR=str(box["state"]),
               DEPLOY_SKIP_TESTS="1",
               PATH=f"{box['bindir']}:{os.environ['PATH']}")
    env.pop("FRANKENSTEIN_DEPLOY_REEXEC", None)
    # check=False on purpose: with docker stubbed there is no stack to serve,
    # so deploy.sh correctly exits 1 at the health verdict it added in
    # SCRUM-107. The secret is written well before that, and it has to be —
    # see test_the_secret_is_written_before_the_containers_start.
    return sh("bash", str(box["clone"] / "scripts" / "deploy.sh"), "production",
              env=env, check=False)


def secret_of(env_file: Path) -> str | None:
    values = re.findall(rf"^{KEY}=(.*)$", env_file.read_text(), re.M)
    assert len(values) <= 1, f"{KEY} appears {len(values)} times — the last one wins"
    return values[0] if values else None


def test_a_box_with_no_env_file_still_gets_a_secret(box):
    """`.env` absent is the state a fresh box is in, and deploy.sh already
    continues past it with defaults. Calendar sync should not be the one
    feature that stays broken."""
    assert not box["env_file"].exists()
    deploy(box)
    assert box["env_file"].exists()
    assert len(secret_of(box["env_file"])) == 64


def test_an_env_file_without_the_key_gains_one(box):
    box["env_file"].write_text("FIREFLY_TOKEN=abc\nPLEX_TOKEN=xyz\n")
    deploy(box)
    text = box["env_file"].read_text()
    assert len(secret_of(box["env_file"])) == 64
    # Everything that was already there survives untouched.
    assert "FIREFLY_TOKEN=abc" in text and "PLEX_TOKEN=xyz" in text


def test_the_empty_placeholder_from_env_example_is_filled_in(box):
    """.env.example ships the key with no value, so the common case is a file
    that HAS the line and is still not configured. Appending a second line
    would leave two, and the last one wins — which would be the blank."""
    box["env_file"].write_text(f"{KEY}=\nFIREFLY_TOKEN=abc\n")
    deploy(box)
    assert len(secret_of(box["env_file"])) == 64


def test_an_existing_secret_is_never_rotated(box):
    """Rewriting it mid-deploy leaves the two containers holding different
    values until both happen to restart."""
    box["env_file"].write_text(f"{KEY}=deadbeef" + "0" * 56 + "\n")
    before = secret_of(box["env_file"])
    deploy(box)
    deploy(box)
    assert secret_of(box["env_file"]) == before


def test_two_deploys_do_not_produce_two_keys(box):
    deploy(box)
    first = secret_of(box["env_file"])
    deploy(box)
    assert secret_of(box["env_file"]) == first  # also asserts it appears once


def test_the_secret_never_reaches_the_deploy_output(box):
    """Deploy output gets pasted into tickets and chat. scripts/verify.sh has
    the same rule and this is the same reason."""
    r = deploy(box)
    written = secret_of(box["env_file"])
    assert written and written not in r.stdout and written not in r.stderr
    assert KEY in r.stdout, "generating it silently leaves no trace to diagnose"


def test_the_generated_value_is_not_predictable(box, tmp_path):
    """A constant would be worse than nothing: it would look configured while
    every box on earth shared it."""
    seen = {secret_of(box["env_file"]) for _ in [deploy(box)]}
    for _ in range(2):
        box["env_file"].unlink()
        deploy(box)
        seen.add(secret_of(box["env_file"]))
    assert len(seen) == 3, "the secret repeats across boxes"
    assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in seen), seen


def test_the_secret_is_written_before_the_containers_start():
    """Ordering, not just presence. Generated after `up -d` it would reach the
    containers a whole deploy late — the same one-cycle lag the
    FRANKENSTEIN_STATE_DIR export shipped with."""
    script = DEPLOY.read_text()
    generated = script.index('NEW_SECRET="$(head -c 32')
    compose_up = script.index("$DC up -d")
    assert generated < compose_up, (
        "the secret is generated after the containers are started, so they "
        "would come up with the previous value")
