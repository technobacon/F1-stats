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


def score_guess(guess: float, actual: float, lam: float = DEFAULT_LAMBDA) -> int:
    """Score a numerical guess against the true value.

    Args:
        guess:  Raw numerical value the player entered.
        actual: True statistical value from the validated database.
        lam:    Structural severity multiplier (lambda). Higher = more punishing.

    Returns:
        Integer score in [0, 5000].

    Edge cases (PRD §2):
        * guess == actual            -> 5000 (exact hit, resolved instantly)
        * actual == 0 and guess != 0 -> 0    (avoid division-by-zero)
        * actual == 0 and guess == 0 -> 5000 (exact hit)
    """
    if guess == actual:
        return MAX_SCORE

    if actual == 0:
        # guess != actual is implied here, so any non-zero guess scores 0.
        return MIN_SCORE

    percentage_error = abs(guess - actual) / abs(actual)
    raw = MAX_SCORE * math.exp(-lam * percentage_error)
    return int(round(_clamp(raw, MIN_SCORE, MAX_SCORE)))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
