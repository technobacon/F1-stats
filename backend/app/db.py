"""SQLite persistence layer.

Prototype stand-in for the production PostgreSQL database (Architecture §0).
SQLite keeps the prototype zero-dependency and runnable anywhere; the schema
mirrors the staging + production tables defined in the Technical Pipeline Specs
so the migration to Postgres is mechanical (SERIAL->bigserial, NUMERIC->numeric,
gen_random_uuid() etc.).

Tables:
    staging_race_results      (Pipeline §1.1) -- wins, podiums, points, fastest laps
    staging_qualifying_results(Pipeline §1.1) -- poles (quali P1, NOT race grid)
    staging_drivers           (Pipeline §1.1)
    staging_constructors      (Pipeline §1.1)
    production_trivia_questions(Pipeline §4)  -- verified, client-facing questions
    etl_metadata                              -- weekly-refresh bookkeeping (etl.py)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Configurable so hosts with an ephemeral/read-only app dir can point at a
# writable path (e.g. F1_DB_PATH=/tmp/f1stats.db). Defaults next to the app.
DB_PATH = Path(os.environ.get("F1_DB_PATH", Path(__file__).resolve().parent.parent / "f1stats.db"))


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    # Resolve DB_PATH at call time (not as a default arg) so tests/hosts can
    # override the module attribute and have it take effect.
    conn = sqlite3.connect(str(db_path if db_path is not None else DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Robustness: one connection-per-request means score writes (verify) can
    # overlap reads. WAL lets readers and a writer run concurrently, and a busy
    # timeout makes a brief lock wait-and-retry instead of throwing "database is
    # locked". Both are no-ops on an in-memory DB and safe to set every connect.
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")  # ms
    except sqlite3.OperationalError:
        # Some filesystems (e.g. certain network mounts) reject WAL; the default
        # rollback journal still works, just with coarser locking.
        pass
    return conn


SCHEMA = """
-- Race results: used for wins, podiums, points, fastest laps, DNFs,
-- positions gained, per-circuit stats (Pipeline §1.1)
CREATE TABLE IF NOT EXISTS staging_race_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_id      TEXT    NOT NULL,
    constructor_id TEXT,
    year           INTEGER NOT NULL,
    round          INTEGER NOT NULL,
    circuit_id     TEXT,               -- enables per-circuit questions
    position       INTEGER,            -- finish position; NULL = DNF/DNS
    grid           INTEGER,            -- starting grid position
    fastest_lap    INTEGER DEFAULT 0,  -- boolean (0/1)
    points         REAL    NOT NULL    -- REAL preserves half-points (e.g. 0.5)
);

-- Qualifying results: pole counts use quali P1, NOT race grid (Pipeline §1.1, §3)
CREATE TABLE IF NOT EXISTS staging_qualifying_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_id      TEXT    NOT NULL,
    constructor_id TEXT,
    year           INTEGER NOT NULL,
    round          INTEGER NOT NULL,
    quali_position INTEGER NOT NULL    -- 1 = pole position
);

CREATE TABLE IF NOT EXISTS staging_drivers (
    driver_id   TEXT PRIMARY KEY,
    full_name   TEXT NOT NULL,
    nationality TEXT,
    active_from INTEGER,
    active_to   INTEGER
);

CREATE TABLE IF NOT EXISTS staging_constructors (
    constructor_id TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    nationality    TEXT
);

CREATE TABLE IF NOT EXISTS staging_circuits (
    circuit_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    country    TEXT
);

-- Verified, client-facing questions (Pipeline §4)
CREATE TABLE IF NOT EXISTS production_trivia_questions (
    id                TEXT PRIMARY KEY,            -- UUID
    question_string   TEXT NOT NULL UNIQUE,
    verified_answer   REAL NOT NULL,               -- REAL not INT: F1 has half-points
    answer_kind       TEXT DEFAULT 'count',        -- 'count' | 'points' | 'year' | 'percentage'
    category          TEXT,                        -- UI grouping, e.g. 'reliability'
    display_min       REAL,                        -- optional slider bounds (year/percentage)
    display_max       REAL,
    difficulty_weight REAL DEFAULT 1.0,
    game_mode         TEXT NOT NULL,               -- 'daily','race_week' (legacy 'one_shot' retired -> race_week)
    era_year          INTEGER,                     -- representative year (mid-span) for era-biased serving
    is_active         INTEGER DEFAULT 1,
    scheduled_date    TEXT,                        -- ISO date for cron rotations
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ptq_game_mode      ON production_trivia_questions (game_mode);
CREATE INDEX IF NOT EXISTS idx_ptq_is_active      ON production_trivia_questions (is_active);
CREATE INDEX IF NOT EXISTS idx_ptq_era_year       ON production_trivia_questions (era_year);
CREATE INDEX IF NOT EXISTS idx_ptq_scheduled_date ON production_trivia_questions (scheduled_date);

-- ETL bookkeeping: tracks when staging was last refreshed from the live API so
-- the ingest can honor the weekly cadence (data updates once a week) and skip
-- redundant fetches. Keys: 'last_refresh' (ISO ts), 'source', 'row_counts'.
CREATE TABLE IF NOT EXISTS etl_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ── User accounts & server-authoritative play history ───────────────────────
-- IMPORTANT: these tables are deliberately OUTSIDE reset_db()'s drop list. The
-- question bank is wiped and reloaded on every boot (load_dataset -> reset_db),
-- but accounts and their verified play history must survive that. They persist
-- for as long as the SQLite file does, so on an ephemeral host point F1_DB_PATH
-- at a persistent volume or the accounts vanish on redeploy (see README).
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,                  -- UUID
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,                     -- pbkdf2_sha256$rounds$salt$hash
    selected_team TEXT DEFAULT 'mclaren',            -- cosmetic carry-over (Architecture §2.2)
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Opaque bearer tokens, validated server-side. Logout / expiry = delete the row.
CREATE TABLE IF NOT EXISTS auth_sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions (user_id);

-- Every server-scored guess. The score is computed server-side (scoring.py) and
-- NEVER accepted from the client, so any leaderboard/total derived from this
-- table is trustworthy (Architecture §2.2 trust boundary). Guest play is logged
-- against a client-generated anon_id and reassigned to a user on sign-in (claim).
CREATE TABLE IF NOT EXISTS play_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT REFERENCES users(id) ON DELETE CASCADE,  -- NULL while anonymous
    anon_id      TEXT,                                         -- guest device id (pre-account)
    question_id  TEXT NOT NULL,
    game_mode    TEXT,
    score        INTEGER NOT NULL,
    guess        REAL,
    actual       REAL,
    -- Leaderboard integrity (trust boundary): the daily set is deterministic, so
    -- without a guard a player could replay it and stack the same questions onto
    -- their total over and over. identity_key (the user id, or the guest anon id)
    -- plus period (UTC play date) let a partial UNIQUE index keep at most one
    -- scored row per identity per question per day — see record_event's INSERT OR
    -- IGNORE. Orphan reveals (no identity) are exempt and never counted.
    identity_key TEXT,
    period       TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_play_events_user ON play_events (user_id);
CREATE INDEX IF NOT EXISTS idx_play_events_anon ON play_events (anon_id);
CREATE INDEX IF NOT EXISTS idx_play_events_created ON play_events (created_at);
-- Supports the per-question social-proof aggregate (auth.question_insight).
CREATE INDEX IF NOT EXISTS idx_play_events_question ON play_events (question_id);
-- One scored row per (identity, question, day). Partial so the throwaway
-- anonymous reveals (identity_key NULL/'') are never deduped against each other.
CREATE UNIQUE INDEX IF NOT EXISTS uq_play_events_dedup
    ON play_events (identity_key, question_id, period)
    WHERE identity_key IS NOT NULL AND identity_key != '';

-- First-party product analytics. Pseudonymous, self-contained (no third-party
-- tag, no cross-site cookies): events are keyed by the same client-generated
-- anon_id used for guest play, plus a per-tab session id. This is UNTRUSTED
-- client data used only for AGGREGATE metrics — it never feeds scoring or the
-- leaderboard (those stay on play_events behind the trust boundary). Kept OUTSIDE
-- reset_db()'s drop list so history survives the boot-time question reseed; the
-- boot path prunes rows older than the retention window to bound growth.
CREATE TABLE IF NOT EXISTS analytics_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event      TEXT NOT NULL,        -- allow-listed name (analytics.EVENT_NAMES)
    anon_id    TEXT,                 -- stable per-device visitor id (pseudonymous)
    user_id    TEXT,                 -- set when signed in; no FK (best-effort insert)
    session_id TEXT,                 -- per-tab visit id
    props      TEXT,                 -- small sanitized JSON blob (e.g. {"mode":"daily"})
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_analytics_created ON analytics_events (created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_event   ON analytics_events (event);
CREATE INDEX IF NOT EXISTS idx_analytics_anon    ON analytics_events (anon_id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created. CREATE TABLE IF
    NOT EXISTS never alters an existing table, so a DB seeded before a column was
    added would otherwise crash on the schema's CREATE INDEX (and on SELECTs that
    reference the column). PRAGMA returns no rows when the table is absent, so this
    is a safe no-op on a fresh database."""
    existing = {r[1] for r in conn.execute("PRAGMA table_info(production_trivia_questions)")}
    if existing and "era_year" not in existing:
        conn.execute("ALTER TABLE production_trivia_questions ADD COLUMN era_year INTEGER")

    # play_events gained dedup columns (identity_key, period) after launch. Add
    # them before SCHEMA's CREATE UNIQUE INDEX runs, or that index would fail on a
    # pre-existing table. PRAGMA returns no rows when the table is absent (fresh
    # DB), so this is a safe no-op there.
    pe_cols = {r[1] for r in conn.execute("PRAGMA table_info(play_events)")}
    if pe_cols and "identity_key" not in pe_cols:
        conn.execute("ALTER TABLE play_events ADD COLUMN identity_key TEXT")
    if pe_cols and "period" not in pe_cols:
        conn.execute("ALTER TABLE play_events ADD COLUMN period TEXT")


def init_db(conn: sqlite3.Connection) -> None:
    _migrate(conn)              # bring a pre-existing DB up to the current columns first
    conn.executescript(SCHEMA)  # then create any missing tables/indexes
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Drop and recreate the DATA tables (staging + question bank). Used by the
    seed script and tests, and on every boot via load_dataset.

    The user/account tables (users, auth_sessions, play_events) are intentionally
    NOT dropped here: re-seeding the question bank must never wipe accounts or
    their verified play history. init_db (re)creates them if absent and otherwise
    leaves their data untouched."""
    for table in (
        "staging_race_results",
        "staging_qualifying_results",
        "staging_drivers",
        "staging_constructors",
        "staging_circuits",
        "production_trivia_questions",
        "etl_metadata",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db(conn)
