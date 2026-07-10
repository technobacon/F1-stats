"""Server-authoritative scoring engine (PRD §2).

Percentage-error exponential decay. This is the single source of truth for
all exact-numerical guessing modes (Daily, Race-Week, One-Shots). True answers
are never sent to the client; scoring always runs here, server-side.

    Score = 5000 * e^(-lambda * |guess - actual| / actual)

The result is clamped to [0, 5000] and rounded to the nearest integer.
"""

from __future__ import annotations

import math

MAX_SCORE = 5000
MIN_SCORE = 0
DEFAULT_LAMBDA = 3.0

# Per-kind absolute-error scales. Percentage error is the right yardstick for
# counts and points totals, but it degenerates for answers on a fixed scale:
# a year has a ~2000 denominator (guessing 1950 for 2000 still kept 93% of the
# points), and a percentage answer of 4 made a guess of 6 a "50% error". These
# kinds score on ABSOLUTE error instead: score = 5000 * e^(-|guess-actual|/tau).
TAU_YEAR = 3.0          # 3 years off ≈ 37% of max; 10 years off ≈ 3.6%
TAU_PERCENTAGE = 8.0    # 8 points of percentage off ≈ 37% of max
# actual == 0 fallback (theoretical: the generator never ships a 0 answer):
# absolute decay so off-by-one isn't scored like off-by-fifty.
TAU_ZERO = 2.0


def score_guess(guess: float, actual: float, lam: float = DEFAULT_LAMBDA,
                kind: str = "count") -> int:
    """Score a numerical guess against the true value.

    Args:
        guess:  Raw numerical value the player entered.
        actual: True statistical value from the validated database.
        lam:    Structural severity multiplier (lambda). Higher = more punishing.
        kind:   The question's answer_kind. `count`/`points` score on percentage
                error (PRD §2); `year`/`percentage` score on absolute error
                (see TAU_YEAR / TAU_PERCENTAGE above).

    Returns:
        Integer score in [0, 5000].

    Edge cases:
        * guess == actual -> 5000 (exact hit, resolved instantly)
        * actual == 0     -> absolute-error decay (TAU_ZERO), so a near miss
          on a zero answer is a near miss, not a wipe-out
    """
    if guess == actual:
        return MAX_SCORE

    if kind == "year":
        raw = MAX_SCORE * math.exp(-abs(guess - actual) / TAU_YEAR)
    elif kind == "percentage":
        raw = MAX_SCORE * math.exp(-abs(guess - actual) / TAU_PERCENTAGE)
    elif actual == 0:
        raw = MAX_SCORE * math.exp(-abs(guess) / TAU_ZERO)
    else:
        percentage_error = abs(guess - actual) / abs(actual)
        raw = MAX_SCORE * math.exp(-lam * percentage_error)
    return int(round(_clamp(raw, MIN_SCORE, MAX_SCORE)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
