"""The systemd unit templates must be internally consistent.

Codex FC-002 correction 2, finding 5: "all executable paths in unit templates
consistent". These templates are the activation plan's executable half — if a
unit's User, its Environment paths and its ExecStart disagree, the plan reads
as complete and installs as broken, and the mismatch is discovered on the box
with production credentials already created.

Every assertion here is a static read of files in this repository. Nothing is
installed, enabled, started or contacted.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "scripts" / "agent"

SERVICES = sorted(AGENT.glob("*.service"))
# The worker template is deliberately unfilled: it ships with a placeholder
# user because which account runs the worker is an owner decision.
PLACEHOLDER = "REPLACE_WITH_USER"


def directives(unit_text):
    """Directive lines only -- comments explain trade-offs and alternatives
    and must not be read as configuration."""
    return [l.strip() for l in unit_text.splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


def value(unit_text, key):
    for line in directives(unit_text):
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def environments(unit_text):
    out = {}
    for line in directives(unit_text):
        if line.startswith("Environment="):
            k, _, v = line[len("Environment="):].partition("=")
            out[k] = v
    return out


def test_there_is_a_template_for_every_documented_unit():
    """DEPLOYMENT-BASELINE.md documents frankenstein-deploy.service running on
    the box. A unit with no template in the repository cannot be reviewed and
    can drift from the installed one with nothing to catch it."""
    baseline = (ROOT / "docs" / "DEPLOYMENT-BASELINE.md").read_text()
    for name in set(re.findall(r'frankenstein-[a-z]+\.service', baseline)):
        assert (AGENT / name).exists(), f"{name} is documented but has no template"


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_every_service_names_its_user_and_a_matching_home(unit):
    text = unit.read_text()
    user = value(text, "User")
    assert user, f"{unit.name} does not say which user it runs as"
    if user == PLACEHOLDER:
        return
    exec_start = value(text, "ExecStart")
    assert exec_start, f"{unit.name} has no ExecStart"
    assert f"/home/{user}/" in exec_start, (
        f"{unit.name} runs as {user} but executes from {exec_start}")


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_every_execstart_points_at_a_script_that_exists(unit):
    text = unit.read_text()
    exec_start = value(text, "ExecStart")
    script = re.search(r'(/home/[^/]+/FrankensteinCentral/(scripts/\S+))', exec_start)
    assert script, f"{unit.name} does not execute a script from the checkout"
    assert (ROOT / script.group(2)).exists(), \
        f"{unit.name} executes {script.group(2)}, which is not in this repository"


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_a_units_own_directories_live_under_its_own_home(unit):
    """FRANKENSTEIN_DIR, FRANKENSTEIN_STATE_DIR and the writable paths belong
    to the unit's own user. Only explicitly read-only cross-user inputs may
    point elsewhere."""
    text = unit.read_text()
    user = value(text, "User")
    if not user or user == PLACEHOLDER:
        return
    env = environments(text)
    for key in ("FRANKENSTEIN_DIR", "FRANKENSTEIN_STATE_DIR",
                "FRANKENSTEIN_AGENT_DIR", "FRANKENSTEIN_WORKTREE_ROOT"):
        if key in env:
            assert env[key].startswith(f"/home/{user}/"), (
                f"{unit.name} runs as {user} but {key} is {env[key]}")
    for path in (value(text, "ReadWritePaths") or "").split():
        assert path.startswith(f"/home/{user}/"), (
            f"{unit.name} runs as {user} but may write {path}")


def test_the_status_publisher_reads_the_deploy_record_the_deployer_writes():
    """The one legitimate cross-user path, and it must name the actual
    deployer. It used to name Anthony's own account inside a template whose
    whole premise is that these are different users."""
    status = (AGENT / "frankenstein-status.service").read_text()
    deploy = (AGENT / "frankenstein-deploy.service").read_text()
    deployer = value(deploy, "User")
    deploy_state = environments(deploy)["FRANKENSTEIN_STATE_DIR"]
    published = environments(status)["FRANKENSTEIN_DEPLOYED"]
    assert published == f"{deploy_state}/deployed.json", (
        f"the status publisher reads {published}, but the deployer "
        f"({deployer}) writes to {deploy_state}/deployed.json")


def test_the_status_publisher_reads_the_release_record_the_releaser_writes():
    status = (AGENT / "frankenstein-status.service").read_text()
    release = (AGENT / "frankenstein-release.service").read_text()
    assert (environments(status)["FRANKENSTEIN_RELEASE_DIR"]
            == environments(release)["FRANKENSTEIN_RELEASE_DIR"])


def test_every_actor_runs_as_a_different_user():
    """Separation of duty is the whole design. Two of these sharing an account
    is the Tier 1 deviation, and it must not be the shipped template."""
    users = {}
    for unit in SERVICES:
        user = value(unit.read_text(), "User")
        if user and user != PLACEHOLDER:
            users.setdefault(user, []).append(unit.name)
    for user, units in users.items():
        assert len(units) == 1, f"{user} runs {units}; these must be separate"


def test_only_the_release_service_carries_a_production_credential():
    """The release service's guarantee is that its only effect on the world is
    one fast-forward push. No other unit may hold a credential that could
    move production."""
    for unit in SERVICES:
        if unit.name == "frankenstein-release.service":
            continue
        env = environments(unit.read_text())
        for key, val in env.items():
            assert "token" not in val.lower(), \
                f"{unit.name} references a token path: {key}={val}"
            assert "GIT_CONFIG_GLOBAL" not in key, \
                f"{unit.name} points git at a credential config"


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_no_unit_template_is_marked_installed(unit):
    """These are proposals. A template that reads as installed state is how a
    plan gets reported as an accomplished activation."""
    head = unit.read_text().splitlines()[0]
    assert "NOT INSTALLED" in head.upper(), \
        f"{unit.name} does not open by saying it is not installed"


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_every_service_has_a_timer_that_points_back_at_it(unit):
    timer = unit.with_suffix(".timer")
    assert timer.exists(), f"{unit.name} has no timer template"
    assert value(timer.read_text(), "Unit") == unit.name, \
        f"{timer.name} does not name {unit.name}"


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_no_unit_runs_as_root_or_gains_privileges(unit):
    text = unit.read_text()
    assert value(text, "User") not in (None, "root")
    assert value(text, "NoNewPrivileges") == "yes", \
        f"{unit.name} does not set NoNewPrivileges=yes"
