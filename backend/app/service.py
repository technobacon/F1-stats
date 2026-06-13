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

import hashlib
import json
import random
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import scoring
from .validation import compute_metric

# tracking_token -> (question_id, verified_answer, game_mode, issued_at). Server-
# side only; the answer is never serialized to the client. Redis in production.
_TOKEN_STORE: dict[str, tuple[str, float, str, float]] = {}
# Robustness: this in-memory store would otherwise grow without bound (a new
# token per question served, forever). Tokens expire after a generous play window
# and the store is capped, evicting the oldest. Redis TTLs replace this in prod.
_TOKEN_TTL_SECONDS = 6 * 3600
_TOKEN_STORE_MAX = 100_000


def _prune_tokens() -> None:
    """Drop expired tokens, then enforce the size cap by evicting the oldest."""
    now = time.monotonic()
    expired = [t for t, v in _TOKEN_STORE.items() if now - v[3] > _TOKEN_TTL_SECONDS]
    for t in expired:
        del _TOKEN_STORE[t]
    overflow = len(_TOKEN_STORE) - _TOKEN_STORE_MAX
    if overflow > 0:
        for t in sorted(_TOKEN_STORE, key=lambda k: _TOKEN_STORE[k][3])[:overflow]:
            del _TOKEN_STORE[t]


def _live_token(token: str) -> tuple[str, float, str, float] | None:
    """Return a token's entry if present and not expired, else None (expired
    tokens are dropped, so the caller sees them as unknown)."""
    entry = _TOKEN_STORE.get(token)
    if entry is None:
        return None
    if time.monotonic() - entry[3] > _TOKEN_TTL_SECONDS:
        del _TOKEN_STORE[token]
        return None
    return entry

ARCADE_METRICS = {
    "wins": "Race Wins",
    "podiums": "Podiums",
    "poles": "Pole Positions",
    "fastest_laps": "Fastest Laps",
    "points_finishes": "Points Finishes",
    "front_rows": "Front-Row Starts",
    "dnfs": "DNFs",
}

# Per-mode session size (PRD §4.1). The competitive daily sets are six questions each.
MODE_QUESTION_COUNT = {"daily": 6, "race_week": 6}

# Free Practice: an unlimited, non-competitive training mode. Questions are drawn
# one at a time at random and the score is NEVER recorded — verify() looks for
# this game_mode on the token and skips persistence so nothing reaches a user's
# totals or the leaderboard.
FREE_PRACTICE_MODE = "free_practice"

# Era-biased serving: the quiz mix focuses on the modern era, dips into history
# only occasionally, and leans a little extra on the two golden eras. Weights are
# relative (only their ratios matter) and are applied per question by mid-span
# year (production_trivia_questions.era_year). Tune the bands here.
ERA_WEIGHT_BANDS = (
    # (year_lo, year_hi, weight)
    (2014, 9999, 1.00),   # modern turbo-hybrid era — the primary focus
    (2007, 2013, 0.50),   # recent, but not current
    (1994, 2006, 0.38),   # Schumacher era — occasional, with a lean
    (1984, 1993, 0.42),   # Prost / Senna / Mansell / Piquet — a touch more
    (1980, 1983, 0.16),   # early '80s — rare
)
DEFAULT_ERA_WEIGHT = 0.12  # outside the bands (pre-1980) or unknown era


def _era_weight(era_year: int | None) -> float:
    """Relative sampling weight for a question given its representative year."""
    if era_year is None:
        return DEFAULT_ERA_WEIGHT
    for lo, hi, w in ERA_WEIGHT_BANDS:
        if lo <= era_year <= hi:
            return w
    return DEFAULT_ERA_WEIGHT


def _weighted_sample(rng: random.Random, rows: list, weights: list[float], k: int) -> list:
    """Deterministic weighted sample WITHOUT replacement (Efraimidis-Spirakis
    A-Res): draw a key u**(1/w) per row and take the top-k. Stable for a given
    rng sequence, so the per-period selection stays identical across clients."""
    if k >= len(rows):
        return list(rows)
    keyed = []
    for row, w in zip(rows, weights):
        u = rng.random()
        key = u ** (1.0 / w) if w > 0 else 0.0
        keyed.append((key, row))
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [row for _key, row in keyed[:k]]


def _utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _deterministic_rng(*parts: str) -> random.Random:
    """Stable RNG seeded by the given parts (e.g. mode + UTC date), so every
    client gets the same provisioned set for the same period (Architecture §1.1)."""
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


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


def build_quiz(conn: sqlite3.Connection, game_mode: str = "daily", period: str | None = None) -> dict:
    """Provision a quiz session: deterministically pick verified questions for the
    given mode + period, mint tracking tokens.

    Mirrors the 00:00 UTC cron provisioning (Architecture §1.1): the selection is
    seeded by (mode, period) so it is stable for everyone within the period and
    rotates to a fresh set the next period. The verified answer is stashed in the
    token store, NOT returned to the client.
    """
    count = MODE_QUESTION_COUNT.get(game_mode, 5)
    period = period or _utc_today()
    _prune_tokens()  # keep the in-memory token store bounded

    pool = conn.execute(
        "SELECT id, question_string, verified_answer, answer_kind, category, "
        "       display_min, display_max, difficulty_weight, era_year "
        "FROM production_trivia_questions "
        "WHERE is_active = 1 AND game_mode = ? ORDER BY id",
        (game_mode,),
    ).fetchall()

    rng = _deterministic_rng(game_mode, period)
    # Bias the per-period selection toward the modern era (with a lean on the
    # golden eras) while still surfacing older questions occasionally.
    weights = [_era_weight(row["era_year"]) for row in pool]
    rows = _weighted_sample(rng, pool, weights, count)

    questions = []
    for row in rows:
        token = secrets.token_urlsafe(16)
        _TOKEN_STORE[token] = (row["id"], row["verified_answer"], game_mode, time.monotonic())
        # Prefer explicit display bounds (year/percentage); else a non-revealing band.
        if row["display_min"] is not None and row["display_max"] is not None:
            smin, smax = row["display_min"], row["display_max"]
        else:
            smin, smax = _slider_bounds(row["verified_answer"])
        questions.append({
            "tracking_token": token,
            "question_text": row["question_string"],
            "difficulty_weight": row["difficulty_weight"],
            "answer_kind": row["answer_kind"],
            "category": row["category"] or "",
            "slider_min": smin,
            "slider_max": smax,
        })
    return {"game_mode": game_mode, "questions": questions}


def build_practice_question(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict | None:
    """Provision a single random Free Practice question, or None if the bank is empty.

    Unlike the daily/race sets, Free Practice is unlimited and personal:
    questions are pulled one at a time, truly at random, from the WHOLE active bank
    (any game_mode). The mix keeps the same era bias as the rest of the game so
    practice resembles real play. The verified answer is stashed in the token store
    (tagged free_practice) and never returned to the client; because the token
    carries that mode, verify() records nothing for it.
    """
    rng = rng or random.Random()
    _prune_tokens()  # keep the in-memory token store bounded

    pool = conn.execute(
        "SELECT id, question_string, verified_answer, answer_kind, category, "
        "       display_min, display_max, difficulty_weight, era_year "
        "FROM production_trivia_questions WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    if not pool:
        return None

    weights = [_era_weight(row["era_year"]) for row in pool]
    row = _weighted_sample(rng, pool, weights, 1)[0]

    token = secrets.token_urlsafe(16)
    _TOKEN_STORE[token] = (row["id"], row["verified_answer"], FREE_PRACTICE_MODE, time.monotonic())
    if row["display_min"] is not None and row["display_max"] is not None:
        smin, smax = row["display_min"], row["display_max"]
    else:
        smin, smax = _slider_bounds(row["verified_answer"])
    return {
        "game_mode": FREE_PRACTICE_MODE,
        "question": {
            "tracking_token": token,
            "question_text": row["question_string"],
            "difficulty_weight": row["difficulty_weight"],
            "answer_kind": row["answer_kind"],
            "category": row["category"] or "",
            "slider_min": smin,
            "slider_max": smax,
        },
    }


def verify_guess(token: str, guess: float) -> dict | None:
    """Score a guess server-side. Returns None if the token is unknown/expired."""
    entry = _live_token(token)
    if entry is None:
        return None
    _question_id, actual, _game_mode, _issued = entry
    score = scoring.score_guess(guess, actual)
    return {"score": score, "actual": actual, "guess": guess, "max_score": scoring.MAX_SCORE}


def token_meta(token: str) -> tuple[str, str] | None:
    """(question_id, game_mode) for a tracking token, or None if unknown. Lets the
    API persist a server-scored play_event without re-exposing the answer."""
    entry = _live_token(token)
    if entry is None:
        return None
    question_id, _actual, game_mode, _issued = entry
    return question_id, game_mode


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


# Over/Under should be a genuine close call, not a blowout: the two drivers'
# totals must land within this fraction of each other (the smaller is at least
# 70% of the larger). Pairs further apart than this are rejected.
ARCADE_MAX_GAP = 0.30


def _within_gap(va: float, vb: float, max_gap: float = ARCADE_MAX_GAP) -> bool:
    """True when two values are within max_gap of each other (relative to the
    larger). Two zeros count as identical, hence close."""
    hi = max(abs(va), abs(vb))
    if hi == 0:
        return True
    return abs(va - vb) / hi <= max_gap


def _pick_close_pair(pairs, metrics, rng, value_fn, attempts: int = 80):
    """Sample (driver pair, metric) combos until the two totals are within
    ARCADE_MAX_GAP, so the matchup is a real toss-up rather than a runaway.

    value_fn(entity, metric) -> float is memoised per (driver_id, metric) so the
    same total is never recomputed. Falls back to the closest combo seen if no
    sample clears the gap within `attempts` (guarantees a result on any dataset).
    Returns (a, b, metric, value_a, value_b).
    """
    cache: dict = {}

    def val(entity, metric):
        key = (entity["driver_id"], metric)
        if key not in cache:
            cache[key] = value_fn(entity, metric)
        return cache[key]

    best = None  # (gap, a, b, metric, va, vb)
    for _ in range(attempts):
        a, b = rng.choice(pairs)
        metric = rng.choice(metrics)
        va, vb = val(a, metric), val(b, metric)
        if _within_gap(va, vb):
            return a, b, metric, va, vb
        hi = max(abs(va), abs(vb))
        gap = 0.0 if hi == 0 else abs(va - vb) / hi
        if best is None or gap < best[0]:
            best = (gap, a, b, metric, va, vb)
    _, a, b, metric, va, vb = best
    return a, b, metric, va, vb


def build_arcade_pair(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict:
    """Generate a 'Who Has More?' matchup (PRD §4.2).

    Picks two drivers from an overlapping era and a shared metric, computing
    career totals via index-friendly lookups (no ORDER BY RANDOM(); Architecture
    §1.2). The pairing is biased toward close totals (within ARCADE_MAX_GAP) so
    the call is hard. v1 is non-competitive, so both values are returned for
    client-side evaluation.
    """
    rng = rng or random.Random()
    drivers = conn.execute(
        "SELECT driver_id, full_name, active_from, active_to FROM staging_drivers"
    ).fetchall()

    # No staging (e.g. serving from the committed question bank): use the
    # arcade snapshot so Over/Under still works offline.
    if not drivers:
        return _arcade_from_dataset(rng)

    # Apply the same era-tiered significance gate the question generator uses, so
    # Over/Under only pits drivers who actually matter — no insignificant also-rans.
    # Guarded: if a (e.g. tiny synthetic) dataset leaves too few, keep the full set.
    from .seed import significant_driver_ids  # lazy import: avoids a load-time cycle
    keep = significant_driver_ids(conn)
    significant = [d for d in drivers if d["driver_id"] in keep]
    if len(significant) >= 2:
        drivers = significant

    # Find all driver pairs whose careers overlap (shared era).
    overlapping = [
        (a, b)
        for i, a in enumerate(drivers)
        for b in drivers[i + 1:]
        if a["active_from"] <= b["active_to"] and b["active_from"] <= a["active_to"]
    ]
    a, b, metric, va, vb = _pick_close_pair(
        overlapping, list(ARCADE_METRICS), rng,
        lambda d, m: _career_total(conn, d["driver_id"], m),
    )

    return {
        "metric": metric,
        "metric_label": ARCADE_METRICS[metric],
        "entity_a": {
            "driver_id": a["driver_id"], "full_name": a["full_name"], "value": va,
        },
        "entity_b": {
            "driver_id": b["driver_id"], "full_name": b["full_name"], "value": vb,
        },
    }


_ARCADE_DATASET: list | None = None


def _arcade_from_dataset(rng: random.Random) -> dict:
    """Build an Over/Under matchup from the committed arcade snapshot (career
    totals per driver), used when staging tables aren't loaded."""
    global _ARCADE_DATASET
    if _ARCADE_DATASET is None:
        from .seed import ARCADE_PATH
        _ARCADE_DATASET = json.loads(Path(ARCADE_PATH).read_text())["drivers"]
    ds = _ARCADE_DATASET
    overlapping = [
        (a, b)
        for i, a in enumerate(ds)
        for b in ds[i + 1:]
        if a["active_from"] <= b["active_to"] and b["active_from"] <= a["active_to"]
    ]
    a, b, metric, va, vb = _pick_close_pair(
        overlapping, list(ARCADE_METRICS), rng,
        lambda d, m: float(d["stats"][m]),
    )
    return {
        "metric": metric,
        "metric_label": ARCADE_METRICS[metric],
        "entity_a": {"driver_id": a["driver_id"], "full_name": a["full_name"], "value": va},
        "entity_b": {"driver_id": b["driver_id"], "full_name": b["full_name"], "value": vb},
    }
