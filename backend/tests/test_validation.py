"""Tests for the anti-hallucination validation layer (Pipeline §3) and the
seed pipeline, run against an in-memory SQLite database."""

import pytest

from app import db
from app.seed import (
    mock_llm_questions,
    run_validation_pipeline,
    seed_staging,
)
from app.validation import compute_metric, validate_ai_question


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    seed_staging(c)
    yield c
    c.close()


def _params(**kw):
    base = {
        "target_entity": "driver",
        "entity_id": "schumacher",
        "filter_constructor_id": "benetton",
        "start_year": 1991,
        "end_year": 1995,
        "metric_target": "wins",
    }
    base.update(kw)
    return base


def test_wins_recomputed_from_rows(conn):
    assert compute_metric(conn, _params()) == 19


def test_poles_use_qualifying_not_grid(conn):
    # Schumacher @ Benetton seeded with 4 poles in staging_qualifying_results.
    assert compute_metric(conn, _params(metric_target="poles")) == 4


def test_career_total_omits_constructor_filter(conn):
    # Raikkonen career podiums across McLaren (25) + Ferrari (24) = 49, no filter.
    params = {
        "target_entity": "driver", "entity_id": "raikkonen",
        "start_year": 2002, "end_year": 2009, "metric_target": "podiums",
    }
    assert "filter_constructor_id" not in params
    assert compute_metric(conn, params) == 49


def test_points_preserve_decimal(conn):
    val = compute_metric(conn, _params(
        entity_id="hamilton", filter_constructor_id="mercedes",
        start_year=2013, end_year=2020, metric_target="points",
    ))
    assert val == 3000


def test_unsupported_metric_rejected(conn):
    q = {
        "question_text": "bogus",
        "validation_parameters": _params(metric_target="lap_records"),
        "proposed_answer": 5,
    }
    result = validate_ai_question(conn, q)
    assert result.ok is False
    assert "Unsupported" in result.reason


def test_correct_answer_validates(conn):
    q = {
        "question_text": "wins?",
        "validation_parameters": _params(),
        "proposed_answer": 19,
    }
    assert validate_ai_question(conn, q).ok is True


def test_hallucinated_answer_rejected(conn):
    q = {
        "question_text": "wins?",
        "validation_parameters": _params(),
        "proposed_answer": 25,  # staging says 19
    }
    result = validate_ai_question(conn, q)
    assert result.ok is False
    assert result.expected == 19
    assert result.proposed == 25


def test_pipeline_commits_valid_rejects_hallucination(conn):
    summary = run_validation_pipeline(conn)
    total = len(mock_llm_questions())
    # Exactly one planted hallucination in the mock set.
    assert summary["rejected"] == 1
    assert summary["committed"] == total - 1
    assert summary["rejections"][0]["proposed"] == 80

    # The hallucinated Schumacher/Ferrari WINS question must NOT reach production
    # (other metrics for that stint are legitimately generated and may exist).
    rows = conn.execute(
        "SELECT 1 FROM production_trivia_questions "
        "WHERE question_string = "
        "'How many race wins did Michael Schumacher take with Ferrari (1996-2006)?'"
    ).fetchall()
    assert rows == []


def test_production_stores_trusted_value_not_llm_value(conn):
    run_validation_pipeline(conn)
    row = conn.execute(
        "SELECT verified_answer FROM production_trivia_questions "
        "WHERE question_string LIKE '%Benetton (1991-1995)%'"
    ).fetchone()
    assert row["verified_answer"] == 19
