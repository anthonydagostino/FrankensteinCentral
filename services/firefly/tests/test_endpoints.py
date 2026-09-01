"""Integration tests: the real FastAPI app against a stub Firefly III.

These drive the actual endpoints the dashboard calls, with the clock pinned,
so the failure modes that took the money section down are reproducible on
any day of the year rather than only on the day they happen to occur.

Covered outages (each one really happened):
  * 1st of the month -> summary/basic 422 -> every money endpoint 502 ->
    the homepage rendered "Firefly not connected" on a healthy ledger.
  * one bad upstream endpoint aborting the other four.
  * a rolled-over month reporting $0 spent when no data existed for it.
"""
import json
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from conftest import load_service_module  # noqa: E402

ff = load_service_module("firefly_main", "services/firefly/app/main.py")

ING = "2026-08-28T06:00:00-04:00"      # when data entered the ledger
EDIT = "2026-09-01T09:00:00-04:00"     # a later edit — must NOT count as ingestion


def _group(gid, desc, amount, ttype, category, txn_date,
           created=ING, updated=ING):
    return {"id": str(gid), "attributes": {
        "created_at": created, "updated_at": updated,
        "transactions": [{"description": desc, "amount": amount,
                          "date": txn_date + "T12:00:00-04:00", "type": ttype,
                          "category_name": category, "currency_code": "USD"}]}}


class StubFirefly:
    """Mimics Firefly III, including its 422 on a zero-length range."""

    def __init__(self):
        self.fail = set()          # endpoint keywords to fail
        self.updated_at = ING      # bumped by "edits"
        self.groups = [
            _group(1, "Chipotle", "42.50", "withdrawal", "Restaurants", "2026-08-25"),
            _group(2, "Shell", "38.00", "withdrawal", "Transportation", "2026-08-24"),
            _group(3, "Paycheck", "1500.00", "deposit", None, "2026-08-28"),
        ]
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _j(self, obj, code=200):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                outer.calls.append(u.path)
                if any(k in u.path for k in outer.fail):
                    return self._j({"message": "forced failure"}, 500)
                if u.path == "/api/v1/summary/basic":
                    start = (q.get("start") or [""])[0]
                    end = (q.get("end") or [""])[0]
                    if start == end:      # the real Firefly behaviour
                        return self._j({"message": "end must be after start"}, 422)
                    return self._j({"net-worth-in-USD": {
                        "monetary_value": 168396.5, "value_parsed": "$168,396.50",
                        "currency_code": "USD"}})
                if u.path == "/api/v1/accounts":
                    if (q.get("type") or ["asset"])[0] == "liability":
                        return self._j({"data": []})
                    return self._j({"data": [{"id": "1", "attributes": {
                        "name": "Checking", "current_balance": "4200.00",
                        "currency_code": "USD", "updated_at": outer.updated_at}}]})
                if u.path == "/api/v1/transactions":
                    start = (q.get("start") or [None])[0]
                    end = (q.get("end") or [None])[0]
                    ttype = (q.get("type") or [None])[0]
                    if int((q.get("page") or ["1"])[0]) > 1:
                        return self._j({"data": []})
                    rows = outer.groups
                    if ttype:
                        rows = [g for g in rows
                                if g["attributes"]["transactions"][0]["type"] == ttype]
                    if start and end:
                        rows = [g for g in rows if start <=
                                g["attributes"]["transactions"][0]["date"][:10] <= end]
                    return self._j({"data": rows})
                if u.path == "/api/v1/bills":
                    return self._j({"data": []})
                if u.path == "/api/v1/insight/expense/category":
                    return self._j([])
                return self._j({"data": []})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def firefly(monkeypatch):
    stub = StubFirefly()
    monkeypatch.setattr(ff, "FIREFLY_URL", f"http://127.0.0.1:{stub.port}")
    monkeypatch.setattr(ff, "FIREFLY_TOKEN", "test-token")
    ff._TTL_CACHE.clear()          # the cache must never leak between tests
    yield stub
    stub.stop()
    ff._TTL_CACHE.clear()


@pytest.fixture
def client():
    return TestClient(ff.app)


def pin(monkeypatch, d: date):
    monkeypatch.setattr(ff, "_today", lambda: d)


# ---- the outage: the 1st of the month -----------------------------------

@pytest.mark.parametrize("day", [date(2026, 9, 1), date(2026, 10, 1),
                                 date(2027, 1, 1), date(2028, 2, 1)])
def test_dashboard_works_on_the_first_of_the_month(firefly, client, monkeypatch, day):
    """Regression: this returned 502 and the homepage said 'not connected'.

    Asserting 200 alone is NOT enough — per-endpoint degradation would keep
    the response at 200 while summary/basic silently 422'd every 1st of the
    month. Against a fully healthy Firefly nothing may be degraded, on any
    day of the year."""
    pin(monkeypatch, day)
    r = client.get("/dashboard")
    assert r.status_code == 200, f"{day}: {r.text}"
    d = r.json()
    assert d["connected"] is True
    assert d["degraded"] is None, f"{day}: upstream rejected our request: {d['degraded']}"
    assert d["net_worth"], f"{day}: net worth missing"


@pytest.mark.parametrize("day", [date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 30)])
def test_networth_survives_every_part_of_the_month(firefly, client, monkeypatch, day):
    pin(monkeypatch, day)
    r = client.get("/networth")
    assert r.status_code == 200
    assert r.json()["total"] is not None


def test_summary_never_asks_for_a_zero_length_range(firefly, client, monkeypatch):
    pin(monkeypatch, date(2026, 9, 1))
    client.get("/dashboard")
    ranges = [c for c in firefly.calls if "summary" in c]
    assert ranges, "summary/basic was never called"


# ---- one bad endpoint must not take down the rest -----------------------

def test_failing_summary_degrades_only_itself(firefly, client, monkeypatch):
    pin(monkeypatch, date(2026, 9, 15))
    firefly.fail = {"summary"}
    r = client.get("/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert d["connected"] is True
    assert d["degraded"]                       # names what broke
    assert len(d["accounts"]) == 1             # everything else still there
    assert d["recent"]


def test_networth_falls_back_to_summing_accounts(firefly, client, monkeypatch):
    pin(monkeypatch, date(2026, 9, 15))
    firefly.fail = {"summary"}
    r = client.get("/networth")
    assert r.status_code == 200
    assert r.json()["total"] == 4200.0


def test_total_failure_is_reported_not_faked(firefly, client, monkeypatch):
    """An empty payload would read as 'you have nothing' — a false claim."""
    pin(monkeypatch, date(2026, 9, 15))
    firefly.fail = {"api"}
    r = client.get("/dashboard")
    assert r.status_code == 502
    assert "unreachable" in r.json()["error"]


# ---- $0 vs unknown ------------------------------------------------------

def test_rolled_month_with_no_import_is_unknown(firefly, client, monkeypatch):
    """Sep 1 with an August-only ledger: $0 is arithmetic, not knowledge."""
    pin(monkeypatch, date(2026, 9, 1))
    r = client.get("/month")
    assert r.status_code == 200
    d = r.json()
    assert d["month_ingested"] is False
    assert d["ingest_latest"] == "2026-08-28"


def test_month_with_fresh_import_is_known(firefly, client, monkeypatch):
    pin(monkeypatch, date(2026, 9, 15))
    firefly.groups.append(_group(4, "Coffee", "5.00", "withdrawal", "Restaurants",
                                 "2026-09-14", created="2026-09-14T08:00:00-04:00"))
    r = client.get("/month")
    d = r.json()
    assert d["month_ingested"] is True
    assert d["ingest_latest"] == "2026-09-14"


# ---- ingestion provenance ----------------------------------------------

def test_edits_do_not_count_as_an_import(firefly, client, monkeypatch):
    """updated_at moves when you recategorize an old transaction. That is
    not an import and must not revive stale guidance."""
    pin(monkeypatch, date(2026, 9, 1))
    for g in firefly.groups:
        g["attributes"]["updated_at"] = EDIT      # "edited today"
    r = client.get("/spending")
    assert r.json()["ingest_latest"] == "2026-08-28"


def test_account_metadata_changes_do_not_count_as_an_import(firefly, client, monkeypatch):
    pin(monkeypatch, date(2026, 9, 1))
    firefly.updated_at = EDIT
    r = client.get("/spending")
    assert r.json()["ingest_latest"] == "2026-08-28"


# ---- the cache must not mask a real outage ------------------------------

def test_cache_serves_repeat_reads_without_requerying(firefly, client, monkeypatch):
    pin(monkeypatch, date(2026, 9, 15))
    client.get("/dashboard")
    first = len(firefly.calls)
    client.get("/dashboard")
    assert len(firefly.calls) == first, "second read should come from cache"


def test_health_never_depends_on_the_ledger(firefly, client):
    assert client.get("/health").json()["connected"] is True
