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


def test_race_week_circuit_filtering():
    """Race-week questions tagged with a circuit are served when (and only when)
    that circuit is requested, with a full daily session's worth per circuit."""
    if not seed.DATASET_PATH.exists():
        pytest.skip("committed dataset not present")
    conn = db.connect(":memory:")
    seed.load_dataset(conn)

    circuits = [
        r["circuit_id"]
        for r in conn.execute(
            "SELECT DISTINCT circuit_id FROM production_trivia_questions "
            "WHERE circuit_id IS NOT NULL"
        )
    ]
    assert circuits, "expected circuit-specific race-week questions in the bank"

    need = service.MODE_QUESTION_COUNT["race_week"]
    for cid in circuits:
        served = service.build_quiz(conn, "race_week", circuit_id=cid)["questions"]
        assert len(served) == need
        # A circuit-filtered session draws only from that circuit's bank.
        rows = {
            q["question_string"]
            for q in conn.execute(
                "SELECT question_string FROM production_trivia_questions WHERE circuit_id = ?",
                (cid,),
            )
        }
        assert all(q["question_text"] in rows for q in served)
    conn.close()
