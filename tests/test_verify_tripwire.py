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


# Files that name a card PRODUCT rather than an account. The distinction is
# real, and it is the whole distinction this file is about: `credits.py` says
# "platinum" because The Platinum Card is the thing whose benefits it lists,
# and it never sees a Firefly account at all. The rest are the wiring that
# reaches that service by name.
#
# One line each, saying WHY the word is there. Adding a path here is a
# decision, and the companion test below is what stops it being a loophole.
CARD_PRODUCT_FILES = {
    "services/amex/app/credits.py":
        "the benefit catalogue itself; the card is its subject, not its input",
    "services/amex/app/main.py":
        "that service's own routes, table name and card query parameter",
    "services/assistant/app/main.py": "AMEX_URL, and the fan-out that calls it",
    "services/assistant/app/dashboard.py": "amex_brief reshapes that service's reply",
    "gateway/app/registry.py": "the sub-app entry that gives it a route and a tile",
    "gateway/static/home.js": "renders the card, and badges PLAT vs GOLD",
    "gateway/static/app.js": "the full credit list behind the tile, same badge",
}

# The mechanical shape of name matching: case-folding a string and looking for
# a substring in it. Comparing against our OWN catalogue key (`c.card ===
# "platinum"`) is deliberately not here — that is a value this repo wrote, not
# a label the user can edit in Firefly.
MATCHING_CONSTRUCTS = (
    ".lower()", ".upper()", ".casefold()", ".tolowercase()", ".touppercase()",
    "startswith", "endswith", ".includes(", ".match(", "re.search", "re.match",
)


@pytest.mark.parametrize("word", CARD_WORDS)
def test_no_shipped_module_matches_on_card_names(word):
    """A service that name-matches accounts is deciding money from a string
    the user can edit in Firefly."""
    offenders = [str(p.relative_to(ROOT)) for p in _code_files()
                 if word in p.read_text().lower()
                 and str(p.relative_to(ROOT)) not in CARD_PRODUCT_FILES]
    assert offenders == [], (
        f"{word!r} appears in shipped code: {offenders}. The runway "
        "classification must come from account_role, Firefly's liability "
        "type, or explicit configuration — never from an account's name.")


@pytest.mark.parametrize("path", sorted(CARD_PRODUCT_FILES))
def test_allowed_files_name_a_card_but_never_match_on_one(path):
    """The check that keeps the allowlist above from becoming the loophole.

    An exemption that only says "trust me" is how a boundary dies. These files
    may SAY "platinum" — they are about the Platinum Card — but none of them
    may case-fold a string and hunt for it, which is the shape the runway
    defect took and the one thing verify.sh is allowed to do.

    WHAT THIS CAN AND CANNOT PROVE. It catches the mechanical form, within a
    few lines of the word. It could not see a card word assembled from pieces,
    or compared twenty lines from where it was defined. It is a tripwire on
    the exemption, not a proof about it — which is why the exemption is six
    named files and not a directory glob.
    """
    lines = (ROOT / path).read_text().lower().splitlines()
    hits = []
    for i, line in enumerate(lines):
        if not any(w in line for w in CARD_WORDS):
            continue
        window = "\n".join(lines[max(0, i - 2):i + 3])
        for construct in MATCHING_CONSTRUCTS:
            if construct in window:
                hits.append(f"{path}:{i + 1} {construct} near {line.strip()[:60]!r}")
    assert hits == [], (
        f"{path} is allowlisted because it NAMES a card, but it is matching on "
        f"one: {hits}. If this is classification, it belongs in account_role, "
        "not in a string comparison.")


def test_the_card_product_allowlist_has_no_stale_entries():
    """A path that no longer contains a card word, or no longer exists, is an
    exemption still standing over nothing — and the next file to land at that
    path inherits it silently."""
    stale = []
    for path in sorted(CARD_PRODUCT_FILES):
        p = ROOT / path
        if not p.exists():
            stale.append(f"{path} (gone)")
        elif not any(w in p.read_text().lower() for w in CARD_WORDS):
            stale.append(f"{path} (no card word left)")
    assert stale == [], (
        f"stale entries in CARD_PRODUCT_FILES: {stale}. Delete them — an "
        "exemption should expire with the reason it was written for.")


def test_verify_sh_is_not_importable_by_anything():
    """The property that makes the tripwire safe. A .sh file cannot be
    imported by a Python service or required by the gateway's JS; if it ever
    grew an importable twin, this is where that would show up."""
    twins = [p for p in ROOT.glob("**/*verify*")
             if p.suffix in (".py", ".js")
             and "test" not in p.name and "__pycache__" not in p.parts]
    assert twins == [], f"verify logic became importable: {twins}"
