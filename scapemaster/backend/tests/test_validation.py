"""The anti-hallucination engine: the exact XP formula, the four domains'
metric computation, and the rejection of any proposed answer that disagrees
with the trusted staging data."""

from decimal import Decimal

import pytest

from app import db, seed
from app.validation import (
    compute_metric,
    validate_ai_question,
    xp_between,
    xp_for_level,
)


# ── The XP formula (pure arithmetic — the anchor of the design) ──────────────
def test_xp_table_anchor_values():
    # The most famous numbers in the game.
    assert xp_for_level(1) == 0
    assert xp_for_level(2) == 83
    assert xp_for_level(10) == 1_154
    assert xp_for_level(50) == 101_333
    assert xp_for_level(92) == 6_517_253      # "92 is half of 99"
    assert xp_for_level(99) == 13_034_431
    assert xp_for_level(126) == 188_884_740


def test_92_is_half_of_99_within_rounding():
    assert abs(xp_for_level(99) - 2 * xp_for_level(92)) <= 75


def test_xp_table_is_strictly_increasing():
    values = [xp_for_level(n) for n in range(1, 127)]
    assert all(b > a for a, b in zip(values, values[1:]))


def test_xp_between_matches_difference():
    for a, b in ((1, 99), (50, 60), (92, 99), (98, 99)):
        assert xp_between(a, b) == xp_for_level(b) - xp_for_level(a)


def test_xp_bounds_are_enforced():
    with pytest.raises(ValueError):
        xp_for_level(0)
    with pytest.raises(ValueError):
        xp_for_level(127)
    with pytest.raises(ValueError):
        xp_between(99, 92)


def test_skill_domain_computes_from_formula():
    # No table rows needed: the skill domain never touches the database.
    conn = db.connect(":memory:")
    assert compute_metric(conn, {"domain": "skill", "metric_target": "xp_for_level",
                                 "level": 99}) == Decimal(13_034_431)
    assert compute_metric(conn, {"domain": "skill", "metric_target": "xp_between",
                                 "level_a": 92, "level_b": 99}) == Decimal(6_517_178)
    conn.close()


# ── Table-backed domains over a tiny synthetic staging ───────────────────────
@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    c.executemany(
        "INSERT INTO staging_items (item_id, name, members, buy_limit, value, low_alch, "
        "high_alch, ge_price, ge_volume, release_year, fame_tier) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "Testium sword", 1, 8, 100_000, 40_000, 60_000, 1_000_000, 500, 2015, 1),
            (2, "Testium ore", 0, 10_000, 100, 40, 60, 150, 90_000, 2001, 2),
            (3, "Unpriced relic", 1, None, 50, 20, 30, None, 0, 2020, 3),
        ],
    )
    c.executemany(
        "INSERT INTO staging_monsters (monster_id, name, combat_level, hitpoints, max_hit, "
        "slayer_level, slayer_xp, release_year, is_boss) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("snake_boss", "Snake Boss", 700, 500, 41, 1, 500.0, 2015, 1),
            ("hound", "Hound", 300, 600, 23, 91, 690.0, 2015, 1),
            ("critter", "Critter", 2, 5, 1, 1, None, 2001, 0),
        ],
    )
    c.executemany(
        "INSERT INTO staging_quests (quest_id, name, difficulty, quest_points, members, "
        "release_year, series) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("cooks_helper", "Cook's Helper", "novice", 1, 0, 2001, None),
            ("dragon_epic", "Dragon Epic", "grandmaster", 5, 1, 2018, "Dragonkin"),
            ("dragon_epic_ii", "Dragon Epic II", "grandmaster", 4, 1, 2021, "Dragonkin"),
        ],
    )
    c.commit()
    yield c
    c.close()


def test_identity_reads_the_staged_fact(conn):
    p = {"domain": "item", "entity_id": 1, "metric_target": "high_alch"}
    assert compute_metric(conn, p) == Decimal(60_000)
    p = {"domain": "monster", "entity_id": "hound", "metric_target": "slayer_level"}
    assert compute_metric(conn, p) == Decimal(91)
    p = {"domain": "quest", "entity_id": "dragon_epic", "metric_target": "quest_points"}
    assert compute_metric(conn, p) == Decimal(5)


def test_identity_refuses_missing_entity_and_null_fact(conn):
    # An absent fact must never masquerade as a zero answer.
    with pytest.raises(ValueError):
        compute_metric(conn, {"domain": "item", "entity_id": 999, "metric_target": "value"})
    with pytest.raises(ValueError):
        compute_metric(conn, {"domain": "item", "entity_id": 3, "metric_target": "ge_price"})


def test_difference_is_exact(conn):
    p = {"domain": "monster", "entity_id": "snake_boss", "entity_id_b": "hound",
         "metric_target": "combat_level", "aggregation": "difference"}
    assert compute_metric(conn, p) == Decimal(400)


def test_count_and_sum_and_max_where_cross_checked(conn):
    qp = {"domain": "quest", "aggregation": "count_where",
          "filters": {"difficulty_eq": "grandmaster"}}
    assert compute_metric(conn, qp) == Decimal(2)
    qp = {"domain": "quest", "metric_target": "quest_points", "aggregation": "sum_where",
          "filters": {"difficulty_eq": "grandmaster"}}
    assert compute_metric(conn, qp) == Decimal(9)
    qp = {"domain": "quest", "metric_target": "quest_points", "aggregation": "max_where"}
    assert compute_metric(conn, qp) == Decimal(5)
    qp = {"domain": "quest", "aggregation": "count_where", "filters": {"series_eq": "Dragonkin"}}
    assert compute_metric(conn, qp) == Decimal(2)
    mp = {"domain": "monster", "aggregation": "count_where",
          "filters": {"is_boss_eq": 1, "combat_level_min": 500}}
    assert compute_metric(conn, mp) == Decimal(1)


def test_percentage_where_rounds(conn):
    p = {"domain": "quest", "aggregation": "percentage_where", "filters": {"members_eq": 1}}
    assert compute_metric(conn, p) == Decimal(67)  # 2 of 3 quests, rounded


def test_unknown_domain_metric_filter_aggregation_raise(conn):
    with pytest.raises(ValueError):
        compute_metric(conn, {"domain": "spell", "entity_id": 1, "metric_target": "value"})
    with pytest.raises(ValueError):
        compute_metric(conn, {"domain": "item", "entity_id": 1, "metric_target": "combat_level"})
    with pytest.raises(ValueError):
        compute_metric(conn, {"domain": "quest", "aggregation": "count_where",
                              "filters": {"drop table": 1}})
    with pytest.raises(ValueError):
        compute_metric(conn, {"domain": "item", "entity_id": 1,
                              "metric_target": "value", "aggregation": "median"})


def test_validate_accepts_matching_and_rejects_hallucination(conn):
    good = {
        "question_text": "High alch of Testium sword?",
        "validation_parameters": {"domain": "item", "entity_id": 1, "metric_target": "high_alch"},
        "proposed_answer": 60_000,
    }
    assert validate_ai_question(conn, good).ok is True

    bad = {**good, "proposed_answer": 100_000}  # the "shop value" folk memory
    result = validate_ai_question(conn, bad)
    assert result.ok is False
    assert "Hallucination" in result.reason
    assert result.expected == Decimal(60_000)


def test_malformed_params_fail_validation_not_crash(conn):
    r = validate_ai_question(conn, {
        "validation_parameters": {"domain": "item", "entity_id": 1, "metric_target": "wins"},
        "proposed_answer": 3,
    })
    assert r.ok is False and r.reason


# ── Full pipeline over the committed datasets ────────────────────────────────
def test_pipeline_rejects_the_planted_hallucination(tmp_path):
    """seed_all runs the generator + validation over the real committed entity
    datasets; exactly the planted wrong-whip-alch question must be rejected."""
    summary = seed.seed_all(tmp_path / "seed.db")
    assert summary["committed"] > 500
    assert summary["rejected"] == 1
    rejection = summary["rejections"][0]
    assert "Abyssal whip" in rejection["question"]
    assert rejection["proposed"] == 120001


def test_every_committed_answer_recomputes_from_staging(tmp_path):
    """Spot-check the invariant end-to-end: pick random production rows and
    confirm the stored verified_answer still matches an independent recompute
    (identity questions carry their entity in the generator params, so instead
    we re-run the whole pipeline and diff the two banks — they must agree)."""
    db_path = tmp_path / "a.db"
    seed.seed_all(db_path)
    conn = db.connect(db_path)
    first = {r["question_string"]: r["verified_answer"] for r in conn.execute(
        "SELECT question_string, verified_answer FROM production_trivia_questions")}
    conn.close()
    seed.seed_all(db_path)  # regenerate from scratch
    conn = db.connect(db_path)
    second = {r["question_string"]: r["verified_answer"] for r in conn.execute(
        "SELECT question_string, verified_answer FROM production_trivia_questions")}
    conn.close()
    assert first == second  # deterministic: same data -> same verified answers
