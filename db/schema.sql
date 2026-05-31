-- Diet Loop: Adaptive TDEE closed-loop diet coach
-- SQLite schema

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Single-user profile
CREATE TABLE IF NOT EXISTS profile (
    id              INTEGER PRIMARY KEY,
    sex             TEXT NOT NULL CHECK(sex IN ('male','female')),
    birth_date      DATE NOT NULL,
    height_cm       REAL NOT NULL,
    activity_factor REAL NOT NULL DEFAULT 1.55 CHECK(activity_factor BETWEEN 1.2 AND 1.9),
    goal_weight_kg  REAL,
    target_date     DATE,
    macro_carb_pct  REAL NOT NULL DEFAULT 50,
    macro_prot_pct  REAL NOT NULL DEFAULT 30,
    macro_fat_pct   REAL NOT NULL DEFAULT 20,
    food_prefs_json TEXT DEFAULT '{}',   -- {allergies:[], dislikes:[], cuisine:"korean"}
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- Health checkup measurements (time-series, latest used)
CREATE TABLE IF NOT EXISTS health_check (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            DATE NOT NULL,
    fasting_glucose REAL,
    sbp             INTEGER,
    dbp             INTEGER,
    total_chol      REAL,
    ldl             REAL,
    hdl             REAL,
    triglyceride    REAL,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Daily log: weight + actual intake/exercise
CREATE TABLE IF NOT EXISTS daily_log (
    date                DATE PRIMARY KEY,
    weight_kg           REAL,
    intake_kcal         REAL,
    exercise_kcal       REAL,
    intake_raw          TEXT,
    intake_items_json   TEXT DEFAULT '[]',
    exercise_raw        TEXT,
    exercise_items_json TEXT DEFAULT '[]',
    adherence           TEXT,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- Exercise MET values (kcal = MET × kg × hours)
CREATE TABLE IF NOT EXISTS met_values (
    activity_key TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    met          REAL NOT NULL
);

-- Weekly plan snapshots
CREATE TABLE IF NOT EXISTS weekly_plan (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start          DATE NOT NULL UNIQUE,
    est_tdee            REAL NOT NULL,
    target_intake_kcal  REAL NOT NULL,
    target_exercise_kcal REAL NOT NULL,
    planned_loss_kg     REAL NOT NULL,
    diet_json           TEXT DEFAULT '{}',
    exercise_json       TEXT DEFAULT '{}',
    flags_json          TEXT DEFAULT '[]',
    created_at          TEXT DEFAULT (datetime('now'))
);

-- Goal schedule (trajectory from start to goal)
CREATE TABLE IF NOT EXISTS goal_schedule (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT DEFAULT (datetime('now')),
    mode                TEXT NOT NULL CHECK(mode IN ('deadline_fixed','rate_safe')),
    start_weight_kg     REAL NOT NULL,
    start_date          DATE NOT NULL,
    goal_weight_kg      REAL NOT NULL,
    target_date         DATE NOT NULL,
    projected_date      DATE,
    feasible            INTEGER NOT NULL DEFAULT 1 CHECK(feasible IN (0,1)),
    weekly_targets_json TEXT DEFAULT '[]'  -- [{week_start, target_weight_kg, planned_loss_kg}]
);

-- Food nutrition database (source of truth)
CREATE TABLE IF NOT EXISTS foods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    kcal_per_100g   REAL NOT NULL,
    carb_g          REAL NOT NULL DEFAULT 0,
    protein_g       REAL NOT NULL DEFAULT 0,
    fat_g           REAL NOT NULL DEFAULT 0,
    serving_desc    TEXT,
    source          TEXT DEFAULT 'mfds'
);

CREATE INDEX IF NOT EXISTS idx_foods_name ON foods(name);
CREATE INDEX IF NOT EXISTS idx_foods_category ON foods(category);
CREATE INDEX IF NOT EXISTS idx_daily_log_date ON daily_log(date);
CREATE INDEX IF NOT EXISTS idx_health_check_date ON health_check(date);
CREATE INDEX IF NOT EXISTS idx_weekly_plan_week ON weekly_plan(week_start);
