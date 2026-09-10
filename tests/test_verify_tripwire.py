"""The runway name-tripwire must stay a diagnostic and never become a rule.

`scripts/verify.sh` FAILs if an account whose NAME looks like a credit card
("card", "amex", "visa", "discover", "platinum", "credit") is counted as
spendable cash. That is name matching, which the runway design refuses: an
account rename would silently change a money figure, and guessing from names
is exactly what `account_role` exists to avoid.

It is acceptable in verify.sh and nowhere else, and the reason is placement
rather than restraint. verify.sh is a shell script run against the live box:
it can shout, and it cannot move a number. The moment that word list lives
somewhere a service can import, it stops being a diagnostic and becomes the
fourth failed classification rule.

This test pins that boundary. It exists because the tripwire is genuinely
useful — the failure it watches for was invisible to every unit test in this
repo, because it lived in real data — and useful things migrate.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Brand tokens only. "card", "credit" and "discover" are all ordinary English
# — `recurring.py` and `runway.py` both use "discover" as a verb — so asserting
# on them finds prose, not name matching. That collision is itself worth
# noticing: a tripwire keyed on "discover" will fire on any account with the
# word in its name, which is the fragility that keeps name matching out of the
# services in the first place.
CARD_WORDS = ("amex", "platinum", "visa", "mastercard")
SHIPPED = [ROOT / "services", ROOT / "gateway"]


def _code_files():
    for base in SHIPPED:
        for pat in ("**/*.py", "**/*.js"):
            for p in base.glob(pat):
                if "test" in p.parts or "__pycache__" in p.parts:
                    continue
                if p.name.startswith("test_") or p.name.endswith(".test.js"):
                    continue
                yield p


def test_the_tripwire_lives_in_verify_sh():
    """If it moved or was deleted, the rest of this file passes vacuously."""
    text = (ROOT / "scripts" / "verify.sh").read_text()
    assert all(w in text for w in ("amex", "platinum", "visa")), (
        "the card-name tripwire is no longer in verify.sh — either it was "
        "removed, or it moved somewhere it can influence a figure")
    assert "runway inputs" in text


@pytest.mark.parametrize("word", CARD_WORDS)
def test_no_shipped_module_matches_on_card_names(word):
    """A service that name-matches accounts is deciding money from a string
    the user can edit in Firefly."""
    offenders = [str(p.relative_to(ROOT)) for p in _code_files()
                 if word in p.read_text().lower()]
    assert offenders == [], (
        f"{word!r} appears in shipped code: {offenders}. The runway "
        "classification must come from account_role, Firefly's liability "
        "type, or explicit configuration — never from an account's name.")


def test_verify_sh_is_not_importable_by_anything():
    """The property that makes the tripwire safe. A .sh file cannot be
    imported by a Python service or required by the gateway's JS; if it ever
    grew an importable twin, this is where that would show up."""
    twins = [p for p in ROOT.glob("**/*verify*")
             if p.suffix in (".py", ".js")
             and "test" not in p.name and "__pycache__" not in p.parts]
    assert twins == [], f"verify logic became importable: {twins}"
