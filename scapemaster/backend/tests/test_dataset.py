"""Tests for the committed question bank: the export/load machinery and a smoke
test of the shipped files."""

import json

import pytest

from app import db, seed, service


@pytest.fixture
def staged():
    c = db.connect(":memory:")
    db.init_db(c)
    seed.seed_staging(c)
    yield c
    c.close()


def test_export_then_load_round_trips(staged, tmp_path):
    qpath, apath = tmp_path / "questions.json", tmp_path / "arcade.json"
    res = seed.export_dataset(staged, n=60, out_path=qpath)
    arc = seed.export_arcade(staged, out_path=apath)
    assert res["written"] > 0 and arc["items"] > 0 and arc["monsters"] > 0

    data = json.loads(qpath.read_text())
    # Every exported row carries the columns the loader/serving need.
    for q in data:
        assert q["question_string"] and q["game_mode"] in service.MODE_QUESTION_COUNT
        assert isinstance(q["verified_answer"], (int, float))

    target = db.connect(":memory:")
    summary = seed.load_dataset(target, path=qpath)
    assert summary["committed"] == len(data)
    # Loaded bank serves every mode, with no staging tables involved.
    assert target.execute("SELECT COUNT(*) FROM staging_items").fetchone()[0] == 0
    for mode, n in service.MODE_QUESTION_COUNT.items():
        served = service.build_quiz(target, mode)["questions"]
        assert len(served) == n
    target.close()


def test_exported_questions_have_no_subjective_wording(staged, tmp_path):
    qpath = tmp_path / "questions.json"
    seed.export_dataset(staged, n=80, out_path=qpath)
    for q in json.loads(qpath.read_text()):
        text = q["question_string"].lower()
        assert "in our database" not in text and "roughly" not in text


def test_fame_gate_filters_arcade_items(staged, tmp_path):
    """The Duel Arena must never hinge on an item nobody has heard of: only
    fame_tier <= 2 items reach the snapshot."""
    staged.execute(
        "INSERT INTO staging_items (item_id, name, members, buy_limit, value, low_alch, "
        "high_alch, ge_price, ge_volume, release_year, fame_tier) "
        "VALUES (999901, 'Obscure trinket', 1, 5, 100, 40, 60, 1000, 50, 2024, 3)"
    )
    staged.commit()
    out = tmp_path / "arcade.json"
    seed.export_arcade(staged, out_path=out)
    snap = json.loads(out.read_text())
    ids = {i["entity_id"] for i in snap["items"]}
    assert "999901" not in ids
    assert str(4151) in ids  # the Abyssal whip is always famous enough


def test_ge_price_questions_respect_the_liquidity_gates(staged, tmp_path):
    """No GE-price question for cheap, thin or obscure items — percentage-error
    scoring is meaningless there."""
    qpath = tmp_path / "questions.json"
    seed.export_dataset(staged, n=1500, out_path=qpath)
    items = {i["name"]: i for i in json.loads(seed.ITEMS_PATH.read_text())}
    for q in json.loads(qpath.read_text()):
        text = q["question_string"]
        if "trade for on the Grand Exchange" in text:
            item = next(i for n, i in items.items() if f"one {n} " in text)
            assert item["ge_price"] >= seed.MIN_GE_PRICE
            assert item["ge_volume"] >= seed.MIN_GE_VOLUME
            assert item["fame_tier"] <= seed.GE_PRICE_MAX_FAME


def test_committed_bank_loads_and_serves():
    """The shipped questions.json / arcade.json power quiz + arcade with no staging."""
    if not seed.DATASET_PATH.exists():
        pytest.skip("committed dataset not present")
    conn = db.connect(":memory:")
    summary = seed.load_dataset(conn)
    assert summary["committed"] >= 500
    for mode, n in service.MODE_QUESTION_COUNT.items():
        assert len(service.build_quiz(conn, mode)["questions"]) == n
    # Arcade falls back to the committed snapshot when staging is absent.
    import random
    service._ARCADE_DATASET = None  # reset module cache for a clean read
    pair = service.build_arcade_pair(conn, random.Random(0))
    assert pair["entity_a"]["full_name"] and pair["entity_b"]["full_name"]
    conn.close()


def test_arcade_pairs_are_close_calls():
    """Over/Under should be a genuine toss-up: across many draws the two values
    almost always land within ARCADE_MAX_GAP of each other."""
    if not seed.DATASET_PATH.exists():
        pytest.skip("committed dataset not present")
    import random
    service._ARCADE_DATASET = None
    conn = db.connect(":memory:")
    seed.load_dataset(conn)
    rng = random.Random(1)
    close = 0
    trials = 60
    for _ in range(trials):
        p = service.build_arcade_pair(conn, rng)
        if service._within_gap(p["entity_a"]["value"], p["entity_b"]["value"]):
            close += 1
    conn.close()
    # The sampler falls back to the closest pair it found, so the vast majority
    # of matchups should clear the gap on the real snapshot.
    assert close >= trials * 0.9


def test_committed_bank_answer_kinds_are_known():
    if not seed.DATASET_PATH.exists():
        pytest.skip("committed dataset not present")
    kinds = {q["answer_kind"] for q in json.loads(seed.DATASET_PATH.read_text())}
    assert kinds <= {"count", "level", "xp", "coins", "year", "percentage"}
