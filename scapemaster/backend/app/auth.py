"""User accounts, sessions, and server-authoritative play history.

This is the implementation of the account layer the Architecture Blueprint only
sketched (§2.1 guest-first, §2.2 account merging + trust boundary). It is
deliberately self-contained — no third-party auth service, no OAuth, no extra
dependencies:

  * Passwords are hashed with PBKDF2-HMAC-SHA256 from the standard-library
    ``hashlib`` (per-user random salt, many rounds). The plaintext is never
    stored and never logged.
  * Sessions are opaque random bearer tokens stored server-side. "Log out" and
    expiry are just a row delete, so a token can be revoked instantly.
  * The trust boundary (Architecture §2.2): leaderboard/profile totals are
    recomputed from ``play_events``, whose ``score`` is always the value the
    server computed in scoring.py — never a number supplied by the client. Guest
    play is recorded against a client-generated ``anon_id`` and reassigned to the
    account on sign-in (``claim_anon_events``), so nothing earned as a guest is
    lost and nothing can be injected by editing localStorage.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from . import profanity

_PBKDF2_ROUNDS = 200_000
_SALT_BYTES = 16
_SESSION_TTL = timedelta(days=30)

# The god factions a player can pledge to (mirrors the frontend GODS map). Used to validate the cosmetic god choice and to bucket the
# God Wars championship leaderboard. 'saradomin' is the default.
GODS = (
    "saradomin", "zamorak", "guthix", "armadyl", "bandos", "zaros",
)
DEFAULT_GOD = "saradomin"


def normalize_god(god: str | None) -> str:
    """Return a known god key, falling back to the default for anything unknown."""
    t = (god or "").strip().lower()
    return t if t in GODS else DEFAULT_GOD


# Conservative username rules: keep it simple, predictable and URL/display-safe.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# Email is OPTIONAL (only collected for future opt-in reminders). We validate the
# shape loosely — a single @ with a dotted domain — rather than trying to be RFC
# 5322 perfect, and cap the length. Blank/None means "no email", not an error.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_EMAIL_LEN = 254


def normalize_email(email: str | None) -> str | None:
    """Return a trimmed, lower-cased email, or None if blank. Raises AuthError on
    a non-empty value that doesn't look like an email."""
    e = (email or "").strip().lower()
    if not e:
        return None
    if len(e) > _MAX_EMAIL_LEN or not _EMAIL_RE.match(e):
        raise AuthError("That doesn't look like a valid email address.")
    return e
_MIN_PASSWORD_LEN = 8
# Cap the password length BEFORE hashing: PBKDF2 hashes the whole input, so an
# unbounded password is a cheap denial-of-service (hash a multi-MB string). 1024
# is far above any real password and well within OWASP guidance. The cap is in
# UTF-8 BYTES and enforced identically at registration (reject) and login
# (cheap reject, no hash) — the two paths must agree, or a password whose byte
# length exceeds what one of them hashes could never verify.
_MAX_PASSWORD_BYTES = 1024


class AuthError(ValueError):
    """Raised for bad input or auth failures the API turns into 4xx responses."""


class RateLimitError(Exception):
    """Raised when an account is temporarily locked after too many failed logins.
    The API turns this into a 429."""

    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__("Too many attempts. Please wait and try again.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


# ── Passwords ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    """Return a self-describing PBKDF2 hash: ``pbkdf2_sha256$rounds$salt$hash``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of ``password`` against a stored PBKDF2 hash."""
    encoded = (password or "").encode()
    if len(encoded) > _MAX_PASSWORD_BYTES:
        # Registration rejects these, so no stored hash can match; reject before
        # hashing (never truncate — a truncated digest could silently diverge
        # from the registration-time hash of the full input).
        return False
    try:
        algo, rounds_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(hash_hex)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", encoded, bytes.fromhex(salt_hex), int(rounds_s),
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(candidate, expected)


# A throwaway hash of a random value. authenticate() verifies against this when a
# username doesn't exist so a missing user costs the same time as a wrong
# password — closing the timing side-channel that would otherwise reveal which
# usernames are registered.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ── Login rate limiting (brute-force protection) ─────────────────────────────
# In-memory sliding window keyed by username. Adequate for the single-process
# prototype; production would back this with Redis (Architecture §0). Successful
# logins clear the counter, so a legitimate user is never locked out by their own
# eventual success.
_MAX_FAILED_LOGINS = 8
_LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes
_failed_logins: dict[str, list[float]] = {}


def _norm_key(username: str) -> str:
    return (username or "").strip().lower()


def check_login_allowed(username: str) -> None:
    """Raise RateLimitError if this username has too many recent failures."""
    key = _norm_key(username)
    now = time.monotonic()
    hits = [t for t in _failed_logins.get(key, []) if now - t < _LOCKOUT_WINDOW_SECONDS]
    _failed_logins[key] = hits
    if len(hits) >= _MAX_FAILED_LOGINS:
        retry_after = int(_LOCKOUT_WINDOW_SECONDS - (now - hits[0])) + 1
        raise RateLimitError(retry_after)


def note_failed_login(username: str) -> None:
    _failed_logins.setdefault(_norm_key(username), []).append(time.monotonic())


def clear_failed_logins(username: str) -> None:
    _failed_logins.pop(_norm_key(username), None)


def reset_rate_limits() -> None:
    """Clear all rate-limit state (used by tests)."""
    _failed_logins.clear()


# ── Users & sessions ─────────────────────────────────────────────────────────
def _user_public(row: sqlite3.Row) -> dict:
    """The non-secret view of a user (never includes the password hash)."""
    return {
        "id": row["id"],
        "username": row["username"],
        "selected_god": row["selected_god"],
        "created_at": row["created_at"],
    }


def create_user(
    conn: sqlite3.Connection, username: str, password: str,
    selected_god: str | None = None, email: str | None = None,
) -> dict:
    """Create an account, returning its public view. Raises AuthError on invalid
    input or a taken username. The god is the cosmetic faction the player pledges
    to (PRD §5.3); an unknown value falls back to the default rather than erroring.
    Email is optional (opt-in reminders) and validated only when provided."""
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        raise AuthError(
            "Username must be 3-32 characters using letters, numbers, '.', '_' or '-'."
        )
    if profanity.contains_profanity(username):
        raise AuthError("Please choose a different username.")
    if len(password or "") < _MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
    if len(password.encode()) > _MAX_PASSWORD_BYTES:
        raise AuthError("That password is too long.")
    email = normalize_email(email)  # raises AuthError on a malformed non-empty value

    user_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, selected_god, email) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, hash_password(password), normalize_god(selected_god), email),
        )
    except sqlite3.IntegrityError as exc:  # UNIQUE(username) — case-insensitive
        raise AuthError("That username is already taken.") from exc
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_public(row)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    """Return the user's public view if the credentials are valid, else None.

    Runs a password verification even when the username is unknown (against a
    dummy hash) so the response time doesn't reveal whether a username exists."""
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", ((username or "").strip(),)
    ).fetchone()
    if row is None:
        verify_password(password or "", _DUMMY_HASH)  # equalize timing; result ignored
        return None
    if not verify_password(password or "", row["password_hash"]):
        return None
    return _user_public(row)


def create_session(conn: sqlite3.Connection, user_id: str) -> str:
    """Mint and store an opaque session token for the user; return the token."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, _iso(_now() + _SESSION_TTL)),
    )
    conn.commit()
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> dict | None:
    """Resolve a bearer token to a user's public view, or None if it is missing,
    unknown or expired. Expired tokens are pruned opportunistically."""
    if not token:
        return None
    row = conn.execute(
        "SELECT u.*, s.expires_at AS _expires_at "
        "FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    try:
        expires = datetime.strptime(row["_expires_at"], "%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError):
        expires = None
    if expires is not None and expires < _now():
        delete_session(conn, token)
        return None
    return _user_public(row)


def delete_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    conn.commit()


def prune_expired_sessions(conn: sqlite3.Connection) -> int:
    """Bulk-delete expired sessions; returns how many were removed. session_user
    only prunes a token when it is presented, so sessions of users who never
    return would otherwise accumulate forever. Run at boot (main.lifespan)."""
    cur = conn.execute(
        "DELETE FROM auth_sessions WHERE expires_at < ?", (_iso(_now()),)
    )
    conn.commit()
    return cur.rowcount


def set_selected_god(conn: sqlite3.Connection, user_id: str, god: str) -> str:
    """Persist the cosmetic god choice (Architecture §2.2 cosmetic carry-over,
    PRD §5.3). The value is normalized to a known god; returns what was stored."""
    god = normalize_god(god)
    conn.execute("UPDATE users SET selected_god = ? WHERE id = ?", (god, user_id))
    conn.commit()
    return god


# ── Play history (the trust-boundary foundation) ─────────────────────────────
def record_event(
    conn: sqlite3.Connection,
    *,
    question_id: str,
    score: int,
    user_id: str | None = None,
    anon_id: str | None = None,
    game_mode: str | None = None,
    guess: float | None = None,
    actual: float | None = None,
    period: str | None = None,
) -> bool:
    """Persist one server-scored guess. Called from the verify endpoint with the
    score the server just computed — the client never supplies the score.

    Returns True if a new scored row was written, False if it was a deduped
    replay. The daily set is deterministic, so a player could otherwise re-run it
    and stack the same questions onto their total; the (identity, question, day)
    UNIQUE index plus INSERT OR IGNORE pin each question to one scored row per
    player per day. Reveals with no identity (no account and no anon id) are
    always written as orphans — they belong to nobody and never count."""
    identity_key = user_id or anon_id or ""
    period = period or _now().strftime("%Y-%m-%d")
    cur = conn.execute(
        "INSERT OR IGNORE INTO play_events "
        "(user_id, anon_id, question_id, game_mode, score, guess, actual, identity_key, period) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, anon_id, question_id, game_mode, int(score), guess, actual,
         identity_key or None, period),
    )
    conn.commit()
    return cur.rowcount > 0


def claim_anon_events(conn: sqlite3.Connection, anon_id: str | None, user_id: str) -> int:
    """Reassign a guest device's events to a freshly-signed-in account and return
    how many were claimed. Only server-verified rows move, so totals stay honest
    (Architecture §2.2, verified-event merge). Idempotent: already-owned rows are
    untouched by the ``user_id IS NULL`` guard."""
    if not anon_id:
        return 0
    # Re-key the rows to the user (identity_key too), so the dedup guard now treats
    # them as the account's: replaying the same day's question after signing in is
    # rejected instead of double-counted.
    cur = conn.execute(
        "UPDATE OR IGNORE play_events SET user_id = ?, anon_id = NULL, identity_key = ? "
        "WHERE anon_id = ? AND user_id IS NULL",
        (user_id, user_id, anon_id),
    )
    conn.commit()
    return cur.rowcount


# Streak freeze: a single missed day inside an otherwise unbroken run is forgiven
# once, so one slip doesn't reset a long streak to zero. This is the auto "streak
# freeze" Duolingo popularised — one of the highest-impact churn reducers there is,
# because losing a long streak is the moment players quit for good. A second gap,
# or any gap of two-or-more days in a row, still ends the streak. The forgiven day
# itself is NOT counted toward the total; it only bridges the run.
STREAK_FREEZE_MAX_GAP = 2  # a gap of exactly one missing day (2 days apart) is bridgeable


def daily_streak(conn: sqlite3.Connection, user_id: str) -> int:
    """Consecutive-day streak of completing a daily-mode challenge, recomputed
    server-side from play_events (Architecture §2.2 — never trusts the client).

    A day counts if the user logged any 'daily' play that UTC date. The streak is
    the run of days ending today or yesterday; a single missed day inside the run
    is forgiven once (the streak freeze, see STREAK_FREEZE_MAX_GAP). If the most
    recent daily play is older than yesterday the streak has lapsed and is 0 — a
    freeze protects a gap *within* a live run, it does not revive a dead one."""
    rows = conn.execute(
        "SELECT DISTINCT date(created_at) AS d FROM play_events "
        "WHERE user_id = ? AND game_mode = 'daily' ORDER BY d DESC",
        (user_id,),
    ).fetchall()
    days = [r["d"] for r in rows if r["d"]]
    if not days:
        return 0
    today = _now().date()
    most_recent = datetime.strptime(days[0], "%Y-%m-%d").date()
    if (today - most_recent).days > 1:
        return 0  # lapsed: nothing today or yesterday
    streak, prev = 1, most_recent
    freeze_available = True
    for d in days[1:]:
        cur = datetime.strptime(d, "%Y-%m-%d").date()
        gap = (prev - cur).days
        if gap == 1:
            streak += 1
            prev = cur
        elif gap == STREAK_FREEZE_MAX_GAP and freeze_available:
            # Forgive a single one-day gap, once: the missed day isn't counted,
            # but the run continues from the earlier play.
            freeze_available = False
            streak += 1
            prev = cur
        else:
            break
    return streak


def question_insight(conn: sqlite3.Connection, question_id: str, score: int) -> dict | None:
    """Aggregate social proof for one question, derived from server-scored
    play_events: how many players have answered it, the average score, and what
    percentage of them this score beats. Returns None until a small sample exists
    so we never show a lonely 'you beat 0%' on a brand-new question.

    Like every number behind the trust boundary (Architecture §2.2) this comes
    only from server-computed scores, so the comparison can't be gamed."""
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(AVG(score), 0) AS avg_score, "
        "       SUM(CASE WHEN score < ? THEN 1 ELSE 0 END) AS below "
        "FROM play_events WHERE question_id = ?",
        (int(score), question_id),
    ).fetchone()
    n = row["n"] or 0
    if n < _INSIGHT_MIN_SAMPLE:
        return None
    return {
        "players_answered": n,
        "average_score": int(round(row["avg_score"] or 0)),
        "beat_percent": int(round((row["below"] or 0) / n * 100)),
    }


# Don't surface "you beat X% of players" until at least this many have answered —
# a percentile off one or two data points is noise, not social proof.
_INSIGHT_MIN_SAMPLE = 5


def user_stats(conn: sqlite3.Connection, user_id: str) -> dict:
    """Server-derived competitive stats for a user, recomputed from play_events.
    Never trusts a client-supplied aggregate (Architecture §2.2)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(score), 0) AS points, COUNT(*) AS answered, "
        "       COALESCE(AVG(score), 0) AS avg_score, COALESCE(MAX(score), 0) AS best "
        "FROM play_events WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    answered = row["answered"] or 0
    return {
        "lifetime_points": int(row["points"] or 0),
        "questions_answered": answered,
        # accuracy = average proximity, i.e. mean(score)/max_score, in [0, 1]
        "average_accuracy": round((row["avg_score"] or 0) / 5000.0, 3) if answered else 0.0,
        "best_answer": int(row["best"] or 0),
        "daily_streak": daily_streak(conn, user_id),
    }


def _period_cutoff(period: str) -> str | None:
    """UTC lower bound (as a 'YYYY-MM-DD HH:MM:SS' string comparable to
    play_events.created_at) for a leaderboard window, or None for all-time.

      * 'daily'  -> since 00:00 UTC today
      * 'weekly' -> since 00:00 UTC Monday (ISO week start)
      * anything else -> None (all-time)

    Daily and weekly boards reset, so newcomers always have a fresh race to win —
    that is the daily-return hook an all-time-only board can't give."""
    now = _now()
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight - timedelta(days=now.weekday())  # Monday
    else:
        return None
    return start.strftime("%Y-%m-%d %H:%M:%S")


def leaderboard(conn: sqlite3.Connection, limit: int = 20, period: str = "all") -> list[dict]:
    """Top players by server-verified points over a window ('all'|'daily'|'weekly').
    The whole point of the trust boundary: these numbers come only from
    play_events, so they can't be forged by editing localStorage."""
    cutoff = _period_cutoff(period)
    where = "e.created_at >= ?" if cutoff else "1=1"
    params: tuple = (cutoff, limit) if cutoff else (limit,)
    rows = conn.execute(
        "SELECT u.username AS username, u.selected_god AS selected_god, "
        "       COALESCE(SUM(e.score), 0) AS points, COUNT(e.id) AS answered "
        "FROM users u JOIN play_events e ON e.user_id = u.id "
        f"WHERE {where} "
        "GROUP BY u.id HAVING answered > 0 "
        "ORDER BY points DESC, answered ASC LIMIT ?",
        params,
    ).fetchall()
    return [
        {
            "rank": i + 1,
            "username": r["username"],
            "selected_god": r["selected_god"],
            "lifetime_points": int(r["points"] or 0),
            "questions_answered": r["answered"],
        }
        for i, r in enumerate(rows)
    ]


def daily_field(conn: sqlite3.Connection, identity_key: str | None,
                period: str | None = None) -> dict:
    """Today's task field: everyone — member or guest — who has scored in the
    day's Slayer Task, and where the caller finished among them. Built from the
    same server-scored play_events as the HiScores, so a field position can't be
    forged; unlike the HiScores it counts guest identities too, so a brand-new
    player still gets a real 'P4 of 23 today'.

    Returns {players, rank, points, beat_percent}; rank is 0 when the caller
    hasn't scored in the window (players still reports the field size). Ties
    share the better rank."""
    period = period or _now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT identity_key AS ik, COALESCE(SUM(score), 0) AS pts "
        "FROM play_events "
        "WHERE game_mode = 'daily' AND period = ? "
        "  AND identity_key IS NOT NULL AND identity_key != '' "
        "GROUP BY identity_key",
        (period,),
    ).fetchall()
    players = len(rows)
    points = next(
        (int(r["pts"]) for r in rows if identity_key and r["ik"] == identity_key), None
    )
    if points is None:
        return {"players": players, "rank": 0, "points": 0, "beat_percent": 0}
    rank = 1 + sum(1 for r in rows if r["pts"] > points)
    beaten = sum(1 for r in rows if r["pts"] < points)
    beat_percent = int(round(beaten / (players - 1) * 100)) if players > 1 else 0
    return {"players": players, "rank": rank, "points": points, "beat_percent": beat_percent}


def god_leaderboard(conn: sqlite3.Connection, period: str = "all") -> list[dict]:
    """The God Wars championship (PRD §5.3): every player's server-verified
    points bucketed by the god faction they pledged to, ranked to show
    'which god's followers are statistically the wisest'. Points come only from
    play_events, so a faction's standing can't be inflated from the client.

    Average points per member is reported alongside the total so a small, sharp
    fanbase isn't buried purely by a larger one's headcount."""
    cutoff = _period_cutoff(period)
    where = "e.created_at >= ?" if cutoff else "1=1"
    params: tuple = (cutoff,) if cutoff else ()
    rows = conn.execute(
        "SELECT u.selected_god AS god, COALESCE(SUM(e.score), 0) AS points, "
        "       COUNT(e.id) AS answered, COUNT(DISTINCT u.id) AS members "
        "FROM users u JOIN play_events e ON e.user_id = u.id "
        f"WHERE {where} "
        "GROUP BY u.selected_god HAVING answered > 0 "
        "ORDER BY points DESC",
        params,
    ).fetchall()
    return [
        {
            "rank": i + 1,
            "god": r["god"] or DEFAULT_GOD,
            "points": int(r["points"] or 0),
            "members": r["members"],
            "questions_answered": r["answered"],
            "avg_per_member": int(round((r["points"] or 0) / r["members"])) if r["members"] else 0,
        }
        for i, r in enumerate(rows)
    ]


def god_overview(conn: sqlite3.Connection) -> dict:
    """A full-grid snapshot for the first-run god picker: how many registered
    players have pledged to each god and how the Constructors'
    Championship is going.

    Unlike god_leaderboard (which only lists factions that have already scored),
    this returns EVERY god — including ones nobody has joined yet — so a newcomer
    deciding which side to back sees the complete grid: the headcount behind each
    god and its all-time points race. Members come from the users table;
    points come only from play_events, so the standings can't be inflated from the
    client (Architecture §2.2)."""
    rows = conn.execute(
        "SELECT u.selected_god AS god, COUNT(DISTINCT u.id) AS members, "
        "       COALESCE(SUM(e.score), 0) AS points "
        "FROM users u LEFT JOIN play_events e ON e.user_id = u.id "
        "GROUP BY u.selected_god",
    ).fetchall()
    # Fold each stored god into its normalized key (unknown/legacy values collapse
    # onto the default rather than appearing as a phantom faction).
    tally: dict[str, list[int]] = {}
    for r in rows:
        key = normalize_god(r["god"])
        agg = tally.setdefault(key, [0, 0])
        agg[0] += r["members"]
        agg[1] += int(r["points"] or 0)
    gods = []
    for key in GODS:
        members, points = tally.get(key, (0, 0))
        gods.append({"god": key, "members": members, "points": points})
    # Rank by points, then headcount, then name for a stable, sensible order.
    gods.sort(key=lambda t: (-t["points"], -t["members"], t["god"]))
    for i, t in enumerate(gods):
        t["rank"] = i + 1
    return {"gods": gods, "total_players": sum(t["members"] for t in gods)}


# ── Personal standing ("your bank") ────────────────────────────────────────
def my_rank(conn: sqlite3.Connection, user_id: str, period: str = "all") -> dict:
    """Where the caller sits on the global board for a window, plus a percentile.
    Rank is 1 + (players with strictly more verified points); percentile is the
    share of ranked players at or below them. Reuses the same play_events totals
    the public leaderboard does, so the number can't be forged from the client."""
    cutoff = _period_cutoff(period)
    where = "e.created_at >= ?" if cutoff else "1=1"
    # Per-player point totals for the window (only those who have scored count).
    rows = conn.execute(
        "SELECT u.id AS uid, COALESCE(SUM(e.score), 0) AS points "
        "FROM users u JOIN play_events e ON e.user_id = u.id "
        f"WHERE {where} "
        "GROUP BY u.id HAVING COUNT(e.id) > 0",
        (cutoff,) if cutoff else (),
    ).fetchall()
    totals = {r["uid"]: int(r["points"] or 0) for r in rows}
    mine = totals.get(user_id, 0)
    total_ranked = len(totals)
    if user_id not in totals:
        # Hasn't scored in this window yet: unranked, but report the field size.
        return {"rank": 0, "total_ranked": total_ranked, "points": 0, "percentile": 0}
    ahead = sum(1 for p in totals.values() if p > mine)
    rank = ahead + 1
    percentile = round(100 * (total_ranked - rank) / max(total_ranked - 1, 1)) if total_ranked > 1 else 100
    return {"rank": rank, "total_ranked": total_ranked, "points": mine, "percentile": percentile}


def god_detail(conn: sqlite3.Connection, user_id: str, god: str, period: str = "all") -> dict:
    """The caller's personal stake in the God Wars championship: their
    faction's standing (rank + total) and a within-god leaderboard with the
    caller located in it. All points come only from play_events."""
    god = normalize_god(god)
    cutoff = _period_cutoff(period)
    where = "e.created_at >= ?" if cutoff else "1=1"
    base_params: tuple = (cutoff,) if cutoff else ()

    # Faction's championship rank for the window (reuse the public board).
    standings = god_leaderboard(conn, period=period)
    god_row = next((t for t in standings if t["god"] == god), None)
    god_rank = god_row["rank"] if god_row else 0
    god_points = god_row["points"] if god_row else 0
    members = god_row["members"] if god_row else 0

    # Within-god leaderboard: every scoring member of this faction, ranked.
    rows = conn.execute(
        "SELECT u.id AS uid, u.username AS username, COALESCE(SUM(e.score), 0) AS points "
        "FROM users u JOIN play_events e ON e.user_id = u.id "
        f"WHERE u.selected_god = ? AND {where} "
        "GROUP BY u.id HAVING COUNT(e.id) > 0 "
        "ORDER BY points DESC, u.username ASC",
        (god, *base_params),
    ).fetchall()
    leaders, me = [], None
    for i, r in enumerate(rows):
        entry = {"rank": i + 1, "username": r["username"], "points": int(r["points"] or 0)}
        if r["uid"] == user_id:
            me = entry
        leaders.append(entry)
    return {
        "god": god,
        "god_rank": god_rank,
        "god_points": god_points,
        "members": members,
        "your_points": me["points"] if me else 0,
        "your_rank_in_god": me["rank"] if me else 0,
        "leaders": leaders[:8],
    }


def play_history(conn: sqlite3.Connection, user_id: str, days: int = 126) -> dict:
    """Per-day Daily-Challenge play totals for the last `days` days, for the
    streak heatmap. Only days the user actually played appear; the client fills
    the empty cells. Derived from play_events, so it matches the streak/score."""
    days = max(1, min(days, 366))
    cutoff = (_now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT date(created_at) AS d, COUNT(*) AS q, COALESCE(SUM(score), 0) AS p "
        "FROM play_events "
        "WHERE user_id = ? AND game_mode = 'daily' AND date(created_at) >= ? "
        "GROUP BY date(created_at) ORDER BY d",
        (user_id, cutoff),
    ).fetchall()
    return {
        "days": [
            {"date": r["d"], "questions": r["q"], "points": int(r["p"] or 0)}
            for r in rows
        ]
    }
