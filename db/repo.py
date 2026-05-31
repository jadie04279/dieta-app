"""
DB repository — SQLite (local, no DATABASE_URL) or PostgreSQL (cloud).
Set DATABASE_URL env var to switch to PostgreSQL (Supabase, Railway, etc.).
All public functions return plain dicts/lists; no ORM; no leaked connections.
"""
import sqlite3
import json
import os
import datetime as _dt
from pathlib import Path
from typing import Optional

_DB_PATH     = Path(__file__).parent.parent / "data" / "app.db"
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SCHEMA_PG   = Path(__file__).parent / "schema_pg.sql"


def _normalize_row(row: dict) -> dict:
    """PostgreSQL returns date/datetime as native objects; convert to ISO strings for consistency."""
    return {
        k: v.isoformat() if isinstance(v, (_dt.date, _dt.datetime)) else v
        for k, v in row.items()
    }


# ── Connection wrapper ────────────────────────────────────────────────────────

class _Cur:
    """Cursor wrapper: fetchone/fetchall always return plain dicts with dates as ISO strings."""
    def __init__(self, raw):
        self._raw = raw

    def fetchone(self) -> Optional[dict]:
        row = self._raw.fetchone()
        return _normalize_row(dict(row)) if row is not None else None

    def fetchall(self) -> list[dict]:
        return [_normalize_row(dict(r)) for r in self._raw.fetchall()]

    @property
    def lastrowid(self):
        return self._raw.lastrowid


class _Conn:
    """
    Unified connection for both SQLite and psycopg2.
    Translates ? → %s and datetime('now') → CURRENT_TIMESTAMP for PostgreSQL.
    """
    _PG_SUBS = (
        ("?",               "%s"),
        ("datetime('now')", "CURRENT_TIMESTAMP"),
        ("INSERT OR IGNORE INTO", "INSERT INTO"),
    )

    def __init__(self, raw, driver: str):
        self._raw    = raw
        self._driver = driver   # "sqlite" | "pg"

    def _adapt(self, sql: str) -> str:
        if self._driver == "pg":
            for old, new in self._PG_SUBS:
                sql = sql.replace(old, new)
        return sql

    def execute(self, sql: str, params=()) -> _Cur:
        sql = self._adapt(sql)
        if self._driver == "pg":
            cur = self._raw.cursor()
            cur.execute(sql, params or None)
        else:
            cur = self._raw.execute(sql, params)
        return _Cur(cur)

    def executemany(self, sql: str, rows) -> None:
        sql = self._adapt(sql)
        if self._driver == "pg":
            cur = self._raw.cursor()
            cur.executemany(sql, rows)
        else:
            self._raw.executemany(sql, rows)

    def executescript(self, sql: str) -> None:
        if self._driver == "pg":
            stmts = [
                s.strip() for s in sql.split(";")
                if s.strip() and not s.strip().startswith("--")
            ]
            try:
                cur = self._raw.cursor()
                for stmt in stmts:
                    cur.execute(stmt)
                self._raw.commit()
            except Exception:
                self._raw.rollback()
                raise
        else:
            self._raw.executescript(sql)

    def __enter__(self):
        self._raw.__enter__()
        return self

    def __exit__(self, *args):
        return self._raw.__exit__(*args)

    def close(self) -> None:
        self._raw.close()


def get_connection() -> _Conn:
    url = os.environ.get("DATABASE_URL", "")
    if url:
        import psycopg2
        import psycopg2.extras
        raw = psycopg2.connect(url)
        raw.cursor_factory = psycopg2.extras.RealDictCursor
        return _Conn(raw, "pg")
    else:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(_DB_PATH))
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute("PRAGMA foreign_keys=ON")
        return _Conn(raw, "sqlite")


def init_db() -> None:
    """Create tables and seed reference data (idempotent)."""
    conn = get_connection()
    schema_path = _SCHEMA_PG if conn._driver == "pg" else _SCHEMA_PATH
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    _seed_met_values(conn)
    _seed_foods(conn)
    conn.close()


def _seed_met_values(conn: _Conn) -> None:
    count = (conn.execute("SELECT COUNT(*) AS cnt FROM met_values").fetchone() or {}).get("cnt", 0)
    if count > 0:
        return
    csv_path = Path(__file__).parent.parent / "data" / "met_seed.csv"
    if not csv_path.exists():
        return
    import csv
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = [(r["activity_key"], r["name"], r["category"], float(r["met"]))
                for r in csv.DictReader(f)]
    with conn:
        conn.executemany(
            "INSERT INTO met_values(activity_key,name,category,met) VALUES(?,?,?,?)",
            rows,
        )


def _seed_foods(conn: _Conn) -> None:
    count = (conn.execute("SELECT COUNT(*) AS cnt FROM foods").fetchone() or {}).get("cnt", 0)
    if count > 0:
        return
    csv_path = Path(__file__).parent.parent / "data" / "foods_seed.csv"
    if not csv_path.exists():
        return
    import csv
    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append((
                r["name"], r["category"],
                float(r["kcal_per_100g"]), float(r["carb_g"]),
                float(r["protein_g"]), float(r["fat_g"]),
                r.get("serving_desc", ""),
            ))
    with conn:
        conn.executemany(
            """INSERT INTO foods(name,category,kcal_per_100g,carb_g,protein_g,fat_g,serving_desc)
               VALUES(?,?,?,?,?,?,?)""",
            rows,
        )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def get_profile() -> Optional[dict]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
    conn.close()
    if row and row.get("food_prefs_json"):
        try:
            row["food_prefs_json"] = json.loads(row["food_prefs_json"])
        except (json.JSONDecodeError, TypeError):
            row["food_prefs_json"] = {}
    return row


def upsert_profile(data: dict) -> None:
    fields = [
        "sex", "birth_date", "height_cm", "activity_factor",
        "goal_weight_kg", "target_date",
        "macro_carb_pct", "macro_prot_pct", "macro_fat_pct",
        "food_prefs_json",
    ]
    values = {k: data.get(k) for k in fields}
    if isinstance(values.get("food_prefs_json"), dict):
        values["food_prefs_json"] = json.dumps(values["food_prefs_json"], ensure_ascii=False)
    conn = get_connection()
    with conn:
        existing = conn.execute("SELECT id FROM profile WHERE id=1").fetchone()
        if existing:
            set_clause = ", ".join(f"{k}=?" for k in values)
            conn.execute(
                f"UPDATE profile SET {set_clause}, updated_at=datetime('now') WHERE id=1",
                list(values.values()),
            )
        else:
            cols         = "id, " + ", ".join(values.keys())
            placeholders = "1, " + ", ".join("?" * len(values))
            conn.execute(
                f"INSERT INTO profile({cols}) VALUES({placeholders})",
                list(values.values()),
            )
    conn.close()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def add_health_check(data: dict) -> int:
    fields = ["date", "fasting_glucose", "sbp", "dbp", "total_chol", "ldl", "hdl", "triglyceride", "notes"]
    values = [data.get(f) for f in fields]
    conn   = get_connection()
    with conn:
        cur = conn.execute(
            f"INSERT INTO health_check({','.join(fields)}) VALUES({','.join('?'*len(fields))}) RETURNING id",
            values,
        )
        row = cur.fetchone()
    conn.close()
    return row["id"] if row else -1


def get_latest_health_check() -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM health_check ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()
    return row


def list_health_checks(limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM health_check ORDER BY date DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Daily log
# ---------------------------------------------------------------------------

def upsert_daily_log(data: dict) -> None:
    for field in ("intake_items_json", "exercise_items_json"):
        if isinstance(data.get(field), (list, dict)):
            data[field] = json.dumps(data[field], ensure_ascii=False)

    all_fields = [
        "date", "weight_kg", "intake_kcal", "exercise_kcal",
        "intake_raw", "intake_items_json",
        "exercise_raw", "exercise_items_json", "adherence",
    ]
    # 전달된 필드만 INSERT/UPDATE — None 필드는 건드리지 않아 기존 데이터 보존
    present = ["date"] + [f for f in all_fields if f != "date" and data.get(f) is not None]
    values  = [data.get(f) for f in present]
    update_fields = [f for f in present if f != "date"]

    if not update_fields:
        return

    conn = get_connection()
    with conn:
        conn.execute(
            f"""INSERT INTO daily_log({','.join(present)}, updated_at)
                VALUES({','.join('?'*len(present))}, datetime('now'))
                ON CONFLICT(date) DO UPDATE SET
                {', '.join(f"{f}=excluded.{f}" for f in update_fields)},
                updated_at=datetime('now')""",
            values,
        )
    conn.close()


def get_daily_log(log_date: str) -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM daily_log WHERE date=?", (log_date,)).fetchone()
    conn.close()
    if not row:
        return None
    for field in ("intake_items_json", "exercise_items_json"):
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except (json.JSONDecodeError, TypeError):
                row[field] = []
    return row


def list_daily_logs(start_date: str, end_date: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM daily_log WHERE date BETWEEN ? AND ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    conn.close()
    for d in rows:
        for field in ("intake_items_json", "exercise_items_json"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
    return rows


def get_recent_logs(days: int = 30) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM daily_log ORDER BY date DESC LIMIT ?", (days,)
    ).fetchall()
    conn.close()
    for d in rows:
        for field in ("intake_items_json", "exercise_items_json"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
    return list(reversed(rows))


# ---------------------------------------------------------------------------
# Weekly plan
# ---------------------------------------------------------------------------

def upsert_weekly_plan(data: dict) -> int:
    for field in ("diet_json", "exercise_json", "flags_json"):
        if isinstance(data.get(field), (list, dict)):
            data[field] = json.dumps(data[field], ensure_ascii=False)

    fields = [
        "week_start", "est_tdee", "target_intake_kcal", "target_exercise_kcal",
        "planned_loss_kg", "diet_json", "exercise_json", "flags_json",
    ]
    values = [data.get(f) for f in fields]
    conn   = get_connection()
    with conn:
        cur = conn.execute(
            f"""INSERT INTO weekly_plan({','.join(fields)})
                VALUES({','.join('?'*len(fields))})
                ON CONFLICT(week_start) DO UPDATE SET
                {', '.join(f"{f}=excluded.{f}" for f in fields if f != 'week_start')}
                RETURNING id""",
            values,
        )
        row = cur.fetchone()
    conn.close()
    return row["id"] if row else -1


def get_weekly_plan(week_start: str) -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM weekly_plan WHERE week_start=?", (week_start,)).fetchone()
    conn.close()
    if not row:
        return None
    for field in ("diet_json", "exercise_json", "flags_json"):
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except (json.JSONDecodeError, TypeError):
                row[field] = [] if field == "flags_json" else {}
    return row


def get_latest_weekly_plan() -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM weekly_plan ORDER BY week_start DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    for field in ("diet_json", "exercise_json", "flags_json"):
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except (json.JSONDecodeError, TypeError):
                row[field] = [] if field == "flags_json" else {}
    return row


# ---------------------------------------------------------------------------
# Goal schedule
# ---------------------------------------------------------------------------

def save_goal_schedule(data: dict) -> int:
    if isinstance(data.get("weekly_targets_json"), list):
        data["weekly_targets_json"] = json.dumps(data["weekly_targets_json"], ensure_ascii=False)
    fields = [
        "mode", "start_weight_kg", "start_date",
        "goal_weight_kg", "target_date", "projected_date",
        "feasible", "weekly_targets_json",
    ]
    values = [data.get(f) for f in fields]
    conn   = get_connection()
    with conn:
        cur = conn.execute(
            f"INSERT INTO goal_schedule({','.join(fields)}) VALUES({','.join('?'*len(fields))}) RETURNING id",
            values,
        )
        row = cur.fetchone()
    conn.close()
    return row["id"] if row else -1


def get_latest_goal_schedule() -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM goal_schedule ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    if row.get("weekly_targets_json"):
        try:
            row["weekly_targets_json"] = json.loads(row["weekly_targets_json"])
        except (json.JSONDecodeError, TypeError):
            row["weekly_targets_json"] = []
    return row


# ---------------------------------------------------------------------------
# Foods
# ---------------------------------------------------------------------------

def search_foods(query: str, limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM foods WHERE name LIKE ? ORDER BY name LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    conn.close()
    return rows


def get_food_by_id(food_id: int) -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()
    conn.close()
    return row


def list_foods_by_category(category: str, limit: int = 50) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM foods WHERE category=? ORDER BY name LIMIT ?",
        (category, limit),
    ).fetchall()
    conn.close()
    return rows


def get_foods_for_planner(max_kcal_per_100g: float = 600, limit: int = 100) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM foods WHERE kcal_per_100g <= ? ORDER BY category, name LIMIT ?",
        (max_kcal_per_100g, limit),
    ).fetchall()
    conn.close()
    return rows


def get_all_foods() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM foods ORDER BY category, name").fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# MET values
# ---------------------------------------------------------------------------

def get_met_value(activity_key: str) -> Optional[dict]:
    conn = get_connection()
    row  = conn.execute("SELECT * FROM met_values WHERE activity_key=?", (activity_key,)).fetchone()
    conn.close()
    return row


def get_all_met_values() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM met_values ORDER BY category, name").fetchall()
    conn.close()
    return rows


def search_met_values(query: str, limit: int = 10) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM met_values WHERE name LIKE ? ORDER BY category, name LIMIT ?",
        (f"%{query}%", limit),
    ).fetchall()
    conn.close()
    return rows
