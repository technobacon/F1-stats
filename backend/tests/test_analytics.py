"""First-party analytics: ingestion validation, the token gate, and the funnel /
retention aggregation. Analytics is untrusted client telemetry — these tests pin
that it's bounded on the way in and never exposed without the token."""

import pytest
from fastapi.testclient import TestClient

from app import db, seed
from app.main import app

TOKEN = "test-analytics-token"


@pytest.fixture(autouse=True)
def seeded_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    seed.seed_all(test_db)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def collect(client, events, anon="visitor-1", session="sess-1"):
    return client.post("/api/v1/analytics/collect",
                       json={"anon_id": anon, "session_id": session, "events": events})


# ── Ingestion ────────────────────────────────────────────────────────────────
def test_collect_stores_allowlisted_and_drops_unknown(client):
    r = collect(client, [
        {"event": "app_open", "props": {"signed_in": False}},
        {"event": "quiz_start", "props": {"mode": "daily"}},
        {"event": "totally_made_up", "props": {}},   # not allow-listed -> dropped
        {"event": "share"},
    ])
    assert r.status_code == 200
    assert r.json()["stored"] == 3   # the bogus one is not stored


def test_collect_caps_batch_size(client):
    big = [{"event": "arcade_play"} for _ in range(200)]
    assert collect(client, big).json()["stored"] == 50   # MAX_EVENTS_PER_BATCH


def test_collect_is_best_effort_and_never_errors(client):
    # Garbage shapes must not 500 — telemetry can't break the app.
    r = client.post("/api/v1/analytics/collect",
                    json={"anon_id": "x", "events": [{"event": "view", "props": "not-a-dict"}]})
    assert r.status_code == 200


# ── Token gate ───────────────────────────────────────────────────────────────
def test_summary_disabled_without_token_env(client, monkeypatch):
    monkeypatch.delenv("F1_ANALYTICS_TOKEN", raising=False)
    assert client.get("/api/v1/analytics/summary").status_code == 404


def test_summary_requires_matching_token(client, monkeypatch):
    monkeypatch.setenv("F1_ANALYTICS_TOKEN", TOKEN)
    assert client.get("/api/v1/analytics/summary").status_code == 401
    assert client.get("/api/v1/analytics/summary",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/v1/analytics/summary",
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200


# ── Aggregation ──────────────────────────────────────────────────────────────
def test_summary_funnel_and_modes(client, monkeypatch):
    monkeypatch.setenv("F1_ANALYTICS_TOKEN", TOKEN)
    # Two visitors open; one plays daily and completes; the other plays race_week.
    collect(client, [{"event": "app_open"}, {"event": "quiz_start", "props": {"mode": "daily"}},
                     {"event": "quiz_complete", "props": {"mode": "daily"}},
                     {"event": "share"}], anon="v1", session="s1")
    collect(client, [{"event": "app_open"}, {"event": "quiz_start", "props": {"mode": "race_week"}},
                     {"event": "arcade_play"}], anon="v2", session="s2")

    d = client.get("/api/v1/analytics/summary",
                   headers={"Authorization": f"Bearer {TOKEN}"}).json()
    f = d["funnel"]
    assert f["app_open"] == 2 and f["quiz_start"] == 2 and f["quiz_complete"] == 1 and f["share"] == 1
    assert f["completion_rate"] == 0.5            # 1 complete / 2 starts
    assert d["modes"]["daily"] == 1 and d["modes"]["race_week"] == 1
    assert d["modes"]["arcade"] == 1
    assert d["dau"] == 2                           # two distinct visitors today


def test_summary_signed_in_events_attach_to_user(client, monkeypatch):
    monkeypatch.setenv("F1_ANALYTICS_TOKEN", TOKEN)
    token = client.post("/api/v1/auth/register",
                        json={"username": "analytic1", "password": "password1"}).json()["token"]
    # Posting with a bearer token attributes the event to the user (best-effort).
    r = client.post("/api/v1/analytics/collect",
                    json={"anon_id": "va", "session_id": "sa", "events": [{"event": "app_open"}]},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.json()["stored"] == 1
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT user_id FROM analytics_events WHERE event='app_open' AND anon_id='va'"
        ).fetchone()
        assert row["user_id"] is not None   # resolved from the bearer token
    finally:
        conn.close()
