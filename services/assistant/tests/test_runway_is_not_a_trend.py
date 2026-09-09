"""A bound is not a measurement, and must never be tracked over time.

While `certain` is False the runway is a RANGE, and `months_low` is the floor
of that range — the balance of whatever the ledger has deliberately confirmed
is cash. On the real ledger that is Marcus plus a $0 Cash wallet, so the floor
rests on a single account.

That is fine as a bound and dangerous as a trend. Moving $15k from Marcus to
Chase to cover a month — an ordinary thing to do — drops the floor from ~7.9
months to ~2.1 while actual liquidity is unchanged. Nothing has got worse; a
bound moved because money crossed between two accounts the ledger classifies
differently.

So an open range may be DISPLAYED and must not be alerted on, trended,
compared week-over-week, or turned into a nudge. The natural next feature
request is "tell me when my runway drops", and granting it against `months_low`
would silently convert a bound into an alarm that fires on internal transfers.

These tests pin the boundary while it is still cheap to hold.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

_rw = load_service_module("assistant_runway_trend", "services/assistant/app/runway.py")
cash_runway = _rw.cash_runway

ROOT = Path(__file__).resolve().parents[3]
FRESH = {"window_complete": True, "ingest_days": 1}


def acct(name, balance, role):
    return {"name": name, "balance": balance, "role": role, "kind": "asset"}


LEDGER = [acct("Marcus", 20500.0, "savingAsset"),
          acct("Cash wallet", 0.0, "cashWalletAsset"),
          acct("Chase Checking", 13000.0, "defaultAsset"),
          acct("Fidelity", 82000.0, "defaultAsset")]
BURN = 2593.11


def test_an_internal_transfer_moves_the_floor_without_changing_liquidity():
    """The instability itself, stated as a fact rather than a bug. It is why
    the floor must not be trended — not a reason to change the floor."""
    before = cash_runway(LEDGER, BURN, 30, freshness=FRESH)
    moved = [acct("Marcus", 5500.0, "savingAsset"),
             acct("Cash wallet", 0.0, "cashWalletAsset"),
             acct("Chase Checking", 28000.0, "defaultAsset"),
             acct("Fidelity", 82000.0, "defaultAsset")]
    after = cash_runway(moved, BURN, 30, freshness=FRESH)

    assert before["certain"] is False and after["certain"] is False
    # The floor falls by more than half...
    assert before["months_low"] > 7.5 and after["months_low"] < 2.5
    # ...while the total and therefore the ceiling are untouched.
    assert before["months_high"] == after["months_high"]


def test_a_configured_ledger_is_stable_under_the_same_transfer():
    """Once the ambiguity is resolved the figure stops moving for this reason,
    which is the real answer to the instability."""
    cfg = ["Fidelity"]
    before = cash_runway(LEDGER, BURN, 30, freshness=FRESH, not_spendable=cfg)
    moved = [acct("Marcus", 5500.0, "savingAsset"),
             acct("Cash wallet", 0.0, "cashWalletAsset"),
             acct("Chase Checking", 28000.0, "defaultAsset"),
             acct("Fidelity", 82000.0, "defaultAsset")]
    after = cash_runway(moved, BURN, 30, freshness=FRESH, not_spendable=cfg)
    assert before["certain"] is after["certain"] is True
    assert before["months"] == after["months"]


def _shipped_python():
    for p in (ROOT / "services").glob("**/*.py"):
        if "test" in p.parts or "__pycache__" in p.parts or p.name.startswith("test_"):
            continue
        yield p


@pytest.mark.parametrize("field", ["months_low", "months_high"])
def test_only_the_runway_module_computes_the_bounds(field):
    """If another service starts reading the bounds, it is almost certainly to
    compare or alert on them. The payload is the boundary: consumers get
    `months`, which is null precisely when comparison would be meaningless."""
    readers = [str(p.relative_to(ROOT)) for p in _shipped_python()
               if field in p.read_text() and p.name != "runway.py"]
    assert readers == [], (
        f"{field} is read outside runway.py by {readers}. An open range is a "
        "bound, not a measurement — it must not be alerted on, trended, or "
        "compared over time. Use `months`, which is null while the range is "
        "open, or resolve the ambiguity with finance.not_spendable.")


def test_runway_does_not_reach_observations_or_nudges():
    """`_money()` publishes runway for display. Nothing that generates advice
    may consume it while the range is open."""
    main = (ROOT / "services" / "assistant" / "app" / "main.py").read_text()
    for line in main.splitlines():
        low = line.lower()
        if "runway" not in low or low.strip().startswith("#"):
            continue
        assert not any(w in low for w in ("observation", "nudge", "alert",
                                          "trend", "compare")), (
            f"runway reached advice-generating code: {line.strip()!r}")
