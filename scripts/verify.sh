#!/usr/bin/env bash
# Live-deployment verification for FrankensteinCentral. Run ON THE BOX:
#
#   bash scripts/verify.sh
#
# Prints a PASS/WARN/FAIL report that is SAFE TO PASTE anywhere:
#  - never reads or prints secret values (.env values are reported only as
#    SET / EMPTY, by key name)
#  - never prints email bodies/snippets (sender domain + truncated subject only)
#  - redacts anything token-shaped that leaks through an upstream error string
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== FrankensteinCentral verification — $(date '+%Y-%m-%d %H:%M %Z') =="
echo "-- containers --"
(cd "$REPO_DIR" && (docker compose ps 2>/dev/null || docker-compose ps 2>/dev/null)) \
  | sed -E 's/[A-Za-z0-9+\/_-]{40,}/REDACTED/g' || echo "(couldn't list containers)"
echo

REPO_DIR="$REPO_DIR" python3 - <<'PY'
import json, os, re, sys, urllib.request, urllib.error
from datetime import datetime

HOST = "localhost"
REPO = os.environ.get("REPO_DIR", ".")
RESULTS = []   # (level, section, message)

TOKENISH = re.compile(r"[A-Za-z0-9+/._-]{32,}")
SECRETY = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization|bearer|cookie)\S*")

def redact(s: str) -> str:
    s = str(s)
    s = SECRETY.sub("REDACTED", s)
    s = TOKENISH.sub("REDACTED", s)
    return s

def get(port, path, timeout=25):
    url = f"http://{HOST}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(body), None
            except json.JSONDecodeError:
                return r.status, None, f"non-JSON response: {redact(body[:100])}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:150]
        return e.code, None, f"HTTP {e.code}: {redact(body)}"
    except Exception as e:  # noqa: BLE001
        return None, None, redact(f"{type(e).__name__}: {e}")

def add(level, section, msg):
    RESULTS.append((level, section, msg))
    print(f"[{level}] {section:<18} {msg}")

def domain(addr):
    m = re.search(r"@([\w.-]+)", addr or "")
    return m.group(1).lower() if m else "(unknown)"

def trunc(s, n=34):
    s = str(s or "")
    return s[: n - 1] + "…" if len(s) > n else s

print("-- env config (values never shown) --")
env_keys = ["FIREFLY_URL", "FIREFLY_TOKEN", "FIREFLY_WEB_URL", "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GMAIL_QUERY", "LOCAL_TZ",
            "AUTO_SYNC_SECONDS"]
envfile = os.path.join(REPO, ".env")
envset = {}
if os.path.exists(envfile):
    for line in open(envfile, encoding="utf-8", errors="replace"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            envset[k.strip()] = bool(v.strip())
    for k in env_keys:
        state = "SET" if envset.get(k) else ("empty" if k in envset else "missing")
        lvl = "PASS" if envset.get(k) else "WARN"
        if k in ("GMAIL_QUERY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET") and not envset.get(k):
            lvl = "WARN"
        add(lvl, f".env {k}", state)
else:
    add("FAIL", ".env", "no .env file found in repo dir")
print()

print("-- gateway / core --")
st, _, err = get(8080, "/api/apps")
add("PASS" if st == 200 else "FAIL", "gateway :8080", "hub responding" if st == 200 else f"{err}")

st, h, err = get(8098, "/health")
if st == 200 and h:
    tz = h.get("tz")
    if tz:
        add("PASS", "core", f"ok — tz={tz}, today={h.get('today')}")
    else:
        add("WARN", "core", "running an OLD build (no tz field) — redeploy needed")
    st2, t, err2 = get(8098, "/today")
    if st2 == 200 and t:
        add("PASS", "core /today", f"score={t.get('score',{}).get('score')}, "
            f"study {t.get('study',{}).get('today_min')}m, water {t.get('water',{}).get('oz')}oz, "
            f"gym {t.get('gym',{}).get('week')}/{t.get('gym',{}).get('goal')} "
            f"(gym source {'reachable' if t.get('gym',{}).get('available') else 'UNREACHABLE'})")
    else:
        add("FAIL", "core /today", f"{err2}")
    st3, cs, err3 = get(8098, "/settings")
    if st3 == 200 and cs:
        hl = ((cs.get("market") or {}).get("holdings") or [])
        syms = ", ".join(f"{h.get('symbol')}×{h.get('shares')}" for h in hl) or "none"
        add("PASS" if hl else "WARN", "core holdings",
            f"{len(hl)} persisted: {syms}" if hl else "none persisted (⚙ Settings → Investments)")
    else:
        add("WARN", "core holdings", f"{err3}")
else:
    add("FAIL", "core", f"{err}")
print()

print("-- Firefly (money) --")
st, fh, err = get(8097, "/health")
if st == 200 and fh:
    if fh.get("connected"):
        add("PASS", "firefly conn", "CONNECTED (token + URL accepted)")
        st2, sp, err2 = get(8097, "/spending", timeout=40)
        if st2 == 200 and sp and sp.get("connected"):
            if "txn_count" not in sp:
                add("WARN", "firefly build", "OLD build (no txn_count/tz) — redeploy needed")
            add("PASS", "firefly txns", f"{sp.get('txn_count','?')} withdrawals fetched "
                f"(last month start → today), tz={sp.get('tz','?')}")
            # Freshness root-cause: any-type newest vs withdrawal newest.
            ds = sp.get("days_stale"); ll = sp.get("ledger_latest_txn"); lw = sp.get("latest_txn")
            if ll is None and lw is None:
                add("WARN", "firefly fresh", "couldn't determine ledger freshness")
            else:
                verdict = ("FRESH" if (ds is not None and ds < 2) else f"STALE ({ds}d)")
                src = ("source-data staleness — Firefly itself has nothing newer (import needed)"
                       if ll == lw or ll is None else
                       "newer non-withdrawal activity exists — ledger is fresher than the withdrawal window")
                add("PASS", "firefly fresh", f"{verdict}: newest ANY-type txn {ll}, "
                    f"newest withdrawal {lw} → {src}")
            add("PASS", "firefly calc", f"today=${sp.get('today')}, week=${sp.get('week')}, "
                f"month=${sp.get('month')}, daily_avg=${sp.get('daily_avg')}")
            pace = sp.get("pace_pct"); lm = sp.get("last_month_to_date")
            base = sp.get("baseline"); note = sp.get("pace_note")
            if base is None:
                add("WARN", "firefly pace", "OLD build (no baseline confidence field) — redeploy needed")
            elif base == "ok":
                add("PASS", "firefly pace", f"last-month-to-date=${lm}, pace={pace:+}% "
                    f"(ledger covers comparison window: {sp.get('earliest_txn')} → {sp.get('latest_txn')})")
            else:
                add("PASS", "firefly pace", f"comparison correctly SUPPRESSED ({base}) — {note}")
            big = sp.get("biggest_today")
            add("PASS", "firefly today", f"biggest charge today: "
                f"{trunc(big['desc'],24)+' $'+str(big['amount']) if big else 'none'}")
        else:
            add("FAIL", "firefly /spending", f"{err2 or (sp or {}).get('error','no data')}")
        st3, nw, err3 = get(8097, "/networth")
        if st3 == 200 and nw and nw.get("connected"):
            add("PASS", "firefly networth", f"total={nw.get('total_display') or nw.get('total')}, "
                f"{len(nw.get('accounts', []))} account line(s)")
        else:
            add("WARN", "firefly networth", f"{err3 or 'not available'}")
        st4, db, err4 = get(8097, "/dashboard", timeout=40)
        if st4 == 200 and db:
            add("PASS", "firefly extras", f"{len(db.get('categories',[]))} spend categories, "
                f"{len(db.get('recent',[]))} recent txns, {len(db.get('liabilities',[]))} liability account(s)")
        else:
            add("WARN", "firefly extras", f"{err4}")
    else:
        add("FAIL", "firefly conn", "NOT connected — FIREFLY_URL/FIREFLY_TOKEN not reaching the container")
else:
    add("FAIL", "firefly svc", f"{err}")
print()

print("-- Gmail (email) --")
st, gh, err = get(8083, "/health")
if st == 200 and gh:
    add("PASS" if gh.get("connected") else "FAIL", "gmail conn",
        "connected (token present)" if gh.get("connected") else "NOT connected — no Google token")
    st2, nr, err2 = get(8083, "/needs-reply", timeout=40)
    if st2 == 200 and nr:
        mode = nr.get("mode")
        emails = nr.get("emails", [])
        lvl = "PASS" if mode == "live" else "FAIL"
        add(lvl, "gmail fetch", f"mode={mode}, {len(emails)} message(s) classified needs-reply"
            + ("" if mode == "live" else " — token likely needs re-auth" if mode == "error" else ""))
        if emails:
            with_age = [e for e in emails if e.get("age_hours") is not None]
            if with_age:
                ages = sorted(e["age_hours"] for e in with_age)
                add("PASS", "gmail age", f"{len(with_age)}/{len(emails)} have age "
                    f"(newest {ages[0]}h, oldest {ages[-1]}h)")
            else:
                add("WARN", "gmail age", "no age on messages — gmail container is an OLD build, redeploy")
    else:
        add("FAIL", "gmail fetch", f"{err2}")
    # Whole-inbox classification sample (sanitized server-side): the review set
    # for judging classifier quality, not just the needs-reply survivors.
    st3, smp, err3 = get(8083, "/sample", timeout=45)
    if st3 == 404 or (err3 and "404" in str(err3)):
        add("WARN", "gmail sample", "OLD build (no /sample endpoint) — redeploy needed")
    elif st3 == 200 and smp and smp.get("items") is not None:
        counts = smp.get("counts", {})
        add("PASS", "gmail classify", f"{smp.get('total')} recent messages: " +
            ", ".join(f"{k}×{v}" for k, v in sorted(counts.items())) +
            f" — {smp.get('needs_reply_count')} flagged needs-reply")
        auto_n = sum(1 for i in smp["items"] if i.get("automated"))
        add("PASS", "gmail automation", f"{auto_n}/{smp.get('total')} detected as automated senders")
        bad = [i for i in smp["items"] if i.get("automated") and i.get("needs_reply")]
        add("PASS" if not bad else "FAIL", "gmail precision",
            "no automated mail flagged needs-reply" if not bad
            else f"{len(bad)} automated message(s) still flagged needs-reply")
        print("      sample (domain · category · reply? · age · truncated subject):")
        for i in smp["items"][:10]:
            age = i.get("age_hours")
            print(f"        - {i.get('domain'):<24} {i.get('category'):<12} "
                  f"{'REPLY' if i.get('needs_reply') else '     '} "
                  f"{(str(age) + 'h') if age is not None else '?':>7}  \"{i.get('subject')}\"")
    else:
        add("WARN", "gmail sample", f"{err3 or 'no sample available'}")
else:
    add("FAIL", "gmail svc", f"{err}")
print()

print("-- stocks / tasks / schedule / fitness --")
st, pq, errq = get(8099, "/quotes?symbols=NVDA", timeout=30)
if st == 200 and pq is not None:
    got = (pq.get("quotes") or [])
    if got:
        q0 = got[0]
        add("PASS", "quote sources", f"pipeline OK — NVDA ${q0.get('price')} via {q0.get('source','?')}")
    else:
        add("WARN", "quote sources", "NVDA returned no quote — both sources unreachable "
            "or rate-limited from this network right now (retries automatically)")
else:
    add("FAIL", "quote sources", f"{errq}")

st, pf, err = get(8099, "/portfolio", timeout=60)
if st == 200 and pf:
    if pf.get("configured"):
        pos = pf.get("positions", [])
        dead = pf.get("quotes_failed", [p["symbol"] for p in pos if not p.get("available")])
        add("PASS", "stocks", f"{len(pos)} position(s), {pf.get('quotes_ok', len(pos)-len(dead))} priced, "
            f"value=${pf.get('value')}, day {pf.get('day_change_pct')}%")
        if dead:
            add("WARN", "stocks quotes", f"{len(dead)} symbol(s) unpriced: {', '.join(dead[:8])}"
                + ("…" if len(dead) > 8 else ""))
    else:
        add("WARN", "stocks", "no holdings configured yet (⚙ Settings → Investments)")
else:
    add("FAIL", "stocks svc", f"{err}")

st, ts, err = get(8087, "/summary")
add("PASS" if st == 200 else "FAIL", "tasks",
    f"{(ts or {}).get('open')} open task(s)" if st == 200 else f"{err}")
st, ev, err = get(8084, "/events")
add("PASS" if st == 200 else "FAIL", "schedule",
    f"{len((ev or {}).get('events', []))} event(s)" if st == 200 else f"{err}")
st, vs, err = get(8082, "/visits")
add("PASS" if st == 200 else "FAIL", "fitness",
    f"{(vs or {}).get('count')} gym visit(s) logged" if st == 200 else f"{err}")
print()

print("-- homepage aggregator (assistant /home) --")
st, home, err = get(8085, "/home?fresh=1", timeout=60)
if st == 200 and home:
    missing = [k for k in ("inbox", "money", "portfolio", "do_next", "attention",
                           "health", "score", "briefing") if k not in home]
    if missing:
        add("WARN", "home build", f"payload missing {missing} — assistant is an OLD build, redeploy")
    else:
        add("PASS", "home build", f"all sections present, mode={home.get('mode')}, "
            f"greeting=\"{home.get('greeting')}\", now={home.get('now','')[:19]}")
    dn = home.get("do_next", {})
    add("PASS" if dn.get("title") else "WARN", "home do_next",
        f"\"{dn.get('title')}\" — {trunc(dn.get('reason'), 70)}")
    money = home.get("money", {})
    add("PASS" if money.get("connected") else "WARN", "home money",
        f"connected={money.get('connected')}, today=${money.get('today')}, "
        f"month=${money.get('month')}, pace={money.get('pace_pct')}%, "
        f"obs={len(money.get('observations', []))}")
    inbox = home.get("inbox", {})
    add("PASS" if inbox.get("mode") == "live" else "WARN", "home inbox",
        f"mode={inbox.get('mode')}, {len(inbox.get('items', []))} surfaced, "
        f"{inbox.get('need_reply')} need reply")
    sysh = home.get("systems", {})
    add("PASS" if sysh.get("healthy") else "FAIL", "home systems",
        "healthy" if sysh.get("healthy") else f"down: {sysh.get('down')}")
    # cache check
    st2, cached, _ = get(8085, "/home")
    if st2 == 200 and cached:
        same = cached.get("last_updated") == home.get("last_updated")
        add("PASS", "home cache", "cached copy served within TTL (~30s)" if same
            else "cache refreshed between calls (also fine)")
else:
    add("FAIL", "home", f"{err}")

print()
print("== summary ==")
counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
for lvl, _, _ in RESULTS:
    counts[lvl] = counts.get(lvl, 0) + 1
print(f"PASS {counts['PASS']}   WARN {counts['WARN']}   FAIL {counts['FAIL']}")
if counts["FAIL"]:
    print("FAILs:")
    for lvl, sec, msg in RESULTS:
        if lvl == "FAIL":
            print(f"  - {sec}: {msg}")
print("\nThis output contains no secrets — safe to paste back.")
PY
