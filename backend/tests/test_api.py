"""End-to-end API tests (Architecture §1). Verifies the trust boundary: the
daily payload never carries the answer, and scoring happens server-side."""

import pytest
from fastapi.testclient import TestClient

from app import db, seed, service
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
    assert len(body["questions"]) == 6
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


@pytest.mark.parametrize("mode,count", [("daily", 6), ("race_week", 6)])
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


def test_hardcore_mode_removed(client):
    # The hardcore (one_shot) game mode was retired; it must no longer be served.
    assert "one_shot" not in service.MODE_QUESTION_COUNT
    assert client.get("/api/v1/quiz/one_shot").status_code == 404


def test_daily_set_is_deterministic_within_period(client):
    a = [q["question_text"] for q in client.get("/api/v1/quiz/daily").json()["questions"]]
    b = [q["question_text"] for q in client.get("/api/v1/quiz/daily").json()["questions"]]
    assert a == b  # stable provisioning for the same UTC day


def test_dev_questions_exposes_answers_for_proofreading(client):
    r = client.get("/api/v1/dev/questions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["questions"]) > 0
    for q in body["questions"][:5]:
        assert q["question_string"] and "verified_answer" in q


def test_dev_questions_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv("F1_DEV_TOOLS", "0")
    assert client.get("/api/v1/dev/questions").status_code == 404


def test_practice_question_hides_answer(client):
    r = client.get("/api/v1/practice/question")
    assert r.status_code == 200
    body = r.json()
    assert body["game_mode"] == "free_practice"
    q = body["question"]
    assert "tracking_token" in q
    # Same trust boundary as the daily set: no answer reaches the client.
    assert "answer" not in q and "verified_answer" not in q and "actual" not in q


def test_practice_questions_are_random(client):
    # Unlimited + non-deterministic: repeated draws should not be locked to one
    # question the way the per-period daily set is. Allow for the small chance of
    # repeats by sampling several times and asking for more than one distinct text.
    seen = {client.get("/api/v1/practice/question").json()["question"]["question_text"]
            for _ in range(12)}
    assert len(seen) > 1


def test_practice_scores_server_side_but_is_not_recorded(client):
    # A Free Practice guess is scored like any other...
    q = client.get("/api/v1/practice/question").json()["question"]
    r = client.post("/api/v1/quiz/verify",
                    json={"tracking_token": q["tracking_token"], "guess": 1,
                          "anon_id": "practice-device"})
    assert r.status_code == 200
    assert 0 <= r.json()["score"] <= 5000

    # ...but it must NEVER reach a user's totals. Claiming this device's events into
    # a fresh account should pull in nothing, proving the score was not persisted.
    acct = client.post("/api/v1/auth/register",
                       json={"username": "practicer", "password": "supersecret",
                             "anon_id": "practice-device"}).json()
    assert acct["claimed_events"] == 0
    assert acct["stats"]["lifetime_points"] == 0
    assert acct["stats"]["questions_answered"] == 0


def test_arcade_pair_shape(client):
    r = client.get("/api/v1/arcade/pair")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_a"]["driver_id"] != body["entity_b"]["driver_id"]
    assert "value" in body["entity_a"] and "value" in body["entity_b"]
