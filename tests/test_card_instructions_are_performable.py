"""An instruction on the dashboard must correspond to a control that exists.

Twice in one night this project told the user to go and do something that
could not be done:

  * "set account_role to sharesAsset on your brokerages" — Firefly has no such
    value, so the option is not in the dropdown;
  * "tell it which of these aren't cash in Settings" — shipped onto the
    homepage while `finance.not_spendable` existed only as a default in the
    core service, with nothing in the UI able to write it.

Both are the same defect: an instruction whose target does not exist. It is
worse than a missing feature, because the reader burns time looking for
something that was never there and concludes the dashboard is lying.

These tests tie the runway card's instruction to the control that satisfies
it, so the two cannot drift apart again.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOME_JS = (ROOT / "gateway" / "static" / "home.js").read_text()
CORE = (ROOT / "services" / "core" / "app" / "main.py").read_text()


def test_the_runway_card_points_at_settings():
    """If this fails the wording changed; update the pairing below with it."""
    assert "aren't cash in Settings" in HOME_JS, (
        "the runway card's instruction moved — re-pair it with its control")


def test_settings_can_actually_write_not_spendable():
    """The control the instruction sends the reader to."""
    assert "runway-x" in HOME_JS, "no runway account picker in the settings modal"
    assert "not_spendable:" in HOME_JS, (
        "the settings modal never writes finance.not_spendable, so the "
        "instruction on the card cannot be carried out")


def test_the_setting_exists_on_the_service_that_stores_it():
    assert "not_spendable" in CORE, (
        "core does not carry finance.not_spendable, so a save would be dropped")


def test_saving_the_picker_preserves_the_rest_of_finance():
    """`finance` holds large_txn and low_balance, which this modal does not
    edit. A bare {not_spendable} patch would silently reset them."""
    i = HOME_JS.index("not_spendable:")
    window = HOME_JS[max(0, i - 400):i]
    assert "_financeSettings" in window, (
        "the finance patch does not spread the existing settings — saving the "
        "runway picker would drop large_txn and low_balance")


@pytest.mark.parametrize("dead_value", ["sharesAsset"])
def test_no_instruction_names_a_firefly_value_that_does_not_exist(dead_value):
    """Firefly's complete account_role vocabulary is defaultAsset,
    sharedAsset, savingAsset, ccAsset, cashWalletAsset. Anything else in
    user-facing text is an errand nobody can run."""
    for name in ("gateway/static/home.js", "services/assistant/app/runway.py",
                 "scripts/verify.sh"):
        text = (ROOT / name).read_text()
        for line in text.splitlines():
            if dead_value not in line:
                continue
            stripped = line.strip()
            assert stripped.startswith(("#", "//", "*")) or '"""' in text[:text.index(line)], (
                f"{name} names {dead_value} outside a comment: {stripped!r}")
