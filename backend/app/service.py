"""Quiz + Arcade service layer (game logic).

Holds the server-authoritative pieces the API endpoints delegate to:
  * the daily quiz token store (answer kept server-side),
  * verify-and-score (delegates to scoring.score_guess),
  * the arcade over/under pairing engine.

The token store is an in-memory dict for the prototype. In production this is the
Redis daily-provisioning cache (Architecture §0, §1.1); swapping it is a localized
change behind these functions.
"""

from __future__ import annotations

import random
import secrets
import sqlite3

from . import scoring
from .validation import compute_metric

# tracking_token -> (question_id, verified_answer). Server-side only; the answer
# is never serialized to the client. Redis in production.
_TOKEN_STORE: dict[str, tuple[str, float]] = {}

ARCADE_METRICS = {
    "wins": "Race Wins",
    "podiums": "Podiums",
    "poles": "Pole Positions",
    "fastest_laps": "Fastest Laps",
}


def _slider_bounds(answer: float) -> tuple[float, float]:
    """Derive non-revealing slider bounds for the odometer UI (Architecture §3.2).

    Bounds are a wide, rounded band that contains the answer without pinning it —
    the true value is not recoverable from min/max alone.
    """
    if answer <= 0:
        return 0.0, 10.0
    upper = 10.0
    while upper < answer * 2:
        upper *= 2
    return 0.0, round(upper)


def build_daily_quiz(conn: sqlite3.Connection, game_mode: str = "daily", limit: int = 5) -> dict:
    """Provision the daily quiz: pick verified questions, mint tracking tokens.

    Mirrors the 00:00 UTC cron provisioning (Architecture §1.1). The verified
    answer is stashed in the token store, NOT returned.
    """
    rows = conn.execute(
        "SELECT id, question_string, verified_answer, difficulty_weight "
        "FROM production_trivia_questions "
        "WHERE is_active = 1 AND game_mode = ? "
        "ORDER BY id LIMIT ?",
        (game_mode, limit),
    ).fetchall()

    questions = []
    for row in rows:
        token = secrets.token_urlsafe(16)
        _TOKEN_STORE[token] = (row["id"], row["verified_answer"])
        smin, smax = _slider_bounds(row["verified_answer"])
        questions.append({
            "tracking_token": token,
            "question_text": row["question_string"],
            "difficulty_weight": row["difficulty_weight"],
            "slider_min": smin,
            "slider_max": smax,
        })
    return {"game_mode": game_mode, "questions": questions}


def verify_guess(token: str, guess: float) -> dict | None:
    """Score a guess server-side. Returns None if the token is unknown/expired."""
    entry = _TOKEN_STORE.get(token)
    if entry is None:
        return None
    _question_id, actual = entry
    score = scoring.score_guess(guess, actual)
    return {"score": score, "actual": actual, "guess": guess, "max_score": scoring.MAX_SCORE}


def _career_total(conn: sqlite3.Connection, driver_id: str, metric: str) -> float:
    """Career total for a metric across all teams/years (reuses the validated
    recomputation path so arcade and quiz agree on the numbers)."""
    span = conn.execute(
        "SELECT MIN(year) AS lo, MAX(year) AS hi FROM staging_race_results WHERE driver_id = ?",
        (driver_id,),
    ).fetchone()
    params = {
        "metric_target": metric,
        "entity_id": driver_id,
        "start_year": span["lo"],
        "end_year": span["hi"],
    }
    return float(compute_metric(conn, params))


def build_arcade_pair(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict:
    """Generate a 'Who Has More?' matchup (PRD §4.2).

    Picks two drivers from an overlapping era and a shared metric, computing
    career totals via index-friendly lookups (no ORDER BY RANDOM(); Architecture
    §1.2). v1 is non-competitive, so both values are returned for client-side
    evaluation.
    """
    rng = rng or random.Random()
    drivers = conn.execute(
        "SELECT driver_id, full_name, active_from, active_to FROM staging_drivers"
    ).fetchall()

    # Find all driver pairs whose careers overlap (shared era).
    overlapping = [
        (a, b)
        for i, a in enumerate(drivers)
        for b in drivers[i + 1:]
        if a["active_from"] <= b["active_to"] and b["active_from"] <= a["active_to"]
    ]
    a, b = rng.choice(overlapping)
    metric = rng.choice(list(ARCADE_METRICS))

    return {
        "metric": metric,
        "metric_label": ARCADE_METRICS[metric],
        "entity_a": {
            "driver_id": a["driver_id"], "full_name": a["full_name"],
            "value": _career_total(conn, a["driver_id"], metric),
        },
        "entity_b": {
            "driver_id": b["driver_id"], "full_name": b["full_name"],
            "value": _career_total(conn, b["driver_id"], metric),
        },
    }
