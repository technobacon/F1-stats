"""Tests for the scoring engine (PRD §2), including the doc's worked examples."""

import math

import pytest

from app.scoring import (DEFAULT_LAMBDA, MAX_SCORE, TAU_PERCENTAGE, TAU_YEAR,
                         TAU_ZERO, score_guess)


def test_exact_guess_is_max():
    assert score_guess(7, 7) == 5000
    assert score_guess(1075, 1075) == 5000


def test_worked_example_10_percent_error():
    # PRD §2: 10% error with lambda=3.0 -> 5000 * e^(-0.3) ~= 3704
    assert score_guess(110, 100) == 3704
    assert score_guess(90, 100) == 3704  # symmetric in |guess - actual|


def test_worked_example_50_percent_error():
    # PRD §2: 50% error with lambda=3.0 -> 5000 * e^(-1.5) ~= 1116
    assert score_guess(150, 100) == 1116
    assert score_guess(50, 100) == 1116


def test_actual_zero_decays_on_absolute_error():
    # actual=0 falls back to absolute-error decay (TAU_ZERO): a near miss on a
    # zero answer is a near miss, not a wipe-out — but it still decays fast.
    off_by_one = score_guess(1, 0)
    off_by_five = score_guess(5, 0)
    assert 0 < off_by_five < off_by_one < 5000
    assert off_by_one == round(MAX_SCORE * math.exp(-1 / TAU_ZERO))


def test_actual_zero_zero_guess_is_max():
    # guess == actual == 0 resolves as an exact hit
    assert score_guess(0, 0) == 5000


def test_year_kind_scores_on_years_off():
    # A year is not a quantity: 5 years off used to be a 0.25% "error" worth
    # 4,963 points. On the absolute curve it's a real miss.
    assert score_guess(1995, 2000, kind="year") == round(MAX_SCORE * math.exp(-5 / TAU_YEAR))
    # Symmetric, and independent of the century the answer sits in.
    assert score_guess(2005, 2000, kind="year") == score_guess(1955, 1950, kind="year")
    # Exact year still banks everything; a decade off is nearly nothing.
    assert score_guess(2000, 2000, kind="year") == 5000
    assert score_guess(2010, 2000, kind="year") < 200


def test_percentage_kind_scores_on_points_of_percentage():
    # actual 4%, guess 6% was a "50% error" (≈1116 pts) on the old curve even
    # though the player was 2 points of percentage away on a 0-100 scale.
    two_points_off = score_guess(6, 4, kind="percentage")
    assert two_points_off == round(MAX_SCORE * math.exp(-2 / TAU_PERCENTAGE))
    assert two_points_off > 3000
    # The same absolute miss scores the same anywhere on the scale.
    assert score_guess(96, 94, kind="percentage") == two_points_off


def test_result_is_clamped_and_integer():
    s = score_guess(10_000, 1)
    assert isinstance(s, int)
    assert 0 <= s <= MAX_SCORE


def test_lambda_controls_severity():
    # A higher lambda punishes the same error more harshly.
    gentle = score_guess(150, 100, lam=1.0)
    harsh = score_guess(150, 100, lam=5.0)
    assert harsh < gentle


def test_half_point_actual():
    # F1 has half-point races; the engine must handle non-integer actuals.
    expected = round(MAX_SCORE * math.exp(-DEFAULT_LAMBDA * abs(0.6 - 0.5) / 0.5))
    assert score_guess(0.6, 0.5) == expected


def test_slider_bounds_do_not_pin_the_answer():
    """The doubling scheme guaranteed the answer sat in (25%, 50%] of every
    derived slider — a player parking at ~35% was never far off. The randomized
    band spreads the answer's position while staying deterministic per question."""
    from app.service import _slider_bounds
    fracs = []
    for i in range(200):
        answer = 10 + i * 3
        lo, hi = _slider_bounds(answer, f"question {i}")
        assert lo == 0 and hi > answer
        # Deterministic: the same question always gets the same bounds.
        assert (lo, hi) == _slider_bounds(answer, f"question {i}")
        fracs.append(answer / hi)
    assert min(fracs) < 0.25 and max(fracs) > 0.55
