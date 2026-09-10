"""The Backrest deliverable (SCRUM-34) is configuration, and configuration
that is wrong fails silently at 03:30 on the box. These pin the parts that
are checkable from a checkout:

  * the JSON is valid and every field/enum is one Backrest actually accepts —
    the names below are copied from upstream proto/v1/config.proto, so a typo
    like CONDITION_SNAPSHOT_STARTED cannot survive;
  * the pre-snapshot hook runs a script that exists, and is FATAL on error,
    because that is the entire contract with dump-all.sh;
  * a fresh repo uses autoInitialize and NOT guid (mutually exclusive);
  * nothing in the shipped config is a real secret, and every placeholder is
    documented in docs/SETUP-BACKUP.md so none can be missed;
  * the unit waits for docker.service, since the hook runs docker exec.

What these cannot check — that B2 accepts the key, that a snapshot lands, that
a restore comes back — is the acceptance signal, and it is performed on the
box (SCRUM-36, SCRUM-15). Passing here is necessary, not sufficient.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CFG_PATH = ROOT / "scripts" / "backrest" / "config.json"
UNIT_PATH = ROOT / "scripts" / "backrest" / "backrest.service"
DOC = (ROOT / "docs" / "SETUP-BACKUP.md").read_text()
CFG = json.loads(CFG_PATH.read_text())

# Verbatim from garethgeorge/backrest proto/v1/config.proto.
CONDITIONS = {
    "CONDITION_UNKNOWN", "CONDITION_ANY_ERROR", "CONDITION_SNAPSHOT_START",
    "CONDITION_SNAPSHOT_END", "CONDITION_SNAPSHOT_ERROR", "CONDITION_SNAPSHOT_WARNING",
    "CONDITION_SNAPSHOT_SUCCESS", "CONDITION_SNAPSHOT_SKIPPED", "CONDITION_PRUNE_START",
    "CONDITION_PRUNE_ERROR", "CONDITION_PRUNE_SUCCESS", "CONDITION_CHECK_START",
    "CONDITION_CHECK_ERROR", "CONDITION_CHECK_SUCCESS", "CONDITION_FORGET_START",
    "CONDITION_FORGET_ERROR", "CONDITION_FORGET_SUCCESS",
}
ON_ERROR = {"ON_ERROR_IGNORE", "ON_ERROR_CANCEL", "ON_ERROR_FATAL",
            "ON_ERROR_RETRY_1MINUTE", "ON_ERROR_RETRY_10MINUTES",
            "ON_ERROR_RETRY_EXPONENTIAL_BACKOFF"}
CLOCKS = {"CLOCK_DEFAULT", "CLOCK_LOCAL", "CLOCK_UTC", "CLOCK_LAST_RUN_TIME"}
REPO_FIELDS = {"id", "uri", "guid", "password", "env", "flags", "prunePolicy",
               "checkPolicy", "hooks", "autoUnlock", "autoInitialize",
               "commandPrefix", "shared", "originInstanceId", "forgetPolicy"}
PLAN_FIELDS = {"id", "repo", "paths", "excludes", "iexcludes", "schedule",
               "retention", "hooks", "backupFlags", "skipIfUnchanged"}
HOOK_ACTIONS = {"actionCommand", "actionWebhook", "actionDiscord", "actionGotify",
                "actionSlack", "actionShoutrrr", "actionHealthchecks", "actionTelegram"}


def all_hooks():
    for r in CFG["repos"]:
        yield "repo", r["id"], r.get("hooks", [])
    for p in CFG["plans"]:
        yield "plan", p["id"], p.get("hooks", [])


# ---- shape -----------------------------------------------------------------

def test_config_version_is_current_and_instance_is_set():
    """version 0 is rejected outright; CurrentVersion = len(migrations) = 6.
    An empty instance is warned about today and 'will be required'."""
    assert CFG["version"] == 6
    assert CFG.get("instance")


def test_only_known_fields_are_used():
    for r in CFG["repos"]:
        unknown = set(r) - REPO_FIELDS
        assert not unknown, f"repo {r['id']}: unknown fields {unknown}"
    for p in CFG["plans"]:
        unknown = set(p) - PLAN_FIELDS
        assert not unknown, f"plan {p['id']}: unknown fields {unknown}"


def test_every_hook_uses_real_conditions_actions_and_on_error():
    for kind, owner, hooks in all_hooks():
        for h in hooks:
            bad = set(h["conditions"]) - CONDITIONS
            assert not bad, f"{kind} {owner}: invented condition(s) {bad}"
            assert h["conditions"], f"{kind} {owner}: hook with no conditions"
            actions = set(h) & HOOK_ACTIONS
            assert len(actions) == 1, f"{kind} {owner}: hook must have exactly one action, has {actions}"
            if "onError" in h:
                assert h["onError"] in ON_ERROR, h["onError"]


def test_schedules_use_real_clock_values():
    def clocks(obj):
        if isinstance(obj, dict):
            if "clock" in obj:
                yield obj["clock"]
            for v in obj.values():
                yield from clocks(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from clocks(v)
    for c in clocks(CFG):
        assert c in CLOCKS, c


def test_a_fresh_repo_auto_initializes_and_carries_no_guid():
    """validate.go: 'guid or AutoInitialize must be set, but not both'."""
    for r in CFG["repos"]:
        assert r.get("autoInitialize") is True
        assert "guid" not in r, "guid and autoInitialize are mutually exclusive"


def test_plan_references_an_existing_repo_and_has_paths():
    ids = {r["id"] for r in CFG["repos"]}
    for p in CFG["plans"]:
        assert p["repo"] in ids
        assert p["paths"], "at least one path is required"
        assert "/srv/dumps/current" in p["paths"], \
            "the plan must snapshot the dump set dump-all.sh publishes"
        assert p["schedule"].get("cron")


# ---- the contract with dump-all.sh -------------------------------------------

def test_the_pre_snapshot_hook_runs_dump_all_and_is_fatal_on_error():
    """ON_ERROR_FATAL 'fails the operation and subsequent hooks' (proto). CANCEL
    would stop the snapshot too but not as an error — no /fail ping. A failed
    dump has to be an error, or healthchecks stays green over an empty night."""
    plan = CFG["plans"][0]
    starts = [h for h in plan["hooks"]
              if h["conditions"] == ["CONDITION_SNAPSHOT_START"] and "actionCommand" in h]
    assert len(starts) == 1, "exactly one pre-snapshot command hook"
    h = starts[0]
    assert h["onError"] == "ON_ERROR_FATAL"
    cmd = h["actionCommand"]["command"]
    assert cmd.startswith("#!/bin/bash"), "Backrest feeds the command to sh via stdin; the shebang selects bash"
    assert "scripts/dump-all.sh" in cmd
    assert (ROOT / "scripts" / "dump-all.sh").exists()
    assert (ROOT / "scripts" / "dump-all.sh").stat().st_mode & 0o111, "dump-all.sh is not executable"
    assert "{{" not in cmd, "Backrest renders the command as a Go template; braces would be interpreted"


def test_healthchecks_gets_start_success_and_error():
    """Backrest's Healthchecks action appends /start and /fail itself
    (internal/hook/types/healthchecks.go), so one URL covers all three."""
    plan = CFG["plans"][0]
    hc = [h for h in plan["hooks"] if "actionHealthchecks" in h]
    assert len(hc) == 1
    conds = set(hc[0]["conditions"])
    assert {"CONDITION_SNAPSHOT_START", "CONDITION_SNAPSHOT_SUCCESS",
            "CONDITION_SNAPSHOT_ERROR"} <= conds
    assert hc[0]["actionHealthchecks"]["webhookUrl"].startswith("https://hc-ping.com/")


def test_repo_maintenance_errors_also_page():
    repo = CFG["repos"][0]
    conds = set().union(*(set(h["conditions"]) for h in repo["hooks"]
                          if "actionHealthchecks" in h))
    assert {"CONDITION_PRUNE_ERROR", "CONDITION_CHECK_ERROR"} <= conds


def test_uses_the_s3_compatible_endpoint_restic_recommends():
    assert CFG["repos"][0]["uri"].startswith("s3:https://s3."), \
        "restic docs: prefer B2's S3-compatible API over the native b2: backend"
    env = " ".join(CFG["repos"][0]["env"])
    assert "AWS_ACCESS_KEY_ID=" in env and "AWS_SECRET_ACCESS_KEY=" in env


# ---- no secrets shipped, every placeholder documented ----------------------

PLACEHOLDER = re.compile(r"REPLACE_WITH_[A-Z0-9_]+")


def test_every_secret_bearing_field_is_a_placeholder():
    repo = CFG["repos"][0]
    assert PLACEHOLDER.fullmatch(repo["password"]), "a real restic password is in the repo"
    for e in repo["env"]:
        k, _, v = e.partition("=")
        assert PLACEHOLDER.fullmatch(v), f"{k} carries a real value"
    for _, _, hooks in all_hooks():
        for h in hooks:
            url = h.get("actionHealthchecks", {}).get("webhookUrl", "")
            if url:
                assert PLACEHOLDER.search(url), "a real healthchecks UUID is in the config"


def test_every_placeholder_is_explained_in_the_setup_doc():
    """A token the doc does not mention is one the reader will miss at 3am."""
    tokens = set(PLACEHOLDER.findall(CFG_PATH.read_text())) | \
             set(PLACEHOLDER.findall(UNIT_PATH.read_text()))
    assert tokens, "no placeholders at all — did real values get committed?"
    missing = {t for t in tokens if t not in DOC}
    assert not missing, f"undocumented placeholder(s): {missing}"


# ---- the unit ----------------------------------------------------------------

def test_unit_file_is_well_formed_and_waits_for_docker():
    text = UNIT_PATH.read_text()
    for section in ("[Unit]", "[Service]", "[Install]"):
        assert section in text
    after = re.search(r"^After=(.*)$", text, re.M).group(1)
    assert "docker.service" in after, "the pre-snapshot hook runs docker exec"
    assert re.search(r"^User=REPLACE_WITH_USER$", text, re.M)
    assert re.search(r"^ExecStart=/usr/local/bin/backrest$", text, re.M)
    assert re.search(r"^Environment=BACKREST_PORT=127\.0\.0\.1:9898$", text, re.M), \
        "the UI must bind loopback only"
    assert "NoNewPrivileges=yes" in text
    assert "WantedBy=multi-user.target" in text
    assert "BACKREST_CONFIG=" in text and "BACKREST_DATA=" in text, \
        "pin the paths; do not depend on which HOME systemd resolves"


def test_the_doc_makes_the_restore_drill_non_optional():
    assert "Restore drill" in DOC
    assert "cmp " in DOC and "RESTORE DRILL PASSED" in DOC
    assert "not Done" in DOC
