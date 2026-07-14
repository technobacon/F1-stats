"""Offline seed + question-generation pipeline.

The data flow mirrors a production ETL: committed entity datasets (built from
the OSRS Wiki and its real-time prices API by scripts/build_datasets.py) ->
staging tables -> question generator -> deterministic validation ->
production table. Everything runs fully offline at serve time — the committed
datasets ship in the repo, so gameplay never fetches from external APIs.

  1. Load the committed entity datasets into staging.
  2. The generator emits question objects in a strict schema, INCLUDING one
     deliberately hallucinated answer (a demo of the rejection path).
  3. Every question runs through validation.validate_ai_question; only the
     ones whose proposed answer matches the independently-computed staging
     value (or XP-formula value) are committed to production_trivia_questions.

The validation layer's guarantee is that a question's answer matches the
staging data — keeping staging faithful to the game is build_datasets.py's
responsibility (see docs/DATA_SOURCES.md for provenance and CC BY-SA
attribution).
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .validation import compute_metric, validate_ai_question, xp_for_level

# Curated, validated question bank committed to the repo. The site can serve
# straight from this (OSRS_DATA_SOURCE=dataset) with no network or live ETL.
DATA_DIR = Path(__file__).resolve().parent / "data"
DATASET_PATH = DATA_DIR / "questions.json"
ARCADE_PATH = DATA_DIR / "arcade.json"
# Provenance for the committed bank: when it was built, surfaced on the home
# page (see main.data_status). Written by --export.
DATASET_META_PATH = DATA_DIR / "dataset_meta.json"

# Committed entity datasets (the trusted staging source).
ITEMS_PATH = DATA_DIR / "items.json"
MONSTERS_PATH = DATA_DIR / "monsters.json"
QUESTS_PATH = DATA_DIR / "quests.json"
SKILLS_PATH = DATA_DIR / "skills.json"


def seed_staging(conn: sqlite3.Connection) -> None:
    """Populate the staging tables from the committed entity datasets."""
    items = json.loads(ITEMS_PATH.read_text())
    monsters = json.loads(MONSTERS_PATH.read_text())
    quests = json.loads(QUESTS_PATH.read_text())
    skills = json.loads(SKILLS_PATH.read_text())
    conn.executemany(
        "INSERT INTO staging_items (item_id, name, members, buy_limit, value, low_alch, "
        "high_alch, ge_price, ge_volume, release_year, fame_tier) "
        "VALUES (:item_id, :name, :members, :buy_limit, :value, :low_alch, :high_alch, "
        ":ge_price, :ge_volume, :release_year, :fame_tier)",
        items,
    )
    conn.executemany(
        "INSERT INTO staging_monsters (monster_id, name, combat_level, hitpoints, max_hit, "
        "slayer_level, slayer_xp, release_year, is_boss) "
        "VALUES (:monster_id, :name, :combat_level, :hitpoints, :max_hit, :slayer_level, "
        ":slayer_xp, :release_year, :is_boss)",
        monsters,
    )
    conn.executemany(
        "INSERT INTO staging_quests (quest_id, name, difficulty, quest_points, members, "
        "release_year, series) "
        "VALUES (:quest_id, :name, :difficulty, :quest_points, :members, :release_year, :series)",
        quests,
    )
    conn.executemany(
        "INSERT INTO staging_skills (skill_id, name, members, release_year) "
        "VALUES (:skill_id, :name, :members, :release_year)",
        skills,
    )
    conn.commit()


# ── Emit gates ───────────────────────────────────────────────────────────────
# Percentage-error scoring is brutal at tiny scales and undefined at zero, so
# coin questions need a floor; GE-price questions additionally need liquidity
# (a thin market makes the weekly snapshot meaningless) and fame (nobody can
# price an item they've never heard of).
MIN_COIN_ANSWER = 50          # no coin question whose answer is below this
MIN_GE_PRICE = 500            # no GE-price question for items cheaper than this
MIN_GE_VOLUME = 100           # ... or thinner than this over 24h
GE_PRICE_MAX_FAME = 2         # ... or more obscure than this
YEAR_MIN, YEAR_MAX = 1999, datetime.now(timezone.utc).year


def _emit(conn, out, seen, text, params, weight,
          kind="count", category="", dmin=None, dmax=None, era=None) -> None:
    """Compute the true answer for a question and stage it as generator output.
    Skips empty/trivial answers so the pool stays interesting."""
    if text in seen:
        return
    try:
        ans = compute_metric(conn, params)
    except Exception:  # noqa: BLE001 — missing fact / malformed combo, just skip
        return
    if kind in ("count", "level", "xp", "percentage", "year") and ans <= 0:
        return
    if kind == "coins" and ans < MIN_COIN_ANSWER:
        return
    pv = int(ans) if ans == ans.to_integral_value() else float(ans)
    seen.add(text)
    out.append({
        "question_text": text, "difficulty_weight": weight, "game_mode": "daily",
        "answer_kind": kind, "category": category, "display_min": dmin, "display_max": dmax,
        "validation_parameters": params, "proposed_answer": pv, "era_year": era,
    })


# Bosses whose names are descriptions rather than proper names read with "the"
# ("the Corporeal Beast" but plain "Zulrah"). Editorial, never numeric.
_THE_BOSSES = {
    "Corporeal Beast", "King Black Dragon", "Kalphite Queen", "Giant Mole",
    "Abyssal Sire", "Kraken", "Thermonuclear smoke devil", "Alchemical Hydra",
    "Chaos Elemental", "Grotesque Guardians", "Phantom Muspah",
}


def _monster_article(name: str, boss: bool) -> str:
    """Article for mid-sentence mentions: proper-named bosses take none,
    descriptive bosses take 'the', species take 'a'/'an'."""
    if name.startswith("The "):
        return ""
    if boss:
        return "the " if name in _THE_BOSSES else ""
    return "an " if name[0].upper() in "AEIOU" else "a "


# Famous XP milestones players actually talk about, plus a spread of the table.
_XP_LEVELS = [30, 40, 43, 50, 55, 60, 70, 75, 80, 85, 90, 92, 94, 95, 96, 97, 98, 99]
_XP_SPANS = [
    (1, 50), (1, 99), (50, 99), (60, 99), (70, 99), (80, 99), (85, 99),
    (90, 99), (92, 99), (95, 99), (98, 99), (90, 92), (80, 90), (70, 80),
    (60, 70), (75, 92), (50, 92),
]
_XP_SINGLE_STEPS = [49, 59, 69, 74, 79, 84, 89, 91, 92, 94, 95, 96, 97, 98]


def generate_questions(conn: sqlite3.Connection) -> list[dict]:
    """Build the full validated-question pool by querying the staging data
    across the four domains (item / monster / quest / skill)."""
    out: list[dict] = []
    seen: set[str] = set()

    def E(text, params, *a, **k):
        _emit(conn, out, seen, text, params, *a, **k)

    # ---- Skill XP (the pure-formula anchor: answers are exact arithmetic) ----
    SK = lambda **kw: {"domain": "skill", **kw}
    for lvl in _XP_LEVELS:
        E(f"How much experience does it take to reach level {lvl} in a skill?",
          SK(metric_target="xp_for_level", level=lvl), 2.5 if lvl in (92, 99) else 3.5,
          kind="xp", category="skill", era=2001)
    for a, b in _XP_SPANS:
        E(f"How much experience separates level {a} from level {b}?",
          SK(metric_target="xp_between", level_a=a, level_b=b), 3.5,
          kind="xp", category="skill", era=2001)
    for a in _XP_SINGLE_STEPS:
        E(f"How much experience do you need to advance from level {a} to level {a + 1}?",
          SK(metric_target="xp_between", level_a=a, level_b=a + 1), 4.0,
          kind="xp", category="skill", era=2001)
    # NOTE: deliberately NO per-skill copies of the level-99 / level-92 facts.
    # The XP table is skill-independent, so "a level 99 in Thieving" and
    # "a level 99 in Fishing" are the same question with the same 13,034,431 —
    # a per-skill loop once filled 36 bank slots with two literal answers a
    # player could memorise for free 5,000s. The generic _XP_LEVELS questions
    # above already cover both milestones once each.

    # ---- Items ----
    items = conn.execute(
        "SELECT item_id, name, members, buy_limit, value, high_alch, ge_price, "
        "ge_volume, release_year, fame_tier FROM staging_items ORDER BY item_id"
    ).fetchall()
    for it in items:
        iid, nm, fame, year = it["item_id"], it["name"], it["fame_tier"], it["release_year"]
        I = lambda **kw: {"domain": "item", "entity_id": iid, **kw}
        E(f"How many coins does the High Alchemy spell yield for one {nm}?",
          I(metric_target="high_alch"), 3.0 + 0.5 * fame,
          kind="coins", category="item", era=year)
        E(f"What is the Grand Exchange buy limit for {nm} (per 4 hours)?",
          I(metric_target="buy_limit"), 3.0 + 0.5 * fame,
          category="item", era=year)
        if (it["ge_price"] or 0) >= MIN_GE_PRICE and (it["ge_volume"] or 0) >= MIN_GE_VOLUME \
                and fame <= GE_PRICE_MAX_FAME:
            E(f"As of the latest weekly snapshot, what does one {nm} trade for on the Grand Exchange?",
              I(metric_target="ge_price"), 2.5 + 0.5 * fame,
              kind="coins", category="item", era=year)
        if fame <= 2:
            E(f"In which year was the {nm} first released?",
              I(metric_target="release_year"), 3.0 + 0.5 * fame,
              kind="year", category="item", dmin=YEAR_MIN, dmax=YEAR_MAX, era=year)

    # ---- Monsters ----
    monsters = conn.execute(
        "SELECT monster_id, name, combat_level, hitpoints, max_hit, slayer_level, "
        "slayer_xp, release_year, is_boss FROM staging_monsters ORDER BY monster_id"
    ).fetchall()
    for mo in monsters:
        mid, nm, year = mo["monster_id"], mo["name"], mo["release_year"]
        boss = bool(mo["is_boss"])
        M = lambda **kw: {"domain": "monster", "entity_id": mid, **kw}
        the = _monster_article(nm, boss)
        E(f"What is the combat level of {the}{nm}?",
          M(metric_target="combat_level"), 2.5 if boss else 3.0,
          kind="level", category="monster", era=year)
        E(f"How many hitpoints does {the}{nm} have?",
          M(metric_target="hitpoints"), 3.0,
          category="monster", era=year)
        if mo["max_hit"] is not None:
            E(f"What is the max hit of {the}{nm} (standard attacks)?",
              M(metric_target="max_hit"), 4.0,
              category="monster", era=year)
        if (mo["slayer_level"] or 1) > 1:
            E(f"What Slayer level is required to harm {the}{nm}?",
              M(metric_target="slayer_level"), 3.0,
              kind="level", category="monster", dmin=1, dmax=99, era=year)
        if mo["slayer_xp"]:
            E(f"How much Slayer experience is one {nm} kill worth?",
              M(metric_target="slayer_xp"), 3.5,
              kind="xp", category="monster", era=year)
        if boss:
            E(f"In which year was {the}{nm} released?",
              M(metric_target="release_year"), 3.0,
              kind="year", category="monster", dmin=YEAR_MIN, dmax=YEAR_MAX, era=year)

    # Head-to-head: how many combat levels separate two close bosses?
    bosses = [m for m in monsters if m["is_boss"] and m["combat_level"]]
    for i, a in enumerate(bosses):
        for b in bosses[i + 1:]:
            hi, lo = (a, b) if a["combat_level"] >= b["combat_level"] else (b, a)
            gap = hi["combat_level"] - lo["combat_level"]
            rel = gap / hi["combat_level"]
            if gap == 0 or not (0.05 <= rel <= 0.35):
                continue
            era = max(filter(None, (a["release_year"], b["release_year"])), default=None)
            hi_ref = _monster_article(hi["name"], True) + hi["name"]
            lo_ref = _monster_article(lo["name"], True) + lo["name"]
            E(f"How many combat levels does {hi_ref} have over {lo_ref}?",
              {"domain": "monster", "entity_id": hi["monster_id"],
               "entity_id_b": lo["monster_id"], "metric_target": "combat_level",
               "aggregation": "difference"},
              4.0, category="monster", era=era)

    # ---- Quests (the dataset is COMPLETE, so aggregates are honest) ----
    quests = conn.execute(
        "SELECT quest_id, name, difficulty, quest_points, members, release_year, series "
        "FROM staging_quests ORDER BY quest_id"
    ).fetchall()
    for qu in quests:
        qid, nm, year = qu["quest_id"], qu["name"], qu["release_year"]
        Q = lambda **kw: {"domain": "quest", "entity_id": qid, **kw}
        E(f"How many Quest Points does {nm} award?",
          Q(metric_target="quest_points"), 3.5,
          category="quest", dmin=1, dmax=10, era=year)
        E(f"In which year was the quest {nm} released?",
          Q(metric_target="release_year"), 3.5,
          kind="year", category="quest", dmin=YEAR_MIN, dmax=YEAR_MAX, era=year)

    QA = lambda **kw: {"domain": "quest", **kw}
    E("How many quests are there in Old School RuneScape?",
      QA(aggregation="count_where"), 2.0, category="quest")
    E("What is the total number of Quest Points available in Old School RuneScape?",
      QA(metric_target="quest_points", aggregation="sum_where"), 2.5, category="quest")
    E("How many free-to-play quests are there?",
      QA(aggregation="count_where", filters={"members_eq": 0}), 3.0, category="quest")
    E("How many Quest Points can a free-to-play player earn?",
      QA(metric_target="quest_points", aggregation="sum_where", filters={"members_eq": 0}),
      3.5, category="quest")
    E("What percentage of all quests are members-only?",
      QA(aggregation="percentage_where", filters={"members_eq": 1}),
      4.0, kind="percentage", category="quest", dmin=0, dmax=100)
    for diff in ("novice", "intermediate", "experienced", "master", "grandmaster"):
        E(f"How many quests are rated {diff.capitalize()} difficulty?",
          QA(aggregation="count_where", filters={"difficulty_eq": diff}),
          3.5, category="quest")
        E(f"How many Quest Points do the {diff.capitalize()} quests award in total?",
          QA(metric_target="quest_points", aggregation="sum_where",
             filters={"difficulty_eq": diff}),
          4.0, category="quest")
    E("What is the most Quest Points any single quest awards?",
      QA(metric_target="quest_points", aggregation="max_where"), 3.0,
      category="quest", dmin=1, dmax=10)
    for n in (1, 2, 3, 4, 5):
        E(f"How many quests award exactly {n} Quest Point{'s' if n > 1 else ''}?",
          QA(aggregation="count_where", filters={"quest_points_eq": n}),
          4.0, category="quest")
    for era_lo, era_hi, label in ((2001, 2007, "the original 2001-2007 era"),
                                  (2013, 2016, "OSRS's first years (2013-2016)"),
                                  (2017, 2021, "2017-2021"), (2022, 2026, "2022 onwards")):
        E(f"How many quests were released in {label}?",
          QA(aggregation="count_where",
             filters={"release_year_lo": era_lo, "release_year_hi": era_hi}),
          4.0, category="quest", era=(era_lo + era_hi) // 2)
    # Quest series with enough entries to be interesting.
    for sr in conn.execute(
        "SELECT series, COUNT(*) AS n FROM staging_quests WHERE series IS NOT NULL "
        "GROUP BY series HAVING n >= 3 ORDER BY n DESC"
    ).fetchall():
        E(f"How many quests are in the {sr['series']} series?",
          QA(aggregation="count_where", filters={"series_eq": sr["series"]}),
          4.0, category="quest")

    # ---- Cross-domain flavour: monster census ----
    MA = lambda **kw: {"domain": "monster", **kw}
    E("Of the monsters on ScapeMaster's boss roster, how many have a combat level over 500?",
      MA(aggregation="count_where", filters={"is_boss_eq": 1, "combat_level_min": 501}),
      4.5, category="monster")
    E("What is the highest combat level of any monster in Old School RuneScape?",
      MA(metric_target="combat_level", aggregation="max_where"), 3.0,
      category="monster")

    return out


# PLANTED HALLUCINATION (demo): the prices API says the Abyssal whip's High
# Alchemy value is 72,000 coins, but the "LLM" proposes the folk-memory 120,001
# (its shop value). The validation layer must reject this and keep it out of
# production.
_PLANTED_HALLUCINATION = {
    "question_text": "How many coins does the High Alchemy spell yield for one Abyssal whip?",
    "difficulty_weight": 3.0, "game_mode": "daily",
    "answer_kind": "coins", "category": "item", "display_min": None, "display_max": None,
    "validation_parameters": {
        "domain": "item", "entity_id": 4151, "metric_target": "high_alch",
    },
    "proposed_answer": 120001,  # WRONG — the trusted staging value is 72,000
    "era_year": 2005,
}


def mock_llm_questions(conn: sqlite3.Connection, planted: bool = True) -> list[dict]:
    """Full generator batch, plus (for the demo) one planted hallucination so
    the rejection path is exercised. Export runs pass `planted=False`."""
    questions = generate_questions(conn)
    if planted:
        questions = questions + [_PLANTED_HALLUCINATION]
    return questions


def run_validation_pipeline(conn: sqlite3.Connection, today: str | None = None,
                            planted: bool = True) -> dict:
    """Validate every generated question; commit the passing ones to production.

    Returns a summary {committed, rejected, rejections:[...]} for logging/CLI.
    """
    committed, rejections = 0, []
    for q in mock_llm_questions(conn, planted):
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
                float(result.expected),       # store the TRUSTED value, not the proposal
                q.get("answer_kind", "count"),
                q.get("category", ""),
                q.get("display_min"),
                q.get("display_max"),
                q.get("difficulty_weight", 1.0),
                q.get("game_mode", "daily"),
                q.get("era_year"),
                today,
            ),
        )
        committed += 1
    conn.commit()
    return {"committed": committed, "rejected": len(rejections), "rejections": rejections}


# --- Committed question bank: export (build-time) + load (serve-time) ----------
_DATASET_COLS = ("question_string", "verified_answer", "answer_kind", "category",
                 "display_min", "display_max", "difficulty_weight", "game_mode", "era_year")


def _era_weighted_sample(rng: random.Random, rows: list, k: int) -> list:
    """Era-biased sample without replacement (Efraimidis-Spirakis A-Res), using
    the same release-year weights the live service applies."""
    from .service import _era_weight  # lazy import to avoid a load-time cycle
    if k >= len(rows):
        return list(rows)
    keyed = []
    for r in rows:
        w = _era_weight(r["era_year"])
        keyed.append((rng.random() ** (1.0 / w) if w > 0 else 0.0, r))
    keyed.sort(key=lambda t: t[0], reverse=True)
    return [r for _key, r in keyed[:k]]


def _sample_dataset(rows: list, n: int, seed: int = 42) -> list:
    """Pick ~n questions with a per-category (item/monster/quest/skill) mix that
    mirrors the pool, era-weighted within each category so recent famous content
    dominates without wiping out the classics."""
    rng = random.Random(seed)
    from collections import defaultdict
    by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    total = len(rows) or 1
    raw = {c: n * len(g) / total for c, g in by_cat.items()}
    alloc = {c: int(v) for c, v in raw.items()}
    for c in sorted(raw, key=lambda c: raw[c] - alloc[c], reverse=True)[: n - sum(alloc.values())]:
        alloc[c] += 1
    out: list = []
    for c, g in by_cat.items():
        out += _era_weighted_sample(rng, g, min(alloc[c], len(g)))
    return out


def dataset_meta() -> dict:
    """The committed bank's provenance (see DATASET_META_PATH), or {} if absent."""
    try:
        return json.loads(DATASET_META_PATH.read_text())
    except (OSError, ValueError):
        return {}


def export_dataset(conn: sqlite3.Connection, n: int = 1200, out_path=None) -> dict:
    """Regenerate the full validated pool from the staging already in `conn`,
    then write an era-weighted, category-balanced sample of ~n questions to JSON.

    Also stamps the bank's build date to DATASET_META_PATH, so the dataset-served
    site can show when its data was last refreshed."""
    conn.execute("DELETE FROM production_trivia_questions")
    run_validation_pipeline(conn, planted=False)
    rows = conn.execute(
        f"SELECT {', '.join(_DATASET_COLS)} FROM production_trivia_questions"
    ).fetchall()
    data = [dict(r) for r in _sample_dataset(rows, n)]
    out = Path(out_path or DATASET_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False))

    meta = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    # The meta stamp lives NEXT TO the exported bank, not hard-wired to the
    # committed copy: an export to a custom out_path (e.g. the test suite's tmp
    # dir) must never touch the committed dataset_meta.json — that left a dirty
    # working tree after every pytest run.
    (out.parent / DATASET_META_PATH.name).write_text(
        json.dumps(meta, indent=1, ensure_ascii=False)
    )
    return {"written": len(data), "pool": len(rows), "path": str(out)}


# Duel Arena snapshot: entities + the stats the over/under compares, so the
# mode runs from the committed bank with no staging tables present.
ARCADE_ITEM_STATS = ("ge_price", "high_alch", "buy_limit")
ARCADE_MONSTER_STATS = ("combat_level", "hitpoints", "slayer_xp")


def export_arcade(conn: sqlite3.Connection, out_path=None) -> dict:
    """Snapshot item + monster stats for the Duel Arena. Only fame-gated items
    are included — the matchup should never hinge on an item nobody knows."""
    items = [
        {"entity_id": str(r["item_id"]), "full_name": r["name"],
         "stats": {k: r[k] for k in ARCADE_ITEM_STATS if r[k] is not None}}
        for r in conn.execute(
            "SELECT item_id, name, ge_price, high_alch, buy_limit FROM staging_items "
            "WHERE fame_tier <= 2 ORDER BY item_id"
        )
    ]
    monsters = [
        {"entity_id": r["monster_id"], "full_name": r["name"],
         "stats": {k: r[k] for k in ARCADE_MONSTER_STATS if r[k] is not None}}
        for r in conn.execute(
            "SELECT monster_id, name, combat_level, hitpoints, slayer_xp "
            "FROM staging_monsters ORDER BY monster_id"
        )
    ]
    out = Path(out_path or ARCADE_PATH)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"items": items, "monsters": monsters},
                              indent=1, ensure_ascii=False))
    return {"items": len(items), "monsters": len(monsters), "path": str(out)}


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
             q.get("display_max"), q.get("difficulty_weight", 1.0),
             q.get("game_mode", "daily"), q.get("era_year")),
        )
    conn.commit()
    return {"committed": len(data), "rejected": 0, "rejections": []}


def seed_all(db_path=None) -> dict:
    """Full reset: rebuild schema, load the committed entity datasets into
    staging, run the validation pipeline. Fully offline (the datasets ship in
    the repo); see `refresh` for the live GE-price refresh path."""
    conn = db.connect(db_path)  # None -> resolves db.DB_PATH at call time
    try:
        db.reset_db(conn)
        seed_staging(conn)
        summary = run_validation_pipeline(conn)
    finally:
        conn.close()
    return summary


# Sources that refresh the GE snapshot from the live wiki prices API first.
_REAL_SOURCES = {"wiki", "prices", "live"}
# Sources that serve the curated, committed question bank (no regeneration).
_DATASET_SOURCES = {"dataset", "bank", "file"}


def refresh(db_path=None, source: str | None = None, force: bool = False) -> dict:
    """Build/refresh the playable database.

    `source` (or env `OSRS_DATA_SOURCE`) selects the data origin:
      * "dataset"  — load the curated, validated question bank committed in the
        repo (backend/app/data/questions.json). No network, instant boot.
      * "entities" (or anything else) — regenerate + validate the pool from the
        committed entity datasets. Still offline.
      * "wiki" — refresh the GE price snapshot from the live wiki prices API
        (rate-limited, disk-cached, weekly-gated), then regenerate + validate.
        Falls back to the committed snapshot if the network is unreachable.

    Returns a status dict (always includes "source").
    """
    source = (source or os.environ.get("OSRS_DATA_SOURCE", "dataset")).lower()
    if source in _DATASET_SOURCES:
        conn = db.connect(db_path)
        try:
            summary = load_dataset(conn)
        finally:
            conn.close()
        return {"source": "dataset", **summary}
    if source not in _REAL_SOURCES:
        return {"source": "entities", **seed_all(db_path)}

    from . import etl  # local import: offline paths never need the network stack

    etl_note = None
    try:
        etl_status = etl.refresh_snapshot_if_stale(force=force)
    except etl.NetworkError as exc:
        # Committed snapshot still works; note the failure and serve from it.
        etl_status = {"status": "error", "skipped": True, "error": str(exc)}
        etl_note = "served from the committed GE snapshot"

    summary = seed_all(db_path)  # regenerate from (possibly refreshed) datasets
    out = {"source": "wiki", "etl": etl_status, **summary}
    if etl_note:
        out["note"] = etl_note
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Seed / refresh the ScapeMaster database.")
    ap.add_argument("--source", default=None,
                    help="dataset (committed bank), entities (regenerate offline) or "
                         "wiki (refresh GE prices first). Falls back to env OSRS_DATA_SOURCE.")
    ap.add_argument("--force", action="store_true",
                    help="ignore the weekly freshness gate (wiki only)")
    ap.add_argument("--export", action="store_true",
                    help="rebuild the committed question bank (questions.json + arcade.json) "
                         "from the entity datasets, then exit")
    ap.add_argument("--count", type=int, default=1200,
                    help="number of questions to write to the bank with --export")
    args = ap.parse_args()

    if args.export:
        # Make sure staging is populated for the chosen source, then snapshot.
        refresh(source=args.source or "entities", force=True)
        conn = db.connect()
        try:
            d = export_dataset(conn, n=args.count)
            a = export_arcade(conn)
        finally:
            conn.close()
        print(f"Wrote {d['written']} questions (from a pool of {d['pool']}) -> {d['path']}")
        print(f"Wrote {a['items']} item + {a['monsters']} monster stat lines -> {a['path']}")
        raise SystemExit(0)

    summary = refresh(source=args.source, force=args.force)
    print(f"[{summary['source']}] Committed {summary.get('committed', 0)} questions, "
          f"rejected {summary.get('rejected', 0)}.")
    if "etl" in summary:
        print("  ETL:", summary["etl"])
    for r in summary.get("rejections", []):
        print(f"  REJECTED [{r['metric']}] {r['question']!r}: "
              f"expected {r['expected']}, proposed {r['proposed']} -- {r['reason']}")

# Sanity anchor used by tests and verify_bank: the most famous number in the
# game. If the XP formula ever drifts, everything stops.
XP_99 = 13_034_431
assert xp_for_level(99) == XP_99
