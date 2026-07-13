"""SQLite persistence layer for ScapeMaster.

Prototype stand-in for a production PostgreSQL database. SQLite keeps the
prototype zero-dependency and runnable anywhere; the schema is written so the
migration to Postgres is mechanical (SERIAL->bigserial, NUMERIC->numeric,
gen_random_uuid() etc.).

Tables:
    staging_items      -- curated tradeables + Grand Exchange snapshot (etl.py)
    staging_monsters   -- curated monsters/bosses (OSRS Wiki, CC BY-SA)
    staging_quests     -- curated quest list (OSRS Wiki, CC BY-SA)
    staging_skills     -- the 23 skills (XP values are always computed, never stored)
    production_trivia_questions -- verified, client-facing questions
    etl_metadata       -- weekly-refresh bookkeeping (etl.py)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Configurable so hosts with an ephemeral/read-only app dir can point at a
# writable path (e.g. OSRS_DB_PATH=/tmp/scapemaster.db). Defaults next to the app.
DB_PATH = Path(os.environ.get("OSRS_DB_PATH", Path(__file__).resolve().parent.parent / "scapemaster.db"))


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    # Resolve DB_PATH at call time (not as a default arg) so tests/hosts can
    # override the module attribute and have it take effect.
    # check_same_thread=False: the request-scoped dependency (main.db_conn) opens
    # the connection in one threadpool thread, but FastAPI may run the endpoint
    # (and the generator's cleanup) in a DIFFERENT thread — with the default
    # check the app 500s intermittently under a real server ("SQLite objects
    # created in a thread can only be used in that same thread"). Access is
    # strictly sequential within the request, so cross-thread hand-off is safe.
    conn = sqlite3.connect(
        str(db_path if db_path is not None else DB_PATH), check_same_thread=False
    )
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
-- Tradeable items: curated allowlist joined with the Grand Exchange snapshot.
-- Curated fields (release_year, fame_tier) are never network-sourced; price
-- fields (ge_price, ge_volume) come only from the OSRS Wiki prices API.
CREATE TABLE IF NOT EXISTS staging_items (
    item_id      INTEGER PRIMARY KEY,   -- canonical in-game item id
    name         TEXT    NOT NULL,
    members      INTEGER DEFAULT 1,     -- boolean (0/1)
    buy_limit    INTEGER,               -- GE buy limit per 4 hours
    value        INTEGER,               -- base shop value
    low_alch     INTEGER,               -- Low Level Alchemy yield (coins)
    high_alch    INTEGER,               -- High Level Alchemy yield (coins)
    ge_price     INTEGER,               -- snapshot mid price (24h averages)
    ge_volume    INTEGER,               -- snapshot 24h traded volume
    release_year INTEGER,               -- RS-timeline year the item first existed
    fame_tier    INTEGER DEFAULT 2      -- 1 = iconic ... 3 = known-to-regulars
);

-- Monsters & bosses (values curated from the OSRS Wiki, CC BY-SA).
CREATE TABLE IF NOT EXISTS staging_monsters (
    monster_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    combat_level INTEGER,
    hitpoints    INTEGER,
    max_hit      INTEGER,               -- NULL when the wiki value is ambiguous
    slayer_level INTEGER DEFAULT 1,     -- Slayer level required (1 = none)
    slayer_xp    REAL,                  -- Slayer XP per kill
    release_year INTEGER,
    is_boss      INTEGER DEFAULT 0      -- boolean (0/1)
);

-- Quests (complete list curated from the OSRS Wiki, CC BY-SA).
CREATE TABLE IF NOT EXISTS staging_quests (
    quest_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    difficulty   TEXT,                  -- novice|intermediate|experienced|master|grandmaster
    quest_points INTEGER NOT NULL,
    members      INTEGER DEFAULT 1,     -- boolean (0/1)
    release_year INTEGER,
    series       TEXT
);

-- The 23 skills. XP thresholds are NEVER stored: validation.xp_for_level is the
-- single deterministic source of truth for every XP answer.
CREATE TABLE IF NOT EXISTS staging_skills (
    skill_id     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    members      INTEGER DEFAULT 0,     -- boolean (0/1): members-only skill
    release_year INTEGER
);

-- Verified, client-facing questions.
CREATE TABLE IF NOT EXISTS production_trivia_questions (
    id                TEXT PRIMARY KEY,            -- UUID
    question_string   TEXT NOT NULL UNIQUE,
    verified_answer   REAL NOT NULL,               -- REAL: slayer XP has fractional values
    answer_kind       TEXT DEFAULT 'count',        -- 'count'|'level'|'xp'|'coins'|'year'|'percentage'
    category          TEXT,                        -- UI grouping: 'item'|'monster'|'quest'|'skill'
    display_min       REAL,                        -- optional slider bounds
    display_max       REAL,
    difficulty_weight REAL DEFAULT 1.0,
    game_mode         TEXT NOT NULL,               -- 'daily'
    era_year          INTEGER,                     -- content release year, for era-biased serving
    is_active         INTEGER DEFAULT 1,
    scheduled_date    TEXT,                        -- ISO date for cron rotations
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ptq_game_mode      ON production_trivia_questions (game_mode);
CREATE INDEX IF NOT EXISTS idx_ptq_is_active      ON production_trivia_questions (is_active);
CREATE INDEX IF NOT EXISTS idx_ptq_era_year       ON production_trivia_questions (era_year);
CREATE INDEX IF NOT EXISTS idx_ptq_scheduled_date ON production_trivia_questions (scheduled_date);

-- ETL bookkeeping: tracks when the GE snapshot was last refreshed from the live
-- prices API so the ingest can honor the weekly cadence and skip redundant
-- fetches. Keys: 'last_refresh' (ISO ts), 'source', 'row_counts'.
CREATE TABLE IF NOT EXISTS etl_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Small persistent key/value store for app-level secrets and settings that must
-- survive both reboots and the boot-time bank reseed (reset_db drops
-- etl_metadata, so they can't live there; this table is OUTSIDE the drop list).
-- Currently holds 'slider_salt' — the server-side secret mixed into the
-- slider-bounds RNG so the bounds can't be inverted client-side (service.py).
CREATE TABLE IF NOT EXISTS app_kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── User accounts & server-authoritative play history ───────────────────────
-- IMPORTANT: these tables are deliberately OUTSIDE reset_db()'s drop list. The
-- question bank is wiped and reloaded on every boot (load_dataset -> reset_db),
-- but accounts and their verified play history must survive that. They persist
-- for as long as the SQLite file does, so on an ephemeral host point OSRS_DB_PATH
-- at a persistent volume or the accounts vanish on redeploy (see README).
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,                  -- UUID
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,                     -- pbkdf2_sha256$rounds$salt$hash
    selected_god  TEXT DEFAULT 'saradomin',          -- God Wars faction (cosmetic carry-over)
    email         TEXT,                              -- OPTIONAL: for future streak/daily reminders
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
-- table is trustworthy. Guest play is logged against a client-generated anon_id
-- and reassigned to a user on sign-in (claim).
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

-- Dev-only review queue: questions a maintainer flags as "don't like" from the
-- proofreading tool, so they can be revisited/removed from the committed bank
-- later (see scripts/curate_questions.py). Keyed by question_string (NOT the
-- production_trivia_questions UUID) because the bank is dropped and reseeded with
-- fresh UUIDs on every boot, while the question text is the stable identity. Kept
-- OUTSIDE reset_db()'s drop list for exactly that reason — a flag must outlive the
-- reseed. This is maintainer metadata, never served to players.
CREATE TABLE IF NOT EXISTS dev_flagged_questions (
    question_string TEXT PRIMARY KEY,
    note            TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Drop and recreate the DATA tables (staging + question bank). Used by the
    seed script and tests, and on every boot via load_dataset.

    The user/account tables (users, auth_sessions, play_events) are intentionally
    NOT dropped here: re-seeding the question bank must never wipe accounts or
    their verified play history. init_db (re)creates them if absent and otherwise
    leaves their data untouched."""
    for table in (
        "staging_items",
        "staging_monsters",
        "staging_quests",
        "staging_skills",
        "production_trivia_questions",
        "etl_metadata",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db(conn)
