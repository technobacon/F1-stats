"""Account creation, sessions, and the server-side saving / trust boundary.
Verifies that totals are rebuilt from server-scored events and can never be set
by the client."""

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
                    json={"username": "zezima", "password": "hunter2pass"})
    assert r.status_code == 200
    token = r.json()["token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "zezima"


def test_duplicate_username_rejected_case_insensitively(client):
    client.post("/api/v1/auth/register", json={"username": "Woox", "password": "inferno123"})
    r = client.post("/api/v1/auth/register", json={"username": "woox", "password": "other1234"})
    assert r.status_code == 400


@pytest.mark.parametrize("username,password", [
    ("ab", "longenough1"),     # username too short
    ("ok_name", "short"),      # password too short
    ("bad name!", "longenough1"),  # illegal characters
])
def test_register_validation(client, username, password):
    r = client.post("/api/v1/auth/register", json={"username": username, "password": password})
    assert r.status_code == 400


@pytest.mark.parametrize("username", [
    "shitlord", "Sh1tLord", "f.u.c.k.er", "ass", "ass_hat", "xX_fuck_Xx", "n1gger",
])
def test_register_rejects_profane_usernames(client, username):
    r = client.post("/api/v1/auth/register", json={"username": username, "password": "longenough1"})
    assert r.status_code == 400


@pytest.mark.parametrize("username", [
    "class", "passenger", "Sarachnis_fan", "WhipCollector", "competition", "glasshouse",
])
def test_register_allows_innocent_usernames(client, username):
    # The filter must not trip on clean words that merely contain a rude substring.
    r = client.post("/api/v1/auth/register", json={"username": username, "password": "longenough1"})
    assert r.status_code == 200


def test_login_wrong_password_401(client):
    client.post("/api/v1/auth/register", json={"username": "b0aty", "password": "fishing1234"})
    r = client.post("/api/v1/auth/login", json={"username": "b0aty", "password": "nope"})
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
    client.post("/api/v1/auth/register", json={"username": "lockme", "password": "correctpass1"})
    # 8 wrong attempts are rejected as 401...
    for _ in range(8):
        assert client.post("/api/v1/auth/login",
                           json={"username": "lockme", "password": "wrong"}).status_code == 401
    # ...the next attempt is rate-limited (429), even with the correct password.
    r = client.post("/api/v1/auth/login", json={"username": "lockme", "password": "correctpass1"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_successful_login_resets_the_failure_counter(client):
    client.post("/api/v1/auth/register", json={"username": "swampy", "password": "correctpass1"})
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"username": "swampy", "password": "wrong"})
    # A correct login clears the counter, so the user isn't locked out afterwards.
    assert client.post("/api/v1/auth/login",
                       json={"username": "swampy", "password": "correctpass1"}).status_code == 200
    for _ in range(8):
        assert client.post("/api/v1/auth/login",
                           json={"username": "swampy", "password": "wrong"}).status_code == 401


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
                        json={"username": "logmeout", "password": "runecrafting1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 200
    client.post("/api/v1/auth/logout", headers=hdr)
    assert client.get("/api/v1/auth/me", headers=hdr).status_code == 401


# ── Server-side saving ───────────────────────────────────────────────────────
def test_scored_answers_persist_to_account(client):
    token = client.post("/api/v1/auth/register",
                        json={"username": "gnomechild", "password": "monkeys123"}).json()["token"]
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
                    json={"username": "fan2026", "password": "buyinggf10k", "anon_id": "device-xyz"})
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
    """HiScores integrity: the daily set is deterministic, so re-running it must
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
        # The streak freeze (a sip of Saradomin brew) bridges that single gap
        # instead of resetting to 1.
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


def test_god_wars_buckets_points_by_faction(client):
    # Two Zamorakians and one Saradominist; the god board sums each faction.
    for name, god in [("zammy1", "zamorak"), ("zammy2", "zamorak"), ("sara1", "saradomin")]:
        tok = client.post("/api/v1/auth/register",
                          json={"username": name, "password": "password1",
                                "selected_god": god}).json()["token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        quiz = client.get("/api/v1/quiz/daily").json()
        t = quiz["questions"][0]["tracking_token"]
        actual = _answer(client, t)
        client.post("/api/v1/quiz/verify",
                    json={"tracking_token": t, "guess": actual}, headers=hdr)

    board = client.get("/api/v1/leaderboard/gods").json()["entries"]
    by_god = {e["god"]: e for e in board}
    assert by_god["zamorak"]["points"] == 10000   # two members * 5000
    assert by_god["zamorak"]["members"] == 2
    assert by_god["saradomin"]["points"] == 5000
    assert board[0]["god"] == "zamorak"           # ranked by total points


def test_god_overview_lists_every_god_with_headcounts(client):
    # Two Zamorakians (one scores) and one Guthixian who hasn't played.
    for name, god in [("tic", "zamorak"), ("tac", "zamorak"), ("toe", "guthix")]:
        client.post("/api/v1/auth/register",
                    json={"username": name, "password": "password1", "selected_god": god})
    tok = client.post("/api/v1/auth/login",
                      json={"username": "tic", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    quiz = client.get("/api/v1/quiz/daily").json()
    t = quiz["questions"][0]["tracking_token"]
    client.post("/api/v1/quiz/verify",
                json={"tracking_token": t, "guess": _answer(client, t)}, headers=hdr)

    body = client.get("/api/v1/gods/overview").json()
    by_god = {e["god"]: e for e in body["gods"]}
    # EVERY known god appears, even ones nobody picked (unlike the championship board).
    assert len(body["gods"]) == len(auth.GODS)
    assert body["total_players"] == 3
    assert by_god["zamorak"]["members"] == 2
    assert by_god["zamorak"]["points"] == 5000
    assert by_god["guthix"]["members"] == 1
    assert by_god["guthix"]["points"] == 0            # pledged but hasn't scored
    assert by_god["zaros"]["members"] == 0            # empty faction still listed
    # Ranked by points: Zamorak (the only scorer) leads.
    assert body["gods"][0]["god"] == "zamorak"
    assert body["gods"][0]["rank"] == 1


def test_set_god_persists_server_side(client):
    tok = client.post("/api/v1/auth/register",
                      json={"username": "switcher", "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/v1/profile/god", json={"selected_god": "bandos"}, headers=hdr)
    assert r.status_code == 200
    assert r.json()["selected_god"] == "bandos"
    # Survives a fresh fetch (it's in the DB, not just the response).
    assert client.get("/api/v1/auth/me", headers=hdr).json()["selected_god"] == "bandos"
    # An unknown god is normalized to the default rather than rejected.
    r2 = client.post("/api/v1/profile/god", json={"selected_god": "xau-tak"}, headers=hdr)
    assert r2.json()["selected_god"] == "saradomin"


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


# ── Personal standing: rank, faction stake, play history ─────────────────────
def _register(client, username, god=None):
    token = client.post("/api/v1/auth/register",
                        json={"username": username, "password": "password1"}).json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    if god:
        client.post("/api/v1/profile/god", json={"selected_god": god}, headers=hdr)
    return hdr


def _score_first_n(client, hdr, n):
    """Score the first n Daily questions perfectly for the user behind `hdr`."""
    quiz = client.get("/api/v1/quiz/daily").json()
    for q in quiz["questions"][:n]:
        tok = q["tracking_token"]
        actual = _answer(client, tok)  # anonymous reveal (orphan, not counted)
        client.post("/api/v1/quiz/verify",
                    json={"tracking_token": tok, "guess": actual}, headers=hdr)


def test_leaderboard_me_rank_and_percentile(client):
    a = _register(client, "ace")
    b = _register(client, "back")
    _score_first_n(client, a, 3)   # 15000 pts
    _score_first_n(client, b, 1)   # 5000 pts

    ra = client.get("/api/v1/leaderboard/me", headers=a).json()
    rb = client.get("/api/v1/leaderboard/me", headers=b).json()
    assert (ra["rank"], ra["points"], ra["total_ranked"]) == (1, 15000, 2)
    assert (rb["rank"], rb["points"]) == (2, 5000)
    assert ra["percentile"] == 100 and rb["percentile"] == 0


def test_leaderboard_me_unranked_when_no_score(client):
    hdr = _register(client, "rookie")
    r = client.get("/api/v1/leaderboard/me", headers=hdr).json()
    assert r["rank"] == 0 and r["points"] == 0  # hasn't scored this window


def test_leaderboard_god_detail_and_within_faction_board(client):
    a = _register(client, "zammyboy1", god="zamorak")
    b = _register(client, "zammyboy2", god="zamorak")
    _score_first_n(client, a, 2)   # 10000
    _score_first_n(client, b, 1)   # 5000

    da = client.get("/api/v1/leaderboard/god", headers=a).json()
    assert da["god"] == "zamorak"
    assert da["god_rank"] == 1 and da["god_points"] == 15000 and da["members"] == 2
    assert da["your_points"] == 10000 and da["your_rank_in_god"] == 1
    assert [m["username"] for m in da["leaders"]] == ["zammyboy1", "zammyboy2"]

    db_ = client.get("/api/v1/leaderboard/god", headers=b).json()
    assert db_["your_rank_in_god"] == 2 and db_["your_points"] == 5000


def test_play_history_buckets_daily_play(client):
    hdr = _register(client, "streaker")
    _score_first_n(client, hdr, 3)
    hist = client.get("/api/v1/user/play-history", headers=hdr).json()["days"]
    assert len(hist) == 1                       # one calendar day of play
    assert hist[0]["questions"] == 3
    assert hist[0]["points"] == 15000


def test_personal_endpoints_require_auth(client):
    for path in ("/api/v1/leaderboard/me", "/api/v1/leaderboard/god",
                 "/api/v1/user/play-history"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401
