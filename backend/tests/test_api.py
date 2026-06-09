"""End-to-end API tests (Architecture §1). Verifies the trust boundary: the
daily payload never carries the answer, and scoring happens server-side."""

import pytest
from fastapi.testclient import TestClient

from app import db, seed
from app.main import app


@pytest.fixture(autouse=True)
def seeded_db(tmp_path, monkeypatch):
    # Point the app at a throwaway seeded database for each test.
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    seed.seed_all(test_db)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["active_questions"] >= 5


def test_daily_quiz_hides_answer(client):
    r = client.get("/api/v1/quiz/daily")
    assert r.status_code == 200
    body = r.json()
    assert len(body["questions"]) == 10
    for q in body["questions"]:
        assert "tracking_token" in q
        # The trust boundary: no answer field in any client-facing question.
        assert "answer" not in q
        assert "verified_answer" not in q
        assert "actual" not in q


def test_verify_scores_server_side(client):
    quiz = client.get("/api/v1/quiz/daily").json()
    token = quiz["questions"][0]["tracking_token"]

    r = client.post("/api/v1/quiz/daily/verify",
                    json={"tracking_token": token, "guess": 1})
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["score"] <= 5000
    assert "actual" in body  # revealed only after the guess


def test_exact_guess_scores_max(client):
    quiz = client.get("/api/v1/quiz/daily").json()
    token = quiz["questions"][0]["tracking_token"]
    # First reveal the actual via a throwaway guess, then guess it exactly.
    actual = client.post("/api/v1/quiz/daily/verify",
                         json={"tracking_token": token, "guess": 0}).json()["actual"]
    r = client.post("/api/v1/quiz/daily/verify",
                    json={"tracking_token": token, "guess": actual})
    assert r.json()["score"] == 5000


def test_unknown_token_404(client):
    r = client.post("/api/v1/quiz/daily/verify",
                    json={"tracking_token": "nope", "guess": 5})
    assert r.status_code == 404


@pytest.mark.parametrize("mode,count", [("daily", 10), ("race_week", 10), ("one_shot", 3)])
def test_all_modes_serve_and_hide_answers(client, mode, count):
    r = client.get(f"/api/v1/quiz/{mode}")
    assert r.status_code == 200
    body = r.json()
    assert body["game_mode"] == mode
    assert len(body["questions"]) == count
    for q in body["questions"]:
        assert "actual" not in q and "verified_answer" not in q


def test_unknown_mode_404(client):
    assert client.get("/api/v1/quiz/bogus").status_code == 404


def test_daily_set_is_deterministic_within_period(client):
    a = [q["question_text"] for q in client.get("/api/v1/quiz/daily").json()["questions"]]
    b = [q["question_text"] for q in client.get("/api/v1/quiz/daily").json()["questions"]]
    assert a == b  # stable provisioning for the same UTC day


def test_arcade_pair_shape(client):
    r = client.get("/api/v1/arcade/pair")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_a"]["driver_id"] != body["entity_b"]["driver_id"]
    assert "value" in body["entity_a"] and "value" in body["entity_b"]
