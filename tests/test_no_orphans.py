"""Nothing ships that no one can reach.

WHY THIS FILE EXISTS: `docs/PRODUCT_IDEAS.md` #17 found NINE features that were
built, tested, running and connected to nothing — a severity-tagged attention
feed computed on every request and rendered nowhere, a deadlines table readable
only from a page that had been demoted, a settings field labelled "Alert on
move >= 3%" that produced no alert. None of them failed. None of them errored.
They simply had no consumer, and nothing in the suite could tell.

A settings field that silently does nothing is worse than a missing one: you
configure it, you believe it is on, and you stop watching for the thing it was
supposed to catch.

WHAT THIS CAN AND CANNOT PROVE. It is a reference check, not a proof of
usefulness: it shows that a name appears somewhere outside the module that
defines it. It cannot tell that the reference does something meaningful — the
`move_threshold_pct` defect was a key the settings FORM wrote and round-tripped
while nothing ever read it, which a plain grep would have called consumed. That
is why anything reachable only in a way source text cannot show has to be
declared below with a reason, one line, at the moment it is added. The point is
not to be clever; it is to make "who consumes this?" a question you answer when
it is cheap, instead of one someone excavates a year later.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "services" / "core" / "app" / "main.py"


def source_files():
    """Everything a consumer could plausibly live in."""
    out = [p for p in (ROOT / "services").rglob("*.py") if "tests" not in p.parts]
    out += list((ROOT / "gateway" / "app").rglob("*.py"))
    out += list((ROOT / "gateway" / "static").glob("*.js"))
    out += list((ROOT / "gateway" / "static").glob("*.html"))
    return out


TEXT = {p: p.read_text() for p in source_files()}


# ── settings keys ──────────────────────────────────────────────────────────

def settings_keys():
    """Every leaf key in core's DEFAULT_SETTINGS, with its nesting path."""
    src = CORE.read_text()
    start = src.index("DEFAULT_SETTINGS")
    block = src[start:src.index("\n}\n", start) + 3]
    return sorted(set(re.findall(r'"([a-z_][a-z_0-9]*)":', block)))


# Keys whose consumer cannot be seen in source text. One line each, saying
# WHERE it is consumed and WHY a grep cannot see it. Adding a key here is a
# decision, which is the whole point.
SETTINGS_REACHED_ANOTHER_WAY = {
    "focus_presets": "served to the UI renamed: core /today emits it as study.presets",
    "water_presets": "served to the UI renamed: core /today emits it as water.presets",
    "score_weights": "iterated generically by compute_score; no per-key reference exists",
}


@pytest.mark.parametrize("key", settings_keys())
def test_every_settings_key_has_a_consumer(key):
    """A key defined in core and referenced nowhere else configures nothing."""
    if key in SETTINGS_REACHED_ANOTHER_WAY:
        pytest.skip(f"declared: {SETTINGS_REACHED_ANOTHER_WAY[key]}")
    users = [p for p, t in TEXT.items()
             if p != CORE and (f'"{key}"' in t or f"'{key}'" in t
                               or f".{key}" in t or f"{key}:" in t)]
    assert users, (
        f'DEFAULT_SETTINGS["{key}"] is referenced nowhere outside core. Either '
        f"wire a consumer, or declare it in SETTINGS_REACHED_ANOTHER_WAY with "
        f"the reason a grep cannot see it.")


def test_the_settings_allowlist_has_no_stale_entries():
    """A declared key that has since been deleted, or has gained a real
    consumer, should leave the allowlist so it keeps meaning something."""
    keys = set(settings_keys())
    for key in SETTINGS_REACHED_ANOTHER_WAY:
        assert key in keys, f"{key} is declared but no longer a settings key"


# ── service endpoints ──────────────────────────────────────────────────────

def endpoints():
    out = []
    for main in sorted((ROOT / "services").glob("*/app/main.py")):
        service = main.parts[-3]
        for m in re.finditer(
                r'@app\.(get|post|put|patch|delete|api_route)\(\s*"([^"]+)"',
                main.read_text()):
            out.append((service, m.group(1).upper(), m.group(2), main))
    return out


# Endpoints reachable in a way no source file can show. Same rule: one line,
# saying how.
ENDPOINTS_REACHED_ANOTHER_WAY = {
    ("gmail", "/auth/login"): "the browser navigates here to start OAuth consent",
    ("gmail", "/auth/callback"): "Google redirects the browser here after consent",
    ("gmail", "/sample"): "manual diagnostic, documented in docs/SETUP-GMAIL.md",
    ("gmail", "/sync-status"): "manual diagnostic; the home card reads mode/sync instead",
    ("firefly", "/audit"): "manual diagnostic, documented in docs/BUDGETS.md",
    ("stocks", "/quotes"): "manual diagnostic; the dashboard reads /portfolio",
    ("core", "/history"): "manual diagnostic; the dashboard reads /today",
}


@pytest.mark.parametrize(
    "service,method,path,main",
    [e for e in endpoints() if e[2] not in ("/", "/health")],
    ids=lambda v: v if isinstance(v, str) else "")
def test_every_endpoint_has_a_consumer(service, method, path, main):
    """An endpoint nothing calls is a feature no user can reach."""
    if (service, path) in ENDPOINTS_REACHED_ANOTHER_WAY:
        pytest.skip(f"declared: {ENDPOINTS_REACHED_ANOTHER_WAY[(service, path)]}")
    # A templated path is built dynamically by callers ("/capture/" + id), so
    # match on the literal prefix rather than the template.
    probe = path.split("{")[0].rstrip("/") or path
    users = [p for p, t in TEXT.items() if p != main and probe in t]
    assert users, (
        f"{service} {method} {path} is called from nowhere. Either wire a "
        f"consumer, or declare it in ENDPOINTS_REACHED_ANOTHER_WAY with the "
        f"reason a grep cannot see it.")


def test_the_endpoint_allowlist_has_no_stale_entries():
    live = {(s, p) for s, _, p, _ in endpoints()}
    for entry in ENDPOINTS_REACHED_ANOTHER_WAY:
        assert entry in live, f"{entry} is declared but no longer an endpoint"


# ── the nine from the inventory, pinned individually ───────────────────────
#
# The checks above are generic and would pass again if any of these regressed
# in a way that kept a stray reference alive. These name them.

HOME_JS = (ROOT / "gateway" / "static" / "home.js").read_text()
ASSISTANT = (ROOT / "services" / "assistant" / "app" / "main.py").read_text()


def test_the_attention_feed_reaches_the_home_screen():
    """core._nudges() — AUDIT.md §3's promised feed — was computed on every
    /today and the string "nudges" appeared nowhere outside core."""
    assert '"nudges"' in ASSISTANT, "the assistant drops the feed again"
    assert "renderAttention" in HOME_JS, "nothing renders the feed"


def test_deadlines_reach_the_home_screen():
    assert '"deadlines"' in ASSISTANT
    assert "renderDeadlines" in HOME_JS


def test_the_weekly_review_reaches_the_home_screen():
    assert "/weekly-review" in ASSISTANT
    assert "renderWeeklyReview" in HOME_JS


def test_sleep_has_a_control():
    assert "data-sleep" in HOME_JS, "no way to log sleep, so the column stays null"
    assert "/core/sleep" in HOME_JS


def test_the_move_threshold_produces_an_alert():
    """The alert must be COMPUTED and RENDERED.

    The first cut of this test asserted only that "pf-alert" appeared in
    home.js. It did -- inside a template assigned to `alertLine`, a variable
    that was never interpolated into any innerHTML. The markup existed, the
    alert was invisible, and the check was green: exactly the failure mode this
    whole file is about, reproduced inside the guard against it. Referencing a
    name is not the same as using it.
    """
    assert "portfolio_alerts" in ASSISTANT
    assert "pf-alert" in HOME_JS, "the alert markup is gone"
    assert "${alertLine}" in HOME_JS, \
        "alertLine is built but never interpolated, so the alert never renders"


def test_the_low_balance_floor_is_consumed():
    assert "low_balance_accounts" in ASSISTANT


def test_capture_can_write_more_than_one_kind():
    assert "cap-kind" in HOME_JS, "the UI only ever writes 'note' again"


def test_a_focus_session_can_be_labelled():
    assert "hx-focus-label" in HOME_JS, "the UI only ever writes 'Study' again"


def test_the_job_board_is_reachable_from_the_hub():
    """jobs.html was linked only from the legacy lounge, so demoting that page
    took the board offline with it."""
    assert "/jobs.html" in HOME_JS
