"""End-to-end API tests. Verifies the trust boundary: the daily payload never
carries the answer, and scoring happens server-side."""

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


def test_404_serves_branded_page_for_browsers(client):
    """A stray browser navigation gets the styled 404 page, not a bare error."""
    r = client.get("/no-such-page", headers={"accept": "text/html"})
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "Back to Lumbridge" in r.text


def test_404_stays_json_for_api_clients(client):
    """API and non-HTML clients keep the plain JSON 404 contract (trust boundary
    and error shapes are unchanged by the branded-page handler)."""
    r = client.get("/api/v1/definitely-not-a-route")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert "detail" in r.json()

    # An existing endpoint's own 404 still surfaces its specific JSON detail.
    r2 = client.get("/api/v1/quiz/bogus-mode")
    assert r2.status_code == 404
    assert "Unknown game mode" in r2.json()["detail"]


def test_data_status_reports_refresh_date(client):
    r = client.get("/api/v1/data/status")
    assert r.status_code == 200
    # Always carries the key; the value may be None when no refresh stamp exists.
    assert "refreshed_at" in r.json()


def test_arcade_skips_tie_and_zero_pairs():
    # A tie (including 0 vs 0) has no "which is greater?" answer, so the pair
    # picker must never return one when any metric distinguishes the entities.
    import random
    a, b = {"entity_id": "a"}, {"entity_id": "b"}
    vals = {("a", "both_zero"): 0.0, ("b", "both_zero"): 0.0,
            ("a", "equal"): 4.0, ("b", "equal"): 4.0,
            ("a", "real"): 3.0, ("b", "real"): 8.0}
    value_fn = lambda e, m: vals[(e["entity_id"], m)]
    for s in range(25):
        _, _, metric, va, vb = service._pick_close_pair(
            [(a, b)], ["both_zero", "equal", "real"], random.Random(s), value_fn, attempts=40)
        assert va != vb
        assert metric == "real"


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


@pytest.mark.parametrize("mode,count", [("daily", 6)])
def test_all_modes_serve_and_hide_answers(client, mode, count):
    r = client.get(f"/api/v1/quiz/{mode}")
    assert r.status_code == 200
    body = r.json()
    assert body["game_mode"] == mode
    assert len(body["questions"]) == count
    for q in body["questions"]:
        assert "actual" not in q and "verified_answer" not in q


def test_daily_is_the_only_served_quiz_mode(client):
    assert set(service.MODE_QUESTION_COUNT) == {"daily"}
    assert client.get("/api/v1/quiz/free_practice").status_code == 404
    modes = {q["game_mode"] for q in client.get("/api/v1/dev/questions").json()["questions"]}
    assert modes == {"daily"}


def test_unknown_mode_404(client):
    assert client.get("/api/v1/quiz/bogus").status_code == 404


def test_slider_bounds_never_pin_the_answer(client):
    """Bounds must contain the answer without revealing it: coins/xp kinds get a
    log-friendly band at least three decades wide."""
    for q in client.get("/api/v1/quiz/daily").json()["questions"]:
        assert q["slider_min"] < q["slider_max"]
        if q["answer_kind"] in ("coins", "xp") and q["slider_min"] > 0:
            assert q["slider_max"] / q["slider_min"] >= 100


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
    monkeypatch.setenv("OSRS_DEV_TOOLS", "0")
    assert client.get("/api/v1/dev/questions").status_code == 404


def test_dev_flag_round_trips(client):
    # Grab a real question to flag.
    qs = client.get("/api/v1/dev/questions").json()["questions"][0]["question_string"]

    # Flag it: it shows up flagged in the bank view and in the review queue.
    r = client.post("/api/v1/dev/flag", json={"question_string": qs, "note": "too obscure"})
    assert r.status_code == 200 and r.json()["flagged"] is True
    assert r.json()["flagged_count"] == 1

    body = client.get("/api/v1/dev/questions").json()
    assert body["flagged_count"] == 1
    assert next(q for q in body["questions"] if q["question_string"] == qs)["flagged"] is True
    flags = client.get("/api/v1/dev/flags").json()
    assert flags["count"] == 1 and flags["flags"][0]["note"] == "too obscure"

    # Unflag it: back to a clean queue.
    r = client.post("/api/v1/dev/flag", json={"question_string": qs, "flagged": False})
    assert r.status_code == 200 and r.json()["flagged"] is False
    assert client.get("/api/v1/dev/questions").json()["flagged_count"] == 0
    assert client.get("/api/v1/dev/flags").json()["count"] == 0


def test_dev_flag_unknown_question_404s(client):
    r = client.post("/api/v1/dev/flag", json={"question_string": "not a real question"})
    assert r.status_code == 404


def test_dev_flag_survives_bank_reseed(client):
    """A flag is keyed by question text, so it must outlive the boot-time reseed
    that drops and rebuilds production_trivia_questions with fresh UUIDs."""
    qs = client.get("/api/v1/dev/questions").json()["questions"][0]["question_string"]
    client.post("/api/v1/dev/flag", json={"question_string": qs})
    # Reseed the question bank (what every boot does); flags table is preserved.
    seed.seed_all(db.DB_PATH)
    flags = client.get("/api/v1/dev/flags").json()
    assert any(f["question_string"] == qs for f in flags["flags"])


def test_dev_flag_disabled_with_dev_tools_off(client, monkeypatch):
    monkeypatch.setenv("OSRS_DEV_TOOLS", "0")
    assert client.post("/api/v1/dev/flag", json={"question_string": "x"}).status_code == 404
    assert client.get("/api/v1/dev/flags").status_code == 404


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
    # A Training Grounds guess is scored like any other...
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
    assert body["entity_a"]["entity_id"] != body["entity_b"]["entity_id"]
    assert "value" in body["entity_a"] and "value" in body["entity_b"]
