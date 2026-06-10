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
import json
import os
import random
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import db
from .validation import compute_metric, validate_ai_question

# Curated, validated question bank committed to the repo. The site can serve
# straight from this (F1_DATA_SOURCE=dataset) with no network or live ETL.
DATA_DIR = Path(__file__).resolve().parent / "data"
DATASET_PATH = DATA_DIR / "questions.json"
ARCADE_PATH = DATA_DIR / "arcade.json"


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

# Curated overlapping-era rivalries for head-to-head questions (synthetic ids).
_HEAD_TO_HEAD = [
    ("senna", "prost"), ("hamilton", "vettel"), ("hamilton", "alonso"),
    ("verstappen", "leclerc"), ("verstappen", "russell"), ("alonso", "raikkonen"),
    ("vettel", "webber"), ("rosberg", "hamilton"), ("button", "hamilton"),
    ("massa", "raikkonen"),
]
_H2H_METRICS = [("wins", "race wins"), ("podiums", "podiums")]

# Head-to-head questions are only worth asking when they're hard: the two tallies
# must be close (within ~20-30% of each other) and both substantial, so the answer
# can't be eyeballed from a lopsided matchup.
_H2H_MIN_GAP = 0.18   # >= ~18% apart -> a real, non-trivial difference to find
_H2H_MAX_GAP = 0.32   # <= ~32% apart -> the two totals are genuinely close
_H2H_MIN_VALUE = 8    # both drivers' tallies are meaningful (no "3 vs 4")

# Era windows used to scope team/venue questions so each lands in a real period
# (and gets a representative era_year for era-biased serving) rather than one
# mushy all-time figure. Boundaries mirror service.ERA_WEIGHT_BANDS.
_ERA_WINDOWS = [
    (1980, 1993, "1980-1993"),
    (1994, 2006, "1994-2006"),
    (2007, 2013, "2007-2013"),
    (2014, 2025, "2014-2025"),
]


def _p(entity: str, **kw) -> dict:
    """Build a validation_parameters payload."""
    return {"target_entity": "driver", "entity_id": entity, **kw}


# F1 dropped a driver's worst results from the championship through 1990 (various
# "best N of M" schemes). Ergast/Jolpica reports the points scored in each race,
# so summing race points OVERCOUNTS the official championship total for any window
# that touches 1950-1990 (e.g. Prost's raw 798.5 vs official 768.5). From 1991 on,
# every result counts, so the race-points sum equals the official standings total
# exactly (verified). We therefore only ask points questions scoped to 1991+.
POINTS_ALL_COUNT_YEAR = 1991


def _emit(conn, out, seen, text, params, mode, weight,
          kind="count", category="", dmin=None, dmax=None) -> None:
    """Compute the true answer for a question and stage it as an LLM output.
    Skips empty/trivial answers so the pool stays interesting."""
    if text in seen:
        return
    # Points only count cleanly in the all-results-count era; skip any points
    # question whose scope reaches back into the dropped-scores years.
    if kind == "points" and (params.get("start_year") or 0) < POINTS_ALL_COUNT_YEAR:
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


def load_entities_from_staging(conn: sqlite3.Connection) -> list[Driver]:
    """Reconstruct the Driver/Stint structure from whatever is in the staging
    tables (the real Jolpica ETL extract). One stint per driver+constructor,
    spanning that pairing's first..last season. Only the constructor id and year
    range are needed for question generation — the answers themselves are always
    recomputed from staging by the validation engine.
    """
    drivers: list[Driver] = []
    for d in conn.execute(
        "SELECT driver_id, full_name, nationality, active_from, active_to "
        "FROM staging_drivers ORDER BY driver_id"
    ).fetchall():
        stints = [
            Stint(d["driver_id"], s["constructor_id"], s["lo"], s["hi"])
            for s in conn.execute(
                "SELECT constructor_id, MIN(year) AS lo, MAX(year) AS hi "
                "FROM staging_race_results WHERE driver_id = ? AND constructor_id IS NOT NULL "
                "GROUP BY constructor_id ORDER BY lo, constructor_id",
                (d["driver_id"],),
            ).fetchall()
        ]
        if not stints:
            continue  # driver has no races in the ingested range — skip
        af = d["active_from"] if d["active_from"] is not None else min(s.start_year for s in stints)
        at = d["active_to"] if d["active_to"] is not None else max(s.end_year for s in stints)
        drivers.append(Driver(d["driver_id"], d["full_name"], d["nationality"] or "", af, at, stints))
    return drivers


def generate_questions(conn: sqlite3.Connection, drivers: list[Driver] | None = None) -> list[dict]:
    """Build the full validated-question pool by querying the staging data across
    a wide variety of metrics and aggregations (PRD §4).

    `drivers` defaults to the synthetic in-code set; pass the result of
    `load_entities_from_staging` to generate from real ETL'd data instead.
    Name lookups (constructors, circuits) come from the staging tables so the
    same code path works for both synthetic and real data.
    """
    drivers = drivers if drivers is not None else DRIVERS
    name_by_id = {d.driver_id: d.full_name for d in drivers}
    span_by_id = {
        d.driver_id: (min(s.start_year for s in d.stints), max(s.end_year for s in d.stints))
        for d in drivers if d.stints
    }
    cname = {r["constructor_id"]: r["name"]
             for r in conn.execute("SELECT constructor_id, name FROM staging_constructors")}
    circname = {r["circuit_id"]: r["name"]
                for r in conn.execute("SELECT circuit_id, name FROM staging_circuits")}

    out: list[dict] = []
    seen: set[str] = set()

    def E(*a, **k):
        _emit(conn, out, seen, *a, **k)

    for d in drivers:
        name, did = d.full_name, d.driver_id
        if did not in span_by_id:
            continue
        lo, hi = span_by_id[did]

        # ---- Per-stint questions ----
        for s in d.stints:
            c, y1, y2 = s.constructor_id, s.start_year, s.end_year
            cn = cname.get(c, c)
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
            E(f"How many championship points did {name} score for {cn} ({y1}-{y2})?",
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
            E(f"How many runner-up (P2) finishes did {name} score for {cn} ({y1}-{y2})?",
              P(metric_target="second_places"), "race_week", 2.5, category="career")
            E(f"Driving for {cn} ({y1}-{y2}), how many times did {name} climb 10+ places from the grid?",
              P(metric_target="big_comebacks"), "one_shot", 4.0, category="racecraft")

        # ---- Career-level questions (no constructor filter) ----
        C = lambda **kw: _p(did, start_year=lo, end_year=hi, **kw)
        E(f"How many career race wins does {name} have?",
          C(metric_target="wins"), "daily", 2.5, category="career")
        E(f"How many career podiums does {name} have?",
          C(metric_target="podiums"), "daily", 2.5, category="career")
        E(f"How many career pole positions does {name} have?",
          C(metric_target="poles"), "daily", 3.0, category="qualifying")
        E(f"How many career points has {name} scored?",
          C(metric_target="points"), "daily", 3.0, kind="points", category="career")
        E(f"How many different constructors has {name} driven for?",
          C(metric_target="distinct_constructors"), "daily", 2.0, category="career")
        E(f"How many seasons has {name} contested?",
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
        E(f"How many career second-place (P2) finishes does {name} have?",
          C(metric_target="second_places"), "race_week", 2.5, category="career")
        E(f"How many career third-place (P3) finishes does {name} have?",
          C(metric_target="third_places"), "race_week", 2.5, category="career")
        E(f"At how many different circuits has {name} won a race?",
          C(metric_target="distinct_circuits_won"), "daily", 3.0, category="circuit")
        E(f"In how many separate seasons has {name} won at least one race?",
          C(metric_target="winning_seasons"), "one_shot", 3.0, category="milestone")
        E(f"How many times in their career did {name} climb 10+ places from the grid in a race?",
          C(metric_target="big_comebacks"), "one_shot", 4.0, category="racecraft")
        E(f"What is the most places {name} ever made up from grid to finish in a single race?",
          C(metric_target="best_comeback"), "one_shot", 4.0, category="racecraft")
        E(f"Across classified finishes, what is {name}'s average finishing position?",
          C(metric_target="avg_finish"), "one_shot", 4.0, category="consistency", dmin=1, dmax=20)
        E(f"On average, how many championship points has {name} scored per season?",
          C(metric_target="points", aggregation="per_season_avg"), "one_shot", 4.0,
          kind="points", category="career")
        E(f"At their single most successful circuit, how many times has {name} won there?",
          C(metric_target="wins", aggregation="best_circuit"), "one_shot", 3.5, category="circuit")

        # ---- Per-circuit wins (the driver's three best tracks) ----
        for r in conn.execute(
            "SELECT circuit_id, COUNT(*) AS w FROM staging_race_results "
            "WHERE driver_id = ? AND position = 1 GROUP BY circuit_id "
            "ORDER BY w DESC, circuit_id LIMIT 3", (did,)
        ).fetchall():
            track = circname.get(r["circuit_id"], r["circuit_id"])
            E(f"How many times did {name} win at {track}?",
              C(metric_target="wins", filter_circuit_id=r["circuit_id"]),
              "race_week", 3.0, category="circuit")

    # ---- Constructor (team) questions, scoped per era window ----
    # Real teams only (>= 5 career wins) so the pool stays interesting.
    for tr in conn.execute(
        "SELECT constructor_id, SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS w "
        "FROM staging_race_results GROUP BY constructor_id HAVING w >= 5 ORDER BY w DESC"
    ).fetchall():
        cid = tr["constructor_id"]
        tn = cname.get(cid, cid)
        for w1, w2, wlabel in _ERA_WINDOWS:
            T = lambda **kw: {"target_entity": "constructor", "entity_id": cid,
                              "start_year": w1, "end_year": w2, **kw}
            E(f"How many race wins did {tn} score in {wlabel}?",
              T(metric_target="wins"), "daily", 2.5, category="team")
            E(f"How many podium finishes did {tn} score in {wlabel}?",
              T(metric_target="podiums"), "race_week", 3.0, category="team")
            E(f"How many championship points did {tn} score in {wlabel}?",
              T(metric_target="points"), "race_week", 3.5, kind="points", category="team")
            E(f"How many pole positions did {tn} take in {wlabel}?",
              T(metric_target="poles"), "one_shot", 3.5, category="team")
            E(f"How many 1-2 finishes (both cars on the top two steps) did {tn} score in {wlabel}?",
              T(metric_target="one_two_finishes"), "one_shot", 4.0, category="team")
        span = conn.execute(
            "SELECT MIN(year) AS lo, MAX(year) AS hi FROM staging_race_results WHERE constructor_id = ?",
            (cid,),
        ).fetchone()
        clo, chi = span["lo"], span["hi"]
        TC = lambda **kw: {"target_entity": "constructor", "entity_id": cid,
                           "start_year": clo, "end_year": chi, **kw}
        E(f"What is the most race wins {tn} scored in a single season?",
          TC(metric_target="wins", aggregation="best_season"), "one_shot", 3.5, category="team")
        E(f"In which season did {tn} take the most race wins?",
          TC(metric_target="wins", aggregation="which_year"), "one_shot", 4.0,
          kind="year", category="team", dmin=clo, dmax=chi)

    # ---- Circuit (venue) questions ----
    for cr in conn.execute(
        "SELECT circuit_id, COUNT(DISTINCT year || '-' || round) AS n "
        "FROM staging_race_results GROUP BY circuit_id HAVING n >= 5 ORDER BY n DESC"
    ).fetchall():
        ccid = cr["circuit_id"]
        vn = circname.get(ccid, ccid)
        span = conn.execute(
            "SELECT MIN(year) AS lo, MAX(year) AS hi FROM staging_race_results WHERE circuit_id = ?",
            (ccid,),
        ).fetchone()
        vlo, vhi = span["lo"], span["hi"]
        V = lambda **kw: {"target_entity": "circuit", "entity_id": ccid,
                          "start_year": vlo, "end_year": vhi, **kw}
        E(f"How many different drivers have won a Grand Prix at {vn}?",
          V(metric_target="distinct_winners"), "one_shot", 3.5, category="venue")
        E(f"How many championship races has {vn} hosted?",
          V(metric_target="races_held"), "race_week", 2.5, category="venue")

    # ---- Head-to-head differences (overlapping eras) ----
    # Curated rivalries when their drivers are present (synthetic seed); otherwise
    # derive pairs from the top winners in the data (real ETL uses different ids).
    pairs = [(a, b) for a, b in _HEAD_TO_HEAD if a in span_by_id and b in span_by_id]
    # Always enrich with derived top-winner pairs: the closeness filter below
    # drops most lopsided matchups, so we need a wide candidate pool to keep a
    # decent spread of hard head-to-head questions.
    pairs += _derive_h2h_pairs(conn, span_by_id, exclude=set(pairs), limit=16)
    for a, b in pairs:
        span = (min(span_by_id[a][0], span_by_id[b][0]),
                max(span_by_id[a][1], span_by_id[b][1]))
        for metric, label in _H2H_METRICS:
            va = compute_metric(conn, _p(a, start_year=span[0], end_year=span[1], metric_target=metric))
            vb = compute_metric(conn, _p(b, start_year=span[0], end_year=span[1], metric_target=metric))
            lo_val, hi_val = sorted((float(va), float(vb)))
            # Keep only the hard ones: both substantial and the totals close.
            if lo_val < _H2H_MIN_VALUE:
                continue
            gap = (hi_val - lo_val) / hi_val
            if not (_H2H_MIN_GAP <= gap <= _H2H_MAX_GAP):
                continue
            hi_id, lo_id = (a, b) if va > vb else (b, a)
            E(f"How many more career {label} does {name_by_id[hi_id]} have than {name_by_id[lo_id]}?",
              _p(hi_id, entity_id_b=lo_id, start_year=span[0], end_year=span[1],
                 metric_target=metric, aggregation="difference"),
              "one_shot", 3.5, category="head_to_head")

    return out


def _derive_h2h_pairs(conn: sqlite3.Connection, span_by_id: dict,
                      exclude: set, limit: int = 8) -> list[tuple[str, str]]:
    """Pick head-to-head pairs from the top race winners whose careers overlap.
    Used when the curated rivalries aren't in the data (real ETL ids differ)."""
    top = [
        r["driver_id"]
        for r in conn.execute(
            "SELECT driver_id, SUM(CASE WHEN position = 1 THEN 1 ELSE 0 END) AS wins "
            "FROM staging_race_results GROUP BY driver_id "
            "HAVING wins > 0 ORDER BY wins DESC, driver_id LIMIT ?",
            (limit,),
        ).fetchall()
        if r["driver_id"] in span_by_id
    ]
    pairs = []
    for i, a in enumerate(top):
        for b in top[i + 1:]:
            if (a, b) in exclude or (b, a) in exclude:
                continue
            # overlapping eras only
            if span_by_id[a][0] <= span_by_id[b][1] and span_by_id[b][0] <= span_by_id[a][1]:
                pairs.append((a, b))
    return pairs


# PLANTED HALLUCINATION (synthetic demo): staging says 72, the LLM "remembers"
# 80. The validation layer must reject this and keep it out of production.
_PLANTED_HALLUCINATION = {
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


def mock_llm_questions(
    conn: sqlite3.Connection, drivers: list[Driver] | None = None, planted: bool = True
) -> list[dict]:
    """Full mock LLM batch: the generated pool, plus (for the synthetic demo) one
    planted hallucination so the rejection path is exercised. Real ETL runs pass
    `planted=False` — the demo question is meaningless against real driver ids."""
    questions = generate_questions(conn, drivers)
    if planted:
        questions = questions + [_PLANTED_HALLUCINATION]
    return questions


def _era_year(params: dict) -> int | None:
    """Representative year for a question (mid-point of its season span), used by
    the service layer to bias quiz selection toward the modern era. Returns None
    when the params carry no year range (selection then uses a neutral weight)."""
    lo, hi = params.get("start_year"), params.get("end_year")
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    return round((lo + hi) / 2)


def run_validation_pipeline(
    conn: sqlite3.Connection, today: str | None = None,
    drivers: list[Driver] | None = None, planted: bool = True,
) -> dict:
    """Validate every mock question; commit the passing ones to production.

    Returns a summary {committed, rejected, rejections:[...]} for logging/CLI.
    """
    committed, rejections = 0, []
    for q in mock_llm_questions(conn, drivers, planted):
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
            " display_max, difficulty_weight, game_mode, era_year, is_active, scheduled_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
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
                _era_year(q.get("validation_parameters", {})),
                today,
            ),
        )
        committed += 1
    conn.commit()
    return {"committed": committed, "rejected": len(rejections), "rejections": rejections}


# --- Committed question bank: export (build-time) + load (serve-time) ----------
# Metrics surfaced in the arcade Over/Under (snapshot so it works with no staging).
_ARCADE_STAT_KEYS = ["wins", "podiums", "poles", "fastest_laps",
                     "points_finishes", "front_rows", "dnfs"]

_DATASET_COLS = ("question_string", "verified_answer", "answer_kind", "category",
                 "display_min", "display_max", "difficulty_weight", "game_mode", "era_year")


def _era_weighted_sample(rng: random.Random, rows: list, k: int) -> list:
    """Era-biased sample without replacement (Efraimidis-Spirakis A-Res), using
    the same era weights the live service applies."""
    from .service import _era_weight  # lazy import to avoid a load-time cycle
    if k >= len(rows):
        return list(rows)
    keyed = []
    for r in rows:
        w = _era_weight(r["era_year"])
        keyed.append((rng.random() ** (1.0 / w) if w > 0 else 0.0, r))
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [r for _key, r in keyed[:k]]


# Variety categories kept in full so the bank isn't all driver questions.
_FLAVOR_CATEGORIES = {"team", "venue", "head_to_head"}


def _mode_balanced(rng: random.Random, rows: list, n: int) -> list:
    """Era-weighted sample of n rows that preserves the per-mode mix (so every
    game mode keeps a deep enough pool to provision and rotate)."""
    from collections import defaultdict
    by_mode: dict[str, list] = defaultdict(list)
    for r in rows:
        by_mode[r["game_mode"]].append(r)
    total = len(rows) or 1
    raw = {m: n * len(g) / total for m, g in by_mode.items()}
    alloc = {m: int(v) for m, v in raw.items()}
    for m in sorted(raw, key=lambda m: raw[m] - alloc[m], reverse=True)[: n - sum(alloc.values())]:
        alloc[m] += 1
    out: list = []
    for m, g in by_mode.items():
        out += _era_weighted_sample(rng, g, min(alloc[m], len(g)))
    return out


def _sample_dataset(rows: list, n: int, seed: int = 42) -> list:
    """Pick ~n questions: keep the variety (team/venue/head-to-head) categories in
    full, then fill the rest era-weighted while preserving the per-mode mix."""
    rng = random.Random(seed)
    flavor = [r for r in rows if r["category"] in _FLAVOR_CATEGORIES]
    rest = [r for r in rows if r["category"] not in _FLAVOR_CATEGORIES]
    chosen = _era_weighted_sample(rng, flavor, min(len(flavor), n // 4))
    chosen += _mode_balanced(rng, rest, max(0, n - len(chosen)))
    return chosen


def export_dataset(conn: sqlite3.Connection, n: int = 1000, out_path=None) -> dict:
    """Regenerate the full validated pool from the staging already in `conn`, then
    write an era-weighted, mode-balanced sample of ~n questions to JSON."""
    drivers = load_entities_from_staging(conn)
    conn.execute("DELETE FROM production_trivia_questions")
    run_validation_pipeline(conn, drivers=drivers, planted=False)
    rows = conn.execute(
        f"SELECT {', '.join(_DATASET_COLS)} FROM production_trivia_questions"
    ).fetchall()
    data = [dict(r) for r in _sample_dataset(rows, n)]
    out = Path(out_path or DATASET_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    return {"written": len(data), "pool": len(rows), "path": str(out)}


def export_arcade(conn: sqlite3.Connection, out_path=None, min_starts: int = 40) -> dict:
    """Snapshot per-driver career totals for the arcade metrics so Over/Under
    runs from the committed bank with no staging tables present."""
    rows = conn.execute(
        "SELECT r.driver_id AS did, d.full_name AS nm, MIN(r.year) AS af, MAX(r.year) AS at, "
        "COUNT(*) AS starts FROM staging_race_results r "
        "JOIN staging_drivers d ON d.driver_id = r.driver_id "
        "GROUP BY r.driver_id HAVING starts >= ? ORDER BY starts DESC", (min_starts,),
    ).fetchall()
    drivers = []
    for r in rows:
        base = {"target_entity": "driver", "entity_id": r["did"],
                "start_year": r["af"], "end_year": r["at"]}
        stats = {k: int(compute_metric(conn, {**base, "metric_target": k})) for k in _ARCADE_STAT_KEYS}
        drivers.append({"driver_id": r["did"], "full_name": r["nm"],
                        "active_from": r["af"], "active_to": r["at"], "stats": stats})
    out = Path(out_path or ARCADE_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"drivers": drivers}, indent=1, ensure_ascii=False))
    return {"drivers": len(drivers), "path": str(out)}


def load_dataset(conn: sqlite3.Connection, path=None) -> dict:
    """Serve from the committed question bank: rebuild the schema and load the
    questions. No network, no live ETL — instant boot."""
    data = json.loads(Path(path or DATASET_PATH).read_text())
    db.reset_db(conn)
    for q in data:
        conn.execute(
            "INSERT OR IGNORE INTO production_trivia_questions "
            "(id, question_string, verified_answer, answer_kind, category, display_min, "
            " display_max, difficulty_weight, game_mode, era_year, is_active, scheduled_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)",
            (str(uuid.uuid4()), q["question_string"], float(q["verified_answer"]),
             q.get("answer_kind", "count"), q.get("category", ""), q.get("display_min"),
             q.get("display_max"), q.get("difficulty_weight", 1.0), q["game_mode"], q.get("era_year")),
        )
    conn.commit()
    return {"committed": len(data), "rejected": 0, "rejections": []}


def seed_all(db_path=None) -> dict:
    """Full reset: rebuild schema, seed SYNTHETIC staging, run the validation
    pipeline. This is the offline fallback (no network); see `refresh` for the
    real Jolpica ETL path."""
    conn = db.connect(db_path)  # None -> resolves db.DB_PATH at call time
    try:
        db.reset_db(conn)
        seed_staging(conn)
        summary = run_validation_pipeline(conn)
    finally:
        conn.close()
    return summary


# Sources that select the real, cached, weekly Jolpica ETL.
_REAL_SOURCES = {"jolpica", "jolpi", "ergast", "api"}
# Sources that serve the curated, committed question bank (no network).
_DATASET_SOURCES = {"dataset", "bank", "file"}


def refresh(db_path=None, source: str | None = None, force: bool = False) -> dict:
    """Build/refresh the playable database.

    `source` (or env `F1_DATA_SOURCE`) selects the data origin:
      * "dataset" — load the curated, validated question bank committed in the
        repo (backend/app/data/questions.json). No network, instant boot.
      * "synthetic" — the offline in-code seed; resets and rebuilds.
      * "jolpica" — the real, rate-limited, disk-cached Jolpica ETL into staging,
        gated to the weekly cadence, then regenerate + validate production from
        the real data. Falls back to the synthetic seed if the network is
        unreachable and nothing is cached yet.

    Returns a status dict (always includes "source").
    """
    source = (source or os.environ.get("F1_DATA_SOURCE", "synthetic")).lower()
    if source in _DATASET_SOURCES:
        conn = db.connect(db_path)
        try:
            summary = load_dataset(conn)
        finally:
            conn.close()
        return {"source": "dataset", **summary}
    if source not in _REAL_SOURCES:
        return {"source": "synthetic", **seed_all(db_path)}

    from . import etl  # local import: synthetic path never needs httpx

    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        try:
            etl_status = etl.refresh_if_stale(conn, force=force)
        except etl.NetworkError as exc:
            if not etl._staging_has_rows(conn):
                # No live data and nothing cached: fall back so the app still runs.
                conn.close()
                return {"source": "synthetic", "fallback": str(exc), **seed_all(db_path)}
            etl_status = {"status": "error", "skipped": True, "error": str(exc),
                          "note": "served from previously-cached staging"}

        # Regenerate production only when staging actually changed (or it's empty),
        # so a fresh weekly no-op stays cheap.
        prod_count = conn.execute(
            "SELECT COUNT(*) FROM production_trivia_questions"
        ).fetchone()[0]
        if not etl_status.get("skipped") or prod_count == 0:
            drivers = load_entities_from_staging(conn)
            conn.execute("DELETE FROM production_trivia_questions")
            summary = run_validation_pipeline(conn, drivers=drivers, planted=False)
        else:
            summary = {"committed": prod_count, "rejected": 0, "rejections": []}
        conn.commit()
    finally:
        conn.close()
    return {"source": "jolpica", "etl": etl_status, **summary}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Seed / refresh the F1 StatGuesser database.")
    ap.add_argument("--source", default=None,
                    help="synthetic (default) or jolpica (real cached weekly ETL). "
                         "Falls back to env F1_DATA_SOURCE.")
    ap.add_argument("--force", action="store_true",
                    help="ignore the weekly freshness gate (jolpica only)")
    ap.add_argument("--export", action="store_true",
                    help="rebuild the committed question bank (questions.json + arcade.json) "
                         "from the chosen source's staging, then exit")
    ap.add_argument("--count", type=int, default=1000,
                    help="number of questions to write to the bank with --export")
    args = ap.parse_args()

    if args.export:
        # Make sure staging is populated for the chosen source, then snapshot.
        refresh(source=args.source or "jolpica", force=True)
        conn = db.connect()
        try:
            d = export_dataset(conn, n=args.count)
            a = export_arcade(conn)
        finally:
            conn.close()
        print(f"Wrote {d['written']} questions (from a pool of {d['pool']}) -> {d['path']}")
        print(f"Wrote {a['drivers']} driver stat lines -> {a['path']}")
        raise SystemExit(0)

    summary = refresh(source=args.source, force=args.force)
    print(f"[{summary['source']}] Committed {summary.get('committed', 0)} questions, "
          f"rejected {summary.get('rejected', 0)}.")
    if "etl" in summary:
        print("  ETL:", summary["etl"])
    for r in summary.get("rejections", []):
        print(f"  REJECTED [{r['metric']}] {r['question']!r}: "
              f"expected {r['expected']}, LLM proposed {r['proposed']} -- {r['reason']}")
