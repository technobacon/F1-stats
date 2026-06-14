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


def test_replaying_the_daily_does_not_inflate_the_total(client):
    """Leaderboard integrity: the daily set is deterministic, so re-running it must
    not stack the same questions onto a player's total a second time."""
    token = client.post("/api/v1/auth/register",
                        json={"username": "grinder", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}

    quiz = client.get("/api/v1/quiz/daily").json()
    first = quiz["questions"][0]["tracking_token"]
    actual = _answer(client, first)  # anonymous reveal (orphan, not the account's)
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": first, "guess": actual}, headers=hdr)
    after_one = client.get("/api/v1/auth/me", headers=hdr).json()["stats"]
    assert after_one["questions_answered"] == 1
    assert after_one["lifetime_points"] == 5000

    # Replay: same UTC day -> same question id behind a fresh token. The score is
    # still returned to the player, but it must NOT be recorded again.
    replay = client.get("/api/v1/quiz/daily").json()["questions"][0]["tracking_token"]
    r = client.post("/api/v1/quiz/verify",
                    json={"tracking_token": replay, "guess": actual}, headers=hdr)
    assert r.json()["score"] == 5000          # the player still sees their score
    after_two = client.get("/api/v1/auth/me", headers=hdr).json()["stats"]
    assert after_two["questions_answered"] == 1   # but the total is unchanged
    assert after_two["lifetime_points"] == 5000


def test_optional_email_is_stored_when_valid(client):
    from app import db
    r = client.post("/api/v1/auth/register",
                    json={"username": "mailed", "password": "password1",
                          "email": "Fan@Example.com"})
    assert r.status_code == 200
    conn = db.connect()
    try:
        row = conn.execute("SELECT email FROM users WHERE username='mailed'").fetchone()
    finally:
        conn.close()
    assert row["email"] == "fan@example.com"  # normalized to lower-case


def test_registration_without_email_is_allowed(client):
    from app import db
    r = client.post("/api/v1/auth/register",
                    json={"username": "noemail", "password": "password1"})
    assert r.status_code == 200
    conn = db.connect()
    try:
        row = conn.execute("SELECT email FROM users WHERE username='noemail'").fetchone()
    finally:
        conn.close()
    assert row["email"] is None


def test_malformed_email_is_rejected(client):
    r = client.post("/api/v1/auth/register",
                    json={"username": "bademail", "password": "password1",
                          "email": "not-an-email"})
    assert r.status_code == 400


def test_daily_streak_is_server_derived(client):
    import datetime as _dt
    from app import db

    token = client.post("/api/v1/auth/register",
                        json={"username": "streaky", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=hdr).json()
    assert me["stats"]["daily_streak"] == 0  # nothing played yet

    # Play today, then backfill the two prior days directly in the store (the
    # streak must be reconstructed from rows, never taken from the client).
    quiz = client.get("/api/v1/quiz/daily").json()
    qid_token = quiz["questions"][0]["tracking_token"]
    actual = _answer(client, qid_token)
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": qid_token, "guess": actual}, headers=hdr)

    conn = db.connect()
    try:
        uid = conn.execute("SELECT id FROM users WHERE username='streaky'").fetchone()["id"]
        today = _dt.datetime.now(_dt.timezone.utc).date()
        for offset in (1, 2):  # yesterday and the day before
            d = today - _dt.timedelta(days=offset)
            conn.execute(
                "INSERT INTO play_events (user_id, question_id, game_mode, score, "
                "identity_key, period, created_at) VALUES (?, ?, 'daily', 4000, ?, ?, ?)",
                (uid, f"backfill-{offset}", uid, d.isoformat(), f"{d.isoformat()} 12:00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    streak = client.get("/api/v1/auth/me", headers=hdr).json()["stats"]["daily_streak"]
    assert streak == 3  # today + yesterday + day before, consecutive


def _seed_daily_days(conn, uid, offsets):
    """Backfill 'daily' play_events for the given day offsets (0 = today)."""
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).date()
    for offset in offsets:
        d = today - _dt.timedelta(days=offset)
        conn.execute(
            "INSERT INTO play_events (user_id, question_id, game_mode, score, "
            "identity_key, period, created_at) VALUES (?, ?, 'daily', 4000, ?, ?, ?)",
            (uid, f"seed-{offset}", uid, d.isoformat(), f"{d.isoformat()} 12:00:00"),
        )
    conn.commit()


def test_streak_freeze_forgives_a_single_missed_day(client):
    from app import auth, db

    uid = auth.create_user(db.connect(), "frozen", "password1")["id"]
    conn = db.connect()
    try:
        # Played today and the day before yesterday, but MISSED yesterday (offset 1).
        # The streak freeze bridges that single gap instead of resetting to 1.
        _seed_daily_days(conn, uid, [0, 2, 3])
        assert auth.daily_streak(conn, uid) == 3  # today + (skip) + day2 + day3

        # A SECOND gap is not forgiven: the run stops at the second missing day.
        _seed_daily_days(conn, uid, [5])  # day 4 also missing -> second gap
        assert auth.daily_streak(conn, uid) == 3
    finally:
        conn.close()


def test_streak_lapses_when_two_days_missed_in_a_row(client):
    from app import auth, db

    uid = auth.create_user(db.connect(), "lapsed", "password1")["id"]
    conn = db.connect()
    try:
        # Most recent daily play was two days ago: nothing today or yesterday.
        # A freeze protects a gap inside a live run, not a dead one -> 0.
        _seed_daily_days(conn, uid, [2, 3, 4])
        assert auth.daily_streak(conn, uid) == 0
    finally:
        conn.close()


def test_verify_returns_social_proof_insight(client):
    # One player answers perfectly, several others answer poorly: the perfect
    # guess should beat them, and the insight should reflect the sample.
    # Five guests each post a weak guess on the same (deterministic) daily question
    # so a sample exists before we measure the percentile.
    for i in range(5):
        q = client.get("/api/v1/quiz/daily").json()["questions"][0]["tracking_token"]
        client.post("/api/v1/quiz/verify",
                    json={"tracking_token": q, "guess": 0, "anon_id": f"guest-{i}"})

    # A signed-in player nails it; their verify response carries the comparison.
    token = client.post("/api/v1/auth/register",
                        json={"username": "sharp", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    q = client.get("/api/v1/quiz/daily").json()["questions"][0]["tracking_token"]
    actual = _answer_for(client, q)
    res = client.post("/api/v1/quiz/verify",
                      json={"tracking_token": q, "guess": actual}, headers=hdr).json()
    assert res["insight"] is not None
    assert res["insight"]["players_answered"] >= 5
    assert 0 <= res["insight"]["beat_percent"] <= 100


def _answer_for(client, token):
    """Reveal an answer without polluting a signed-in player's total (uses a guest)."""
    return client.post("/api/v1/quiz/verify",
                       json={"tracking_token": token, "guess": 0, "anon_id": "reveal-only"}
                       ).json()["actual"]


def test_daily_and_weekly_leaderboards_filter_by_window(client):
    import datetime as _dt
    from app import db

    token = client.post("/api/v1/auth/register",
                        json={"username": "windowed", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    quiz = client.get("/api/v1/quiz/daily").json()
    t = quiz["questions"][0]["tracking_token"]
    actual = _answer(client, t)
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": t, "guess": actual}, headers=hdr)  # 5000 today

    # An old event (40 days ago) counts all-time but not in the daily/weekly window.
    conn = db.connect()
    try:
        uid = conn.execute("SELECT id FROM users WHERE username='windowed'").fetchone()["id"]
        old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=40)
        conn.execute(
            "INSERT INTO play_events (user_id, question_id, game_mode, score, "
            "identity_key, period, created_at) VALUES (?, 'old-q', 'daily', 3000, ?, ?, ?)",
            (uid, uid, old.date().isoformat(), old.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()

    all_pts = client.get("/api/v1/leaderboard").json()["entries"][0]["lifetime_points"]
    day_pts = client.get("/api/v1/leaderboard?period=daily").json()["entries"][0]["lifetime_points"]
    assert all_pts == 8000   # 5000 today + 3000 old
    assert day_pts == 5000   # only today's


def test_constructors_championship_buckets_points_by_team(client):
    # Two Ferrari fans and one McLaren fan; the team board sums each faction.
    for name, team in [("tifosi1", "ferrari"), ("tifosi2", "ferrari"), ("papaya1", "mclaren")]:
        tok = client.post("/api/v1/auth/register",
                          json={"username": name, "password": "password1",
                                "selected_team": team}).json()["token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        quiz = client.get("/api/v1/quiz/daily").json()
        t = quiz["questions"][0]["tracking_token"]
        actual = _answer(client, t)
        client.post("/api/v1/quiz/verify",
                    json={"tracking_token": t, "guess": actual}, headers=hdr)

    board = client.get("/api/v1/leaderboard/teams").json()["entries"]
    by_team = {e["team"]: e for e in board}
    assert by_team["ferrari"]["points"] == 10000   # two members * 5000
    assert by_team["ferrari"]["members"] == 2
    assert by_team["mclaren"]["points"] == 5000
    assert board[0]["team"] == "ferrari"           # ranked by total points


def test_team_overview_lists_every_team_with_headcounts(client):
    # Two Ferrari fans (one scores) and one McLaren fan who hasn't played.
    for name, team in [("tic", "ferrari"), ("tac", "ferrari"), ("toe", "mclaren")]:
        client.post("/api/v1/auth/register",
                    json={"username": name, "password": "password1", "selected_team": team})
    tok = client.post("/api/v1/auth/login",
                      json={"username": "tic", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    quiz = client.get("/api/v1/quiz/daily").json()
    t = quiz["questions"][0]["tracking_token"]
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": t, "guess": _answer(client, t)}, headers=hdr)

    body = client.get("/api/v1/teams/overview").json()
    by_team = {e["team"]: e for e in body["teams"]}
    # EVERY known team appears, even ones nobody picked (unlike the championship board).
    assert len(body["teams"]) == len(auth.TEAMS)
    assert body["total_players"] == 3
    assert by_team["ferrari"]["members"] == 2
    assert by_team["ferrari"]["points"] == 5000
    assert by_team["mclaren"]["members"] == 1
    assert by_team["mclaren"]["points"] == 0          # joined but hasn't scored
    assert by_team["williams"]["members"] == 0        # empty team still listed
    # Ranked by points: Ferrari (the only scorer) leads.
    assert body["teams"][0]["team"] == "ferrari"
    assert body["teams"][0]["rank"] == 1


def test_set_team_persists_server_side(client):
    tok = client.post("/api/v1/auth/register",
                      json={"username": "switcher", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/v1/profile/team", json={"selected_team": "williams"}, headers=hdr)
    assert r.status_code == 200
    assert r.json()["selected_team"] == "williams"
    # Survives a fresh fetch (it's in the DB, not just the response).
    assert client.get("/api/v1/auth/me", headers=hdr).json()["selected_team"] == "williams"
    # An unknown team is normalized to the default rather than rejected.
    r2 = client.post("/api/v1/profile/team", json={"selected_team": "lego"}, headers=hdr)
    assert r2.json()["selected_team"] == "mclaren"


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
