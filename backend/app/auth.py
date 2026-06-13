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

_PBKDF2_ROUNDS = 200_000
_SALT_BYTES = 16
_SESSION_TTL = timedelta(days=30)

# Conservative username rules: keep it simple, predictable and URL/display-safe.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_MIN_PASSWORD_LEN = 8
# Cap the password length BEFORE hashing: PBKDF2 hashes the whole input, so an
# unbounded password is a cheap denial-of-service (hash a multi-MB string). 1024
# is far above any real password and well within OWASP guidance.
_MAX_PASSWORD_LEN = 1024


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
    try:
        algo, rounds_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(hash_hex)
        candidate = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode()[:_MAX_PASSWORD_LEN],
            bytes.fromhex(salt_hex), int(rounds_s),
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
        "selected_team": row["selected_team"],
        "created_at": row["created_at"],
    }


def create_user(conn: sqlite3.Connection, username: str, password: str) -> dict:
    """Create an account, returning its public view. Raises AuthError on invalid
    input or a taken username."""
    username = (username or "").strip()
    if not _USERNAME_RE.match(username):
        raise AuthError(
            "Username must be 3-32 characters using letters, numbers, '.', '_' or '-'."
        )
    if len(password or "") < _MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
    if len(password) > _MAX_PASSWORD_LEN:
        raise AuthError(f"Password must be at most {_MAX_PASSWORD_LEN} characters.")

    user_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
            (user_id, username, hash_password(password)),
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


def set_selected_team(conn: sqlite3.Connection, user_id: str, team: str) -> None:
    """Persist the cosmetic team choice (Architecture §2.2 cosmetic carry-over)."""
    conn.execute("UPDATE users SET selected_team = ? WHERE id = ?", (team, user_id))
    conn.commit()


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
) -> None:
    """Persist one server-scored guess. Called from the verify endpoint with the
    score the server just computed — the client never supplies the score."""
    conn.execute(
        "INSERT INTO play_events (user_id, anon_id, question_id, game_mode, score, guess, actual) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, anon_id, question_id, game_mode, int(score), guess, actual),
    )
    conn.commit()


def claim_anon_events(conn: sqlite3.Connection, anon_id: str | None, user_id: str) -> int:
    """Reassign a guest device's events to a freshly-signed-in account and return
    how many were claimed. Only server-verified rows move, so totals stay honest
    (Architecture §2.2, verified-event merge). Idempotent: already-owned rows are
    untouched by the ``user_id IS NULL`` guard."""
    if not anon_id:
        return 0
    cur = conn.execute(
        "UPDATE play_events SET user_id = ?, anon_id = NULL "
        "WHERE anon_id = ? AND user_id IS NULL",
        (user_id, anon_id),
    )
    conn.commit()
    return cur.rowcount


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
    }


def leaderboard(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Top players by server-verified lifetime points. The whole point of the
    trust boundary: these numbers come only from play_events, so they can't be
    forged by editing localStorage."""
    rows = conn.execute(
        "SELECT u.username AS username, u.selected_team AS selected_team, "
        "       COALESCE(SUM(e.score), 0) AS points, COUNT(e.id) AS answered "
        "FROM users u JOIN play_events e ON e.user_id = u.id "
        "GROUP BY u.id HAVING answered > 0 "
        "ORDER BY points DESC, answered ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "rank": i + 1,
            "username": r["username"],
            "selected_team": r["selected_team"],
            "lifetime_points": int(r["points"] or 0),
            "questions_answered": r["answered"],
        }
        for i, r in enumerate(rows)
    ]
