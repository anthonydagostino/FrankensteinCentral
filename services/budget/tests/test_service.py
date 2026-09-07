"""Budget service wiring: the real endpoints, with the two upstream reads
(core settings, firefly) stubbed.

The engine tests prove the arithmetic. These prove the arithmetic is actually
reached: a renamed key between the firefly service's /cycle payload and the
paycheck engine produces no error at all — just a permanently "unavailable"
card, or worse, a confident number computed from nothing. The payload below
mirrors what services/firefly/app/main.py::_cycle_payload really returns
(services/firefly/tests/test_endpoints.py pins that end).
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

bm = load_service_module("budget_main", "services/budget/app/main.py")

SETTINGS = {
    "budgets": [{"id": "dining", "name": "Dining", "limit": 300,
                 "categories": ["Restaurants"]}],
    "paycheck": {
        "enabled": True, "match": ["payroll"], "min_amount": 500,
        "cadence_days": 14,
        "allocations": [{"name": "Fidelity", "amount": 1100, "match": ["fidelity"]},
                        {"name": "Marcus", "amount": 500, "match": ["marcus"]}],
    },
}


def _txn(d, desc, amount, category="Uncategorized", source="", destination=""):
    return {"date": d, "desc": desc, "amount": amount, "category": category,
            "source": source, "destination": destination, "type": "", "ingested": d}


CYCLE = {
    "connected": True, "currency": "USD", "tz": "America/New_York",
    "today": "2026-09-04",
    "window": {"start": "2026-06-22", "end": "2026-09-04", "lookback_days": 75},
    "month": {"label": "September 2026", "start": "2026-09-01",
              "days_total": 30, "days_elapsed": 4, "days_left": 26},
    "ledger_latest_txn": "2026-09-03", "days_stale": 1,
    "ingest_latest": "2026-09-03", "ingest_days": 1, "month_ingested": True,
    "importer_url": "http://box:8096",
    "deposits": [_txn("2026-08-28", "ACME PAYROLL", 2400.0, source="ACME Corp")],
    "withdrawals": [_txn("2026-09-01", "Groceries", 212.0, "Groceries"),
                    _txn("2026-09-03", "Chipotle", 100.0, "Restaurants")],
    "transfers": [_txn("2026-08-29", "Savings", 1100.0, destination="Fidelity"),
                  _txn("2026-08-29", "Savings", 500.0, destination="Marcus")],
}

MONTH = {
    "connected": True, "today": "2026-09-04",
    "month": {"label": "September 2026", "start": "2026-09-01",
              "days_total": 30, "days_elapsed": 4, "days_left": 26},
    "days_stale": 1, "ledger_latest_txn": "2026-09-03",
    "ingest_latest": "2026-09-03", "ingest_days": 1, "month_ingested": True,
    "importer_url": "http://box:8096",
    "categories": {"Restaurants": {"spent": 100.0, "refunds": 0.0, "net": 100.0, "count": 1},
                   "Groceries": {"spent": 212.0, "refunds": 0.0, "net": 212.0, "count": 1}},
    "income_month": 0.0, "transactions": [],
}


@pytest.fixture
def upstream(monkeypatch):
    """Serves the two upstream reads; tests mutate `state` to change them."""
    state = {"settings": SETTINGS, "month": MONTH, "cycle": CYCLE,
             "bills": {"connected": True, "supported": False, "items": []}}

    async def fake_get(client, url, timeout=25):
        for key in ("settings", "month", "cycle", "bills"):
            if url.rstrip("/").endswith("/" + key):
                return state[key]
        return None

    monkeypatch.setattr(bm, "_get", fake_get)
    bm._CACHE.update(at=0.0, data=None)
    yield state
    bm._CACHE.update(at=0.0, data=None)


@pytest.fixture
def client():
    return TestClient(bm.app)


def test_status_carries_the_pay_cycle(upstream, client):
    d = client.get("/status?fresh=1").json()
    pay = d["paycheck"]
    assert pay["available"] is True
    assert pay["cycle"]["paycheck"] == 2400.0
    assert pay["cycle"]["savings_total"] == 1600.0
    assert pay["cycle"]["spent"] == 312.0
    assert pay["cycle"]["left"] == 488.0


def test_month_spend_is_the_savings_excluded_figure(upstream, client):
    d = client.get("/status?fresh=1").json()
    assert d["paycheck"]["month"]["spent"] == 312.0
    # the monthly-budget totals are computed independently and stay untouched
    assert d["totals"]["spent_month"] == 312.0


def test_paycheck_endpoint_returns_the_same_answer(upstream, client):
    a = client.get("/status?fresh=1").json()["paycheck"]
    b = client.get("/paycheck?fresh=1").json()
    assert b["cycle"] == a["cycle"]
    assert b["importer_url"] == "http://box:8096"


def test_a_savings_transfer_never_reads_as_spending(upstream, client):
    """The failure this feature exists to prevent, asserted through the real
    endpoint: $1,600 moved to savings, $312 actually spent."""
    d = client.get("/paycheck?fresh=1").json()
    assert d["cycle"]["spent"] == 312.0
    assert d["month"]["savings"] == 0.0      # transfers happened in August


def test_unconfigured_paycheck_says_so_instead_of_guessing(upstream, client):
    upstream["settings"] = {"budgets": []}
    d = client.get("/paycheck?fresh=1").json()
    assert d["configured"] is False
    assert d["cycle"] is None


def test_firefly_down_does_not_fabricate_a_cycle(upstream, client):
    upstream["cycle"] = None
    d = client.get("/paycheck?fresh=1").json()
    assert d["available"] is False
    assert d["cycle"] is None
    assert "unreachable" in d["reason"]


def test_disconnected_firefly_still_reports_paycheck_state(upstream, client):
    upstream["month"] = {"connected": False}
    d = client.get("/status?fresh=1").json()
    assert d["available"] is False
    assert d["paycheck"]["available"] is False
    assert d["paycheck"]["configured"] is True


def test_stale_ledger_pauses_per_day_guidance_through_the_endpoint(upstream, client):
    upstream["cycle"] = {**CYCLE, "ingest_days": 8, "ingest_latest": "2026-08-27",
                         "ledger_latest_txn": "2026-08-29", "days_stale": 6}
    d = client.get("/paycheck?fresh=1").json()
    assert d["fresh"] is False
    assert d["cycle"]["left"] is not None      # true as of the ledger
    assert d["cycle"]["per_day"] is None       # but no forward guidance
    assert "8 days" in d["stale_reason"]
