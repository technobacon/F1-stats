"""Deterministic anti-hallucination validation engine.

THE AUTOMATED VERIFICATION INVARIANT: the pipeline never trusts the number the
question generator supplies in `proposed_answer`. For every question it reads
the structural `validation_parameters`, re-computes a completely independent
value against the trusted staging tables (or the exact skill-XP formula), and
only commits if the values match.

The engine is domain + metric + aggregation based so a wide variety of
questions all reduce to deterministic SQL / arithmetic:

  domain           item | monster | quest | skill
  metric_target    which column (or formula) to measure
  aggregation      how to roll it up:
      identity     the column value for one entity (the workhorse)
      difference   entity_id minus entity_id_b (head-to-head)
      sum_where    SUM(metric) over rows matching `filters`
      count_where  COUNT(*) over rows matching `filters`
      max_where    MAX(metric) over rows matching `filters`
      percentage_where  100 * count(filters) / count(all rows), rounded

The skill domain has no table at all: every XP answer comes from
`xp_for_level`, the exact RuneScape experience formula. That makes skill
questions the anchor of the anti-hallucination design — an infinite supply of
questions whose answers are pure arithmetic.

Data provenance note: the staging tables are built from the OSRS Wiki
(CC BY-SA) and the wiki's real-time prices API — see docs/DATA_SOURCES.md.
The validation layer's guarantee is that a question's answer matches the
staging data; keeping staging faithful to the game is the dataset build's
responsibility.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# ── The skill-XP formula (exact, deterministic) ──────────────────────────────
MAX_LEVEL = 99

_XP_CACHE: dict[int, int] = {}


def xp_for_level(level: int) -> int:
    """Cumulative experience required to reach `level` (1-based, XP(1) == 0).

    The canonical RuneScape formula:
        XP(n) = floor( (1/4) * sum_{L=1}^{n-1} floor(L + 300 * 2^(L/7)) )
    e.g. XP(2) == 83, XP(92) == 6,517,253, XP(99) == 13,034,431.
    """
    if not 1 <= level <= 126:
        raise ValueError(f"level out of range: {level}")
    if level not in _XP_CACHE:
        points = 0
        for lvl in range(1, level):
            points += math.floor(lvl + 300 * 2 ** (lvl / 7))
        _XP_CACHE[level] = points // 4
    return _XP_CACHE[level]


def xp_between(level_a: int, level_b: int) -> int:
    """Experience needed to go from `level_a` to `level_b` (a < b)."""
    if level_a >= level_b:
        raise ValueError("level_a must be below level_b")
    return xp_for_level(level_b) - xp_for_level(level_a)


# ── Table-backed domains ─────────────────────────────────────────────────────
# domain -> (table, id column, metric column allow-list)
_DOMAINS: dict[str, tuple[str, str, frozenset[str]]] = {
    "item": ("staging_items", "item_id", frozenset({
        "ge_price", "high_alch", "low_alch", "buy_limit", "value", "release_year",
    })),
    "monster": ("staging_monsters", "monster_id", frozenset({
        "combat_level", "hitpoints", "max_hit", "slayer_level", "slayer_xp",
        "release_year",
    })),
    "quest": ("staging_quests", "quest_id", frozenset({
        "quest_points", "release_year",
    })),
}

# Skill-domain formula metrics (no table).
_SKILL_METRICS = {"xp_for_level", "xp_between"}

_AGGREGATIONS = {
    "identity", "difference", "sum_where", "count_where", "max_where",
    "percentage_where",
}

# Filter vocabulary for the *_where aggregations. Each key maps to a SQL
# fragment; anything not on this allow-list is rejected, so a malformed
# generator combo fails validation instead of silently computing nonsense.
_FILTERS: dict[str, str] = {
    "members_eq":       "members = ?",
    "difficulty_eq":    "difficulty = ?",
    "series_eq":        "series = ?",
    "is_boss_eq":       "is_boss = ?",
    "quest_points_eq":  "quest_points = ?",
    "release_year_lo":  "release_year >= ?",
    "release_year_hi":  "release_year <= ?",
    "combat_level_min": "combat_level >= ?",
    "slayer_level_min": "slayer_level >= ?",
    "fame_tier_max":    "fame_tier <= ?",
}


@dataclass
class ValidationResult:
    ok: bool
    metric: str
    expected: Any
    proposed: Any
    reason: str = ""


def _domain(params: dict) -> tuple[str, str, frozenset[str]]:
    domain = params.get("domain")
    if domain not in _DOMAINS:
        raise ValueError(f"Unsupported domain: {domain!r}")
    return _DOMAINS[domain]


def _where_clause(filters: dict | None) -> tuple[str, list]:
    """Build a WHERE clause from the allow-listed filter vocabulary."""
    if not filters:
        return "1=1", []
    parts, args = [], []
    for key, value in filters.items():
        frag = _FILTERS.get(key)
        if frag is None:
            raise ValueError(f"Unsupported filter: {key!r}")
        parts.append(frag)
        args.append(value)
    return " AND ".join(parts), args


def _identity(conn: sqlite3.Connection, params: dict, entity_key: str = "entity_id") -> float:
    """The metric column's value for one entity. A missing entity or NULL value
    raises (the emit gate then drops the question) rather than defaulting to 0 —
    an absent fact must never masquerade as a zero answer."""
    table, id_col, metrics = _domain(params)
    metric = params["metric_target"]
    if metric not in metrics:
        raise ValueError(f"Unsupported metric_target for {params.get('domain')}: {metric!r}")
    row = conn.execute(
        f"SELECT {metric} AS v FROM {table} WHERE {id_col} = ?", (params[entity_key],)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown entity: {params[entity_key]!r}")
    if row["v"] is None:
        raise ValueError(f"{metric} is not recorded for {params[entity_key]!r}")
    return float(row["v"])


def _skill_metric(params: dict) -> int:
    metric = params["metric_target"]
    if metric == "xp_for_level":
        return xp_for_level(int(params["level"]))
    if metric == "xp_between":
        return xp_between(int(params["level_a"]), int(params["level_b"]))
    raise ValueError(f"Unsupported skill metric_target: {metric!r}")


def compute_metric(conn: sqlite3.Connection, params: dict) -> Decimal:
    """Independently compute the trusted value for a question. Never reads
    `proposed_answer`."""
    if params.get("domain") == "skill":
        return Decimal(str(_skill_metric(params)))

    aggregation = params.get("aggregation", "identity")
    if aggregation not in _AGGREGATIONS:
        raise ValueError(f"Unsupported aggregation: {aggregation!r}")

    table, _id_col, metrics = _domain(params)

    if aggregation == "identity":
        return Decimal(str(_identity(conn, params)))

    if aggregation == "difference":
        a = _identity(conn, params)
        b = _identity(conn, params, entity_key="entity_id_b")
        return Decimal(str(a - b))

    where, args = _where_clause(params.get("filters"))
    if aggregation == "count_where":
        sql = f"SELECT COUNT(*) AS v FROM {table} WHERE {where}"
    elif aggregation == "percentage_where":
        total = conn.execute(f"SELECT COUNT(*) AS v FROM {table}").fetchone()["v"] or 0
        if total == 0:
            raise ValueError(f"{table} is empty")
        n = conn.execute(f"SELECT COUNT(*) AS v FROM {table} WHERE {where}", args).fetchone()["v"] or 0
        return Decimal(str(round(100 * n / total)))
    else:  # sum_where | max_where
        metric = params["metric_target"]
        if metric not in metrics:
            raise ValueError(f"Unsupported metric_target for {params.get('domain')}: {metric!r}")
        fn = "SUM" if aggregation == "sum_where" else "MAX"
        sql = f"SELECT {fn}({metric}) AS v FROM {table} WHERE {where}"

    value = conn.execute(sql, args).fetchone()["v"]
    if value is None:
        raise ValueError(f"{aggregation} over {table} matched no rows")
    return Decimal(str(value))


def validate_ai_question(conn: sqlite3.Connection, llm_output: dict) -> ValidationResult:
    """Validate one generated question against trusted staging data. The
    generator's `proposed_answer` is compared but NEVER trusted as the source of
    truth."""
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
