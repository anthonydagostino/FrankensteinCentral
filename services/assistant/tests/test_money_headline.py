"""The month headline, through the real assistant -> renderer path.

Codex, review of 6439b18: "the homepage month headline still presents
incomplete spending as an exact month-to-date total... Carry month
completeness independently of paycheck availability and render a lower-bound
label or unknown state at the headline itself; test both with and without a
matching paycheck through the real assistant-to-renderer path."

That last clause is why this file exists rather than another engine test: the
defect lives in the seam between _money() and renderMoney(), and neither
side's own tests can see it. So these drive the real _money() and then the
real renderMoney() extracted from home.js, and assert on rendered text.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "postgresql://unused/unused")

from conftest import load_service_module  # noqa: E402

am = load_service_module("assistant_money_headline", "services/assistant/app/main.py")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="node not available")


def _spending(month=1830.0, complete=True):
    return {"connected": True, "today": 12.0, "week": 40.0, "month": month,
            "month_ingested": True, "ingest_days": 0, "days_stale": 0,
            "window_complete": complete, "last_30": 2100.0, "recent": [],
            "biggest_today": None, "pace_pct": None, "baseline": "ok",
            "daily_avg": 60.0}


def _budget(paycheck):
    return {"available": True, "configured": True, "paycheck": paycheck,
            "freshness": {"current_ok": True}, "warnings": [], "budgets": [],
            "budget_room": None, "month": {"days_left": 23},
            "uncategorized": {}}


def _money(spending, paycheck):
    return am._money({"connected": True, "categories": []}, spending,
                     {"upcoming": []}, _budget(paycheck), {"total": 1000}, {})


PAYCHECK_TRUNCATED = {
    "configured": True, "available": True, "window_complete": False,
    "fresh": False, "stale_reason": "partial view", "as_of": "2026-09-07",
    "month": {"label": "September 2026", "spent": None, "savings": None,
              "daily_avg": None, "complete": False},
    "cycle": {"start": "2026-09-04", "paycheck": None, "spendable": None,
              "spent": None, "left": None, "per_day": None, "state": "unknown",
              "savings_total": None, "text": "partial read", "allocations": [],
              "figures_complete": False}}

# The case Codex singled out: truncated window, and NO paycheck was matched,
# so the brief takes its unavailable path and every pay-cycle field is gone.
PAYCHECK_ABSENT = {"configured": True, "available": False,
                   "reason": "no paycheck found in the ledger"}


def test_month_is_marked_incomplete_when_a_paycheck_was_matched():
    m = _money(_spending(complete=False), PAYCHECK_TRUNCATED)
    assert m["month_complete"] is False


def test_month_is_marked_incomplete_with_NO_matching_paycheck():
    """The regression Codex named: the unavailable brief path drops every
    pay-cycle field, so completeness must not be carried inside it."""
    m = _money(_spending(complete=False), PAYCHECK_ABSENT)
    assert m["paycheck"]["available"] is False
    assert m["month_complete"] is False, "completeness died with the paycheck brief"


def test_a_complete_month_is_not_labelled():
    m = _money(_spending(complete=True), PAYCHECK_ABSENT)
    assert m["month_complete"] is True


def _render(money_payload):
    """Run the REAL renderMoney from home.js over this payload."""
    src = (ROOT / "gateway/static/home.js").read_text()
    body = src[src.index('  // "Aug 28"'):src.index("  function renderPortfolio")]
    harness = """
const money = (n, d = 0) => n === null || n === undefined ? "—" :
  "$" + Number(n).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const esch = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const store = {};
const q = (s) => (store[s] = store[s] || {innerHTML: "", set onclick(v) {}});
const openAppKey = () => {}, openSettings = () => {};
%s
renderMoney(JSON.parse(process.argv[1]), {});
console.log(store["#cc-money"].innerHTML);
""" % body
    out = subprocess.run([NODE, "-e", harness, json.dumps(money_payload)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return re.sub(r"\s+", " ", out.stdout)


@needs_node
def test_the_rendered_headline_says_at_least_when_truncated():
    html = _render(_money(_spending(month=1830.0, complete=False), PAYCHECK_ABSENT))
    assert "at least $1,830" in html, html[:400]
    assert "a floor, not the total" in html


@needs_node
def test_the_rendered_headline_is_plain_when_complete():
    html = _render(_money(_spending(month=1830.0, complete=True), PAYCHECK_ABSENT))
    assert "$1,830" in html
    assert "at least" not in html
    assert "month to date" in html
