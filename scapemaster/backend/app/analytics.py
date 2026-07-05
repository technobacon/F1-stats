"""First-party product analytics — self-contained, pseudonymous, no third party.

Consistent with the rest of the project (its own auth, its own scoring), analytics
is first-party: a small allow-listed event log keyed by the same client-generated
``anon_id`` used for guest play, with a per-tab ``session_id``. There is no
third-party tag and no cross-site cookie, so it stays privacy-respecting while
still answering the questions that matter for a daily-return game: how many people
come back, where they drop off in the funnel, and which modes they play.

Two hard rules:
  * This is UNTRUSTED client input. It is validated/bounded on ingest and used
    only for AGGREGATE reporting — it never touches scoring or the leaderboard,
    which stay on ``play_events`` behind the trust boundary.
  * The reporting endpoint is gated by ``OSRS_ANALYTICS_TOKEN`` (see main.py), so
    the dashboard data is never public.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

# Allow-list of event names the client may emit. Anything else is dropped on
# ingest, so the table can't be bloated with arbitrary names.
EVENT_NAMES = frozenset({
    "app_open",        # a visit starts (per tab/session)
    "view",            # navigated to a section (props.view, props.mode)
    "quiz_start",      # began a Daily Slayer Task session (props.mode)
    "quiz_complete",   # finished a session (props.mode, props.score)
    "share",           # tapped Share on the result (props.mode)
    "arcade_play",     # made an Over/Under pick
    "practice_start",  # began Training Grounds practice
    "signup_open",     # opened the account modal
    "signup_success",  # created an account
    "login_success",   # signed in
    "god_select",      # pledged to a god faction (props.god)
})

# Ingest bounds (cheap DoS protection for the public collect endpoint).
MAX_EVENTS_PER_BATCH = 50
_MAX_ID_LEN = 64
_MAX_PROPS_LEN = 512
RETENTION_DAYS = 180  # rows older than this are pruned on boot


def _clip(value, limit: int) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _clean_props(props) -> str | None:
    """Serialize a small dict of scalar props to a bounded JSON string. Anything
    that isn't a flat dict of scalars, or that overflows the size cap, is dropped
    rather than stored (keeps the blob small and predictable)."""
    if not isinstance(props, dict) or not props:
        return None
    flat = {}
    for k, v in list(props.items())[:12]:
        if isinstance(v, (str, int, float, bool)) or v is None:
            flat[str(k)[:32]] = (v[:120] if isinstance(v, str) else v)
    if not flat:
        return None
    blob = json.dumps(flat, separators=(",", ":"))
    return blob if len(blob) <= _MAX_PROPS_LEN else None


def record_events(
    conn: sqlite3.Connection,
    *,
    events: list[dict],
    anon_id: str | None,
    session_id: str | None,
    user_id: str | None = None,
) -> int:
    """Persist a batch of client events, returning how many were stored. Unknown
    event names and malformed entries are silently skipped; the batch is capped.
    Best-effort: callers treat failure as a no-op so analytics never breaks UX."""
    anon_id = _clip(anon_id, _MAX_ID_LEN)
    session_id = _clip(session_id, _MAX_ID_LEN)
    rows = []
    for ev in (events or [])[:MAX_EVENTS_PER_BATCH]:
        if not isinstance(ev, dict):
            continue
        name = ev.get("event")
        if name not in EVENT_NAMES:
            continue
        rows.append((name, anon_id, user_id, session_id, _clean_props(ev.get("props"))))
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO analytics_events (event, anon_id, user_id, session_id, props) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def prune(conn: sqlite3.Connection, keep_days: int = RETENTION_DAYS) -> int:
    """Delete analytics rows older than the retention window. Bounds table growth
    on a long-lived database; returns the number of rows removed."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("DELETE FROM analytics_events WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


# ── Reporting ────────────────────────────────────────────────────────────────
def _date_list(days: int) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def _count_distinct_since(conn: sqlite3.Connection, days: int) -> int:
    """Distinct visitors (by anon_id) seen in the last ``days`` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT COUNT(DISTINCT anon_id) AS n FROM analytics_events "
        "WHERE anon_id IS NOT NULL AND created_at >= ?",
        (cutoff,),
    ).fetchone()
    return int(row["n"] or 0)


def _retention(conn: sqlite3.Connection) -> dict:
    """D1 / D7 return rates from first-seen cohorts.

    For every visitor, take the first UTC day they appear and the set of days they
    appear. A visitor counts toward the D1 (or D7) cohort only once their window is
    complete — i.e. first-seen is at least 2 (or 8) days ago — so partial, still-
    open cohorts don't deflate the rate. D1 = returned on first+1; D7 = returned on
    any of first+1..first+7."""
    today = datetime.now(timezone.utc).date()
    rows = conn.execute(
        "SELECT anon_id, date(created_at) AS d FROM analytics_events "
        "WHERE anon_id IS NOT NULL GROUP BY anon_id, d"
    ).fetchall()
    days_by_anon: dict[str, set] = {}
    for r in rows:
        if r["d"]:
            days_by_anon.setdefault(r["anon_id"], set()).add(
                datetime.strptime(r["d"], "%Y-%m-%d").date()
            )
    d1_elig = d1_ret = d7_elig = d7_ret = 0
    for days in days_by_anon.values():
        first = min(days)
        age = (today - first).days
        if age >= 1:  # D1 window closed
            d1_elig += 1
            if (first + timedelta(days=1)) in days:
                d1_ret += 1
        if age >= 7:  # D7 window closed
            d7_elig += 1
            if any((first + timedelta(days=k)) in days for k in range(1, 8)):
                d7_ret += 1
    return {
        "d1": round(d1_ret / d1_elig, 3) if d1_elig else 0.0,
        "d1_cohort": d1_elig,
        "d7": round(d7_ret / d7_elig, 3) if d7_elig else 0.0,
        "d7_cohort": d7_elig,
    }


def summary(conn: sqlite3.Connection, days: int = 14) -> dict:
    """Aggregate engagement report over the last ``days`` days.

    Combines first-party analytics (visitors, funnel, modes) with the trustworthy
    play_events log (players, scored answers), so the numbers that gate
    monetization/retention decisions are grounded in server-verified play, not
    only client beacons."""
    days = max(1, min(days, 90))
    dates = _date_list(days)
    since = dates[0] + " 00:00:00"

    # Visitors (analytics) and players (play_events) per day, for the trend chart.
    visit_rows = conn.execute(
        "SELECT date(created_at) AS d, COUNT(DISTINCT anon_id) AS n "
        "FROM analytics_events WHERE created_at >= ? GROUP BY d",
        (since,),
    ).fetchall()
    visitors_by_day = {r["d"]: int(r["n"] or 0) for r in visit_rows}

    player_rows = conn.execute(
        "SELECT date(created_at) AS d, COUNT(DISTINCT COALESCE(user_id, anon_id, '')) AS n "
        "FROM play_events WHERE created_at >= ? AND COALESCE(user_id, anon_id) IS NOT NULL "
        "GROUP BY d",
        (since,),
    ).fetchall()
    players_by_day = {r["d"]: int(r["n"] or 0) for r in player_rows}

    active_by_day = [
        {"date": d, "visitors": visitors_by_day.get(d, 0), "players": players_by_day.get(d, 0)}
        for d in dates
    ]

    # Funnel counts over the window (event occurrences).
    funnel_rows = conn.execute(
        "SELECT event, COUNT(*) AS n FROM analytics_events WHERE created_at >= ? GROUP BY event",
        (since,),
    ).fetchall()
    ev = {r["event"]: int(r["n"] or 0) for r in funnel_rows}
    opens = ev.get("app_open", 0)
    starts = ev.get("quiz_start", 0)
    completes = ev.get("quiz_complete", 0)
    shares = ev.get("share", 0)
    signups = ev.get("signup_success", 0)

    def rate(num, den):
        return round(num / den, 3) if den else 0.0

    funnel = {
        "app_open": opens,
        "quiz_start": starts,
        "quiz_complete": completes,
        "share": shares,
        "signup_success": signups,
        # Conversion ratios that tell you where people drop off.
        "start_rate": rate(starts, opens),         # opened -> started a quiz
        "completion_rate": rate(completes, starts),  # started -> finished
        "share_rate": rate(shares, completes),     # finished -> shared (virality)
        "signup_rate": rate(signups, opens),       # visit -> account
    }

    # Mode popularity: quiz starts by mode + arcade + practice.
    mode_rows = conn.execute(
        "SELECT json_extract(props, '$.mode') AS mode, COUNT(*) AS n "
        "FROM analytics_events WHERE event = 'quiz_start' AND created_at >= ? GROUP BY mode",
        (since,),
    ).fetchall()
    modes = {(r["mode"] or "unknown"): int(r["n"] or 0) for r in mode_rows}
    modes["arcade"] = ev.get("arcade_play", 0)
    modes["free_practice"] = ev.get("practice_start", 0)

    # Account growth from the (trustworthy) users table.
    signup_rows = conn.execute(
        "SELECT date(created_at) AS d, COUNT(*) AS n FROM users WHERE created_at >= ? GROUP BY d",
        (since,),
    ).fetchall()
    signups_by_day_map = {r["d"]: int(r["n"] or 0) for r in signup_rows}
    signups_by_day = [{"date": d, "count": signups_by_day_map.get(d, 0)} for d in dates]

    totals = {
        "visitors_all_time": int(conn.execute(
            "SELECT COUNT(DISTINCT anon_id) AS n FROM analytics_events WHERE anon_id IS NOT NULL"
        ).fetchone()["n"] or 0),
        "accounts": int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] or 0),
        "scored_answers": int(conn.execute("SELECT COUNT(*) AS n FROM play_events").fetchone()["n"] or 0),
    }

    return {
        "window_days": days,
        "since": dates[0],
        "dau": _count_distinct_since(conn, 1),
        "wau": _count_distinct_since(conn, 7),
        "mau": _count_distinct_since(conn, 30),
        "totals": totals,
        "active_by_day": active_by_day,
        "funnel": funnel,
        "modes": modes,
        "retention": _retention(conn),
        "signups_by_day": signups_by_day,
    }


def token_configured() -> bool:
    return bool(os.environ.get("OSRS_ANALYTICS_TOKEN", "").strip())


def token_matches(token: str | None) -> bool:
    """Constant-time check of a bearer token against OSRS_ANALYTICS_TOKEN."""
    import hmac
    expected = os.environ.get("OSRS_ANALYTICS_TOKEN", "").strip()
    return bool(expected) and bool(token) and hmac.compare_digest(token.strip(), expected)
