"""One concept, one key name, across every service boundary.

The completeness flag — "was this read of Firefly truncated?" — was published
under five different names at once: `window_complete` on /spending and /month,
`window.complete` on /cycle and /history, `month.complete` and
`figures_complete` inside the paycheck payload, and `complete` on /recurring.

That is not a style question. Reading the wrong one returns `None`, and `None`
is indistinguishable from "the read was fine" unless the caller happens to know
which spelling this particular endpoint chose. It cost a real misread: a
consumer queried /recurring for `window_complete`, got null next to
`absence_claims_suppressed: false`, and had to work out from scratch whether
the honesty guard had failed or the key was simply named something else.

So this file is a naming guard, not a behaviour test. The behaviour is proven
in services/*/tests; what these assert is that nobody can reintroduce a second
name for the same fact without the suite going red.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "window_complete"

PY_SOURCES = sorted(ROOT.glob("services/*/app/*.py"))
JS_SOURCES = sorted(ROOT.glob("gateway/static/*.js"))

# Producers of the flag, and the payload each one publishes it in. If a file
# is added here it must publish the canonical name; if one is removed the
# test that it publishes at all will fail loudly rather than silently pass.
PRODUCERS = {
    "services/firefly/app/main.py": 4,   # /spending, /month, /cycle, /history
    "services/budget/app/paycheck.py": 3,  # top level, month block, cycle block
    "services/budget/app/recurring.py": 1,
    "services/budget/app/engine.py": 1,
}


def _code_lines(path: Path):
    """Source with comment-only lines dropped.

    The rename is discussed by name in several comments — deliberately, so the
    next reader knows what the old spellings were. Those must not trip a guard
    that is about what the code PUBLISHES.
    """
    out = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
            continue
        out.append(line)
    return out


# A dict key or a .get() read spelling the concept some other way.
BANNED_PY = [
    (re.compile(r'["\']complete["\']\s*:'), '"complete" as a payload key'),
    (re.compile(r'\.get\(\s*["\']complete["\']'), 'reading "complete"'),
    (re.compile(r'figures_complete'), '"figures_complete"'),
]
BANNED_JS = [
    (re.compile(r'\.complete\b'), '.complete on a payload'),
    (re.compile(r'figures_complete'), 'figures_complete'),
]


@pytest.mark.parametrize("path", PY_SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_service_spells_completeness_any_other_way(path):
    for line in _code_lines(path):
        for pattern, what in BANNED_PY:
            assert not pattern.search(line), (
                f"{path.relative_to(ROOT)}: {what} — the flag is "
                f'"{CANONICAL}" everywhere. See docs/BUDGETS.md.\n  {line.strip()}')


@pytest.mark.parametrize("path", JS_SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_dashboard_reads_only_the_canonical_name(path):
    for line in _code_lines(path):
        for pattern, what in BANNED_JS:
            assert not pattern.search(line), (
                f"{path.relative_to(ROOT)}: {what} — read "
                f'"{CANONICAL}".\n  {line.strip()}')


@pytest.mark.parametrize("rel,count", sorted(PRODUCERS.items()))
def test_every_producer_still_publishes_the_flag(rel, count):
    """A guard that only forbids names would also pass if the flag vanished."""
    src = (ROOT / rel).read_text()
    published = len(re.findall(rf'["\']{CANONICAL}["\']\s*:', src))
    assert published >= count, (
        f"{rel} publishes {CANONICAL} {published} time(s), expected at least "
        f"{count} — did a payload stop carrying completeness at all?")


def test_the_firefly_window_object_no_longer_hides_it():
    """It used to sit inside `window` on two endpoints and at the top level on
    two others, which is exactly the trap: same payload shape, different depth."""
    src = (ROOT / "services/firefly/app/main.py").read_text()
    windows = re.findall(r'"window":\s*\{[^}]*\}', src, re.S)
    assert windows, "no window object found — this test is watching nothing"
    for w in windows:
        assert "complete" not in w, f"completeness is back inside `window`:\n{w}"


def test_consumers_read_the_canonical_name_from_every_upstream():
    """budget reads three different upstream payloads; all three must use it."""
    src = (ROOT / "services/budget/app/main.py").read_text()
    for upstream in ("month", "cycle", "hist"):
        assert re.search(rf'{upstream}\.get\("{CANONICAL}"', src), (
            f"budget stopped reading {CANONICAL} from `{upstream}`")
