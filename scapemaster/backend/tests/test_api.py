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


def test_practice_rate_limit_kicks_in(client, monkeypatch):
    """Training Grounds is an answer oracle over the same bank the Daily uses, so
    drawing questions must be throttled server-side — a client-side penalty alone
    is bypassable by calling the API directly."""
    from app import main
    monkeypatch.setattr(main, "_PRACTICE_MAX_PER_WINDOW", 3)
    for _ in range(3):
        assert client.get("/api/v1/practice/question").status_code == 200
    r = client.get("/api/v1/practice/question")
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_security_headers_on_html_and_api(client):
    home = client.get("/")
    assert home.headers["X-Content-Type-Options"] == "nosniff"
    csp = home.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    # API responses get the generic hardening headers but no CSP (not HTML).
    api = client.get("/api/v1/health")
    assert api.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in api.headers


def test_slider_bounds_use_the_server_salt(client):
    """Trust boundary: the slider band must not be reproducible from public data.
    An attacker knows the (public) bounds algorithm and the served question text;
    if the RNG were seeded by those alone they could recover the answer's
    position in the band and invert the bound back to the answer within a few
    percent. So a non-empty server-side secret must exist, and the served bounds
    must be exactly the salted computation (test_scoring proves a different salt
    moves the bounds, so together these pin the leak shut). coins/xp kinds use a
    magnitude band that never encodes the exact answer, so they are exempt."""
    served = client.get("/api/v1/quiz/daily").json()["questions"]
    conn = db.connect()
    try:
        salt = service._get_slider_salt(conn)
        explicit = {
            r["question_string"]
            for r in conn.execute(
                "SELECT question_string FROM production_trivia_questions "
                "WHERE display_min IS NOT NULL AND display_max IS NOT NULL"
            )
        }
    finally:
        conn.close()
    assert salt, "a server-side slider salt must exist"
    derived = [
        q for q in served
        if q["answer_kind"] not in ("coins", "xp") and q["question_text"] not in explicit
    ]
    checked = 0
    for q in derived:
        actual = client.post(
            "/api/v1/quiz/verify",
            json={"tracking_token": q["tracking_token"], "guess": 0},
        ).json()["actual"]
        if actual <= 0:
            continue  # zero answers use the fixed (0, 10) band — nothing to leak
        salted = service._slider_bounds(actual, q["answer_kind"], q["question_text"], salt)
        assert (q["slider_min"], q["slider_max"]) == salted
        checked += 1


# ── The Wise Old Man (hint) ───────────────────────────────────────────────────

import math

from app import service as _service


def test_hint_band_contains_answer_and_costs_score(client):
    """The Wise Old Man's band really contains the answer, and the eventual
    score pays the advertised fee — even on a perfect guess."""
    quiz = client.get("/api/v1/quiz/daily").json()
    for q in quiz["questions"]:
        token = q["tracking_token"]
        h = client.post("/api/v1/quiz/hint", json={"tracking_token": token})
        assert h.status_code == 200
        hint = h.json()
        assert hint["cost_percent"] == int(_service.HINT_COST * 100)
        assert hint["hint_min"] < hint["hint_max"]
        # Any verify reveals the actual — the band must contain it.
        r = client.post("/api/v1/quiz/verify",
                        json={"tracking_token": token, "guess": hint["hint_min"]}).json()
        assert hint["hint_min"] <= r["actual"] <= hint["hint_max"]
        assert r["hint_used"] is True
        # An exact hit after the consultation keeps only (1 - fee) of the max.
        exact = client.post("/api/v1/quiz/verify",
                            json={"tracking_token": token, "guess": r["actual"]}).json()
        assert exact["score"] == round(5000 * (1 - _service.HINT_COST))
        assert exact["score"] == hint["max_score_after"]


def test_hint_band_is_meaningfully_narrower(client):
    """The band must be strictly tighter than the band the player is already
    looking at, for every answer kind. coins/xp ride a log-scale slider, so
    their tightness is measured as a ratio (decades), not a difference."""
    quiz = client.get("/api/v1/quiz/daily").json()
    for q in quiz["questions"]:
        hint = client.post("/api/v1/quiz/hint",
                           json={"tracking_token": q["tracking_token"]}).json()
        if q["answer_kind"] in ("coins", "xp"):
            served = math.log10(max(q["slider_max"], 10)) - math.log10(max(q["slider_min"], 1))
            got = math.log10(max(hint["hint_max"], 10)) - math.log10(max(hint["hint_min"], 1))
            assert got < served, f"log hint no tighter for {q['question_text']!r}"
        else:
            assert (hint["hint_max"] - hint["hint_min"]) < (q["slider_max"] - q["slider_min"]), \
                f"hint band no tighter than the slider for {q['question_text']!r}"
        # ...and inside it: the slider offers nothing beyond the served band.
        assert q["slider_min"] <= hint["hint_min"] <= hint["hint_max"] <= q["slider_max"], \
            f"hint band escapes the slider for {q['question_text']!r}"


def test_hint_is_idempotent_per_token(client):
    q = client.get("/api/v1/quiz/daily").json()["questions"][0]
    first = client.post("/api/v1/quiz/hint", json={"tracking_token": q["tracking_token"]}).json()
    second = client.post("/api/v1/quiz/hint", json={"tracking_token": q["tracking_token"]}).json()
    assert first == second


def test_hint_unknown_token_404(client):
    assert client.post("/api/v1/quiz/hint", json={"tracking_token": "nope"}).status_code == 404


def test_no_hint_means_no_cost(client):
    q = client.get("/api/v1/quiz/daily").json()["questions"][0]
    r = client.post("/api/v1/quiz/verify",
                    json={"tracking_token": q["tracking_token"], "guess": 1}).json()
    assert r["hint_used"] is False
    exact = client.post("/api/v1/quiz/verify",
                        json={"tracking_token": q["tracking_token"], "guess": r["actual"]}).json()
    assert exact["score"] == 5000


def test_hint_cost_reaches_the_recorded_totals(client):
    """The HiScores must see the post-fee score: an exact hit after a
    consultation banks 60%, not the full 5,000."""
    q = client.get("/api/v1/quiz/daily").json()["questions"][0]
    token = q["tracking_token"]
    client.post("/api/v1/quiz/hint", json={"tracking_token": token})
    actual = client.post("/api/v1/quiz/verify",
                         json={"tracking_token": token, "guess": 0}).json()["actual"]
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": token, "guess": actual, "anon_id": "hint-device"})
    acct = client.post("/api/v1/auth/register",
                       json={"username": "hinter", "password": "supersecret",
                             "anon_id": "hint-device"}).json()
    assert acct["stats"]["lifetime_points"] == round(5000 * (1 - _service.HINT_COST))


# ── Today's task board (daily rank among today's players) ────────────────────

def test_daily_field_ranks_guests_and_members(client):
    q = client.get("/api/v1/quiz/daily").json()["questions"][0]
    token = q["tracking_token"]
    actual = client.post("/api/v1/quiz/verify",
                         json={"tracking_token": token, "guess": 0}).json()["actual"]
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": token, "guess": actual, "anon_id": "field-a"})
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": token, "guess": actual * 40 + 1000, "anon_id": "field-b"})

    top = client.get("/api/v1/quiz/daily/field?anon_id=field-a").json()
    assert top["players"] == 2 and top["rank"] == 1
    assert top["points"] == 5000 and top["beat_percent"] == 100
    bottom = client.get("/api/v1/quiz/daily/field?anon_id=field-b").json()
    assert bottom["players"] == 2 and bottom["rank"] == 2 and bottom["beat_percent"] == 0

    outsider = client.get("/api/v1/quiz/daily/field?anon_id=nobody").json()
    assert outsider["players"] == 2 and outsider["rank"] == 0

    acct = client.post("/api/v1/auth/register",
                       json={"username": "fielder", "password": "supersecret",
                             "anon_id": "field-b"}).json()
    mine = client.get("/api/v1/quiz/daily/field",
                      headers={"Authorization": f"Bearer {acct['token']}"}).json()
    assert mine["players"] == 2 and mine["rank"] == 2


def test_daily_field_empty_day(client):
    r = client.get("/api/v1/quiz/daily/field?anon_id=whoever")
    assert r.status_code == 200
    assert r.json() == {"players": 0, "rank": 0, "points": 0, "beat_percent": 0}


def test_daily_field_ignores_practice_scores(client):
    q = client.get("/api/v1/practice/question").json()["question"]
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": q["tracking_token"], "guess": 1,
                      "anon_id": "practice-only"})
    r = client.get("/api/v1/quiz/daily/field?anon_id=practice-only").json()
    assert r["players"] == 0 and r["rank"] == 0


# ── Training Grounds focus filters ───────────────────────────────────────────

def test_practice_focus_by_category(client):
    r = client.get("/api/v1/practice/question?category=monster")
    assert r.status_code == 200
    body = r.json()
    assert body["focus_matched"] is True
    assert body["question"]["category"] == "monster"


def test_practice_focus_by_era(client):
    """An era focus must serve a question whose content-release year sits in the
    requested window (checked via the dev proofreading endpoint)."""
    rows = client.get("/api/v1/dev/questions").json()["questions"]
    era_by_text = {r["question_string"]: r["era_year"] for r in rows}
    lo, hi = _service.PRACTICE_ERAS["osrs"]
    r = client.get("/api/v1/practice/question?era=osrs")
    assert r.status_code == 200
    body = r.json()
    assert body["focus_matched"] is True
    assert lo <= era_by_text[body["question"]["question_text"]] <= hi


def test_practice_focus_falls_back_when_empty(client):
    r = client.get("/api/v1/practice/question?category=definitely_not_a_category")
    assert r.status_code == 200
    assert r.json()["focus_matched"] is False


def test_practice_focus_bogus_params_are_ignored(client):
    r = client.get("/api/v1/practice/question",
                   params={"category": "no'; DROP TABLE--", "era": "1800s"})
    assert r.status_code == 200
    assert r.json()["focus_matched"] is True
