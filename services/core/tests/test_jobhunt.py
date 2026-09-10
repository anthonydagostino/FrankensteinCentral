"""Job-hunt research moves out of the browser (SCRUM-131).

The page saved per key into localStorage. The server keeps that shape — one
row per key — so two devices editing different companies never overwrite each
other, and a reset is a delete the server sees. These hold the rules that
decide what may be stored, without a database.

All fixture data is synthetic; the repo is public.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

jh = load_service_module("core_jobhunt", "services/core/app/jobhunt.py")


def test_the_pages_real_key_shapes_are_accepted():
    """The three shapes jobs.html actually writes: a factor weight, a
    per-company factor score, and a per-company field."""
    up, dele = jh.validate_entries({
        "jobhunt_rank_weight_remote": "3",
        "jobhunt_rank_score_acme_pay": "-2",
        "jobhunt_acme_pros": '["fully remote"]',
        "jobhunt_acme_notes": "recruiter said Thursday",
        "jobhunt_acme_floor": "120000",
    })
    assert len(up) == 5 and dele == []


def test_null_is_a_delete_not_an_empty_value():
    """Reset buttons remove keys. Storing "" instead would make the page
    render an empty list where the research defaults belong."""
    up, dele = jh.validate_entries({"jobhunt_acme_pros": None,
                                    "jobhunt_acme_cons": '["x"]'})
    assert dele == ["jobhunt_acme_pros"]
    assert up == {"jobhunt_acme_cons": '["x"]'}


def test_an_empty_string_is_stored_not_deleted():
    """Clearing the notes box is a real edit. "No notes" must survive a
    reload as no notes, not snap back to the pre-filled default."""
    up, dele = jh.validate_entries({"jobhunt_acme_notes": ""})
    assert up == {"jobhunt_acme_notes": ""} and dele == []


@pytest.mark.parametrize("bad", [
    "seen_snapshot",                # another table's namespace
    "jobhunt",                      # no separator
    "jobhunt_",                     # nothing after it
    "jobhunt_Acme_pros",            # uppercase
    "jobhunt_acme pros",            # whitespace
    "jobhunt_acme/../pros",         # path-looking
    "jobhunt_" + "a" * 121,         # too long
    "",
])
def test_anything_outside_the_namespace_is_refused(bad):
    with pytest.raises(ValueError):
        jh.validate_entries({bad: "1"})


def test_one_bad_key_refuses_the_whole_batch():
    """A partial write would leave the page believing something was saved
    that was not."""
    with pytest.raises(ValueError):
        jh.validate_entries({"jobhunt_acme_pros": "ok", "nope": "x"})


@pytest.mark.parametrize("v", [3, 1.5, [], {}, True])
def test_a_value_must_be_a_string(v):
    """The page stores input.value strings and JSON-encoded lists. A bare
    number or object arriving here is a caller bug, not data."""
    with pytest.raises(ValueError):
        jh.validate_entries({"jobhunt_acme_floor": v})


def test_an_oversized_value_is_refused_and_the_limit_itself_is_allowed():
    with pytest.raises(ValueError):
        jh.validate_entries({"jobhunt_acme_notes": "x" * (jh.MAX_VALUE + 1)})
    up, _ = jh.validate_entries({"jobhunt_acme_notes": "x" * jh.MAX_VALUE})
    assert len(up["jobhunt_acme_notes"]) == jh.MAX_VALUE


def test_too_many_entries_is_refused():
    big = {f"jobhunt_k{i}": "v" for i in range(jh.MAX_ENTRIES + 1)}
    with pytest.raises(ValueError):
        jh.validate_entries(big)


@pytest.mark.parametrize("bad", [None, [], "x", 3])
def test_entries_must_be_an_object(bad):
    with pytest.raises(ValueError):
        jh.validate_entries(bad)
