"""Deterministic anti-hallucination validation layer (Pipeline §3).

THE AUTOMATED VERIFICATION INVARIANT: the pipeline must never trust the integer
supplied in `proposed_answer` by the language model. Instead this module reads
the structural `validation_parameters`, re-computes a completely independent
query against the trusted staging tables, and only commits the question to
production if the independently computed value matches.

Two correctness notes carried over from the spec:
  * Poles are read from staging_qualifying_results (quali P1), NOT race grid,
    which diverges whenever a grid penalty is applied.
  * filter_constructor_id is OPTIONAL. Career-total questions omit it; its
    absence means "no constructor filter".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

# Maps metric_target -> (staging source table, SQL aggregate predicate).
# Each predicate receives a (where_clause, params) pair already scoped to the
# driver / year-range / optional constructor and returns the computed value.
_METRIC_SOURCE = {
    "wins": "race_results",
    "podiums": "race_results",
    "fastest_laps": "race_results",
    "points": "race_results",
    "poles": "qualifying_results",  # NOT race grid — avoids grid-penalty drift
}

_METRIC_PREDICATE = {
    "wins": "COUNT(*)",
    "podiums": "COUNT(*)",
    "fastest_laps": "COUNT(*)",
    # COALESCE so a driver with zero matching rows sums to 0.0, not NULL.
    "points": "COALESCE(SUM(points), 0)",
    "poles": "COUNT(*)",
}

# Extra row-level filters per metric, beyond the base driver/year/constructor scope.
_METRIC_FILTER = {
    "wins": "position = 1",
    "podiums": "position IN (1, 2, 3)",
    "fastest_laps": "fastest_lap = 1",
    "points": None,
    "poles": "quali_position = 1",
}


@dataclass
class ValidationResult:
    ok: bool
    metric: str
    expected: Any  # independently computed (trusted) value
    proposed: Any  # value the LLM proposed
    reason: str = ""


def compute_metric(conn: sqlite3.Connection, params: dict) -> Decimal:
    """Independently compute the true value of a metric from staging data.

    This is the trusted recomputation. It never reads `proposed_answer`.
    """
    metric = params["metric_target"]
    if metric not in _METRIC_SOURCE:
        raise ValueError(f"Unsupported metric_target: {metric!r}")

    source = _METRIC_SOURCE[metric]
    table = f"staging_{source}"
    predicate = _METRIC_PREDICATE[metric]

    where = ["driver_id = ?", "year BETWEEN ? AND ?"]
    args: list[Any] = [
        params["entity_id"],
        params["start_year"],
        params["end_year"],
    ]

    # Constructor filter is optional — omitted for career-total questions.
    constructor_id = params.get("filter_constructor_id")
    if constructor_id:
        where.append("constructor_id = ?")
        args.append(constructor_id)

    row_filter = _METRIC_FILTER[metric]
    if row_filter:
        where.append(row_filter)

    sql = f"SELECT {predicate} AS value FROM {table} WHERE {' AND '.join(where)}"
    value = conn.execute(sql, args).fetchone()["value"]

    # Use Decimal for exact comparison, preserving F1 half-points (e.g. 0.5).
    return Decimal(str(value))


def validate_ai_question(conn: sqlite3.Connection, llm_output: dict) -> ValidationResult:
    """Validate one LLM-generated question against trusted staging data.

    Returns a ValidationResult; the caller decides whether to commit. The LLM's
    `proposed_answer` is compared but NEVER trusted as the source of truth.
    """
    params = llm_output["validation_parameters"]
    metric = params.get("metric_target")
    proposed = llm_output.get("proposed_answer")

    try:
        expected = compute_metric(conn, params)
    except ValueError as exc:
        return ValidationResult(
            ok=False, metric=str(metric), expected=None, proposed=proposed,
            reason=str(exc),
        )

    proposed_dec = Decimal(str(proposed))
    if expected == proposed_dec:
        return ValidationResult(
            ok=True, metric=metric, expected=expected, proposed=proposed,
        )
    return ValidationResult(
        ok=False, metric=metric, expected=expected, proposed=proposed,
        reason="Hallucination detected: proposed answer does not match staging data",
    )
