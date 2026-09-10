"""/ask must never state a figure it did not read.

PRODUCT_IDEAS #9's acceptance signal, verbatim: "ask it something whose data
source is down, and it tells you the source is down instead of guessing."

That is what these assert, intent by intent. Before this, `_get()` returned
`{}` for a service that never answered, so every intent rendered the outage as
a confident zero — "You've spent $0 of $0 (0%), $0 left." was what a down
budget service looked like, and it is indistinguishable from a real answer.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

# Every service names its package `app`, so importing `app.answers` here would
# shadow budget's `app` for the rest of the run — a collision that only shows
# up depending on collection order, which is the worst kind.
answers = load_service_module("assistant_answers", "services/assistant/app/answers.py")

LIVE = {
    "budget": {"total_spent": 312.0, "total_budget": 900.0, "percent_used": 35,
               "remaining": 588.0, "over_budget": ["Dining"]},
    "emails": {"emails": [{"subject": "Offer", "from": "HR <hr@co.com>"}]},
    "tasks": {"open": 3, "top": ["file taxes", "call bank"]},
    "finance": {"upcoming": [{"name": "Rent", "amount": 1400, "days_until": 3}],
                "monthly_total": 2100.0},
    "powerbuy": {"summary": {"expected_profit": 240.0, "unpaid_count": 2,
                             "expiring_soon_count": 1}},
    "fitness": {"today_plan": {"focus": "push", "lifts": ["bench", "dips"]}},
    "availability": {"threads": [{"status": "pending", "subject": "Tuesday?"}]},
    "deals": {"top": [{"title": "OLED 55"}]},
    "networth": {"total": 168396.5, "accounts": [{"name": "Chase", "balance": 4200.0}]},
}

# (question, the source it depends on, a figure that must NOT appear when down)
INTENTS = [
    ("what have I spent this month", "budget", "$0"),
    ("any email I need to reply to", "emails", "clear"),
    ("what tasks are open", "tasks", "0 open"),
    ("what bills are due", "finance", "No bills due"),
    ("what's my expected profit", "powerbuy", "$0"),
    ("what's my workout today", "fitness", "rest day"),
    ("anything awaiting a reply", "availability", "Nothing awaiting"),
    ("any deals", "deals", "No deals"),
    ("what's my net worth", "networth", "No accounts"),
]


@pytest.mark.parametrize("q,source,forbidden", INTENTS, ids=[i[1] for i in INTENTS])
def test_a_down_source_is_named_not_guessed(q, source, forbidden):
    out = answers.answer(q, {**LIVE, source: None})
    assert out["known"] is False, out["answer"]
    assert out["unavailable"] == [source]
    assert "didn't answer" in out["answer"]
    assert answers.SERVICE_NAMES[source] in out["answer"]
    # and specifically NOT the confident-zero phrasing the outage used to produce
    assert forbidden not in out["answer"], out["answer"]


@pytest.mark.parametrize("q,source,_f", INTENTS, ids=[i[1] for i in INTENTS])
def test_a_live_source_still_answers(q, source, _f):
    out = answers.answer(q, LIVE)
    assert out["known"] is True
    assert out["unavailable"] == []
    assert source in out["used"]


def test_empty_is_not_the_same_as_down():
    """The distinction the whole module exists for: a service that answered
    "nothing" is answered as nothing; one that did not answer is not."""
    empty = answers.answer("any email", {**LIVE, "emails": {"emails": []}})
    down = answers.answer("any email", {**LIVE, "emails": None})
    assert empty["known"] is True
    assert "clear" in empty["answer"]
    assert down["known"] is False
    assert "clear" not in down["answer"]


def test_the_service_is_named_so_you_know_what_to_restart():
    out = answers.answer("what have I spent", {**LIVE, "budget": None})
    assert "budget service" in out["answer"]
    assert "unknown, not zero" in out["answer"]


def test_a_proper_noun_source_reads_as_one():
    """"the Gmail didn't answer" is what a blanket article produces."""
    out = answers.answer("any email", {**LIVE, "emails": None})
    assert "the Gmail" not in out["answer"], out["answer"]
    assert "Gmail didn't answer" in out["answer"]


def test_two_down_sources_are_both_named():
    out = answers.rundown({**LIVE, "tasks": None, "deals": None})
    assert out["unavailable"] == ["deals", "tasks"]
    assert "the tasks service" in out["answer"]
    assert "the deals service" in out["answer"]


# ---- the rundown, which answers even while degraded --------------------

def test_the_rundown_still_reports_the_half_it_could_read():
    out = answers.rundown({**LIVE, "tasks": None})
    assert "1 emails to reply" in out["answer"] or "bills due soon" in out["answer"]
    assert "couldn't reach" in out["answer"]
    assert out["known"] is False


def test_a_rundown_that_reached_nothing_does_not_claim_nothing_is_urgent():
    """The subtlest case, and the one my own smoke test caught: with every
    counted source down, "nothing urgent" is a statement about services that
    were never read."""
    blind = {k: None for k in ("emails", "tasks", "finance", "powerbuy", "deals")}
    out = answers.rundown({**LIVE, **blind})
    assert "nothing urgent" not in out["answer"].split("Nothing urgent came back")[0]
    assert "can't tell you what's on your plate" in out["answer"]
    assert out["known"] is False


def test_an_all_clear_rundown_says_so_plainly():
    quiet = {**LIVE, "emails": {"emails": []}, "tasks": {"open": 0},
             "finance": {"upcoming": []}, "powerbuy": {"summary": {}}, "deals": {}}
    out = answers.rundown(quiet)
    assert out["answer"] == "Here's your plate: nothing urgent."
    assert out["known"] is True


def test_used_lists_only_sources_that_answered():
    out = answers.rundown({**LIVE, "tasks": None})
    assert "tasks" not in out["used"]
    assert "budget" in out["used"]


def test_an_unknown_question_falls_through_to_the_rundown():
    out = answers.answer("what is the airspeed of a swallow", LIVE)
    assert "plate" in out["answer"]
