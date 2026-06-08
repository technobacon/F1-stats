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

import hashlib
import random
import sqlite3
import uuid
from dataclasses import dataclass, field

from . import db
from .validation import compute_metric, validate_ai_question


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
    Constructor("williams", "Williams", "British"),
    Constructor("brawn", "Brawn GP", "British"),
    Constructor("sauber", "Sauber", "Swiss"),
    Constructor("racing_point", "Racing Point", "British"),
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
    Driver("senna", "Ayrton Senna", "Brazilian", 1984, 1994, stints=[
        Stint("senna", "lotus", 1985, 1987, wins=6, podiums=18, fastest_laps=10, poles=16, points=180.0),
        Stint("senna", "mclaren", 1988, 1993, wins=35, podiums=60, fastest_laps=15, poles=46, points=480.0),
    ]),
    Driver("prost", "Alain Prost", "French", 1980, 1993, stints=[
        Stint("prost", "mclaren", 1984, 1989, wins=30, podiums=55, fastest_laps=25, poles=18, points=500.0),
        Stint("prost", "williams", 1993, 1993, wins=7, podiums=12, fastest_laps=6, poles=13, points=99.0),
    ]),
    Driver("vettel", "Sebastian Vettel", "German", 2007, 2022, stints=[
        Stint("vettel", "red_bull", 2009, 2014, wins=38, podiums=64, fastest_laps=24, poles=44, points=1800.0),
        Stint("vettel", "ferrari", 2015, 2020, wins=14, podiums=55, fastest_laps=14, poles=12, points=1400.0),
    ]),
    Driver("button", "Jenson Button", "British", 2000, 2017, stints=[
        Stint("button", "brawn", 2009, 2009, wins=6, podiums=9, fastest_laps=3, poles=4, points=95.0),
        Stint("button", "mclaren", 2010, 2016, wins=8, podiums=33, fastest_laps=4, poles=4, points=900.0),
    ]),
    Driver("rosberg", "Nico Rosberg", "German", 2006, 2016, stints=[
        Stint("rosberg", "williams", 2006, 2009, wins=0, podiums=0, fastest_laps=4, poles=1, points=170.0),
        Stint("rosberg", "mercedes", 2010, 2016, wins=23, podiums=57, fastest_laps=20, poles=30, points=1500.0),
    ]),
    Driver("ricciardo", "Daniel Ricciardo", "Australian", 2011, 2024, stints=[
        Stint("ricciardo", "red_bull", 2014, 2018, wins=7, podiums=29, fastest_laps=13, poles=3, points=900.0),
    ]),
    Driver("leclerc", "Charles Leclerc", "Monegasque", 2018, 2024, stints=[
        Stint("leclerc", "ferrari", 2019, 2024, wins=8, podiums=43, fastest_laps=9, poles=26, points=1300.0),
    ]),
    Driver("russell", "George Russell", "British", 2019, 2024, stints=[
        Stint("russell", "mercedes", 2022, 2024, wins=3, podiums=15, fastest_laps=6, poles=4, points=600.0),
    ]),
    Driver("massa", "Felipe Massa", "Brazilian", 2002, 2017, stints=[
        Stint("massa", "ferrari", 2006, 2013, wins=11, podiums=36, fastest_laps=15, poles=15, points=1100.0),
    ]),
    Driver("webber", "Mark Webber", "Australian", 2002, 2013, stints=[
        Stint("webber", "red_bull", 2007, 2013, wins=9, podiums=42, fastest_laps=19, poles=13, points=1000.0),
    ]),
    Driver("perez", "Sergio Perez", "Mexican", 2011, 2024, stints=[
        Stint("perez", "racing_point", 2014, 2020, wins=1, podiums=8, fastest_laps=5, poles=1, points=700.0),
        Stint("perez", "red_bull", 2021, 2024, wins=5, podiums=30, fastest_laps=8, poles=3, points=1000.0),
    ]),
]

# Reserve Schumacher/Ferrari career wins (total) for the planted-hallucination
# demo, so no competing valid question with the same wording reaches production.
_GENERATION_SKIP = {("schumacher", "ferrari", "wins")}

# Circuit calendar used to lay out the synthetic race log (display name, country).
CIRCUITS = [
    ("monaco", "Monaco", "Monaco"), ("silverstone", "Silverstone", "UK"),
    ("monza", "Monza", "Italy"), ("spa", "Spa-Francorchamps", "Belgium"),
    ("suzuka", "Suzuka", "Japan"), ("interlagos", "Interlagos", "Brazil"),
    ("montreal", "Montreal", "Canada"), ("hungaroring", "Hungaroring", "Hungary"),
    ("barcelona", "Barcelona", "Spain"), ("red_bull_ring", "Red Bull Ring", "Austria"),
    ("zandvoort", "Zandvoort", "Netherlands"), ("marina_bay", "Marina Bay", "Singapore"),
    ("cota", "Circuit of the Americas", "USA"), ("mexico_city", "Mexico City", "Mexico"),
    ("sakhir", "Sakhir", "Bahrain"), ("imola", "Imola", "Italy"),
    ("albert_park", "Albert Park", "Australia"), ("baku", "Baku", "Azerbaijan"),
    ("jeddah", "Jeddah", "Saudi Arabia"), ("las_vegas", "Las Vegas", "USA"),
]
_CIRCUIT_NAME = {c[0]: c[1] for c in CIRCUITS}

# Illustrative modern points table; applied to every era for a self-consistent log.
_POINTS_TABLE = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}


def _races_in_year(year: int) -> int:
    if year < 1995:
        return 16
    if year < 2005:
        return 17
    if year < 2012:
        return 18
    return min(20, len(CIRCUITS))


def _dnf_rate(year: int) -> float:
    if year < 1995:
        return 0.22
    if year < 2005:
        return 0.15
    if year < 2015:
        return 0.09
    return 0.06


def _stint_rng(stint: Stint) -> random.Random:
    """Deterministic RNG per stint (hashlib, not salted hash()) so the generated
    log — and therefore every derived answer — is identical across redeploys."""
    key = f"{stint.driver_id}|{stint.constructor_id}|{stint.start_year}|{stint.end_year}"
    return random.Random(int(hashlib.md5(key.encode()).hexdigest()[:16], 16))


def _generate_log(stint: Stint) -> tuple[list[tuple], list[tuple]]:
    """Build a realistic race-by-race log for a stint. The targeted aggregates
    (wins, podiums, poles, fastest laps) are placed EXACTLY; everything else
    (points, DNFs, grids, positions gained) emerges from the log so derived
    metrics are internally consistent. Returns (race_rows, quali_rows)."""
    rng = _stint_rng(stint)
    entries = [(y, i + 1, CIRCUITS[i][0])
               for y in range(stint.start_year, stint.end_year + 1)
               for i in range(_races_in_year(y))]
    total = len(entries)
    if total == 0:
        return [], []

    idx = list(range(total))
    rng.shuffle(idx)
    W = max(0, min(stint.wins, total))
    Pe = max(0, min(stint.podiums - stint.wins, total - W))
    win_idx, pod_idx = set(idx[:W]), set(idx[W:W + Pe])
    rest = idx[W + Pe:]
    D = min(int(round(total * _dnf_rate(stint.start_year))), len(rest))
    dnf_idx, fin_idx = set(rest[:D]), rest[D:]

    position: dict[int, int | None] = {}
    for i in win_idx:
        position[i] = 1
    for i in pod_idx:
        position[i] = rng.choice([2, 3])
    for i in dnf_idx:
        position[i] = None
    fin_pos, fin_w = list(range(4, 17)), [13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    for i in fin_idx:
        position[i] = rng.choices(fin_pos, weights=fin_w)[0]

    classified = [i for i in idx if position[i] is not None]
    F = min(stint.fastest_laps, len(classified))
    fl_idx = set(rng.sample(classified, F)) if F else set()

    Pol = max(0, min(stint.poles, total))
    pole_idx = set(rng.sample(idx, Pol)) if Pol else set()
    remaining = [i for i in idx if i not in pole_idx]
    f2n = min(len(remaining), round(Pol * 0.6) + rng.randint(0, 3))
    front2_idx = set(rng.sample(remaining, f2n)) if f2n else set()

    race_rows, quali_rows = [], []
    for i, (year, rnd, cid) in enumerate(entries):
        pos = position[i]
        quali = 1 if i in pole_idx else 2 if i in front2_idx else rng.randint(3, 18)
        # Grid usually matches qualifying; occasional grid penalty diverges them.
        grid = min(20, quali + rng.randint(3, 8)) if rng.random() < 0.12 else quali
        fl = 1 if i in fl_idx else 0
        pts = _POINTS_TABLE.get(pos, 0) if pos else 0
        if fl and pos and pos <= 10:
            pts += 1
        race_rows.append((stint.driver_id, stint.constructor_id, year, rnd, cid,
                          pos, grid, fl, float(pts)))
        quali_rows.append((stint.driver_id, stint.constructor_id, year, rnd, quali))
    return race_rows, quali_rows


def seed_staging(conn: sqlite3.Connection) -> None:
    """Populate the staging tables (stand-in for the Jolpica ETL extract)."""
    conn.executemany(
        "INSERT INTO staging_constructors (constructor_id, name, nationality) VALUES (?, ?, ?)",
        [(c.constructor_id, c.name, c.nationality) for c in CONSTRUCTORS],
    )
    conn.executemany(
        "INSERT INTO staging_circuits (circuit_id, name, country) VALUES (?, ?, ?)", CIRCUITS,
    )
    conn.executemany(
        "INSERT INTO staging_drivers (driver_id, full_name, nationality, active_from, active_to) "
        "VALUES (?, ?, ?, ?, ?)",
        [(d.driver_id, d.full_name, d.nationality, d.active_from, d.active_to) for d in DRIVERS],
    )
    for driver in DRIVERS:
        for stint in driver.stints:
            race_rows, quali_rows = _generate_log(stint)
            conn.executemany(
                "INSERT INTO staging_race_results "
                "(driver_id, constructor_id, year, round, circuit_id, position, grid, "
                " fastest_lap, points) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                race_rows,
            )
            conn.executemany(
                "INSERT INTO staging_qualifying_results "
                "(driver_id, constructor_id, year, round, quali_position) VALUES (?, ?, ?, ?, ?)",
                quali_rows,
            )
    conn.commit()


# --- Mock LLM output (strict schema, Pipeline §2.2) -------------------------
# In production a real LLM phrases these. Here a data-driven generator emits the
# same rigid JSON the prompt controller would receive (querying the seeded
# staging data for each answer, exactly as an accurate LLM would). The
# deterministic validation layer then independently re-derives every answer
# before anything is committed — including catching the planted hallucination.

_CONSTRUCTOR_NAME = {c.constructor_id: c.name for c in CONSTRUCTORS}
_DRIVER_NAME = {d.driver_id: d.full_name for d in DRIVERS}
_DRIVER_SPAN = {
    d.driver_id: (min(s.start_year for s in d.stints), max(s.end_year for s in d.stints))
    for d in DRIVERS
}

# Curated overlapping-era rivalries for head-to-head questions.
_HEAD_TO_HEAD = [
    ("senna", "prost"), ("hamilton", "vettel"), ("hamilton", "alonso"),
    ("verstappen", "leclerc"), ("verstappen", "russell"), ("alonso", "raikkonen"),
    ("vettel", "webber"), ("rosberg", "hamilton"), ("button", "hamilton"),
    ("massa", "raikkonen"),
]
_H2H_METRICS = [("wins", "race wins"), ("podiums", "podiums")]


def _p(entity: str, **kw) -> dict:
    """Build a validation_parameters payload."""
    return {"target_entity": "driver", "entity_id": entity, **kw}


def _emit(conn, out, seen, text, params, mode, weight,
          kind="count", category="", dmin=None, dmax=None) -> None:
    """Compute the true answer for a question and stage it as an LLM output.
    Skips empty/trivial answers so the pool stays interesting."""
    if text in seen:
        return
    try:
        ans = compute_metric(conn, params)
    except Exception:  # noqa: BLE001 — malformed combo, just skip
        return
    if kind in ("count", "points", "percentage", "year") and ans <= 0:
        return
    pv = int(ans) if ans == ans.to_integral_value() else float(ans)
    seen.add(text)
    out.append({
        "question_text": text, "difficulty_weight": weight, "game_mode": mode,
        "answer_kind": kind, "category": category, "display_min": dmin, "display_max": dmax,
        "validation_parameters": params, "proposed_answer": pv,
    })


def generate_questions(conn: sqlite3.Connection) -> list[dict]:
    """Build the full validated-question pool by querying the seeded staging data
    across a wide variety of metrics and aggregations (PRD §4)."""
    out: list[dict] = []
    seen: set[str] = set()

    def E(*a, **k):
        _emit(conn, out, seen, *a, **k)

    for d in DRIVERS:
        name, did = d.full_name, d.driver_id
        lo, hi = _DRIVER_SPAN[did]

        # ---- Per-stint questions ----
        for s in d.stints:
            c, y1, y2 = s.constructor_id, s.start_year, s.end_year
            cn = _CONSTRUCTOR_NAME.get(c, c)
            P = lambda **kw: _p(did, filter_constructor_id=c, start_year=y1, end_year=y2, **kw)

            if (did, c, "wins") not in _GENERATION_SKIP:
                E(f"How many race wins did {name} take with {cn} ({y1}-{y2})?",
                  P(metric_target="wins"), "daily", 2.0, category="career")
            E(f"How many podium finishes did {name} score for {cn} ({y1}-{y2})?",
              P(metric_target="podiums"), "daily", 2.5, category="career")
            E(f"How many pole positions did {name} take for {cn} ({y1}-{y2})?",
              P(metric_target="poles"), "race_week", 3.0, category="qualifying")
            E(f"How many fastest laps did {name} set for {cn} ({y1}-{y2})?",
              P(metric_target="fastest_laps"), "race_week", 2.5, category="career")
            E(f"Roughly how many championship points did {name} score for {cn} ({y1}-{y2})?",
              P(metric_target="points"), "daily", 3.0, kind="points", category="career")
            E(f"How many times did {name} fail to finish (DNF) for {cn} ({y1}-{y2})?",
              P(metric_target="dnfs"), "one_shot", 3.0, category="reliability")
            E(f"How many points-scoring finishes (top 10) did {name} record for {cn} ({y1}-{y2})?",
              P(metric_target="points_finishes"), "race_week", 2.5, category="consistency")
            E(f"How many front-row starts did {name} qualify for {cn} ({y1}-{y2})?",
              P(metric_target="front_rows"), "one_shot", 3.0, category="qualifying")
            E(f"Net across every start, how many positions did {name} gain from the grid for {cn} ({y1}-{y2})?",
              P(metric_target="positions_gained"), "one_shot", 4.0, category="racecraft")
            E(f"What is the most race wins {name} scored in a single season for {cn}?",
              P(metric_target="wins", aggregation="best_season"), "one_shot", 3.5, category="single_season")
            E(f"In which season did {name} win the most races for {cn}?",
              P(metric_target="wins", aggregation="which_year"), "one_shot", 3.5,
              kind="year", category="milestone", dmin=y1, dmax=y2)
            E(f"In which year did {name} score their first win for {cn}?",
              P(metric_target="wins", aggregation="first_season"), "one_shot", 3.5,
              kind="year", category="milestone", dmin=y1, dmax=y2)
            E(f"Driving for {cn}, in what percentage of races did {name} finish on the podium?",
              P(metric_target="podiums", aggregation="percentage_of_races"), "one_shot", 4.0,
              kind="percentage", category="rates", dmin=0, dmax=100)
            E(f"How many of {name}'s pole positions for {cn} converted into a win?",
              P(metric_target="poles_converted"), "one_shot", 3.5, category="rates")

        # ---- Career-level questions (no constructor filter) ----
        C = lambda **kw: _p(did, start_year=lo, end_year=hi, **kw)
        E(f"How many career race wins does {name} have in our database?",
          C(metric_target="wins"), "daily", 2.5, category="career")
        E(f"How many career podiums does {name} have in our database?",
          C(metric_target="podiums"), "daily", 2.5, category="career")
        E(f"How many career pole positions does {name} have in our database?",
          C(metric_target="poles"), "daily", 3.0, category="qualifying")
        E(f"Roughly how many career points has {name} scored in our database?",
          C(metric_target="points"), "daily", 3.0, kind="points", category="career")
        E(f"How many different constructors has {name} driven for in our database?",
          C(metric_target="distinct_constructors"), "daily", 2.0, category="career")
        E(f"How many seasons has {name} contested in our database?",
          C(metric_target="seasons_active"), "daily", 2.0, category="career")
        E(f"In which season did {name} take the most wins of their career?",
          C(metric_target="wins", aggregation="which_year"), "one_shot", 3.5,
          kind="year", category="milestone", dmin=lo, dmax=hi)
        E(f"In which year did {name} score the very first win of their career?",
          C(metric_target="wins", aggregation="first_season"), "one_shot", 3.5,
          kind="year", category="milestone", dmin=lo, dmax=hi)
        E(f"Across their whole career, in what percentage of races did {name} finish on the podium?",
          C(metric_target="podiums", aggregation="percentage_of_races"), "one_shot", 4.0,
          kind="percentage", category="rates", dmin=0, dmax=100)

        # ---- Per-circuit wins (the driver's three best tracks) ----
        for r in conn.execute(
            "SELECT circuit_id, COUNT(*) AS w FROM staging_race_results "
            "WHERE driver_id = ? AND position = 1 GROUP BY circuit_id "
            "ORDER BY w DESC, circuit_id LIMIT 3", (did,)
        ).fetchall():
            track = _CIRCUIT_NAME.get(r["circuit_id"], r["circuit_id"])
            E(f"How many times did {name} win at {track}?",
              C(metric_target="wins", filter_circuit_id=r["circuit_id"]),
              "race_week", 3.0, category="circuit")

    # ---- Head-to-head differences (overlapping eras) ----
    for a, b in _HEAD_TO_HEAD:
        span = (min(_DRIVER_SPAN[a][0], _DRIVER_SPAN[b][0]),
                max(_DRIVER_SPAN[a][1], _DRIVER_SPAN[b][1]))
        for metric, label in _H2H_METRICS:
            va = compute_metric(conn, _p(a, start_year=span[0], end_year=span[1], metric_target=metric))
            vb = compute_metric(conn, _p(b, start_year=span[0], end_year=span[1], metric_target=metric))
            if va == vb:
                continue
            hi_id, lo_id = (a, b) if va > vb else (b, a)
            E(f"How many more career {label} does {_DRIVER_NAME[hi_id]} have than {_DRIVER_NAME[lo_id]}?",
              _p(hi_id, entity_id_b=lo_id, start_year=span[0], end_year=span[1],
                 metric_target=metric, aggregation="difference"),
              "one_shot", 3.5, category="head_to_head")

    return out


def mock_llm_questions(conn: sqlite3.Connection) -> list[dict]:
    """Full mock LLM batch: the generated pool + exactly one planted hallucination."""
    planted = {
        # PLANTED HALLUCINATION: staging says 72, the LLM "remembers" 80.
        # The validation layer must reject this and keep it out of production.
        "question_text": "How many race wins did Michael Schumacher take with Ferrari (1996-2006)?",
        "difficulty_weight": 2.0, "game_mode": "daily",
        "answer_kind": "count", "category": "career", "display_min": None, "display_max": None,
        "validation_parameters": {
            "target_entity": "driver", "entity_id": "schumacher",
            "filter_constructor_id": "ferrari",
            "start_year": 1996, "end_year": 2006, "metric_target": "wins",
        },
        "proposed_answer": 80,  # WRONG — true staging value is 72
    }
    return generate_questions(conn) + [planted]


def run_validation_pipeline(conn: sqlite3.Connection, today: str | None = None) -> dict:
    """Validate every mock question; commit the passing ones to production.

    Returns a summary {committed, rejected, rejections:[...]} for logging/CLI.
    """
    committed, rejections = 0, []
    for q in mock_llm_questions(conn):
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
            "(id, question_string, verified_answer, answer_kind, category, display_min, "
            " display_max, difficulty_weight, game_mode, is_active, scheduled_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (
                str(uuid.uuid4()),
                q["question_text"],
                float(result.expected),       # store the TRUSTED value, not the LLM's
                q.get("answer_kind", "count"),
                q.get("category", ""),
                q.get("display_min"),
                q.get("display_max"),
                q.get("difficulty_weight", 1.0),
                q.get("game_mode", "daily"),
                today,
            ),
        )
        committed += 1
    conn.commit()
    return {"committed": committed, "rejected": len(rejections), "rejections": rejections}


def seed_all(db_path=None) -> dict:
    """Full reset: rebuild schema, seed staging, run the validation pipeline."""
    conn = db.connect(db_path)  # None -> resolves db.DB_PATH at call time
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
