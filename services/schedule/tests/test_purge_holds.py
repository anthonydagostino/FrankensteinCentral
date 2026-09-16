"""What may be deleted from a real Google Calendar, and what may not.

WHY THIS FILE EXISTS. Anthony, 2026-09-16: "my calendar has 3 entries for the
same interview, just remove anything not on my calendar." The app had put them
there. When an availability thread was `pending`, the assistant created ONE
EVENT PER PROPOSED SLOT and pushed every one to Google — so offering an
interviewer three times put three tentative entries on his phone.

The assistant no longer makes them, and `/events/purge-holds` removes the ones
already out there. That endpoint issues DELETEs against a real calendar, which
makes `is_speculative_hold` the most dangerous function in this service: every
row it wrongly returns True for is one of Anthony's actual appointments, gone,
with no undo.

So the tests below are mostly about what must SURVIVE.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from conftest import load_service_module  # noqa: E402

holds = load_service_module("schedule_holds", "services/schedule/app/holds.py")
hold = holds.is_speculative_hold


def row(**kw):
    base = {"source": "gmail", "status": "pending",
            "external_id": "thread:abc:slot:2026-09-20T14:00", "title": "Interview — Acme"}
    return {**base, **kw}


# --- what gets deleted ------------------------------------------------------

def test_a_proposed_slot_is_a_hold():
    assert hold(row()) is True


def test_a_countered_slot_is_a_hold_too():
    """A time they offered back that you have not accepted is still not a
    commitment."""
    assert hold(row(status="countered")) is True


def test_all_three_slots_of_one_thread_are_holds():
    slots = [row(external_id=f"thread:abc:slot:2026-09-2{i}T14:00") for i in range(3)]
    assert [hold(s) for s in slots] == [True, True, True]


# --- what must survive, which is the point ----------------------------------

def test_a_tentative_google_event_is_not_a_hold():
    """THE TRAP. Google calls an unaccepted invite "tentative", and
    sync_from_calendar stores anything that is not "confirmed" as status
    'pending'. So a real entry on Anthony's own calendar looks exactly like a
    hold on two of the three conditions. Only `source` tells them apart."""
    real = row(source="google_calendar", status="pending",
               external_id="gcal:abc123", title="Dentist")
    assert hold(real) is False


def test_a_confirmed_interview_is_not_a_hold():
    assert hold(row(status="confirmed", external_id="thread:abc:confirmed")) is False


def test_a_manually_added_event_is_not_a_hold():
    assert hold(row(source="manual", external_id="manual:uuid-here")) is False


def test_a_gmail_event_that_is_not_a_slot_is_not_a_hold():
    """`:slot:` is the exact shape the assistant minted. A gmail-sourced event
    with any other id was created by some other path and is not ours to
    delete."""
    assert hold(row(external_id="thread:abc:confirmed")) is False
    assert hold(row(external_id="mail:12345")) is False


def test_a_declined_row_is_left_alone():
    """Already handled; deleting it again would just be another API call
    against a calendar for no reason."""
    assert hold(row(status="declined")) is False


@pytest.mark.parametrize("bad", [None, {}, {"source": "gmail"}, "not a row", 42,
                                 {"source": "gmail", "status": "pending",
                                  "external_id": None}])
def test_a_malformed_row_is_never_a_hold(bad):
    """The failure has to be in the safe direction. A row this function cannot
    read is a row it must not delete."""
    assert hold(bad) is False


def test_every_condition_is_load_bearing():
    """Drop any ONE of the three and something that must survive gets deleted.
    If this ever passes with a condition removed, the condition was decoration.
    """
    survivors = [
        row(source="google_calendar"),                      # only source differs
        row(status="confirmed"),                            # only status differs
        row(external_id="thread:abc:confirmed"),            # only the id differs
    ]
    assert [hold(s) for s in survivors] == [False, False, False]
