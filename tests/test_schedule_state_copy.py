"""Every state the week grid can be in has something to say about itself.

WHY THIS FILE EXISTS: `schedule_state()` gained a fifth answer, `not_configured`
— gmail's /internal/token now requires a shared secret and a deployment without
one cannot read the calendar at all (SCRUM-114). The backend was careful about
it: the state is deliberately NOT folded into `disconnected` (which sends you
to re-consent Google) or `unknown` (whose wording invites waiting), because
neither repair can work and a wrong instruction is worse than a vague one.

The browser never got the memo. `home.js` looks the state up in a CAVEAT map,
and a state that is not in the map renders no caveat at all — so a week with
every Google event missing looked exactly like a complete, healthy week. The
one thing the whole state machine exists to prevent, reintroduced by adding a
state to one side of the wire.

That is a class of bug, not an incident: it recurs every time someone adds a
state. So this guard is on the wire itself. Add a sixth state tomorrow and the
suite goes red until the browser can say what it means.

Sibling of tests/test_wire_names.py, and there for the same reason: the
behaviour is proven in services/assistant/tests, what is asserted here is that
the two halves cannot drift apart in silence.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "services" / "assistant" / "app" / "dashboard.py"
HOME_JS = ROOT / "gateway" / "static" / "home.js"

# "ok" is the one state that is meant to render no caveat: the week is
# complete and saying so twice is noise. Every other state is an admission
# that the grid may be missing something, and has to be worded somewhere.
NO_CAVEAT_NEEDED = {"ok"}

# "unreachable" is worded too, just earlier: home.js short-circuits it before
# the grid is built, because there is no grid to caveat. That exemption is
# asserted below rather than assumed, so it cannot quietly become a hole.
HANDLED_BEFORE_THE_GRID = {"unreachable"}


def _declared_states() -> tuple[str, ...]:
    """The backend's own vocabulary, read from the tuple it publishes."""
    source = DASHBOARD.read_text()
    match = re.search(r"SCHEDULE_STATES\s*=\s*\(([^)]*)\)", source, re.S)
    assert match, "SCHEDULE_STATES is gone or reshaped — this guard is stale"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def _returned_states() -> set[str]:
    """What `schedule_state` can actually hand back, from its return lines."""
    source = DASHBOARD.read_text()
    body = source[source.index("def schedule_state("):source.index("def portfolio_state(")]
    return set(re.findall(r'return\s+"([^"]+)"', body))


def _caveat_keys() -> set[str]:
    """The states home.js has copy for."""
    source = HOME_JS.read_text()
    start = source.index("const CAVEAT = {")
    end = source.index("const cv = CAVEAT[", start)
    return set(re.findall(r"^\s{6}(\w+):\s*\{", source[start:end], re.M))


def test_the_vocabulary_is_still_where_this_guard_looks():
    """A scan that matches nothing passes vacuously."""
    states = _declared_states()
    assert len(states) >= 5, states
    assert "ok" in states and "not_configured" in states, states
    assert len(_caveat_keys()) >= 4, _caveat_keys()


@pytest.mark.parametrize("state", sorted(set(_declared_states())
                                         - NO_CAVEAT_NEEDED
                                         - HANDLED_BEFORE_THE_GRID))
def test_every_incomplete_state_says_why(state):
    """No entry in CAVEAT means no caveat on screen, which means a week that
    might be missing everything reads as a week with nothing in it."""
    assert state in _caveat_keys(), (
        f"schedule_state can return {state!r} and gateway/static/home.js has no "
        f"copy for it, so the week grid renders as if it were complete. Add it "
        f"to the CAVEAT map with wording that names the repair.")


def test_the_backend_returns_nothing_the_vocabulary_does_not_list():
    """The reverse drift: a state invented in the function body and never
    added to SCHEDULE_STATES would slip past the test above entirely."""
    undeclared = _returned_states() - set(_declared_states())
    assert not undeclared, (
        f"schedule_state returns {sorted(undeclared)}, which SCHEDULE_STATES "
        f"does not list — nothing downstream knows those states exist")


def test_the_browser_invents_no_states_of_its_own():
    """And copy for a state that can never happen is dead weight that reads
    like coverage."""
    extra = _caveat_keys() - set(_declared_states())
    assert not extra, (
        f"home.js has caveat copy for {sorted(extra)}, which schedule_state "
        f"can never return")


def test_a_configuration_fault_does_not_tell_you_to_reconnect_google():
    """The specific wrong instruction this state was added to avoid. Setting
    a shared secret on the box is not something a Google consent screen can
    do, and offering that link is advice that cannot work."""
    source = HOME_JS.read_text()
    start = source.index("not_configured: {")
    block = source[start:source.index("},", start)]
    assert "fix: false" in block, (
        "the not_configured caveat offers the Connect-Google repair, which "
        "cannot fix a missing FC_INTERNAL_SECRET")
    assert "FC_INTERNAL_SECRET" in block, (
        "the caveat should name the thing that is actually missing")


@pytest.mark.parametrize("state", sorted(HANDLED_BEFORE_THE_GRID))
def test_the_exempt_states_really_are_handled_somewhere_else(state):
    """The exemption above is only honest while the early return exists."""
    source = HOME_JS.read_text()
    grid = source[:source.index("const CAVEAT = {")]
    assert f'week.state === "{state}"' in grid, (
        f"{state!r} is exempt from needing caveat copy on the claim that "
        f"home.js handles it before the grid is drawn — it no longer does")
