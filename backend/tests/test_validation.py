"""Tests for the generalized anti-hallucination validation engine (Pipeline §3)
and the seed pipeline, run against an in-memory SQLite database.

Metrics that the generator places EXACTLY (wins, podiums, poles, fastest_laps)
get exact assertions; emergent metrics (points, DNFs, positions_gained, …) are
checked for internal consistency against an independent direct query.
"""

import pytest

from app import db
from app.seed import mock_llm_questions, run_validation_pipeline, seed_staging
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
        "target_entity": "driver", "entity_id": "schumacher",
        "filter_constructor_id": "benetton",
        "start_year": 1991, "end_year": 1995, "metric_target": "wins",
    }
    base.update(kw)
    return base


# ---- Exact placements -------------------------------------------------------

def test_wins_placed_exactly(conn):
    assert compute_metric(conn, _params()) == 19


def test_poles_use_qualifying_not_grid(conn):
    assert compute_metric(conn, _params(metric_target="poles")) == 4


def test_podiums_placed_exactly(conn):
    assert compute_metric(conn, _params(metric_target="podiums")) == 36


def test_career_total_omits_constructor_filter(conn):
    # Raikkonen career podiums = McLaren (25) + Ferrari (24) = 49, no filter.
    params = {"target_entity": "driver", "entity_id": "raikkonen",
              "start_year": 2002, "end_year": 2009, "metric_target": "podiums"}
    assert "filter_constructor_id" not in params
    assert compute_metric(conn, params) == 49


# ---- Emergent metrics: consistent with an independent query -----------------

def test_points_match_direct_sum(conn):
    val = compute_metric(conn, _params(metric_target="points"))
    direct = conn.execute(
        "SELECT COALESCE(SUM(points),0) FROM staging_race_results "
        "WHERE driver_id='schumacher' AND constructor_id='benetton' "
        "AND year BETWEEN 1991 AND 1995"
    ).fetchone()[0]
    assert float(val) == direct and val > 0


def test_dnfs_match_direct_count(conn):
    val = compute_metric(conn, _params(metric_target="dnfs"))
    direct = conn.execute(
        "SELECT COUNT(*) FROM staging_race_results WHERE driver_id='schumacher' "
        "AND constructor_id='benetton' AND year BETWEEN 1991 AND 1995 AND position IS NULL"
    ).fetchone()[0]
    assert int(val) == direct


def test_positions_gained_match_direct(conn):
    val = compute_metric(conn, _params(metric_target="positions_gained"))
    direct = conn.execute(
        "SELECT COALESCE(SUM(grid-position),0) FROM staging_race_results "
        "WHERE driver_id='schumacher' AND constructor_id='benetton' "
        "AND year BETWEEN 1991 AND 1995 AND position IS NOT NULL AND grid IS NOT NULL"
    ).fetchone()[0]
    assert int(val) == direct


def test_front_rows_at_least_poles(conn):
    # Front-row starts (quali P1 or P2) must include all poles (quali P1).
    poles = compute_metric(conn, _params(metric_target="poles"))
    front = compute_metric(conn, _params(metric_target="front_rows"))
    assert front >= poles


def test_points_finishes_at_least_podiums(conn):
    pod = compute_metric(conn, _params(metric_target="podiums"))
    pts_fin = compute_metric(conn, _params(metric_target="points_finishes"))
    assert pts_fin >= pod  # every podium is a top-10 finish


# ---- Aggregations -----------------------------------------------------------

def test_best_season_within_total(conn):
    best = compute_metric(conn, _params(aggregation="best_season"))
    total = compute_metric(conn, _params())
    assert 1 <= best <= total


def test_which_year_returns_year_in_range(conn):
    yr = compute_metric(conn, _params(aggregation="which_year"))
    assert 1991 <= yr <= 1995


def test_first_season_returns_year_in_range(conn):
    yr = compute_metric(conn, _params(aggregation="first_season"))
    assert 1991 <= yr <= 1995


def test_percentage_in_bounds(conn):
    pct = compute_metric(conn, _params(metric_target="podiums", aggregation="percentage_of_races"))
    assert 0 <= pct <= 100


def test_head_to_head_difference_exact(conn):
    # Career wins placed exactly: Hamilton 103 (21+82) vs Rosberg 23 (0+23) = 80.
    diff = compute_metric(conn, {
        "target_entity": "driver", "entity_id": "hamilton", "entity_id_b": "rosberg",
        "start_year": 2006, "end_year": 2024, "metric_target": "wins", "aggregation": "difference",
    })
    assert diff == 80


def test_poles_converted_not_above_poles(conn):
    conv = compute_metric(conn, _params(metric_target="poles_converted"))
    poles = compute_metric(conn, _params(metric_target="poles"))
    assert 0 <= conv <= poles


def test_second_and_third_make_up_non_win_podiums(conn):
    podiums = compute_metric(conn, _params(metric_target="podiums"))
    wins = compute_metric(conn, _params(metric_target="wins"))
    p2 = compute_metric(conn, _params(metric_target="second_places"))
    p3 = compute_metric(conn, _params(metric_target="third_places"))
    assert p2 + p3 == podiums - wins


def test_comeback_metrics_consistent(conn):
    starts = compute_metric(conn, _params(metric_target="starts"))
    big = compute_metric(conn, _params(metric_target="big_comebacks"))
    best = compute_metric(conn, _params(metric_target="best_comeback"))
    assert 0 <= big <= starts
    assert best >= 0


def test_avg_finish_in_range(conn):
    avg = compute_metric(conn, _params(metric_target="avg_finish"))
    assert 1 <= avg <= 24


def test_distinct_circuits_won_and_winning_seasons_bounded(conn):
    wins = compute_metric(conn, _params(metric_target="wins"))
    dcw = compute_metric(conn, _params(metric_target="distinct_circuits_won"))
    wseasons = compute_metric(conn, _params(metric_target="winning_seasons"))
    seasons = compute_metric(conn, _params(metric_target="seasons_active"))
    assert 0 <= dcw <= wins
    assert 0 <= wseasons <= seasons


def test_per_season_avg_points(conn):
    total = compute_metric(conn, _params(metric_target="points"))
    seasons = compute_metric(conn, _params(metric_target="seasons_active"))
    avg = compute_metric(conn, _params(metric_target="points", aggregation="per_season_avg"))
    assert avg == round(total / seasons)


def test_best_circuit_not_above_total_wins(conn):
    wins = compute_metric(conn, _params(metric_target="wins"))
    best = compute_metric(conn, _params(metric_target="wins", aggregation="best_circuit"))
    assert 0 <= best <= wins


def test_constructor_entity_totals(conn):
    # Synthetic data has only Schumacher at Benetton, so the team total equals his.
    team = compute_metric(conn, {
        "target_entity": "constructor", "entity_id": "benetton",
        "start_year": 1991, "end_year": 1995, "metric_target": "wins",
    })
    driver = compute_metric(conn, _params(metric_target="wins"))
    assert team == driver == 19


def test_one_two_finishes_zero_for_single_car_team(conn):
    # A 1-2 needs two cars in P1 and P2; Benetton fields one synthetic driver.
    val = compute_metric(conn, {
        "target_entity": "constructor", "entity_id": "benetton",
        "start_year": 1991, "end_year": 1995, "metric_target": "one_two_finishes",
    })
    assert val == 0


def test_circuit_facts(conn):
    cid = conn.execute("SELECT circuit_id FROM staging_race_results LIMIT 1").fetchone()["circuit_id"]
    p = {"target_entity": "circuit", "entity_id": cid, "start_year": 1980, "end_year": 2026}
    winners = compute_metric(conn, {**p, "metric_target": "distinct_winners"})
    held = compute_metric(conn, {**p, "metric_target": "races_held"})
    assert held >= 1
    assert 0 <= winners <= held


def test_per_circuit_filter(conn):
    # Sum of per-circuit wins equals total wins for the scope.
    total = compute_metric(conn, _params())
    rows = conn.execute(
        "SELECT DISTINCT circuit_id FROM staging_race_results WHERE driver_id='schumacher' "
        "AND constructor_id='benetton' AND year BETWEEN 1991 AND 1995"
    ).fetchall()
    summed = sum(compute_metric(conn, _params(filter_circuit_id=r["circuit_id"])) for r in rows)
    assert summed == total


# ---- Validation gate --------------------------------------------------------

def test_unsupported_metric_rejected(conn):
    result = validate_ai_question(conn, {
        "question_text": "bogus", "validation_parameters": _params(metric_target="lap_records"),
        "proposed_answer": 5})
    assert result.ok is False and "Unsupported" in result.reason


def test_unsupported_aggregation_rejected(conn):
    result = validate_ai_question(conn, {
        "question_text": "bogus", "validation_parameters": _params(aggregation="median"),
        "proposed_answer": 5})
    assert result.ok is False


def test_correct_answer_validates(conn):
    assert validate_ai_question(conn, {
        "question_text": "wins?", "validation_parameters": _params(),
        "proposed_answer": 19}).ok is True


def test_hallucinated_answer_rejected(conn):
    result = validate_ai_question(conn, {
        "question_text": "wins?", "validation_parameters": _params(), "proposed_answer": 25})
    assert result.ok is False and result.expected == 19 and result.proposed == 25


# ---- Pipeline ---------------------------------------------------------------

def test_pipeline_commits_valid_rejects_hallucination(conn):
    summary = run_validation_pipeline(conn)
    total = len(mock_llm_questions(conn))
    assert summary["rejected"] == 1
    assert summary["committed"] == total - 1
    assert summary["rejections"][0]["proposed"] == 80

    # The hallucinated Schumacher/Ferrari WINS question must NOT reach production.
    rows = conn.execute(
        "SELECT 1 FROM production_trivia_questions WHERE question_string = "
        "'How many race wins did Michael Schumacher take with Ferrari (1996-2006)?'"
    ).fetchall()
    assert rows == []


def test_production_stores_trusted_value_and_metadata(conn):
    run_validation_pipeline(conn)
    row = conn.execute(
        "SELECT verified_answer, answer_kind FROM production_trivia_questions "
        "WHERE question_string = "
        "'How many race wins did Michael Schumacher take with Benetton (1991-1995)?'"
    ).fetchone()
    assert row["verified_answer"] == 19 and row["answer_kind"] == "count"


def test_year_questions_carry_year_kind(conn):
    run_validation_pipeline(conn)
    n = conn.execute(
        "SELECT COUNT(*) FROM production_trivia_questions WHERE answer_kind='year'"
    ).fetchone()[0]
    assert n > 0
