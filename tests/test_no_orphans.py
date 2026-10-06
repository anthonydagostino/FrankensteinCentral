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
SETTINGS_REACHED_ANOTHER_WAY: dict[str, str] = {
    # Empty since 2026-10-06: every remaining settings key has a consumer a
    # grep can see. The three that used to be here (focus and water presets,
    # score weights) went with the habit tracker.
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
    # The gateway is a service too. It was never in this list, so every route
    # it grew — including SCRUM-98's /login, /logout and /api/auth/status —
    # would have shipped with no consumer check at all. Found the hard way: a
    # regex was widened to see `@router.` routes and nothing changed, because
    # the file it was meant to see was never opened.
    mains = sorted((ROOT / "services").glob("*/app/main.py"))
    mains += sorted(p for p in (ROOT / "gateway" / "app").glob("*.py")
                    if p.name != "__init__.py")
    for main in mains:
        service = "gateway" if "gateway" in main.parts else main.parts[-3]
        for m in re.finditer(
                # `router` as well as `app`: gateway/app/auth.py mounts its
                # routes on an APIRouter, and a guard that only saw `@app.`
                # would have let every one of them ship unconsumed.
                r'@(?:app|router)\.(get|post|put|patch|delete|api_route)\(\s*"([^"]+)"',
                main.read_text()):
            out.append((service, m.group(1).upper(), m.group(2), main))
    return out


# Endpoints reachable in a way no source file can show. Same rule: one line,
# saying how.
ENDPOINTS_REACHED_ANOTHER_WAY = {
    ("gmail", "/auth/login"): "the browser navigates here to start OAuth consent",
    ("gmail", "/auth/callback"): "Google redirects the browser here after consent",
    ("gmail", "/auth/finish"): (
        "posted by the form on /auth/login, whose action is the RELATIVE "
        "'finish' so the page works both directly and behind the gateway "
        "proxy — a grep for the path finds nothing. It is the rescue for the "
        "callback landing on a browser that is not on the box; "
        "services/gmail/tests/test_sync.py exercises it end to end."),
    ("gmail", "/sample"): "manual diagnostic, documented in docs/SETUP-GMAIL.md",
    ("gmail", "/sync-status"): "manual diagnostic; the home card reads mode/sync instead",
    ("firefly", "/audit"): "manual diagnostic, documented in docs/BUDGETS.md",
    ("firefly", "/freshness"): (
        "read by scripts/firefly-import.sh (a host cron script, outside the "
        "source tree this test scans) before and after each import to judge "
        "whether rows entered; tests/test_firefly_import.py exercises it"),
    ("stocks", "/quotes"): "manual diagnostic; the dashboard reads /portfolio",
    ("gmail", "/refresh"): (
        "manual trigger (curl -X POST) to poll Gmail now instead of waiting "
        "for GMAIL_REFRESH_SECONDS; the inbox card that had a button for it "
        "left the home screen on 2026-10-06"),
    ("assistant", "/sync"): (
        "manual trigger of the calendar sync (curl -X POST); "
        "_auto_sync_loop calls the same function on AUTO_SYNC_SECONDS."),
    ("assistant", "/notify"): (
        "manual: send yourself a message on the configured channel "
        "(curl -X POST '/api/assistant/notify?text=...'); the sync sends its "
        "digest through notify.send directly. docs/SETUP-NOTIFICATIONS.md"),
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


# ── the ones that survive the 2026-10-06 cut, pinned individually ─────────
#
# The checks above are generic and would pass again if any of these regressed
# in a way that kept a stray reference alive. These name them. Most of the
# nine PRODUCT_IDEAS #17 found (the attention feed, deadlines, the weekly
# review, the "since" block, sleep, capture kinds, focus labels, the
# low-balance floor) were wired up and then removed along with the cards they
# fed — a feature nobody reads is not improved by being reachable.

HOME_JS = (ROOT / "gateway" / "static" / "home.js").read_text()
ASSISTANT = (ROOT / "services" / "assistant" / "app" / "main.py").read_text()


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


def test_the_job_board_is_gone_and_unreferenced():
    """jobs.html was a 554-line hand-maintained comparison of four job offers
    (SCRUM-140). Retired on Anthony's instruction; a dangling link to it would
    be a 404 on the home screen."""
    assert not (ROOT / "gateway" / "static" / "jobs.html").exists()
    assert "/jobs.html" not in HOME_JS
    assert "jobs.html" not in (ROOT / "gateway" / "static" / "index.html").read_text()


def test_the_retired_cards_are_really_gone():
    """The removal was the point. A renderer that quietly survives in home.js
    is dead weight the next reader has to understand, and a payload key the
    assistant still builds is a service fan-out nobody consumes."""
    for name in ("renderAttention", "renderDeadlines", "renderWeeklyReview",
                 "renderSince", "renderDoNext", "renderInbox", "renderToday",
                 "renderHealth", "renderCapture", "startFocus", "openPalette"):
        assert name not in HOME_JS, f"{name} is still in home.js"
    for key in ('"nudges"', '"deadlines"', '"weekly_review"', 'data["since"]',
                '"do_next"', '"big3"', '"captures"', '"score"', '"health"'):
        assert key not in ASSISTANT, f"the assistant still builds {key}"
