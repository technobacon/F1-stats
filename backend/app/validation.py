"""Deterministic anti-hallucination validation engine (Pipeline §3).

THE AUTOMATED VERIFICATION INVARIANT: the pipeline never trusts the integer the
language model supplies in `proposed_answer`. For every question it reads the
structural `validation_parameters`, re-computes a completely independent query
against the trusted staging tables, and only commits if the values match.

The engine is metric + aggregation based so a wide variety of creative questions
all reduce to deterministic SQL:

  metric_target    what to measure per race/qualifying row
  aggregation      how to roll it up across the scope:
      total                straight total over the year range (default)
      best_season          max of the metric over any single season
      which_year           the season in which the metric peaked (returns a year)
      first_season         earliest season the metric was non-zero (returns a year)
      last_season          latest season the metric was non-zero (returns a year)
      percentage_of_races  100 * metric / races-entered, rounded
      difference           entity_id minus entity_id_b (head-to-head)

Correctness notes carried from the spec:
  * Poles/front-rows are counted from the Grand Prix starting grid (grid == 1 /
    grid <= 2). This matches the official record (where a driver actually started
    the main race) and keeps sprint weekends honest — the qualifying session does
    not set the GP grid on a sprint weekend, so grid is the right source.
  * filter_constructor_id and filter_circuit_id are OPTIONAL; absence = no filter
    (e.g. career-total questions omit the constructor).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# metric_target -> SQL aggregate expression, evaluated over a scoped row set.
# Race-sourced metrics read staging_race_results; quali-sourced read qualifying.
_RACE_METRICS = {
    "wins":                 "SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END)",
    "podiums":              "SUM(CASE WHEN position IN (1,2,3) THEN 1 ELSE 0 END)",
    "second_places":        "SUM(CASE WHEN position = 2 THEN 1 ELSE 0 END)",
    "third_places":         "SUM(CASE WHEN position = 3 THEN 1 ELSE 0 END)",
    "fastest_laps":         "SUM(CASE WHEN fastest_lap = 1 THEN 1 ELSE 0 END)",
    "points":               "COALESCE(SUM(points), 0)",
    "dnfs":                 "SUM(CASE WHEN position IS NULL THEN 1 ELSE 0 END)",
    "points_finishes":      "SUM(CASE WHEN position BETWEEN 1 AND 10 THEN 1 ELSE 0 END)",
    "positions_gained":     "COALESCE(SUM(CASE WHEN position IS NOT NULL AND grid IS NOT NULL "
                            "THEN grid - position ELSE 0 END), 0)",
    # Races where the driver climbed 10+ places off the grid — "charge" drives.
    "big_comebacks":        "SUM(CASE WHEN position IS NOT NULL AND grid IS NOT NULL "
                            "AND grid - position >= 10 THEN 1 ELSE 0 END)",
    # Biggest single-race climb from grid to flag (max over the scope).
    "best_comeback":        "COALESCE(MAX(CASE WHEN position IS NOT NULL AND grid IS NOT NULL "
                            "THEN grid - position END), 0)",
    # Mean finishing position across classified (non-DNF) races, rounded.
    "avg_finish":           "COALESCE(CAST(ROUND(AVG(position)) AS INTEGER), 0)",
    # How many *different* circuits the entity has a win at.
    "distinct_circuits_won":"COUNT(DISTINCT CASE WHEN position = 1 THEN circuit_id END)",
    # How many *different* circuits the entity has started from pole at.
    "distinct_circuits_poled": "COUNT(DISTINCT CASE WHEN grid = 1 THEN circuit_id END)",
    # Years between the first and the most recent win in scope (0 = one winning
    # year or none) — the "longevity" stat that makes long careers sing.
    "win_span_years":       "COALESCE(MAX(CASE WHEN position = 1 THEN year END) - "
                            "MIN(CASE WHEN position = 1 THEN year END), 0)",
    # How many distinct seasons featured at least one win.
    "winning_seasons":      "COUNT(DISTINCT CASE WHEN position = 1 THEN year END)",
    "distinct_constructors":"COUNT(DISTINCT constructor_id)",
    "seasons_active":       "COUNT(DISTINCT year)",
    "starts":               "COUNT(*)",
    # Poles / front rows from the Grand Prix starting grid (see module docstring).
    "poles":                "SUM(CASE WHEN grid = 1 THEN 1 ELSE 0 END)",
    "front_rows":           "SUM(CASE WHEN grid IN (1, 2) THEN 1 ELSE 0 END)",
    # Hat-trick weekends: pole, the win AND the fastest lap in one Grand Prix.
    "hat_tricks":           "SUM(CASE WHEN grid = 1 AND position = 1 AND fastest_lap = 1 "
                            "THEN 1 ELSE 0 END)",
    # Wins started from pole / from anywhere else (circuit-keyed this answers
    # "how often has the race here been won from pole?").
    "pole_wins":            "SUM(CASE WHEN grid = 1 AND position = 1 THEN 1 ELSE 0 END)",
    "wins_off_pole":        "SUM(CASE WHEN position = 1 AND grid > 1 THEN 1 ELSE 0 END)",
    # Deepest grid slot a win was taken from (max grid among wins).
    "deepest_win_grid":     "COALESCE(MAX(CASE WHEN position = 1 THEN grid END), 0)",
    # Mean starting slot across the scope, rounded.
    "avg_grid":             "COALESCE(CAST(ROUND(AVG(grid)) AS INTEGER), 0)",
    # How many different drivers have won in this scope (constructor-keyed:
    # distinct race winners the team has fielded).
    "distinct_winning_drivers": "COUNT(DISTINCT CASE WHEN position = 1 THEN driver_id END)",
}
# All metrics are now sourced from staging_race_results; the qualifying table is
# retained in staging but no longer drives any production metric.
_QUALI_METRICS: dict[str, str] = {}

# Special metrics computed by dedicated joins/group-bys rather than a single
# scoped aggregate expression (entity_id meaning varies — see each function).
_SPECIAL_METRICS = {
    "poles_converted", "one_two_finishes", "distinct_winners", "races_held",
    "longest_points_streak", "longest_podium_streak", "longest_win_streak",
    "teammate_count", "front_row_lockouts", "most_wins_one_driver",
}

_AGGREGATIONS = {
    "total", "best_season", "which_year", "first_season", "last_season",
    "percentage_of_races", "difference", "per_season_avg", "best_circuit",
}

# Which entity column a question is keyed on (driver / team / venue questions).
_ENTITY_COL = {"driver": "driver_id", "constructor": "constructor_id", "circuit": "circuit_id"}

# Metrics whose result is a count of races (used to pick slider/answer hints).
SUPPORTED_METRICS = set(_RACE_METRICS) | set(_QUALI_METRICS) | _SPECIAL_METRICS


@dataclass
class ValidationResult:
    ok: bool
    metric: str
    expected: Any
    proposed: Any
    reason: str = ""


def _source(metric: str) -> tuple[str, str]:
    if metric in _RACE_METRICS:
        return "staging_race_results", _RACE_METRICS[metric]
    if metric in _QUALI_METRICS:
        return "staging_qualifying_results", _QUALI_METRICS[metric]
    raise ValueError(f"Unsupported metric_target: {metric!r}")


def _entity_col(params) -> str:
    return _ENTITY_COL.get(params.get("target_entity", "driver"), "driver_id")


def _scalar(conn, metric, entity_id, params, year=None, circuit_id=None) -> float:
    """Compute one metric for one entity (driver or constructor) over a scope,
    optionally narrowed to a single year and/or circuit."""
    table, expr = _source(metric)
    ecol = _entity_col(params)
    where = [f"{ecol} = ?"]
    args: list[Any] = [entity_id]
    if year is not None:
        where.append("year = ?"); args.append(year)
    else:
        where.append("year BETWEEN ? AND ?")
        args += [params["start_year"], params["end_year"]]
    # A constructor filter is only meaningful for driver-keyed questions
    # (a constructor question is already scoped to that constructor).
    if params.get("filter_constructor_id") and ecol != "constructor_id":
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    # Circuit filtering only applies to race-sourced metrics.
    circuit = circuit_id if circuit_id is not None else params.get("filter_circuit_id")
    if circuit and table == "staging_race_results":
        where.append("circuit_id = ?"); args.append(circuit)
    sql = f"SELECT {expr} AS v FROM {table} WHERE {' AND '.join(where)}"
    return conn.execute(sql, args).fetchone()["v"] or 0


def _scope_years(conn, metric, entity_id, params) -> list[int]:
    table, _ = _source(metric)
    ecol = _entity_col(params)
    where = [f"{ecol} = ?", "year BETWEEN ? AND ?"]
    args = [entity_id, params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id") and ecol != "constructor_id":
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    rows = conn.execute(
        f"SELECT DISTINCT year FROM {table} WHERE {' AND '.join(where)} ORDER BY year", args
    ).fetchall()
    return [r["year"] for r in rows]


def _scope_circuits(conn, entity_id, params) -> list[str]:
    """Distinct circuits the entity raced at in scope (for best_circuit)."""
    ecol = _entity_col(params)
    where = [f"{ecol} = ?", "year BETWEEN ? AND ?", "circuit_id IS NOT NULL"]
    args = [entity_id, params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id") and ecol != "constructor_id":
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    rows = conn.execute(
        f"SELECT DISTINCT circuit_id FROM staging_race_results WHERE {' AND '.join(where)}", args
    ).fetchall()
    return [r["circuit_id"] for r in rows]


def _one_two_finishes(conn, constructor_id, params) -> int:
    """Races where one constructor took BOTH P1 and P2 (a team 1-2)."""
    where = ["constructor_id = ?", "year BETWEEN ? AND ?"]
    args = [constructor_id, params["start_year"], params["end_year"]]
    if params.get("filter_circuit_id"):
        where.append("circuit_id = ?"); args.append(params["filter_circuit_id"])
    sql = ("SELECT COUNT(*) AS v FROM ("
           "SELECT year, round FROM staging_race_results "
           f"WHERE {' AND '.join(where)} GROUP BY year, round "
           "HAVING SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) >= 1 "
           "AND SUM(CASE WHEN position = 2 THEN 1 ELSE 0 END) >= 1)")
    return conn.execute(sql, args).fetchone()["v"] or 0


def _circuit_fact(conn, metric, circuit_id, params) -> int:
    """Circuit-keyed facts: distinct race winners, or races hosted, in scope."""
    args = [circuit_id, params["start_year"], params["end_year"]]
    if metric == "distinct_winners":
        sql = ("SELECT COUNT(DISTINCT driver_id) AS v FROM staging_race_results "
               "WHERE circuit_id = ? AND year BETWEEN ? AND ? AND position = 1")
    else:  # races_held
        sql = ("SELECT COUNT(*) AS v FROM (SELECT DISTINCT year, round FROM staging_race_results "
               "WHERE circuit_id = ? AND year BETWEEN ? AND ?)")
    return conn.execute(sql, args).fetchone()["v"] or 0


def _poles_converted(conn, entity_id, params) -> int:
    """Pole positions (started the GP on grid P1) that the driver converted into a
    win that same race."""
    where = ["driver_id = ?", "grid = 1", "position = 1", "year BETWEEN ? AND ?"]
    args = [entity_id, params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id"):
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    if params.get("filter_circuit_id"):
        where.append("circuit_id = ?"); args.append(params["filter_circuit_id"])
    sql = f"SELECT COUNT(*) AS v FROM staging_race_results WHERE {' AND '.join(where)}"
    return conn.execute(sql, args).fetchone()["v"] or 0


def _longest_streak(conn, entity_id, params, max_position: int) -> int:
    """Longest run of consecutive classified finishes at or above `max_position`
    (10 -> top-10 streak, 3 -> podium streak), in chronological race order."""
    ecol = _entity_col(params)
    where = [f"{ecol} = ?", "year BETWEEN ? AND ?"]
    args: list[Any] = [entity_id, params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id") and ecol != "constructor_id":
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    rows = conn.execute(
        f"SELECT position FROM staging_race_results WHERE {' AND '.join(where)} "
        "ORDER BY year, round", args,
    ).fetchall()
    best = run = 0
    for r in rows:
        if r["position"] is not None and r["position"] <= max_position:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _teammate_count(conn, driver_id, params) -> int:
    """How many different drivers shared the entity's car (same constructor in
    the same race) across the scope."""
    sql = ("SELECT COUNT(DISTINCT b.driver_id) AS v FROM staging_race_results a "
           "JOIN staging_race_results b ON b.year = a.year AND b.round = a.round "
           "AND b.constructor_id = a.constructor_id AND b.driver_id != a.driver_id "
           "WHERE a.driver_id = ? AND a.constructor_id IS NOT NULL "
           "AND a.year BETWEEN ? AND ?")
    args = [driver_id, params["start_year"], params["end_year"]]
    return conn.execute(sql, args).fetchone()["v"] or 0


def _front_row_lockouts(conn, constructor_id, params) -> int:
    """Races where one constructor started from BOTH front-row slots (P1 and P2)."""
    sql = ("SELECT COUNT(*) AS v FROM ("
           "SELECT year, round FROM staging_race_results "
           "WHERE constructor_id = ? AND year BETWEEN ? AND ? GROUP BY year, round "
           "HAVING SUM(CASE WHEN grid = 1 THEN 1 ELSE 0 END) >= 1 "
           "AND SUM(CASE WHEN grid = 2 THEN 1 ELSE 0 END) >= 1)")
    args = [constructor_id, params["start_year"], params["end_year"]]
    return conn.execute(sql, args).fetchone()["v"] or 0


def _most_wins_one_driver(conn, entity_id, params) -> int:
    """The win record held by a single driver within the scope (circuit-keyed:
    the most wins anyone has taken at that venue)."""
    ecol = _entity_col(params)
    sql = (f"SELECT COALESCE(MAX(w), 0) AS v FROM ("
           f"SELECT COUNT(*) AS w FROM staging_race_results "
           f"WHERE {ecol} = ? AND position = 1 AND year BETWEEN ? AND ? "
           f"GROUP BY driver_id)")
    args = [entity_id, params["start_year"], params["end_year"]]
    return conn.execute(sql, args).fetchone()["v"] or 0


def compute_metric(conn: sqlite3.Connection, params: dict) -> Decimal:
    """Independently compute the trusted value for a question. Never reads
    `proposed_answer`."""
    metric = params["metric_target"]
    aggregation = params.get("aggregation", "total")

    if aggregation not in _AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation!r}")

    # --- Special metrics: dedicated queries, 'total' aggregation only. ---
    if metric in _SPECIAL_METRICS:
        if aggregation != "total":
            raise ValueError(f"{metric} only supports the 'total' aggregation")
        entity = params["entity_id"]
        if metric == "poles_converted":
            return Decimal(str(_poles_converted(conn, entity, params)))
        if metric == "one_two_finishes":
            return Decimal(str(_one_two_finishes(conn, entity, params)))
        if metric == "longest_points_streak":
            return Decimal(str(_longest_streak(conn, entity, params, max_position=10)))
        if metric == "longest_podium_streak":
            return Decimal(str(_longest_streak(conn, entity, params, max_position=3)))
        if metric == "longest_win_streak":
            return Decimal(str(_longest_streak(conn, entity, params, max_position=1)))
        if metric == "teammate_count":
            return Decimal(str(_teammate_count(conn, entity, params)))
        if metric == "front_row_lockouts":
            return Decimal(str(_front_row_lockouts(conn, entity, params)))
        if metric == "most_wins_one_driver":
            return Decimal(str(_most_wins_one_driver(conn, entity, params)))
        return Decimal(str(_circuit_fact(conn, metric, entity, params)))  # distinct_winners | races_held

    if metric not in SUPPORTED_METRICS:
        raise ValueError(f"Unsupported metric_target: {metric!r}")

    entity = params["entity_id"]

    if aggregation == "total":
        value = _scalar(conn, metric, entity, params)
    elif aggregation == "difference":
        b = params["entity_id_b"]
        value = _scalar(conn, metric, entity, params) - _scalar(conn, metric, b, params)
    elif aggregation == "best_season":
        years = _scope_years(conn, metric, entity, params)
        value = max((_scalar(conn, metric, entity, params, y) for y in years), default=0)
    elif aggregation == "which_year":
        # Only seasons where the metric actually occurred are candidates: "which
        # season did they win the most" is meaningless for a driver who never won,
        # so an all-zero scope returns 0 (and the emit gate drops the question)
        # rather than reporting their debut year as a phantom peak.
        years = [y for y in _scope_years(conn, metric, entity, params)
                 if _scalar(conn, metric, entity, params, y) > 0]
        # Earliest season achieving the peak value (deterministic tie-break).
        value = max(years, key=lambda y: (_scalar(conn, metric, entity, params, y), -y)) if years else 0
    elif aggregation == "first_season":
        years = _scope_years(conn, metric, entity, params)
        value = next((y for y in years if _scalar(conn, metric, entity, params, y) > 0), 0)
    elif aggregation == "last_season":
        years = _scope_years(conn, metric, entity, params)
        value = next((y for y in reversed(years) if _scalar(conn, metric, entity, params, y) > 0), 0)
    elif aggregation == "percentage_of_races":
        starts = _scalar(conn, "starts", entity, params)
        metric_total = _scalar(conn, metric, entity, params)
        value = round(100 * metric_total / starts) if starts else 0
    elif aggregation == "per_season_avg":
        years = _scope_years(conn, metric, entity, params)
        value = round(_scalar(conn, metric, entity, params) / len(years)) if years else 0
    elif aggregation == "best_circuit":
        circuits = _scope_circuits(conn, entity, params)
        value = max((_scalar(conn, metric, entity, params, circuit_id=c) for c in circuits), default=0)

    return Decimal(str(value))


def validate_ai_question(conn: sqlite3.Connection, llm_output: dict) -> ValidationResult:
    """Validate one LLM-generated question against trusted staging data. The
    LLM's `proposed_answer` is compared but NEVER trusted as the source of truth."""
    params = llm_output["validation_parameters"]
    metric = params.get("metric_target")
    proposed = llm_output.get("proposed_answer")

    try:
        expected = compute_metric(conn, params)
    except (ValueError, KeyError) as exc:
        return ValidationResult(False, str(metric), None, proposed, reason=str(exc))

    if expected == Decimal(str(proposed)):
        return ValidationResult(True, metric, expected, proposed)
    return ValidationResult(
        False, metric, expected, proposed,
        reason="Hallucination detected: proposed answer does not match staging data",
    )
