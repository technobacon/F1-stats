"""Tests for the committed question bank: the export/load machinery (run on
synthetic data so it needs no network) and a smoke test of the shipped files."""

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
    arc = seed.export_arcade(staged, out_path=apath, min_starts=1)
    assert res["written"] > 0 and arc["drivers"] > 0

    data = json.loads(qpath.read_text())
    # Every exported row carries the columns the loader/serving need.
    for q in data:
        assert q["question_string"] and q["game_mode"] in service.MODE_QUESTION_COUNT
        assert isinstance(q["verified_answer"], (int, float))

    target = db.connect(":memory:")
    summary = seed.load_dataset(target, path=qpath)
    assert summary["committed"] == len(data)
    # Loaded bank serves every mode, with no staging tables involved.
    assert target.execute("SELECT COUNT(*) FROM staging_race_results").fetchone()[0] == 0
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


def test_significance_gate_filters_arcade_drivers(staged):
    """Arcade must apply the same era-tiered significance gate as the question
    generator: an insignificant modern also-ran (no wins, < 50 career points) is
    excluded, while a multiple champion is kept."""
    # A nobody: races in the 2020s, never wins, scores under the 50-point floor.
    staged.execute(
        "INSERT INTO staging_drivers (driver_id, full_name, active_from, active_to) "
        "VALUES ('nobody', 'Nobody McAlsoran', 2021, 2023)"
    )
    for rnd in range(1, 4):
        staged.execute(
            "INSERT INTO staging_race_results "
            "(driver_id, constructor_id, year, round, position, grid, points) "
            "VALUES ('nobody', 'haas', 2022, ?, 15, 18, 0)", (rnd,)
        )
    staged.commit()

    keep = seed.significant_driver_ids(staged)
    assert "nobody" not in keep
    assert "schumacher" in keep  # 7-time champion, always significant

    # And the export honours it — the also-ran never reaches the snapshot.
    import tempfile
    from pathlib import Path
    out = Path(tempfile.mkdtemp()) / "arcade.json"
    seed.export_arcade(staged, out_path=out, min_starts=1)
    ids = {d["driver_id"] for d in json.loads(out.read_text())["drivers"]}
    assert "nobody" not in ids and "schumacher" in ids


def test_committed_arcade_snapshot_is_significant_only():
    """The shipped arcade snapshot carries no insignificant drivers: every modern
    (2010s+) entry is a race winner and every pre-2000 entry is a champion."""
    if not seed.ARCADE_PATH.exists():
        pytest.skip("committed arcade snapshot not present")
    drivers = json.loads(seed.ARCADE_PATH.read_text())["drivers"]
    for d in drivers:
        era = (d["active_from"] + d["active_to"]) // 2
        if 2010 <= era < 2020:
            assert d["stats"]["wins"] >= 1, f"{d['driver_id']} is a 2010s non-winner"
        if era < 2000:
            assert d["full_name"] in seed.WORLD_CHAMPIONS, f"{d['driver_id']} is a pre-2000 non-champion"


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
    """Over/Under should be a genuine toss-up: across many draws the two totals
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
    # (here, all) of matchups should clear the gap on the real snapshot.
    assert close >= trials * 0.9
