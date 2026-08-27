"""Core service — the personal state & daily-score engine for the life OS.

This owns everything the dashboard tracks about *you* (not an external app):
your goals/settings, per-day metrics (study focus time, water, nutrition,
sleep), focus sessions, your Big 3 for the day, a quick-capture inbox, and a
transparent daily-score. It's pure state + deterministic rules — no LLM.

For the two habit metrics that already live in an existing service it reads the
source of truth instead of duplicating it:
  * gym       -> fitness service (/visits)
  * open tasks-> tasks service (/summary)

Nothing here is secret; no credentials are stored or returned.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

app = FastAPI(title="Core Service")

# "Today" must mean the user's actual day, not the box's UTC day.
EASTERN = ZoneInfo(os.environ.get("LOCAL_TZ", "America/New_York"))

DATABASE_URL = os.environ["DATABASE_URL"]
FITNESS_URL = os.environ.get("FITNESS_URL", "http://fitness:8000").rstrip("/")
TASKS_URL = os.environ.get("TASKS_URL", "http://tasks:8000").rstrip("/")

pool = AsyncConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=5)

SCHEMA = """
CREATE TABLE IF NOT EXISTS core_settings (
    id   INTEGER PRIMARY KEY DEFAULT 1,
    data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_log (
    day          DATE PRIMARY KEY,
    water_oz     INTEGER NOT NULL DEFAULT 0,
    nutrition    TEXT,
    sleep_hours  NUMERIC,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS focus_sessions (
    id         SERIAL PRIMARY KEY,
    day        DATE NOT NULL,
    label      TEXT NOT NULL DEFAULT 'Study',
    minutes    INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS big3 (
    id         SERIAL PRIMARY KEY,
    day        DATE NOT NULL,
    position   INTEGER NOT NULL,
    text       TEXT NOT NULL,
    done       BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS captures (
    id         SERIAL PRIMARY KEY,
    text       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'note',
    done       BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS focus_day_idx ON focus_sessions(day);
CREATE INDEX IF NOT EXISTS big3_day_idx ON big3(day);
"""

DEFAULT_SETTINGS = {
    "study_daily_min": 120,
    "study_weekly_min": 600,
    "gym_weekly": 4,
    "water_goal_oz": 80,
    "water_presets": [8, 16, 24],
    "focus_presets": [25, 45, 60, 90],
    "exam_date": None,           # ISO date, e.g. "2026-10-01"
    "exam_label": "exam",
    "exam_target_hours": None,   # total study hours you want in before it
    # Daily-score weights. A component with weight 0 is excluded and the rest
    # are renormalised to 100, so the score is always out of 100.
    "score_weights": {"study": 30, "fitness": 20, "tasks": 20,
                      "hydration": 10, "nutrition": 10, "sleep": 0},
    "morning_end_hour": 12,      # local hour before which = morning mode
    "evening_start_hour": 18,    # local hour at/after which = evening mode
    "important_senders": [],     # emails/domains the attention feed prioritises
    "market": {"holdings": [], "watchlist": [], "move_threshold_pct": 3.0},
    "finance": {"large_txn": 200, "low_balance": 100},
}

NUTRITION_RATIO = {"poor": 0.34, "okay": 0.67, "good": 1.0}


def _today() -> date:
    return datetime.now(EASTERN).date()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


# ---- models -----------------------------------------------------------------
class WaterIn(BaseModel):
    oz: int


class FocusIn(BaseModel):
    minutes: int
    label: str | None = "Study"


class NutritionIn(BaseModel):
    rating: str  # poor | okay | good


class SleepIn(BaseModel):
    hours: float


class Big3In(BaseModel):
    items: list[str]  # up to 3


class CaptureIn(BaseModel):
    text: str
    kind: str | None = "note"


class CapturePatch(BaseModel):
    kind: str | None = None
    done: bool | None = None


# ---- lifecycle --------------------------------------------------------------
@app.on_event("startup")
async def startup():
    await pool.open(wait=True, timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
        await conn.execute(
            "INSERT INTO core_settings (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (_json(DEFAULT_SETTINGS),),
        )


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


def _json(obj) -> str:
    import json
    return json.dumps(obj)


async def _settings() -> dict:
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute("SELECT data FROM core_settings WHERE id = 1")
        row = await cur.fetchone()
    data = dict(DEFAULT_SETTINGS)
    if row and row[0]:
        # shallow-merge stored over defaults so new keys always exist
        stored = row[0]
        for k, v in stored.items():
            data[k] = v
    return data


# ---- external reads (source-of-truth services) ------------------------------
async def _gym() -> dict:
    """This-week workout count + last workout, read from the fitness service."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{FITNESS_URL}/visits", timeout=5)
            r.raise_for_status()
            visits = r.json().get("visits", [])
    except Exception:  # noqa: BLE001
        return {"week": 0, "last": None, "available": False}
    ws = _week_start(_today())
    week = 0
    last = None
    for v in visits:
        raw = v.get("when") or v.get("when_at") or ""
        d = _parse_day(raw)
        if d is None:
            continue
        if last is None or d > last:
            last = d
        if d >= ws:
            week += 1
    return {"week": week, "last": last.isoformat() if last else None, "available": True}


async def _open_tasks() -> int | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{TASKS_URL}/summary", timeout=5)
            r.raise_for_status()
            return r.json().get("open")
    except Exception:  # noqa: BLE001
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{TASKS_URL}/tasks", timeout=5)
                r.raise_for_status()
                return sum(1 for t in r.json().get("tasks", []) if not t.get("done"))
        except Exception:  # noqa: BLE001
            return None


def _parse_day(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None


# ---- study metrics ----------------------------------------------------------
async def _study() -> dict:
    today = _today()
    ws = _week_start(today)
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "SELECT COALESCE(SUM(minutes),0) FROM focus_sessions WHERE day = %s", (today,)
        )
        (today_min,) = await cur.fetchone()
        cur = await conn.execute(
            "SELECT COALESCE(SUM(minutes),0) FROM focus_sessions WHERE day >= %s", (ws,)
        )
        (week_min,) = await cur.fetchone()
        # streak: consecutive days with study, counting back from today (or
        # yesterday if today has none yet)
        cur = await conn.execute(
            "SELECT DISTINCT day FROM focus_sessions ORDER BY day DESC LIMIT 400"
        )
        days = {r[0] for r in await cur.fetchall()}
    streak = 0
    probe = today if today in days else today - timedelta(days=1)
    while probe in days:
        streak += 1
        probe -= timedelta(days=1)
    return {"today_min": int(today_min), "week_min": int(week_min), "streak": streak}


async def _exam_pace(settings: dict, week_min: int) -> dict | None:
    ex = settings.get("exam_date")
    if not ex:
        return None
    ed = _parse_day(ex)
    if ed is None:
        return None
    days_left = (ed - _today()).days
    out = {"label": settings.get("exam_label", "exam"), "date": ex, "days_left": days_left}
    target = settings.get("exam_target_hours")
    if target:
        async with pool.connection() as conn:
            conn.row_factory = tuple_row
            cur = await conn.execute("SELECT COALESCE(SUM(minutes),0) FROM focus_sessions")
            (total_min,) = await cur.fetchone()
        done_h = round(total_min / 60, 1)
        remaining_h = max(0, round(target - done_h, 1))
        weeks_left = max(days_left / 7, 0.1)
        out.update({
            "target_hours": target,
            "done_hours": done_h,
            "remaining_hours": remaining_h,
            "weekly_needed_hours": round(remaining_h / weeks_left, 1) if days_left > 0 else remaining_h,
            "week_hours": round(week_min / 60, 1),
        })
    return out


# ---- score engine -----------------------------------------------------------
def compute_score(components: dict, weights: dict) -> dict:
    """Transparent daily score. Each component is a 0..1 ratio; the score is the
    weighted average over enabled components (weight>0), renormalised to 100.
    Partial completion is rewarded. `null` components are treated as not-yet."""
    active = {k: w for k, w in weights.items() if w and w > 0}
    total_w = sum(active.values()) or 1
    parts = {}
    acc = 0.0
    for k, w in active.items():
        ratio = components.get(k)
        ratio = 0.0 if ratio is None else max(0.0, min(1.0, float(ratio)))
        parts[k] = {"ratio": round(ratio, 3), "weight": w}
        acc += ratio * w
    return {"score": round(100 * acc / total_w), "parts": parts}


async def _daily_state() -> dict:
    """The full per-day personal state + score. This is the primary read the
    assistant folds into the home screen."""
    s = await _settings()
    today = _today()
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT water_oz, nutrition, sleep_hours FROM daily_log WHERE day = %s", (today,)
        )
        log = await cur.fetchone() or {"water_oz": 0, "nutrition": None, "sleep_hours": None}
        cur = await conn.execute(
            "SELECT id, position, text, done FROM big3 WHERE day = %s ORDER BY position", (today,)
        )
        big3 = await cur.fetchall()

    study = await _study()
    gym = await _gym()
    open_tasks = await _open_tasks()
    exam = await _exam_pace(s, study["week_min"])

    water_oz = int(log["water_oz"] or 0)
    nutrition = log["nutrition"]
    sleep_hours = float(log["sleep_hours"]) if log["sleep_hours"] is not None else None

    big3_done = sum(1 for b in big3 if b["done"])
    big3_total = len(big3)

    # component ratios for the score
    comp = {
        "study": (study["today_min"] / s["study_daily_min"]) if s["study_daily_min"] else 0,
        "fitness": (gym["week"] / s["gym_weekly"]) if s["gym_weekly"] else 0,
        "tasks": (big3_done / big3_total) if big3_total else 0,
        "hydration": (water_oz / s["water_goal_oz"]) if s["water_goal_oz"] else 0,
        "nutrition": NUTRITION_RATIO.get(nutrition, 0.0) if nutrition else None,
        "sleep": (min(1.0, sleep_hours / 8) if sleep_hours is not None else None),
    }
    score = compute_score(comp, s["score_weights"])

    return {
        "date": today.isoformat(),
        "study": {
            "today_min": study["today_min"], "goal_min": s["study_daily_min"],
            "week_min": study["week_min"], "week_goal_min": s["study_weekly_min"],
            "streak": study["streak"], "presets": s["focus_presets"],
            "exam": exam,
        },
        "gym": {"week": gym["week"], "goal": s["gym_weekly"], "last": gym["last"],
                "available": gym["available"]},
        "water": {"oz": water_oz, "goal": s["water_goal_oz"], "presets": s["water_presets"]},
        "nutrition": {"rating": nutrition},
        "sleep": {"hours": sleep_hours},
        "tasks": {"open": open_tasks},
        "big3": [dict(b) for b in big3],
        "score": score,
        "nudges": _nudges(s, study, gym, water_oz, nutrition, big3_done, big3_total, open_tasks),
    }


def _nudges(s, study, gym, water_oz, nutrition, big3_done, big3_total, open_tasks) -> list[dict]:
    """Personal, explainable attention items the assistant folds into the feed."""
    out = []
    now = datetime.now(EASTERN)
    late = now.hour >= s["evening_start_hour"]

    remaining_study = max(0, s["study_daily_min"] - study["today_min"])
    if remaining_study > 0:
        sev = "important" if (late and remaining_study >= 30) else "fyi"
        out.append({
            "key": "study", "severity": sev, "icon": "📚",
            "title": f"{remaining_study} min to your study goal" if study["today_min"] else "Study goal not started",
            "detail": f"{study['today_min']}m / {s['study_daily_min']}m today",
            "action": {"type": "focus", "label": f"Start {min(60, remaining_study)} min",
                       "minutes": min(60, remaining_study) or 25},
        })
    if gym["available"] and gym["week"] < s["gym_weekly"]:
        rem = s["gym_weekly"] - gym["week"]
        days_left_in_week = 7 - now.weekday()
        sev = "important" if rem >= days_left_in_week else "fyi"
        out.append({
            "key": "gym", "severity": sev, "icon": "🏋️",
            "title": f"{rem} workout(s) left this week",
            "detail": f"{gym['week']} / {s['gym_weekly']} done",
            "action": {"type": "gym", "label": "Log workout"},
        })
    if s["water_goal_oz"] and water_oz < s["water_goal_oz"] and late:
        out.append({
            "key": "water", "severity": "fyi", "icon": "💧",
            "title": f"{s['water_goal_oz'] - water_oz} oz of water to go",
            "detail": f"{water_oz} / {s['water_goal_oz']} oz",
            "action": {"type": "water", "label": "+16 oz", "oz": 16},
        })
    if big3_total and big3_done < big3_total and late:
        out.append({
            "key": "big3", "severity": "fyi", "icon": "🎯",
            "title": f"{big3_total - big3_done} of your Big 3 left",
            "detail": f"{big3_done} / {big3_total} done",
            "action": None,
        })
    return out


# ---- endpoints --------------------------------------------------------------
@app.get("/health")
async def health():
    return {"service": "core", "ok": True}


@app.get("/today")
async def today():
    return await _daily_state()


@app.get("/settings")
async def get_settings():
    return await _settings()


@app.put("/settings")
async def put_settings(patch: dict):
    """Shallow-merge a partial settings update."""
    cur = await _settings()
    cur.update(patch or {})
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO core_settings (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            (_json(cur),),
        )
    return cur


@app.post("/water")
async def add_water(w: WaterIn):
    today = _today()
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "INSERT INTO daily_log (day, water_oz) VALUES (%s, %s) "
            "ON CONFLICT (day) DO UPDATE SET water_oz = daily_log.water_oz + EXCLUDED.water_oz, "
            "updated_at = now() RETURNING water_oz",
            (today, max(0, w.oz)),
        )
        (oz,) = await cur.fetchone()
    return {"oz": oz}


@app.post("/focus")
async def add_focus(f: FocusIn):
    today = _today()
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "INSERT INTO focus_sessions (day, label, minutes) VALUES (%s, %s, %s) RETURNING id",
            (today, (f.label or "Study")[:60], max(1, f.minutes)),
        )
        (fid,) = await cur.fetchone()
    return {"id": fid, "logged_min": max(1, f.minutes)}


@app.post("/gym")
async def log_gym():
    """Convenience: log a workout to the fitness service (source of truth)."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{FITNESS_URL}/visits",
                                  json={"note": "logged from dashboard"}, timeout=6)
            r.raise_for_status()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)}


@app.post("/nutrition")
async def set_nutrition(n: NutritionIn):
    today = _today()
    rating = n.rating.lower().strip()
    if rating not in NUTRITION_RATIO:
        rating = "okay"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO daily_log (day, nutrition) VALUES (%s, %s) "
            "ON CONFLICT (day) DO UPDATE SET nutrition = EXCLUDED.nutrition, updated_at = now()",
            (today, rating),
        )
    return {"rating": rating}


@app.post("/sleep")
async def set_sleep(s: SleepIn):
    today = _today()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO daily_log (day, sleep_hours) VALUES (%s, %s) "
            "ON CONFLICT (day) DO UPDATE SET sleep_hours = EXCLUDED.sleep_hours, updated_at = now()",
            (today, s.hours),
        )
    return {"hours": s.hours}


@app.get("/big3")
async def get_big3():
    today = _today()
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, position, text, done FROM big3 WHERE day = %s ORDER BY position", (today,)
        )
        return {"items": await cur.fetchall()}


@app.post("/big3")
async def set_big3(b: Big3In):
    """Replace today's Big 3 with up to 3 items (keeps done-state by position)."""
    today = _today()
    items = [t.strip() for t in b.items if t and t.strip()][:3]
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute("SELECT position, done FROM big3 WHERE day = %s", (today,))
        prev = {r["position"]: r["done"] for r in await cur.fetchall()}
        await conn.execute("DELETE FROM big3 WHERE day = %s", (today,))
        for i, text in enumerate(items):
            await conn.execute(
                "INSERT INTO big3 (day, position, text, done) VALUES (%s, %s, %s, %s)",
                (today, i, text, prev.get(i, False)),
            )
    return await get_big3()


@app.post("/big3/{item_id}/toggle")
async def toggle_big3(item_id: int):
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "UPDATE big3 SET done = NOT done WHERE id = %s RETURNING id, position, text, done",
            (item_id,),
        )
        row = await cur.fetchone()
    return {"item": row}


@app.get("/captures")
async def get_captures():
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT id, text, kind, done, created_at FROM captures "
            "WHERE NOT done ORDER BY created_at DESC LIMIT 100"
        )
        rows = await cur.fetchall()
    return {"items": rows, "count": len(rows)}


@app.post("/capture")
async def add_capture(c: CaptureIn):
    text = c.text.strip()
    if not text:
        return {"error": "empty"}
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "INSERT INTO captures (text, kind) VALUES (%s, %s) RETURNING id",
            (text[:1000], (c.kind or "note")[:30]),
        )
        (cid,) = await cur.fetchone()
    return {"id": cid, "text": text, "kind": c.kind or "note"}


@app.patch("/capture/{cap_id}")
async def patch_capture(cap_id: int, p: CapturePatch):
    sets, args = [], []
    if p.kind is not None:
        sets.append("kind = %s"); args.append(p.kind[:30])
    if p.done is not None:
        sets.append("done = %s"); args.append(p.done)
    if not sets:
        return {"ok": False}
    args.append(cap_id)
    async with pool.connection() as conn:
        await conn.execute(f"UPDATE captures SET {', '.join(sets)} WHERE id = %s", tuple(args))
    return {"ok": True}


@app.delete("/capture/{cap_id}")
async def delete_capture(cap_id: int):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM captures WHERE id = %s", (cap_id,))
    return {"deleted": cap_id}


@app.get("/history")
async def history(days: int = 30):
    """Per-day study minutes, water, nutrition + score inputs for trend charts."""
    start = _today() - timedelta(days=max(1, min(days, 120)))
    async with pool.connection() as conn:
        conn.row_factory = dict_row
        cur = await conn.execute(
            "SELECT day::text, SUM(minutes) AS study_min FROM focus_sessions "
            "WHERE day >= %s GROUP BY day ORDER BY day", (start,)
        )
        study = {r["day"]: int(r["study_min"]) for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT day::text, water_oz, nutrition FROM daily_log WHERE day >= %s ORDER BY day",
            (start,),
        )
        logs = {r["day"]: r for r in await cur.fetchall()}
    out = []
    d = start
    while d <= _today():
        k = d.isoformat()
        out.append({"day": k, "study_min": study.get(k, 0),
                    "water_oz": (logs.get(k) or {}).get("water_oz", 0),
                    "nutrition": (logs.get(k) or {}).get("nutrition")})
        d += timedelta(days=1)
    return {"days": out}


@app.get("/weekly-review")
async def weekly_review():
    today = _today()
    ws = _week_start(today)
    lws = ws - timedelta(days=7)
    s = await _settings()
    async with pool.connection() as conn:
        conn.row_factory = tuple_row
        cur = await conn.execute(
            "SELECT COALESCE(SUM(minutes),0) FROM focus_sessions WHERE day >= %s", (ws,)
        )
        (study_week,) = await cur.fetchone()
        cur = await conn.execute(
            "SELECT COALESCE(SUM(minutes),0) FROM focus_sessions WHERE day >= %s AND day < %s",
            (lws, ws),
        )
        (study_last,) = await cur.fetchone()
        cur = await conn.execute(
            "SELECT COUNT(DISTINCT day) FROM daily_log WHERE day >= %s AND water_oz >= %s",
            (ws, s["water_goal_oz"]),
        )
        (water_days,) = await cur.fetchone()
    gym = await _gym()
    return {
        "study": {"week_min": int(study_week), "last_min": int(study_last),
                  "goal_min": s["study_weekly_min"]},
        "gym": {"week": gym["week"], "goal": s["gym_weekly"]},
        "water": {"days_hit": int(water_days), "of": 7},
    }


@app.get("/")
async def root():
    return {"app": "Core", "endpoints": ["/today", "/settings", "/water", "/focus",
            "/gym", "/nutrition", "/big3", "/capture", "/captures", "/history",
            "/weekly-review", "/health"]}
