"""Account creation, sessions, and the server-side saving / trust boundary
(Architecture §2.2). Verifies that totals are rebuilt from server-scored events
and can never be set by the client."""

import pytest
from fastapi.testclient import TestClient

from app import auth, db, seed
from app.main import app


@pytest.fixture(autouse=True)
def seeded_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    seed.seed_all(test_db)
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # The login limiter is process-global in-memory; clear it around each test so
    # accumulated failures from one test never leak into another.
    auth.reset_rate_limits()
    yield
    auth.reset_rate_limits()


@pytest.fixture
def client():
    return TestClient(app)


def _answer(client, token):
    """Reveal a question's true answer via a throwaway guess (so a test can then
    score a perfect 5000)."""
    return client.post("/api/v1/quiz/verify",
                       json={"tracking_token": token, "guess": 0}).json()["actual"]


# ── Password hashing ─────────────────────────────────────────────────────────
def test_password_hash_roundtrip_and_uniqueness():
    h1 = auth.hash_password("correct horse")
    h2 = auth.hash_password("correct horse")
    assert h1 != h2                       # per-user salt -> different hashes
    assert "correct horse" not in h1      # plaintext never stored
    assert auth.verify_password("correct horse", h1)
    assert not auth.verify_password("wrong", h1)


# ── Registration & login ─────────────────────────────────────────────────────
def test_register_then_use_token(client):
    r = client.post("/api/v1/auth/register",
                    json={"username": "lewis44", "password": "hunter2pass"})
    assert r.status_code == 200
    token = r.json()["token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "lewis44"


def test_duplicate_username_rejected_case_insensitively(client):
    client.post("/api/v1/auth/register", json={"username": "Max", "password": "redbull123"})
    r = client.post("/api/v1/auth/register", json={"username": "max", "password": "other1234"})
    assert r.status_code == 400


@pytest.mark.parametrize("username,password", [
    ("ab", "longenough1"),     # username too short
    ("ok_name", "short"),      # password too short
    ("bad name!", "longenough1"),  # illegal characters
])
def test_register_validation(client, username, password):
    r = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert r.status_code == 400


def test_login_wrong_password_401(client):
    client.post("/api/v1/auth/register", json={"username": "checo", "password": "perez1234"})
    r = client.post("/api/v1/auth/login", json={"username": "checo", "password": "nope"})
    assert r.status_code == 401


def test_login_unknown_username_401(client):
    # Unknown user must look exactly like a wrong password (no enumeration).
    r = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "whatever1"})
    assert r.status_code == 401


def test_overlong_password_rejected(client):
    r = client.post("/api/v1/auth/register",
                    json={"username": "longpass", "password": "x" * 2000})
    assert r.status_code == 400


def test_login_lockout_after_repeated_failures(client):
    client.post("/api/v1/auth/register", json={"username": "norris", "password": "correctpass1"})
    # 8 wrong attempts are rejected as 401...
    for _ in range(8):
        assert client.post("/api/v1/auth/login",
                           json={"username": "norris", "password": "wrong"}).status_code == 401
    # ...the next attempt is rate-limited (429), even with the correct password.
    r = client.post("/api/v1/auth/login", json={"username": "norris", "password": "correctpass1"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_successful_login_resets_the_failure_counter(client):
    client.post("/api/v1/auth/register", json={"username": "piastri", "password": "correctpass1"})
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "piastri", "password": "wrong"})
    # A correct login clears the counter, so the user isn't locked out afterwards.
    assert client.post("/api/v1/auth/login",
                       json={"username": "piastri", "password": "correctpass1"}).status_code == 200
    for _ in range(8):
        assert client.post("/api/v1/auth/login",
                           json={"username": "piastri", "password": "wrong"}).status_code == 401


def test_connection_uses_wal_and_busy_timeout():
    conn = db.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
    finally:
        conn.close()


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_logout_revokes_token(client):
    token = client.post("/api/v1/auth/register",
                        json={"username": "seb5", "password": "ferrari123"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 200
    client.post("/api/v1/auth/logout", headers=hdr)
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 401


# ── Server-side saving ───────────────────────────────────────────────────────
def test_scored_answers_persist_to_account(client):
    token = client.post("/api/v1/auth/register",
                        json={"username": "george63", "password": "mercedes123"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}

    quiz = client.get("/api/v1/quiz/daily").json()
    q = quiz["questions"][0]["tracking_token"]
    actual = _answer(client, q)  # anonymous reveal — not attached to the account
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": q, "guess": actual}, headers=hdr)

    me = client.get("/api/v1/auth/me", headers=hdr).json()
    assert me["stats"]["lifetime_points"] == 5000   # exact hit, server-scored
    assert me["stats"]["questions_answered"] == 1


def test_guest_progress_claimed_on_register(client):
    # Play as a guest with a device id, then create an account that claims it.
    quiz = client.get("/api/v1/quiz/daily").json()
    q = quiz["questions"][0]["tracking_token"]
    actual = client.post("/api/v1/quiz/verify",
                         json={"tracking_token": q, "guess": 0}).json()["actual"]
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": q, "guess": actual, "anon_id": "device-xyz"})

    r = client.post("/api/v1/auth/register",
                    json={"username": "fan2026", "password": "boxboxbox", "anon_id": "device-xyz"})
    body = r.json()
    assert body["claimed_events"] == 1
    assert body["stats"]["lifetime_points"] == 5000


def test_leaderboard_uses_server_scores_not_client_claims(client):
    # Two accounts; only server-scored events count toward the ranking.
    t1 = client.post("/api/v1/auth/register",
                     json={"username": "alpha", "password": "password1"}).json()["token"]
    client.post("/api/v1/auth/register", json={"username": "beta", "password": "password1"})

    quiz = client.get("/api/v1/quiz/daily").json()
    q = quiz["questions"][0]["tracking_token"]
    actual = _answer(client, q)
    client.post("/api/v1/quiz/verify", json={"tracking_token": q, "guess": actual},
                headers={"Authorization": f"Bearer {t1}"})

    board = client.get("/api/v1/leaderboard").json()["entries"]
    # 'alpha' scored; 'beta' has no verified events and must not appear.
    assert [e["username"] for e in board] == ["alpha"]
    assert board[0]["lifetime_points"] == 5000


def test_accounts_survive_question_bank_reseed(client, tmp_path):
    """Re-running the dataset seed (what happens on every boot) must not wipe
    accounts or their play history."""
    token = client.post("/api/v1/auth/register",
                        json={"username": "persisty", "password": "persist123"}).json()["token"]
    conn = db.connect()
    try:
        seed.load_dataset(conn)  # calls reset_db, as the boot path does
    finally:
        conn.close()
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "persisty"
