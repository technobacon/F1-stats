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
      percentage_of_races  100 * metric / races-entered, rounded
      difference           entity_id minus entity_id_b (head-to-head)

Correctness notes carried from the spec:
  * Poles/front-rows come from staging_qualifying_results (quali position), NOT
    race grid, which diverges under grid penalties.
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
    "fastest_laps":         "SUM(CASE WHEN fastest_lap = 1 THEN 1 ELSE 0 END)",
    "points":               "COALESCE(SUM(points), 0)",
    "dnfs":                 "SUM(CASE WHEN position IS NULL THEN 1 ELSE 0 END)",
    "points_finishes":      "SUM(CASE WHEN position BETWEEN 1 AND 10 THEN 1 ELSE 0 END)",
    "positions_gained":     "COALESCE(SUM(CASE WHEN position IS NOT NULL AND grid IS NOT NULL "
                            "THEN grid - position ELSE 0 END), 0)",
    "distinct_constructors":"COUNT(DISTINCT constructor_id)",
    "seasons_active":       "COUNT(DISTINCT year)",
    "starts":               "COUNT(*)",
}
_QUALI_METRICS = {
    "poles":      "SUM(CASE WHEN quali_position = 1 THEN 1 ELSE 0 END)",
    "front_rows": "SUM(CASE WHEN quali_position <= 2 THEN 1 ELSE 0 END)",
}

_AGGREGATIONS = {
    "total", "best_season", "which_year", "first_season",
    "percentage_of_races", "difference",
}

# Metrics whose result is a count of races (used to pick slider/answer hints).
SUPPORTED_METRICS = set(_RACE_METRICS) | set(_QUALI_METRICS) | {"poles_converted"}


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


def _scalar(conn, metric, entity_id, params, year=None) -> float:
    """Compute one metric for one driver over a scope (optionally a single year)."""
    table, expr = _source(metric)
    where = ["driver_id = ?"]
    args: list[Any] = [entity_id]
    if year is not None:
        where.append("year = ?"); args.append(year)
    else:
        where.append("year BETWEEN ? AND ?")
        args += [params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id"):
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    # Circuit filtering only applies to race-sourced metrics.
    if params.get("filter_circuit_id") and table == "staging_race_results":
        where.append("circuit_id = ?"); args.append(params["filter_circuit_id"])
    sql = f"SELECT {expr} AS v FROM {table} WHERE {' AND '.join(where)}"
    return conn.execute(sql, args).fetchone()["v"] or 0


def _scope_years(conn, metric, entity_id, params) -> list[int]:
    table, _ = _source(metric)
    where = ["driver_id = ?", "year BETWEEN ? AND ?"]
    args = [entity_id, params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id"):
        where.append("constructor_id = ?"); args.append(params["filter_constructor_id"])
    rows = conn.execute(
        f"SELECT DISTINCT year FROM {table} WHERE {' AND '.join(where)} ORDER BY year", args
    ).fetchall()
    return [r["year"] for r in rows]


def _poles_converted(conn, entity_id, params) -> int:
    """Pole positions (quali P1) that the same driver converted into a win that
    weekend. Independent join across the two staging tables."""
    where = ["q.driver_id = ?", "q.quali_position = 1", "r.position = 1",
             "r.year BETWEEN ? AND ?"]
    args = [entity_id, params["start_year"], params["end_year"]]
    if params.get("filter_constructor_id"):
        where.append("r.constructor_id = ?"); args.append(params["filter_constructor_id"])
    if params.get("filter_circuit_id"):
        where.append("r.circuit_id = ?"); args.append(params["filter_circuit_id"])
    sql = ("SELECT COUNT(*) AS v FROM staging_qualifying_results q "
           "JOIN staging_race_results r ON q.driver_id = r.driver_id "
           "AND q.year = r.year AND q.round = r.round "
           f"WHERE {' AND '.join(where)}")
    return conn.execute(sql, args).fetchone()["v"] or 0


def compute_metric(conn: sqlite3.Connection, params: dict) -> Decimal:
    """Independently compute the trusted value for a question. Never reads
    `proposed_answer`."""
    metric = params["metric_target"]
    aggregation = params.get("aggregation", "total")

    if aggregation not in _AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation!r}")
    if metric == "poles_converted":
        if aggregation != "total":
            raise ValueError("poles_converted only supports the 'total' aggregation")
        return Decimal(str(_poles_converted(conn, params["entity_id"], params)))
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
        years = _scope_years(conn, metric, entity, params)
        # Earliest season achieving the peak value (deterministic tie-break).
        value = max(years, key=lambda y: (_scalar(conn, metric, entity, params, y), -y)) if years else 0
    elif aggregation == "first_season":
        years = _scope_years(conn, metric, entity, params)
        value = next((y for y in years if _scalar(conn, metric, entity, params, y) > 0), 0)
    elif aggregation == "percentage_of_races":
        starts = _scalar(conn, "starts", entity, params)
        metric_total = _scalar(conn, metric, entity, params)
        value = round(100 * metric_total / starts) if starts else 0

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
