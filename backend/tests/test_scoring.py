"""Tests for the scoring engine (PRD §2), including the doc's worked examples."""

import math

import pytest

from app.scoring import DEFAULT_LAMBDA, MAX_SCORE, score_guess


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


def test_actual_zero_nonzero_guess_is_min():
    # Division-by-zero guard: actual=0, guess>0 -> 0
    assert score_guess(5, 0) == 0


def test_actual_zero_zero_guess_is_max():
    # guess == actual == 0 resolves as an exact hit
    assert score_guess(0, 0) == 5000


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
