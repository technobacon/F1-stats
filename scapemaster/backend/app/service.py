"""Quiz + Duel Arena service layer (game logic).

Holds the server-authoritative pieces the API endpoints delegate to:
  * the daily quiz token store (answer kept server-side),
  * verify-and-score (delegates to scoring.score_guess),
  * the Duel Arena over/under pairing engine.

The token store is an in-memory dict for the prototype. In production this is a
Redis daily-provisioning cache; swapping it is a localized change behind these
functions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from . import scoring

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


# Duel Arena metrics, per staging domain.
ARCADE_ITEM_METRICS = {
    "ge_price": "Grand Exchange price",
    "high_alch": "High Alchemy value",
    "buy_limit": "GE buy limit",
}
ARCADE_MONSTER_METRICS = {
    "combat_level": "Combat level",
    "hitpoints": "Hitpoints",
    "slayer_xp": "Slayer XP per kill",
}

# Per-mode session size. The Daily Slayer Task is six questions drawn from the
# single general bank.
MODE_QUESTION_COUNT = {"daily": 6}

# Training Grounds: an unlimited, non-competitive practice mode. Questions are
# drawn one at a time at random and the score is NEVER recorded — verify() looks
# for this game_mode on the token and skips persistence so nothing reaches a
# user's totals or the HiScores.
FREE_PRACTICE_MODE = "free_practice"

# Era-biased serving: the quiz mix leans on content today's players actually
# know — recent OSRS originals first, the beloved 2005-2007 backports next —
# while still surfacing classic-era content occasionally. Weights are relative
# (only their ratios matter) and are applied per question by content release
# year (production_trivia_questions.era_year). Tune the bands here.
ERA_WEIGHT_BANDS = (
    # (year_lo, year_hi, weight)
    (2019, 9999, 1.00),   # modern OSRS originals — ToA, Nex, DT2, Varlamore
    (2013, 2018, 0.90),   # early OSRS — Zulrah, Vorkath, the Inferno
    (2005, 2007, 0.60),   # the RS2 golden age — GWD, Slayer bosses, the whip
    (2001, 2004, 0.40),   # classic era — runes, dragon gear, the early quests
)
DEFAULT_ERA_WEIGHT = 0.20  # outside the bands or unknown era


def _era_weight(era_year: int | None) -> float:
    """Relative sampling weight for a question given its content release year."""
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
    client gets the same provisioned set for the same period."""
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


# Server-side secret mixed into the slider-bounds RNG seed, cached per process.
# Without it the seed would be just the public question string plus a public
# algorithm, so a client could re-run the RNG, recover the answer's position in
# the band, and invert the bound back to the answer within a few percent —
# quietly defeating the "answer never reaches the client" trust boundary.
_SLIDER_SALT: str | None = None


def _get_slider_salt(conn: sqlite3.Connection) -> str:
    """The slider-bounds secret: OSRS_SLIDER_SALT if set, else a random value
    generated once and persisted in app_kv (which survives reboots and the
    boot-time bank reseed, so bounds stay stable per question)."""
    global _SLIDER_SALT
    if _SLIDER_SALT:
        return _SLIDER_SALT
    env = os.environ.get("OSRS_SLIDER_SALT")
    if env:
        _SLIDER_SALT = env
        return env
    try:
        row = conn.execute("SELECT value FROM app_kv WHERE key = 'slider_salt'").fetchone()
        if row is None:
            conn.execute(
                "INSERT OR IGNORE INTO app_kv (key, value) VALUES ('slider_salt', ?)",
                (secrets.token_hex(16),),
            )
            conn.commit()
            row = conn.execute("SELECT value FROM app_kv WHERE key = 'slider_salt'").fetchone()
        _SLIDER_SALT = row["value"]
    except sqlite3.OperationalError:
        # Un-migrated DB with no app_kv table: fall back to a per-process salt.
        # Still unpredictable, just not stable across restarts.
        _SLIDER_SALT = secrets.token_hex(16)
    return _SLIDER_SALT


def reset_slider_salt_cache() -> None:
    """Forget the cached salt (used by tests that swap databases)."""
    global _SLIDER_SALT
    _SLIDER_SALT = None


def _slider_bounds(answer: float, answer_kind: str = "count",
                   key: str = "", salt: str = "") -> tuple[float, float]:
    """Derive non-revealing slider bounds for the guess UI.

    Bounds are a wide, rounded band that contains the answer without pinning it —
    the true value is not recoverable from min/max alone.

    * coins/xp: answers span five orders of magnitude (a lobster vs a Twisted
      bow), so the band is a power-of-ten window at least two decades wide; the
      client renders these kinds on a log-scale slider with k/m/b formatting.
    * everything else: the answer's position in a 0..upper band is drawn
      deterministically per question, seeded by `salt` + `key`, then the bound is
      rounded up to two significant figures so it reads as a clean scale mark.
      (The old doubling scheme guaranteed the answer sat in the second quarter of
      every slider — a player parking at ~35% was never far off.) The salt is a
      server-side secret (see _get_slider_salt): without it the seed would be
      fully derivable from the served question text, letting a client invert the
      bound back to the answer.
    """
    if answer <= 0:
        return 0.0, 10.0
    if answer_kind in ("coins", "xp"):
        lo_exp = math.floor(math.log10(answer)) - 1
        hi_exp = math.ceil(math.log10(answer)) + 1
        if hi_exp - lo_exp < 3:  # guarantee >= 3 decades so the band can't pin
            hi_exp = lo_exp + 3
        return float(10 ** max(lo_exp, 0)), float(10 ** hi_exp)
    rng = _deterministic_rng("slider-bounds", salt, key or str(answer))
    frac = rng.uniform(0.18, 0.82)   # where in the band the answer lands
    upper = max(10.0, answer / frac)
    mag = 10 ** max(0, math.floor(math.log10(upper)) - 1)
    upper = math.ceil(upper / mag) * mag
    return 0.0, float(upper)


def _provision_question(row: sqlite3.Row, game_mode: str, salt: str) -> dict:
    """Mint a tracking token for one question row and build its client-facing
    payload. The verified answer goes into the server-side token store, NEVER
    into the payload; slider bounds prefer the row's explicit display bounds and
    otherwise fall back to the salted non-revealing band."""
    token = secrets.token_urlsafe(16)
    _TOKEN_STORE[token] = (row["id"], row["verified_answer"], game_mode, time.monotonic())
    if row["display_min"] is not None and row["display_max"] is not None:
        smin, smax = row["display_min"], row["display_max"]
    else:
        smin, smax = _slider_bounds(row["verified_answer"], row["answer_kind"],
                                    row["question_string"], salt)
    return {
        "tracking_token": token,
        "question_text": row["question_string"],
        "difficulty_weight": row["difficulty_weight"],
        "answer_kind": row["answer_kind"],
        "category": row["category"] or "",
        "slider_min": smin,
        "slider_max": smax,
    }


def build_quiz(conn: sqlite3.Connection, game_mode: str = "daily", period: str | None = None) -> dict:
    """Provision a quiz session: deterministically pick verified questions for the
    given mode + period, mint tracking tokens.

    Mirrors a 00:00 UTC cron provisioning: the selection is seeded by
    (mode, period) so it is stable for everyone within the period and rotates to
    a fresh set the next period. The verified answer is stashed in the token
    store, NOT returned to the client.
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
    salt = _get_slider_salt(conn)
    # Bias the per-period selection toward famous, recent content while still
    # surfacing the classics occasionally.
    weights = [_era_weight(row["era_year"]) for row in pool]
    rows = _weighted_sample(rng, pool, weights, count)

    questions = [_provision_question(row, game_mode, salt) for row in rows]
    return {"game_mode": game_mode, "questions": questions}


def build_practice_question(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict | None:
    """Provision a single random Training Grounds question, or None if the bank
    is empty.

    Unlike the daily set, Training Grounds is unlimited and personal: questions
    are pulled one at a time, truly at random, from the WHOLE active bank (any
    game_mode). Practice draws UNIFORMLY at random — deliberately without the
    release-year weighting the competitive sets use — so successive questions
    don't cluster on the same era and every corner of Gielinor turns up. The
    verified answer is stashed in the token store (tagged free_practice) and
    never returned to the client; because the token carries that mode, verify()
    records nothing for it.
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

    row = rng.choice(pool)
    return {
        "game_mode": FREE_PRACTICE_MODE,
        "question": _provision_question(row, FREE_PRACTICE_MODE, _get_slider_salt(conn)),
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


# Over/Under should be a genuine close call, not a blowout: the two entities'
# values must land within this fraction of each other (the smaller is at least
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
    """Sample (entity pair, metric) combos until the two values are within
    ARCADE_MAX_GAP, so the matchup is a real toss-up rather than a runaway.

    value_fn(entity, metric) -> float|None is memoised per (entity_id, metric)
    so the same value is never recomputed; a None (stat not recorded) skips the
    combo. Falls back to the closest combo seen if no sample clears the gap
    within `attempts` (guarantees a result on any dataset).
    Returns (a, b, metric, value_a, value_b).
    """
    cache: dict = {}

    def val(entity, metric):
        key = (entity["entity_id"], metric)
        if key not in cache:
            cache[key] = value_fn(entity, metric)
        return cache[key]

    best = None  # (gap, a, b, metric, va, vb)
    for _ in range(attempts):
        a, b = rng.choice(pairs)
        metric = rng.choice(metrics)
        va, vb = val(a, metric), val(b, metric)
        if va is None or vb is None:
            continue
        # A tie (including both zero) has no "which is greater?" answer, so it is
        # never a valid Over/Under — skip it entirely rather than scoring it close.
        if va == vb:
            continue
        if _within_gap(va, vb):
            return a, b, metric, va, vb
        gap = abs(va - vb) / max(abs(va), abs(vb))  # hi > 0 here (va != vb)
        if best is None or gap < best[0]:
            best = (gap, a, b, metric, va, vb)

    if best is not None:
        _, a, b, metric, va, vb = best
        return a, b, metric, va, vb

    # Nothing but ties/gaps in the sampled combos: scan deterministically for any
    # pair whose values differ on some metric, so we still return a valid question.
    for a, b in pairs:
        for metric in metrics:
            va, vb = val(a, metric), val(b, metric)
            if va is not None and vb is not None and va != vb:
                return a, b, metric, va, vb
    # Truly degenerate dataset: fall back to the first pair on the first metric.
    a, b = pairs[0]
    metric = metrics[0]
    return a, b, metric, val(a, metric) or 0.0, val(b, metric) or 0.0


def _all_pairs(rows: list) -> list:
    return [(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]]


def build_arcade_pair(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict:
    """Generate a Duel Arena 'Which is greater?' matchup.

    Picks two items (GE price / alch value / buy limit) or two monsters (combat
    level / hitpoints / Slayer XP) and a shared metric, biased toward close
    values (within ARCADE_MAX_GAP) so the call is hard. v1 is non-competitive,
    so both values are returned for client-side evaluation.
    """
    rng = rng or random.Random()

    if rng.random() < 0.5:
        rows = conn.execute(
            "SELECT CAST(item_id AS TEXT) AS entity_id, name AS full_name, "
            "ge_price, high_alch, buy_limit FROM staging_items WHERE fame_tier <= 2"
        ).fetchall()
        metrics = ARCADE_ITEM_METRICS
    else:
        rows = conn.execute(
            "SELECT monster_id AS entity_id, name AS full_name, combat_level, "
            "hitpoints, slayer_xp FROM staging_monsters"
        ).fetchall()
        metrics = ARCADE_MONSTER_METRICS

    # No staging (e.g. serving from the committed question bank): use the
    # arcade snapshot so the Duel Arena still works offline.
    if not rows:
        return _arcade_from_dataset(rng)

    entities = [dict(r) for r in rows]
    a, b, metric, va, vb = _pick_close_pair(
        _all_pairs(entities), list(metrics), rng,
        lambda e, m: float(e[m]) if e.get(m) is not None else None,
    )
    label = {**ARCADE_ITEM_METRICS, **ARCADE_MONSTER_METRICS}[metric]
    return {
        "metric": metric,
        "metric_label": label,
        "entity_a": {"entity_id": a["entity_id"], "full_name": a["full_name"], "value": va},
        "entity_b": {"entity_id": b["entity_id"], "full_name": b["full_name"], "value": vb},
    }


_ARCADE_DATASET: dict | None = None


def _arcade_from_dataset(rng: random.Random) -> dict:
    """Build an Over/Under matchup from the committed arcade snapshot, used when
    staging tables aren't loaded."""
    global _ARCADE_DATASET
    if _ARCADE_DATASET is None:
        from .seed import ARCADE_PATH
        _ARCADE_DATASET = json.loads(Path(ARCADE_PATH).read_text())
    if rng.random() < 0.5:
        pool, metrics = _ARCADE_DATASET["items"], ARCADE_ITEM_METRICS
    else:
        pool, metrics = _ARCADE_DATASET["monsters"], ARCADE_MONSTER_METRICS
    a, b, metric, va, vb = _pick_close_pair(
        _all_pairs(pool), list(metrics), rng,
        lambda e, m: float(e["stats"][m]) if e["stats"].get(m) is not None else None,
    )
    label = {**ARCADE_ITEM_METRICS, **ARCADE_MONSTER_METRICS}[metric]
    return {
        "metric": metric,
        "metric_label": label,
        "entity_a": {"entity_id": a["entity_id"], "full_name": a["full_name"], "value": va},
        "entity_b": {"entity_id": b["entity_id"], "full_name": b["full_name"], "value": vb},
    }
