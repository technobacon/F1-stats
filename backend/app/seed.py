"""Offline seed + mock-LLM pipeline (PRD §3, Pipeline §2-4).

The production system runs a weekly ETL: Jolpica API -> staging tables -> LLM
question synthesizer -> deterministic validation -> production table. This
module stands in for that pipeline so the prototype runs fully offline (the PRD
forbids fetching from external APIs during gameplay):

  1. Seed granular staging rows from a set of illustrative driver "stints".
  2. A mock LLM emits question objects in the strict schema (Pipeline §2.2),
     INCLUDING one deliberately hallucinated answer.
  3. Every question is run through validation.validate_ai_question; only the
     ones whose proposed answer matches the independently-computed staging
     value are committed to production_trivia_questions.

NOTE: the per-stint figures below are illustrative round numbers chosen to make
the prototype self-consistent, not a complete historical import. In production
these rows come from the Jolpica ETL. The validation layer's guarantee is that
a question's answer matches the staging data — keeping staging faithful to
reality is the ETL's responsibility.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field

from . import db
from .validation import validate_ai_question


@dataclass
class Stint:
    driver_id: str
    constructor_id: str
    start_year: int
    end_year: int
    wins: int = 0
    podiums: int = 0          # total podiums (>= wins)
    fastest_laps: int = 0
    poles: int = 0
    points: float = 0.0


@dataclass
class Driver:
    driver_id: str
    full_name: str
    nationality: str
    active_from: int
    active_to: int
    stints: list[Stint] = field(default_factory=list)


@dataclass
class Constructor:
    constructor_id: str
    name: str
    nationality: str


CONSTRUCTORS = [
    Constructor("benetton", "Benetton", "Italian"),
    Constructor("ferrari", "Ferrari", "Italian"),
    Constructor("mclaren", "McLaren", "British"),
    Constructor("mercedes", "Mercedes", "German"),
    Constructor("red_bull", "Red Bull", "Austrian"),
    Constructor("lotus", "Lotus", "British"),
    Constructor("renault", "Renault", "French"),
]

DRIVERS = [
    Driver("schumacher", "Michael Schumacher", "German", 1991, 2012, stints=[
        Stint("schumacher", "benetton", 1991, 1995, wins=19, podiums=36, fastest_laps=14, poles=4, points=337.0),
        Stint("schumacher", "ferrari", 1996, 2006, wins=72, podiums=116, fastest_laps=53, poles=58, points=1038.0),
    ]),
    Driver("hamilton", "Lewis Hamilton", "British", 2007, 2024, stints=[
        Stint("hamilton", "mclaren", 2007, 2012, wins=21, podiums=49, fastest_laps=15, poles=26, points=1090.0),
        Stint("hamilton", "mercedes", 2013, 2020, wins=82, podiums=148, fastest_laps=39, poles=72, points=3000.0),
    ]),
    Driver("alonso", "Fernando Alonso", "Spanish", 2001, 2024, stints=[
        Stint("alonso", "renault", 2003, 2006, wins=15, podiums=23, fastest_laps=12, poles=14, points=420.0),
        Stint("alonso", "ferrari", 2010, 2014, wins=11, podiums=44, fastest_laps=11, poles=2, points=860.0),
    ]),
    Driver("raikkonen", "Kimi Raikkonen", "Finnish", 2001, 2021, stints=[
        Stint("raikkonen", "mclaren", 2002, 2006, wins=9, podiums=25, fastest_laps=18, poles=12, points=350.0),
        Stint("raikkonen", "ferrari", 2007, 2009, wins=10, podiums=24, fastest_laps=17, poles=4, points=400.0),
    ]),
    Driver("verstappen", "Max Verstappen", "Dutch", 2015, 2024, stints=[
        Stint("verstappen", "red_bull", 2016, 2023, wins=54, podiums=98, fastest_laps=30, poles=36, points=2500.0),
    ]),
]


def _insert_stint_rows(conn: sqlite3.Connection, stint: Stint, round_offset: int) -> int:
    """Generate granular staging rows for a stint so the metric queries recompute
    the stint totals exactly. Returns the next free round offset.

    Layout (each row a distinct round so the validation COUNTs are exact):
      * `wins` rows           position=1
      * `podiums - wins` rows position=2  (fills out the podium total)
      * `fastest_laps` rows   position=8, fastest_lap=1 (kept off the podium)
      * one summary row       position=11 carrying the whole `points` total
      * `poles` quali rows    quali_position=1
    """
    rnd = round_offset
    rows: list[tuple] = []

    for _ in range(stint.wins):
        rows.append((stint.driver_id, stint.constructor_id, stint.start_year, rnd, 1, 1, 0, 0.0)); rnd += 1
    for _ in range(stint.podiums - stint.wins):
        rows.append((stint.driver_id, stint.constructor_id, stint.start_year, rnd, 2, 3, 0, 0.0)); rnd += 1
    for _ in range(stint.fastest_laps):
        rows.append((stint.driver_id, stint.constructor_id, stint.start_year, rnd, 8, 9, 1, 0.0)); rnd += 1
    # Single summary row carries the full points total (SUM is what validation checks).
    rows.append((stint.driver_id, stint.constructor_id, stint.start_year, rnd, 11, 12, 0, stint.points)); rnd += 1

    conn.executemany(
        "INSERT INTO staging_race_results "
        "(driver_id, constructor_id, year, round, position, grid, fastest_lap, points) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    quali_rows = [
        (stint.driver_id, stint.constructor_id, stint.start_year, round_offset + i, 1)
        for i in range(stint.poles)
    ]
    conn.executemany(
        "INSERT INTO staging_qualifying_results "
        "(driver_id, constructor_id, year, round, quali_position) VALUES (?, ?, ?, ?, ?)",
        quali_rows,
    )
    return rnd


def seed_staging(conn: sqlite3.Connection) -> None:
    """Populate the staging tables (stand-in for the Jolpica ETL extract)."""
    conn.executemany(
        "INSERT INTO staging_constructors (constructor_id, name, nationality) VALUES (?, ?, ?)",
        [(c.constructor_id, c.name, c.nationality) for c in CONSTRUCTORS],
    )
    conn.executemany(
        "INSERT INTO staging_drivers (driver_id, full_name, nationality, active_from, active_to) "
        "VALUES (?, ?, ?, ?, ?)",
        [(d.driver_id, d.full_name, d.nationality, d.active_from, d.active_to) for d in DRIVERS],
    )
    round_offset = 1
    for driver in DRIVERS:
        for stint in driver.stints:
            round_offset = _insert_stint_rows(conn, stint, round_offset)
    conn.commit()


# --- Mock LLM output (strict schema, Pipeline §2.2) -------------------------
# Each item is exactly what the LLM prompt controller would receive: rigid JSON,
# no markdown, no proposed answer the validator trusts. One item is a planted
# hallucination to demonstrate that the validation layer rejects it.

def mock_llm_questions() -> list[dict]:
    return [
        {
            "question_text": "How many race wins did Michael Schumacher claim during his "
                             "tenure with Benetton (1991-1995)?",
            "difficulty_weight": 2.5,
            "game_mode": "daily",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "schumacher",
                "filter_constructor_id": "benetton",
                "start_year": 1991, "end_year": 1995, "metric_target": "wins",
            },
            "proposed_answer": 19,
        },
        {
            "question_text": "How many pole positions did Lewis Hamilton take with McLaren "
                             "(2007-2012)?",
            "difficulty_weight": 2.0,
            "game_mode": "daily",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "hamilton",
                "filter_constructor_id": "mclaren",
                "start_year": 2007, "end_year": 2012, "metric_target": "poles",
            },
            "proposed_answer": 26,
        },
        {
            # Career-total question: filter_constructor_id intentionally OMITTED.
            "question_text": "How many career podiums did Kimi Raikkonen score across all teams?",
            "difficulty_weight": 3.0,
            "game_mode": "daily",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "raikkonen",
                "start_year": 2002, "end_year": 2009, "metric_target": "podiums",
            },
            "proposed_answer": 49,  # 25 (McLaren) + 24 (Ferrari)
        },
        {
            "question_text": "How many fastest laps did Max Verstappen set with Red Bull "
                             "(2016-2023)?",
            "difficulty_weight": 2.5,
            "game_mode": "daily",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "verstappen",
                "filter_constructor_id": "red_bull",
                "start_year": 2016, "end_year": 2023, "metric_target": "fastest_laps",
            },
            "proposed_answer": 30,
        },
        {
            "question_text": "How many career wins did Fernando Alonso score with Renault "
                             "(2003-2006)?",
            "difficulty_weight": 2.0,
            "game_mode": "daily",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "alonso",
                "filter_constructor_id": "renault",
                "start_year": 2003, "end_year": 2006, "metric_target": "wins",
            },
            "proposed_answer": 15,
        },
        {
            "question_text": "How many championship points did Lewis Hamilton score with "
                             "Mercedes (2013-2020)?",
            "difficulty_weight": 3.5,
            "game_mode": "race_week",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "hamilton",
                "filter_constructor_id": "mercedes",
                "start_year": 2013, "end_year": 2020, "metric_target": "points",
            },
            "proposed_answer": 3000,
        },
        {
            # PLANTED HALLUCINATION: staging says 72, the LLM "remembers" 80.
            # The validation layer must reject this and keep it out of production.
            "question_text": "How many race wins did Michael Schumacher take with Ferrari "
                             "(1996-2006)?",
            "difficulty_weight": 2.0,
            "game_mode": "daily",
            "validation_parameters": {
                "target_entity": "driver", "entity_id": "schumacher",
                "filter_constructor_id": "ferrari",
                "start_year": 1996, "end_year": 2006, "metric_target": "wins",
            },
            "proposed_answer": 80,  # WRONG — true staging value is 72
        },
    ]


def run_validation_pipeline(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """Validate every mock question; commit the passing ones to production.

    Returns a summary {committed, rejected, rejections:[...]} for logging/CLI.
    """
    committed, rejections = 0, []
    for q in mock_llm_questions():
        result = validate_ai_question(conn, q)
        if not result.ok:
            rejections.append({
                "question": q["question_text"],
                "metric": result.metric,
                "expected": str(result.expected),
                "proposed": result.proposed,
                "reason": result.reason,
            })
            continue
        conn.execute(
            "INSERT OR IGNORE INTO production_trivia_questions "
            "(id, question_string, verified_answer, difficulty_weight, game_mode, "
            " is_active, scheduled_date) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (
                str(uuid.uuid4()),
                q["question_text"],
                float(result.expected),       # store the TRUSTED value, not the LLM's
                q.get("difficulty_weight", 1.0),
                q.get("game_mode", "daily"),
                today,
            ),
        )
        committed += 1
    conn.commit()
    return {"committed": committed, "rejected": len(rejections), "rejections": rejections}


def seed_all(db_path=db.DB_PATH) -> dict:
    """Full reset: rebuild schema, seed staging, run the validation pipeline."""
    conn = db.connect(db_path)
    try:
        db.reset_db(conn)
        seed_staging(conn)
        summary = run_validation_pipeline(conn)
    finally:
        conn.close()
    return summary


if __name__ == "__main__":
    summary = seed_all()
    print(f"Seed complete. Committed {summary['committed']} questions, "
          f"rejected {summary['rejected']}.")
    for r in summary["rejections"]:
        print(f"  REJECTED [{r['metric']}] {r['question']!r}: "
              f"expected {r['expected']}, LLM proposed {r['proposed']} -- {r['reason']}")
