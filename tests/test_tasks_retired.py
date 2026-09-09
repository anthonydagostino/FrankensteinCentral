"""The tasks service is retired, and retiring it did not lose anything.

docs/PRODUCT_IDEAS.md #6: four systems answered "what's open" — Jira, the
`tasks` service, Big 3 and quick capture. Big 3 (today's commitment) and
capture (the scratchpad) have distinct jobs and stay. `tasks` and Jira were the
same tool twice, and the one actually maintained is Jira.

The failure modes of a retirement are (a) quietly deleting data, (b) removing a
real capability along with the duplicate one, and (c) the thing creeping back.
This file pins all three.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()
REGISTRY = (ROOT / "gateway" / "app" / "registry.py").read_text()
ASSISTANT = (ROOT / "services" / "assistant" / "app" / "main.py").read_text()
CORE = (ROOT / "services" / "core" / "app" / "main.py").read_text()
HOME_JS = (ROOT / "gateway" / "static" / "home.js").read_text()


def test_the_service_is_gone():
    assert not (ROOT / "services" / "tasks").exists()
    assert 'key="tasks"' not in REGISTRY, "still a registered sub-app"
    assert not re.search(r'^\s{2}tasks:\s*$', COMPOSE, re.M), "still a compose service"


def test_nothing_still_calls_it():
    """A live caller would 502 on every dashboard load."""
    for name, src in (("assistant", ASSISTANT), ("core", CORE)):
        assert "TASKS_URL" not in src, f"{name} still calls the retired service"
    assert "/tasks/tasks" not in HOME_JS, "the command palette still writes to it"


def test_the_data_is_not_deleted_anywhere():
    """THE ONE THAT MATTERS. The `tasks` table lives in the SHARED Postgres
    database, so removing the container leaves every row exactly where it is —
    readable with SQL, and restorable by reinstating the service from git
    history. Retiring must never become deleting."""
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts:
            continue
        src = path.read_text()
        assert not re.search(r'DROP\s+TABLE\s+(IF\s+EXISTS\s+)?tasks', src, re.I), \
            f"{path} drops the tasks table"
    for path in ROOT.rglob("*.sh"):
        assert "DROP TABLE" not in path.read_text().upper() or "tasks" not in path.read_text(), \
            f"{path} may drop the tasks table"


def test_the_capability_tess_had_still_exists():
    """Tess turned deadline emails into to-dos. That was a SECOND copy: the
    same loop, over the same `category == "deadline"` mails, already writes a
    `deadlines` row with the same mail:{id} key — and deadlines are on the home
    screen. Removing the duplicate must not remove the original."""
    assert "_add_deadline" in ASSISTANT
    assert 'e.get("category") == "deadline"' in ASSISTANT, \
        "the deadline-email path went with the duplicate"
    assert '"deadlines"' in ASSISTANT, "deadlines no longer reach the home screen"


def test_big3_and_capture_are_untouched():
    """The two that were never duplicates."""
    assert "big3" in CORE and "captures" in CORE
    assert "renderCapture" in HOME_JS


def test_an_open_task_count_is_absent_rather_than_zero():
    """We can no longer know it, and `0 open tasks` is a claim, not a blank —
    the rule docs/BUDGETS.md enforces one layer over."""
    assert "open_tasks" not in ASSISTANT, "a count we cannot obtain is still reported"
    assert '"tasks": {"open"' not in CORE


def test_the_score_component_key_is_left_alone():
    """`score_weights.tasks` has always been Big 3, not the tasks service —
    `comp["tasks"] = big3_done / big3_total`. Renaming the KEY would silently
    reset every saved weight, so only the UI LABEL changed."""
    assert '"tasks": 20' in CORE or "'tasks': 20" in CORE, \
        "the saved score weight key was renamed; existing settings would reset"
    assert "Tasks/Big3" not in HOME_JS, "the misleading label survived"


def test_the_backlog_has_a_door_and_no_url_is_invented():
    """Unset must send you to Settings, never to a guessed board."""
    assert '"links": {"jira": ""}' in CORE, "no setting for where the backlog is"
    assert "openJira" in HOME_JS
    assert "s-jira" in HOME_JS, "no way to set the URL"
    assert "atlassian.net/jira" not in CORE, "a URL was invented as a default"
