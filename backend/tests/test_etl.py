"""ETL ingestion tests (Pipeline §1) — fully offline.

A fake transport returns recorded Ergast/Jolpica JSON envelopes, so we exercise
the real normalizers, pagination, disk cache, rate limiter, and the weekly
freshness gate without any network. The live fetch (httpx) is never called.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app import db, etl, seed


# --- Fake API ----------------------------------------------------------------

def _mr(extra: dict, total: int = 0) -> dict:
    return {"MRData": {"total": str(total), **extra}}


def make_fake_api():
    """Return (fetch, calls): a fetch(url)->dict over a tiny but realistic
    two-driver, one-race dataset spanning the ingest range."""
    drivers = _mr({"DriverTable": {"Drivers": [
        {"driverId": "alpha", "givenName": "Anna", "familyName": "Alpha", "nationality": "Italian"},
        {"driverId": "bravo", "givenName": "Ben", "familyName": "Bravo", "nationality": "British"},
    ]}}, total=2)
    constructors = _mr({"ConstructorTable": {"Constructors": [
        {"constructorId": "red", "name": "Red Team", "nationality": "Austrian"},
        {"constructorId": "blue", "name": "Blue Team", "nationality": "British"},
    ]}}, total=2)
    circuits = _mr({"CircuitTable": {"Circuits": [
        {"circuitId": "monza", "circuitName": "Monza", "Location": {"country": "Italy"}},
    ]}}, total=1)

    def season_results(year):
        return _mr({"RaceTable": {"Races": [{
            "season": str(year), "round": "1",
            "Circuit": {"circuitId": "monza"},
            "Results": [
                {"positionText": "1", "grid": "2", "points": "25",
                 "FastestLap": {"rank": "1"},
                 "Driver": {"driverId": "alpha"}, "Constructor": {"constructorId": "red"}},
                {"positionText": "R", "grid": "1", "points": "0",  # DNF -> position NULL
                 "Driver": {"driverId": "bravo"}, "Constructor": {"constructorId": "blue"}},
            ],
        }]}}, total=2)

    def season_qualifying(year):
        return _mr({"RaceTable": {"Races": [{
            "season": str(year), "round": "1",
            "QualifyingResults": [
                {"position": "1", "Driver": {"driverId": "bravo"},
                 "Constructor": {"constructorId": "blue"}},
                {"position": "2", "Driver": {"driverId": "alpha"},
                 "Constructor": {"constructorId": "red"}},
            ],
        }]}}, total=2)

    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        if "/drivers.json" in url:
            return drivers
        if "/constructors.json" in url:
            return constructors
        if "/circuits.json" in url:
            return circuits
        if "/results.json" in url:
            year = int(url.split("/f1/")[1].split("/")[0])
            return season_results(year)
        if "/qualifying.json" in url:
            year = int(url.split("/f1/")[1].split("/")[0])
            return season_qualifying(year)
        raise AssertionError(f"unexpected url: {url}")

    return fetch, calls


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "etl.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    fetch, calls = make_fake_api()
    cl = etl.JolpicaClient(
        cache_dir=tmp_path / "cache",
        fetch=fetch,
        limiter=etl.RateLimiter(per_second=1e9, per_hour=10**9),  # no real waits
    )
    cl._calls = calls  # type: ignore[attr-defined]
    return cl


# --- Normalizers -------------------------------------------------------------

def test_norm_race_results_maps_dnf_and_fastest_lap():
    fetch, _ = make_fake_api()
    mr = fetch("https://x/f1/2020/results.json?limit=100&offset=0")["MRData"]
    rows, total = etl._norm_race_results(mr)
    assert total == 2
    by_driver = {r[0]: r for r in rows}
    # alpha: win (pos 1), grid 2, fastest lap flagged, 25 pts
    assert by_driver["alpha"][5] == 1 and by_driver["alpha"][7] == 1
    # bravo: positionText 'R' -> NULL finish (DNF), 0 pts
    assert by_driver["bravo"][5] is None


def test_norm_qualifying_uses_quali_position():
    fetch, _ = make_fake_api()
    mr = fetch("https://x/f1/2020/qualifying.json?limit=100&offset=0")["MRData"]
    rows, _ = etl._norm_qualifying(mr)
    pole = {r[0]: r[4] for r in rows}
    assert pole["bravo"] == 1 and pole["alpha"] == 2  # pole is quali P1, not grid


# --- Caching -----------------------------------------------------------------

def test_disk_cache_avoids_refetch(client):
    a = client.get("drivers", limit=100, offset=0)
    n_after_first = len(client._calls)
    b = client.get("drivers", limit=100, offset=0)
    assert a == b
    assert len(client._calls) == n_after_first  # second call served from disk
    assert client.stats["cache_hits"] == 1


def test_stale_cache_is_refetched(tmp_path):
    fetch, calls = make_fake_api()
    cl = etl.JolpicaClient(cache_dir=tmp_path / "cache", fetch=fetch,
                           limiter=etl.RateLimiter(1e9, 10**9), cache_ttl_days=7)
    cl.get("drivers", limit=100, offset=0)
    # Age the cache file past the TTL.
    import os
    path = cl._cache_path(f"{cl.base_url}/drivers.json?limit=100&offset=0")
    old = (datetime.now() - timedelta(days=8)).timestamp()
    os.utime(path, (old, old))
    cl.get("drivers", limit=100, offset=0)
    assert len(calls) == 2  # refetched because stale


# --- Rate limiter ------------------------------------------------------------

def test_rate_limiter_enforces_burst():
    """A fake clock that doesn't advance forces the burst bucket to empty, so the
    limiter must sleep; we assert it requests a positive wait."""
    waits = []
    now = [0.0]
    rl = etl.RateLimiter(per_second=2, per_hour=10**9,
                         clock=lambda: now[0], sleep=lambda w: (waits.append(w), now.__setitem__(0, now[0] + w)))
    for _ in range(5):
        rl.acquire()
    assert any(w > 0 for w in waits)  # had to throttle once burst was spent


def test_rate_limiter_enforces_hourly():
    waits = []
    now = [0.0]

    def sleep(w):
        waits.append(w)
        now[0] += w

    rl = etl.RateLimiter(per_second=1e9, per_hour=3, clock=lambda: now[0], sleep=sleep)
    for _ in range(4):
        rl.acquire()
        now[0] += 0.001  # tiny real-time progression, well under an hour
    # The 4th request exceeds the hourly cap of 3 and must wait ~an hour.
    assert any(w > 3000 for w in waits)


# --- Full ingest + weekly gate ----------------------------------------------

def test_run_etl_populates_staging(conn, client):
    status = etl.run_etl(conn, client=client, start_year=2018, end_year=2020)
    assert status["status"] == "refreshed"
    c = status["counts"]
    assert c["drivers"] == 2 and c["constructors"] == 2 and c["circuits"] == 1
    # 3 seasons x (2 race rows, 2 quali rows)
    assert c["race_results"] == 6 and c["qualifying_results"] == 6
    # active span derived from the race log
    span = conn.execute(
        "SELECT active_from, active_to FROM staging_drivers WHERE driver_id='alpha'"
    ).fetchone()
    assert span["active_from"] == 2018 and span["active_to"] == 2020


def test_refresh_if_stale_skips_when_fresh(conn, client):
    etl.run_etl(conn, client=client, start_year=2020, end_year=2020)
    calls_before = len(client._calls)
    status = etl.refresh_if_stale(conn, client=client)
    assert status["skipped"] is True and status["status"] == "fresh"
    assert len(client._calls) == calls_before  # no network


def test_refresh_if_stale_refetches_when_old(conn, client):
    etl.run_etl(conn, client=client, start_year=2020, end_year=2020)
    # Backdate the recorded refresh beyond the weekly window.
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    conn.execute("UPDATE etl_metadata SET value=? WHERE key='last_refresh'", (old,))
    conn.commit()
    assert etl.is_stale(conn) is True
    status = etl.refresh_if_stale(conn, client=client, start_year=2020, end_year=2020)
    assert status["skipped"] is False


def test_merge_sprint_points_folds_into_race_points(conn):
    """Sprint points are added onto the same weekend's GP points (so points
    totals include them) without creating phantom race entries."""
    conn.execute(
        "INSERT INTO staging_race_results "
        "(driver_id, constructor_id, year, round, circuit_id, position, grid, fastest_lap, points) "
        "VALUES ('alpha','team',2023,1,'cir',2,3,0,18.0)"
    )
    rows_before = conn.execute("SELECT COUNT(*) FROM staging_race_results").fetchone()[0]
    # alpha scored 8 sprint points round 1; bravo scored sprint points but has no
    # GP classification that weekend -> a non-classified carrier row is inserted.
    merged = etl._merge_sprint_points(conn, [
        ("alpha", "team", 2023, 1, "cir", 8.0),
        ("bravo", "team", 2023, 1, "cir", 6.0),
        ("alpha", "team", 2023, 1, "cir", 0.0),  # zero -> skipped, no-op
    ])
    assert merged == 2
    alpha = conn.execute(
        "SELECT points, grid, position FROM staging_race_results "
        "WHERE driver_id='alpha' AND year=2023 AND round=1"
    ).fetchone()
    assert alpha["points"] == 26.0 and alpha["grid"] == 3  # 18 + 8, grid untouched
    bravo = conn.execute(
        "SELECT points, position, grid FROM staging_race_results WHERE driver_id='bravo'"
    ).fetchone()
    assert bravo["points"] == 6.0 and bravo["position"] is None and bravo["grid"] is None
    # exactly one carrier row added (bravo); alpha's points were merged in place
    assert conn.execute("SELECT COUNT(*) FROM staging_race_results").fetchone()[0] == rows_before + 1


def test_validation_runs_on_real_etl_data(conn, client):
    """End-to-end: real-style ETL -> data-driven generation -> validation ->
    production. Every committed answer is recomputed from staging."""
    etl.run_etl(conn, client=client, start_year=2018, end_year=2020)
    drivers = seed.load_entities_from_staging(conn)
    assert {d.driver_id for d in drivers} == {"alpha", "bravo"}
    summary = seed.run_validation_pipeline(conn, drivers=drivers, planted=False)
    assert summary["committed"] > 0 and summary["rejected"] == 0
    # alpha won the single race each of 3 seasons -> 3 career wins, verified.
    row = conn.execute(
        "SELECT verified_answer FROM production_trivia_questions "
        "WHERE question_string LIKE 'How many career race wins does Anna Alpha%'"
    ).fetchone()
    assert row is not None and row["verified_answer"] == 3
