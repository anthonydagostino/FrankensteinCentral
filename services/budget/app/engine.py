"""Budget engine — pure, deterministic calculations. No I/O.

All formulas are documented in docs/BUDGETS.md. Inputs:
  budgets_cfg : [{id, name, limit, categories: [firefly category names]}]
  month       : {"days_total": D, "days_elapsed": d, "days_left": r, "label": str}
  categories  : {name: {"spent": w, "refunds": c, "net": w-c, "count": n}}
  txns        : [{date, desc, amount(+credit/-spend), category}]
  freshness   : {"ingest_days": int|None, "activity_days": int|None}
                ingest_days   = days since data last ENTERED Firefly
                                (transaction created/updated timestamps +
                                account updated_at) — synchronization recency
                activity_days = days since the newest transaction DATE —
                                spending recency (supporting info only)

Freshness: current-period guidance (pace, projection, safe/day, warnings,
budget room) is only computed when the INGESTION signal is fresh
(ingest_days < INGEST_MAX_DAYS). A user who synced today but simply hasn't
spent in 3 days stays ACTIVE. If no ingestion signal exists at all, we fall
back conservatively to activity (< ACTIVITY_FALLBACK_MAX days) and say so.
Totals that are true "as of the ledger" always compute. Zero and unknown stay
distinct: suppressed values are None, never 0.
"""
from __future__ import annotations

INGEST_MAX_DAYS = 3         # data imported within this many days => guidance allowed
ACTIVITY_FALLBACK_MAX = 2   # no ingestion signal: fall back to newest-txn age
EARLY_MONTH_DAYS = 3        # before this many elapsed days, pace is too noisy for WATCH
APPROACH_REMAINING_PCT = 0.10   # remaining <= 10% of limit => approaching
APPROACH_PACE_FACTOR = 0.5      # safe/day < 50% of the budget's implied daily => approaching
UNCAT_CONFIDENCE_PCT = 20       # uncategorized >= 20% of spend => low confidence
UNCAT_CONFIDENCE_MIN = 50.0     # ...and at least this many dollars


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def budget_status(budgets_cfg: list, month: dict, categories: dict,
                  txns: list, freshness) -> dict:
    D = max(1, int(month.get("days_total") or 1))
    d = min(D, max(1, int(month.get("days_elapsed") or 1)))
    r = max(0, int(month.get("days_left") if month.get("days_left") is not None else D - d))

    fr = freshness if isinstance(freshness, dict) else {"ingest_days": None,
                                                        "activity_days": freshness}
    ingest_days = fr.get("ingest_days")
    activity_days = fr.get("activity_days")
    # Has ANYTHING entered the ledger since this month began? If not, the
    # month-to-date total computes to $0 purely because the month is empty —
    # that is arithmetic, not evidence, and must render as unknown.
    month_ingested = fr.get("month_ingested")
    if ingest_days is not None:
        fresh = ingest_days < INGEST_MAX_DAYS
        signal = "ingest"
        paused_reason = (None if fresh else
                         f"financial data hasn't been imported for {ingest_days} days")
    else:
        fresh = activity_days is not None and activity_days < ACTIVITY_FALLBACK_MAX
        signal = "activity_fallback"
        paused_reason = (None if fresh else
                         ("no ingestion signal and the newest transaction is "
                          f"{activity_days} days old" if activity_days is not None
                          else "ledger freshness unknown"))

    cat_lookup = {_norm(k): (k, v) for k, v in (categories or {}).items()}
    txns = txns or []

    budgets_out = []
    warnings = []
    mapped_norm: set[str] = set()
    safe_total = 0.0

    for cfg in budgets_cfg or []:
        limit = float(cfg.get("limit") or 0)
        if limit <= 0:
            continue
        cats = [c for c in (cfg.get("categories") or []) if c and c.strip()]
        norm_cats = {_norm(c) for c in cats}
        mapped_norm |= norm_cats

        spent = 0.0
        count = 0
        for nc in norm_cats:
            if nc in cat_lookup:
                _, v = cat_lookup[nc]
                spent += float(v.get("net", 0))
                count += int(v.get("count", 0))
        spent = round(spent, 2)
        remaining = round(limit - spent, 2)
        pct = round(100 * spent / limit) if limit else 0

        # ---- states (see docs/BUDGETS.md for the exact rules) ----
        if not fresh:
            state = "paused"
            daily_rate = safe_per_day = projected = projected_delta = None
        else:
            daily_rate = round(spent / d, 2)
            projected = round(daily_rate * D, 2)
            projected_delta = round(projected - limit, 2)
            safe_per_day = round(max(remaining, 0.0) / max(r, 1), 2)
            implied_daily = limit / D
            if spent > limit:
                state = "over"
            elif remaining <= APPROACH_REMAINING_PCT * limit or (
                    r > 0 and remaining > 0 and safe_per_day < APPROACH_PACE_FACTOR * implied_daily):
                state = "approaching"
            elif projected > limit and d >= EARLY_MONTH_DAYS:
                state = "watch"
            else:
                state = "healthy"

        my_txns = [t for t in txns if _norm(t.get("category")) in norm_cats][:20]

        entry = {
            "id": cfg.get("id") or _norm(cfg.get("name", "")),
            "name": cfg.get("name", ""),
            "categories": cats,
            "limit": round(limit, 2),
            "spent": spent,
            "remaining": remaining,
            "pct": pct,
            "count": count,
            "state": state,
            "daily_rate": daily_rate,
            "safe_per_day": safe_per_day,
            "projected": projected,
            "projected_delta": projected_delta,
            "txns": my_txns,
        }
        budgets_out.append(entry)
        safe_total += max(remaining, 0.0)

        if state == "over":
            warnings.append({"budget": entry["name"], "state": "over", "severity": "important",
                             "text": f"{entry['name']} is over budget: ${spent:,.0f} of "
                                     f"${limit:,.0f} (${spent - limit:,.0f} over) with {r} day(s) left."})
        elif state == "approaching":
            warnings.append({"budget": entry["name"], "state": "approaching", "severity": "important",
                             "text": f"{entry['name']} has ${max(remaining, 0):,.0f} remaining with {r} "
                                     f"day(s) left — about ${safe_per_day:,.2f}/day keeps you on plan."})
        elif state == "watch":
            warnings.append({"budget": entry["name"], "state": "watch", "severity": "fyi",
                             "text": f"{entry['name']} is pacing above budget — projected "
                                     f"${projected:,.0f} by month end (${projected_delta:,.0f} over)."})

    order = {"over": 0, "approaching": 1, "watch": 2}
    warnings.sort(key=lambda w: order.get(w["state"], 3))
    budgets_out.sort(key=lambda b: (order.get(b["state"], 3), -(b["pct"] or 0)))

    # categorized spending not mapped to any budget
    unbudgeted = {}
    for k, v in (categories or {}).items():
        if _norm(k) not in mapped_norm and k != "Uncategorized" and v.get("net", 0) > 0:
            unbudgeted[k] = round(float(v["net"]), 2)

    uncat = (categories or {}).get("Uncategorized", {}) or {}
    uncat_net = round(float(uncat.get("net", 0)), 2)
    total_spend = round(sum(max(float(v.get("net", 0)), 0) for v in (categories or {}).values()), 2)
    uncat_pct = round(100 * uncat_net / total_spend) if total_spend > 0 and uncat_net > 0 else 0
    low_confidence = uncat_pct >= UNCAT_CONFIDENCE_PCT and uncat_net >= UNCAT_CONFIDENCE_MIN

    return {
        "month": {"label": month.get("label"), "days_total": D,
                  "days_elapsed": d, "days_left": r},
        "freshness": {"ingest_days": ingest_days, "activity_days": activity_days,
                      "signal": signal, "current_ok": fresh,
                      "month_ingested": month_ingested,
                      "paused_reason": paused_reason},
        "budgets": budgets_out,
        "warnings": warnings,
        # "Budget Room": remaining capacity across configured budgets. The
        # label SAFE TO SPEND is reserved for a future engine that also
        # accounts for bills, obligations and liquidity — this number is one
        # future component of it, and is always scoped in the UI.
        "budget_room": round(safe_total, 2) if (fresh and budgets_out) else None,
        "budget_room_scope": "across active budgets" if budgets_out else None,
        "unbudgeted": {"total": round(sum(unbudgeted.values()), 2), "categories": unbudgeted},
        "uncategorized": {
            "amount": uncat_net,
            "count": int(uncat.get("count", 0)),
            "pct_of_spend": uncat_pct,
            "low_confidence": low_confidence,
            "txns": [t for t in txns if _norm(t.get("category")) == "uncategorized"][:10],
        },
        "totals": {
            # None, not 0: no data for this month means we do not know.
            "spent_month": None if month_ingested is False else total_spend,
            "budgeted_limit": round(sum(b["limit"] for b in budgets_out), 2),
            "budgeted_spent": round(sum(b["spent"] for b in budgets_out), 2),
        },
        "state_counts": {
            s: sum(1 for b in budgets_out if b["state"] == s)
            for s in ("healthy", "watch", "approaching", "over", "paused")
        },
    }
