"""Unit tests for the pure budget engine (services/budget/app/engine.py).

Run: python3 -m pytest services/budget/tests/test_engine.py -q
(no external deps beyond pytest; the engine is pure).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engine import budget_status  # noqa: E402

MONTH = {"label": "August", "days_total": 31, "days_elapsed": 20, "days_left": 11}
FRESH = {"ingest_days": 0, "activity_days": 0}


def _cfg(limit=300, cats=("Dining",), name="Dining", bid="dining"):
    return {"id": bid, "name": name, "limit": limit, "categories": list(cats)}


def _cats(**kw):
    """spent/refunds/net/count per category, e.g. Dining=(200, 0, 5)."""
    out = {}
    for name, (spent, refunds, count) in kw.items():
        out[name] = {"spent": spent, "refunds": refunds,
                     "net": round(spent - refunds, 2), "count": count}
    return out


def run(cfgs=None, cats=None, txns=None, month=MONTH, freshness=FRESH):
    return budget_status(cfgs if cfgs is not None else [_cfg()],
                         month, cats or {}, txns or [], freshness)


# ---------------- freshness: dual-signal model ----------------

def test_synced_today_no_recent_spending_stays_active():
    """The PO's core case: ingested today, newest txn 3 days old => ACTIVE."""
    s = run(cats=_cats(Dining=(100, 0, 3)),
            freshness={"ingest_days": 0, "activity_days": 3})
    assert s["freshness"]["current_ok"] is True
    assert s["freshness"]["signal"] == "ingest"
    assert s["freshness"]["paused_reason"] is None
    assert s["budgets"][0]["state"] != "paused"
    assert s["budget_room"] is not None


def test_stale_ingestion_pauses_with_import_reason():
    s = run(cats=_cats(Dining=(100, 0, 3)),
            freshness={"ingest_days": 25, "activity_days": 25})
    fr = s["freshness"]
    assert fr["current_ok"] is False
    assert "imported for 25 days" in fr["paused_reason"]
    b = s["budgets"][0]
    assert b["state"] == "paused"
    # as-of-ledger totals still shown; guidance suppressed as None, never 0
    assert b["spent"] == 100 and b["remaining"] == 200
    assert b["daily_rate"] is None and b["safe_per_day"] is None
    assert b["projected"] is None
    assert s["budget_room"] is None
    assert s["warnings"] == []


def test_ingest_signal_wins_over_fresh_activity():
    """Fresh-looking txn dates cannot mask a broken import pipeline."""
    s = run(freshness={"ingest_days": 10, "activity_days": 0})
    assert s["freshness"]["current_ok"] is False
    assert "imported" in s["freshness"]["paused_reason"]


def test_no_ingest_signal_falls_back_to_activity():
    s = run(freshness={"ingest_days": None, "activity_days": 1})
    assert s["freshness"]["signal"] == "activity_fallback"
    assert s["freshness"]["current_ok"] is True
    s2 = run(freshness={"ingest_days": None, "activity_days": 5})
    assert s2["freshness"]["current_ok"] is False
    assert "no ingestion signal" in s2["freshness"]["paused_reason"]


def test_no_signals_at_all_is_unknown_not_fresh():
    s = run(freshness={"ingest_days": None, "activity_days": None})
    assert s["freshness"]["current_ok"] is False
    assert s["freshness"]["paused_reason"] == "ledger freshness unknown"


def test_legacy_scalar_freshness_still_accepted():
    s = run(freshness=1)
    assert s["freshness"]["signal"] == "activity_fallback"
    assert s["freshness"]["current_ok"] is True


def test_freshness_echoed_in_payload():
    s = run(freshness={"ingest_days": 2, "activity_days": 4})
    assert s["freshness"]["ingest_days"] == 2
    assert s["freshness"]["activity_days"] == 4


# ---------------- states ----------------

def test_healthy():
    s = run(cats=_cats(Dining=(100, 0, 4)))
    b = s["budgets"][0]
    assert b["state"] == "healthy" and b["pct"] == 33


def test_over():
    s = run(cats=_cats(Dining=(350, 0, 9)))
    b = s["budgets"][0]
    assert b["state"] == "over" and b["remaining"] == -50
    assert s["warnings"][0]["state"] == "over"
    assert s["warnings"][0]["severity"] == "important"


def test_approaching_low_remaining():
    s = run(cats=_cats(Dining=(275, 0, 8)))  # remaining 25 <= 10% of 300
    assert s["budgets"][0]["state"] == "approaching"


def test_approaching_pace():
    # remaining 40 over 11 days => 3.64/day < 0.5 * (300/31)=4.84
    s = run(cats=_cats(Dining=(260, 0, 8)))
    assert s["budgets"][0]["state"] == "approaching"


def test_watch_projection():
    # 220 spent / 20d => 11/d => projected 341 > 300, remaining 80 not approaching
    s = run(cats=_cats(Dining=(220, 0, 8)))
    b = s["budgets"][0]
    assert b["state"] == "watch"
    assert b["projected"] == 341.0
    assert s["warnings"][0]["severity"] == "fyi"


def test_watch_suppressed_early_month():
    month = {"label": "Aug", "days_total": 31, "days_elapsed": 2, "days_left": 29}
    s = run(cats=_cats(Dining=(40, 0, 2)), month=month)  # projects 620 > 300
    assert s["budgets"][0]["state"] == "healthy"


# ---------------- money math ----------------

def test_refunds_reduce_spend():
    s = run(cats=_cats(Dining=(200, 50, 6)))
    assert s["budgets"][0]["spent"] == 150


def test_net_negative_month_is_healthy_zero_pct():
    s = run(cats=_cats(Dining=(20, 80, 3)))
    b = s["budgets"][0]
    assert b["spent"] == -60 and b["state"] == "healthy"


def test_multi_category_case_insensitive():
    cfg = _cfg(cats=("dining out", "RESTAURANTS"))
    s = run([cfg], cats=_cats(**{"Dining Out": (100, 0, 3), "Restaurants": (50, 0, 2)}))
    assert s["budgets"][0]["spent"] == 150 and s["budgets"][0]["count"] == 5


def test_zero_or_missing_limit_skipped():
    s = run([_cfg(limit=0), {"name": "x", "categories": ["y"]}])
    assert s["budgets"] == []


# ---------------- budget room ----------------

def test_budget_room_sums_positive_remainders_only():
    cfgs = [_cfg(300, ("Dining",), "Dining", "d"),
            _cfg(100, ("Gas",), "Gas", "g")]
    s = run(cfgs, cats=_cats(Dining=(350, 0, 9), Gas=(40, 0, 2)))
    # Dining over (contributes 0, no offset), Gas has 60 left
    assert s["budget_room"] == 60.0
    assert s["budget_room_scope"] == "across active budgets"


def test_budget_room_none_when_paused_or_unconfigured():
    assert run(freshness={"ingest_days": 9, "activity_days": 9})["budget_room"] is None
    assert run(cfgs=[])["budget_room"] is None


# ---------------- uncategorized / unbudgeted ----------------

def test_uncategorized_low_confidence_flag():
    s = run(cats=_cats(Dining=(100, 0, 3), Uncategorized=(60, 0, 4)))
    un = s["uncategorized"]
    assert un["amount"] == 60 and un["pct_of_spend"] == 38
    assert un["low_confidence"] is True


def test_uncategorized_small_amount_not_flagged():
    s = run(cats=_cats(Dining=(400, 0, 9), Uncategorized=(30, 0, 2)))
    assert s["uncategorized"]["low_confidence"] is False


def test_unbudgeted_lists_unmapped_categories():
    s = run(cats=_cats(Dining=(100, 0, 3), Gas=(79, 0, 2)))
    assert s["unbudgeted"]["categories"] == {"Gas": 79.0}
    assert s["unbudgeted"]["total"] == 79.0


# ---------------- txns + payload shape ----------------

def test_budget_txns_filtered_and_capped():
    txns = ([{"date": "2026-08-05", "desc": f"d{i}", "amount": -5, "category": "Dining"}
             for i in range(25)] +
            [{"date": "2026-08-06", "desc": "gas", "amount": -30, "category": "Gas"}])
    s = run(cats=_cats(Dining=(125, 0, 25)), txns=txns)
    assert len(s["budgets"][0]["txns"]) == 20
    assert all(t["category"] == "Dining" for t in s["budgets"][0]["txns"])


def test_sort_order_worst_first():
    cfgs = [_cfg(300, ("A",), "A", "a"), _cfg(300, ("B",), "B", "b"),
            _cfg(300, ("C",), "C", "c")]
    s = run(cfgs, cats=_cats(A=(100, 0, 1), B=(350, 0, 1), C=(290, 0, 1)))
    assert [b["name"] for b in s["budgets"]] == ["B", "C", "A"]


def test_state_counts_and_totals():
    cfgs = [_cfg(300, ("A",), "A", "a"), _cfg(100, ("B",), "B", "b")]
    s = run(cfgs, cats=_cats(A=(120, 0, 4), B=(150, 0, 3)))
    assert s["state_counts"]["over"] == 1
    assert s["totals"]["budgeted_limit"] == 400.0
    assert s["totals"]["budgeted_spent"] == 270.0


# ---------------- empty month (zero != unknown) ----------------

def test_month_with_no_ingestion_reports_unknown_not_zero():
    """A month that just began with nothing imported computes to $0 by
    arithmetic. That is not knowledge, so the headline total is None."""
    s = run(cats={}, freshness={"ingest_days": 4, "activity_days": 4,
                                "month_ingested": False})
    assert s["totals"]["spent_month"] is None
    assert s["freshness"]["month_ingested"] is False


def test_month_with_ingestion_reports_real_zero():
    """Imported today and genuinely nothing spent => a truthful $0."""
    s = run(cats={}, freshness={"ingest_days": 0, "activity_days": 0,
                                "month_ingested": True})
    assert s["totals"]["spent_month"] == 0
    assert s["freshness"]["current_ok"] is True


def test_month_ingested_absent_keeps_totals():
    s = run(cats=_cats(Dining=(100, 0, 3)))
    assert s["totals"]["spent_month"] == 100
