"""Real Jolpica F1 ETL ingestion engine (PRD §3, Pipeline §1).

Pulls historical Formula 1 results from the **Jolpica F1 API**
(`https://api.jolpi.ca/ergast/f1/...`, an Ergast-compatible mirror) into the
`staging_*` tables, so the validation + question pipeline runs on real data
instead of the synthetic in-code seed.

Three properties the docs (and the user's request) require, implemented here:

  1. **Rate-limited ingestion** (Pipeline §1.1): a token-bucket controller that
     respects BOTH a burst limit (requests/second) and a sustained hourly
     ceiling, throttling to whichever is more restrictive. A `429 Too Many
     Requests` halts the pipeline for `RETRY_AFTER_429` seconds before retrying.
     ⚠️ Confirm Jolpica's *current* published limits before a large run — they
     change; the defaults here are deliberately conservative.

  2. **Persistent caching** ("keep the data so we don't always reach back"):
     every raw API page is cached on disk keyed by URL, and the processed rows
     live in the SQLite staging tables. A re-run reuses both. Cache entries
     older than the weekly interval are treated as stale and re-fetched.

  3. **Weekly refresh cadence** (the data updates once a week):
     `refresh_if_stale()` is a no-op when staging was last refreshed within
     `REFRESH_INTERVAL_DAYS`.

Network: the live host (`api.jolpi.ca`) must be reachable. Always use HTTPS
(Pipeline §1.1). In sandboxed environments where outbound network is blocked,
the fetch raises `NetworkError`; the seed orchestrator falls back to the
synthetic seed (see `seed.refresh`).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

# --- Configuration (env-overridable) ----------------------------------------

BASE_URL = os.environ.get("F1_ETL_BASE_URL", "https://api.jolpi.ca/ergast/f1")

# Year span to ingest. Defaults to the turbo-hybrid era onward (rich, complete
# qualifying + fastest-lap data). Set F1_ETL_START_YEAR=1950 to pull everything.
START_YEAR = int(os.environ.get("F1_ETL_START_YEAR", "2004"))
END_YEAR = int(os.environ.get("F1_ETL_END_YEAR", str(datetime.now(timezone.utc).year)))

# Weekly cadence: skip a refresh if staging was updated within this many days.
REFRESH_INTERVAL_DAYS = float(os.environ.get("F1_ETL_REFRESH_DAYS", "7"))

# Rate limits (Pipeline §1.1). Conservative defaults for unauthenticated traffic
# — the controller honors BOTH simultaneously, throttling to whichever bites
# first. VERIFY against Jolpica's current published limits before launch.
RATE_PER_SECOND = float(os.environ.get("F1_ETL_RPS", "3"))
RATE_PER_HOUR = int(os.environ.get("F1_ETL_RPH", "450"))
RETRY_AFTER_429 = float(os.environ.get("F1_ETL_RETRY_429", "300"))  # seconds

# Jolpica caps page size at 100 rows per request.
PAGE_SIZE = int(os.environ.get("F1_ETL_PAGE_SIZE", "100"))

# Sprint races (which award championship points) began in 2021; no point hitting
# the /sprint endpoint for earlier seasons.
FIRST_SPRINT_YEAR = int(os.environ.get("F1_ETL_FIRST_SPRINT_YEAR", "2021"))

_DEFAULT_CACHE_DIR = Path(
    os.environ.get("F1_ETL_CACHE_DIR", Path(__file__).resolve().parent.parent / ".http_cache")
)


class NetworkError(RuntimeError):
    """Raised when the live API cannot be reached (e.g. blocked allowlist)."""


# --- Rate limiter ------------------------------------------------------------

class RateLimiter:
    """Token bucket honoring a burst (per-second) AND a sustained (per-hour)
    limit at once, throttling to whichever is more restrictive (Pipeline §1.1).

    `clock`/`sleep` are injectable so the limiter is unit-testable without real
    wall-clock waits.
    """

    def __init__(
        self,
        per_second: float = RATE_PER_SECOND,
        per_hour: int = RATE_PER_HOUR,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.per_second = float(per_second)
        self.per_hour = int(per_hour)
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(per_second)   # burst bucket, starts full
        self._last = clock()
        self._hour_log: deque[float] = deque()  # request times in the last hour

    def acquire(self) -> None:
        """Block (cooperatively, via the injected sleep) until one request may
        proceed under both limits, then record it."""
        while True:
            now = self._clock()
            # Refill the burst bucket.
            self._tokens = min(
                self.per_second, self._tokens + (now - self._last) * self.per_second
            )
            self._last = now
            # Expire hourly-window entries older than 3600s.
            while self._hour_log and now - self._hour_log[0] >= 3600:
                self._hour_log.popleft()

            wait = 0.0
            if self._tokens < 1.0:
                wait = max(wait, (1.0 - self._tokens) / self.per_second)
            if len(self._hour_log) >= self.per_hour:
                wait = max(wait, 3600 - (now - self._hour_log[0]))

            if wait <= 0:
                self._tokens -= 1.0
                self._hour_log.append(now)
                return
            self._sleep(wait)


# --- HTTP client with on-disk cache -----------------------------------------

class JolpicaClient:
    """Paginating, rate-limited, disk-cached reader for the Jolpica/Ergast API.

    `fetch` is the low-level transport `fetch(url) -> dict`; it defaults to an
    httpx-based fetcher with 429 backoff, but tests inject a fake so no network
    is required.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        cache_dir: Path | str | None = None,
        limiter: RateLimiter | None = None,
        fetch: Callable[[str], dict] | None = None,
        page_size: int = PAGE_SIZE,
        cache_ttl_days: float = REFRESH_INTERVAL_DAYS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE_DIR
        self.limiter = limiter or RateLimiter()
        self._fetch = fetch or self._http_fetch
        self.page_size = page_size
        self.cache_ttl = cache_ttl_days * 86400
        self.stats = {"requests": 0, "cache_hits": 0}

    # -- caching --
    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _cache_read(self, url: str) -> dict | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        if self.cache_ttl >= 0 and (time.time() - path.stat().st_mtime) > self.cache_ttl:
            return None  # stale: weekly cadence applies to the raw cache too
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_write(self, url: str, payload: dict) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(json.dumps(payload))

    # -- transport --
    def _http_fetch(self, url: str) -> dict:
        """Default transport: httpx GET with 429 backoff. Imported lazily so the
        module loads (and unit tests run) without httpx/network."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx is a declared dep
            raise NetworkError(f"httpx not available: {exc}") from exc

        attempts = 0
        while True:
            self.limiter.acquire()
            try:
                resp = httpx.get(url, timeout=30.0, headers={"User-Agent": "F1-StatGuesser-ETL"})
            except httpx.HTTPError as exc:
                raise NetworkError(f"request failed for {url}: {exc}") from exc
            if resp.status_code == 429:
                # Sliding-window violation: halt this many seconds, then retry.
                attempts += 1
                if attempts > 5:
                    raise NetworkError(f"persistent 429 from {url}")
                time.sleep(RETRY_AFTER_429)
                continue
            if resp.status_code >= 400:
                raise NetworkError(f"HTTP {resp.status_code} for {url}")
            try:
                return resp.json()
            except ValueError as exc:
                raise NetworkError(f"non-JSON response from {url}: {exc}") from exc

    def get(self, path: str, limit: int | None = None, offset: int = 0) -> dict:
        """Fetch one page (cached). `path` is relative to base_url, e.g.
        '2023/results'. Returns the parsed `MRData` envelope."""
        page = limit if limit is not None else self.page_size
        url = f"{self.base_url}/{path.lstrip('/')}.json?limit={page}&offset={offset}"

        cached = self._cache_read(url)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached["MRData"] if "MRData" in cached else cached
        payload = self._fetch(url)
        self.stats["requests"] += 1
        self._cache_write(url, payload)
        return payload["MRData"] if "MRData" in payload else payload

    def paginate(self, path: str, extract: Callable[[dict], list]) -> Iterator[dict]:
        """Yield every item across all pages for `path`. `extract` pulls the list
        of interest out of an `MRData` envelope. Stops once `total` is reached."""
        offset = 0
        while True:
            mr = self.get(path, limit=self.page_size, offset=offset)
            items = extract(mr)
            for item in items:
                yield item
            total = int(mr.get("total", 0))
            offset += self.page_size
            if offset >= total or not items:
                return


# --- Normalizers: nested Ergast JSON -> flat staging rows --------------------

def _digit_or_none(text: str | None) -> int | None:
    """Ergast positionText is numeric only for a classified finish; 'R'/'D'/'W'
    etc. mark a retirement/DNS, which we store as NULL."""
    if text is not None and str(text).isdigit():
        return int(text)
    return None


def _norm_drivers(mr: dict) -> list[tuple]:
    rows = []
    for d in mr.get("DriverTable", {}).get("Drivers", []):
        name = " ".join(p for p in (d.get("givenName"), d.get("familyName")) if p)
        rows.append((d["driverId"], name, d.get("nationality"), None, None))
    return rows


def _norm_constructors(mr: dict) -> list[tuple]:
    return [
        (c["constructorId"], c.get("name", c["constructorId"]), c.get("nationality"))
        for c in mr.get("ConstructorTable", {}).get("Constructors", [])
    ]


def _norm_circuits(mr: dict) -> list[tuple]:
    rows = []
    for c in mr.get("CircuitTable", {}).get("Circuits", []):
        country = c.get("Location", {}).get("country")
        rows.append((c["circuitId"], c.get("circuitName", c["circuitId"]), country))
    return rows


def _norm_race_results(mr: dict) -> tuple[list[tuple], int]:
    """Flatten RaceTable -> staging_race_results rows. Returns (rows, total)."""
    rows = []
    for race in mr.get("RaceTable", {}).get("Races", []):
        year = int(race["season"])
        rnd = int(race["round"])
        circuit_id = race.get("Circuit", {}).get("circuitId")
        for r in race.get("Results", []):
            pos = _digit_or_none(r.get("positionText"))
            grid = int(r["grid"]) if str(r.get("grid", "")).lstrip("-").isdigit() else None
            fl = 1 if r.get("FastestLap", {}).get("rank") == "1" else 0
            try:
                points = float(r.get("points", 0) or 0)
            except (TypeError, ValueError):
                points = 0.0
            rows.append((
                r["Driver"]["driverId"],
                r.get("Constructor", {}).get("constructorId"),
                year, rnd, circuit_id, pos, grid, fl, points,
            ))
    return rows, int(mr.get("total", 0))


def _norm_sprint(mr: dict) -> tuple[list[tuple], int]:
    """Flatten RaceTable -> sprint point rows (driver, constructor, year, round,
    circuit, points). Sprint races (2021+) award championship points that we fold
    into the weekend's Grand Prix points. Returns (rows, total)."""
    rows = []
    for race in mr.get("RaceTable", {}).get("Races", []):
        year = int(race["season"])
        rnd = int(race["round"])
        circuit_id = race.get("Circuit", {}).get("circuitId")
        for r in race.get("SprintResults", []):
            try:
                points = float(r.get("points", 0) or 0)
            except (TypeError, ValueError):
                points = 0.0
            rows.append((
                r["Driver"]["driverId"],
                r.get("Constructor", {}).get("constructorId"),
                year, rnd, circuit_id, points,
            ))
    return rows, int(mr.get("total", 0))


def _norm_qualifying(mr: dict) -> tuple[list[tuple], int]:
    """Flatten RaceTable -> staging_qualifying_results rows. Returns (rows, total)."""
    rows = []
    for race in mr.get("RaceTable", {}).get("Races", []):
        year = int(race["season"])
        rnd = int(race["round"])
        for q in race.get("QualifyingResults", []):
            qpos = _digit_or_none(q.get("position"))
            if qpos is None:
                continue
            rows.append((
                q["Driver"]["driverId"],
                q.get("Constructor", {}).get("constructorId"),
                year, rnd, qpos,
            ))
    return rows, int(mr.get("total", 0))


# --- The ingest run ----------------------------------------------------------

def _clear_staging(conn: sqlite3.Connection) -> None:
    for table in (
        "staging_race_results", "staging_qualifying_results",
        "staging_drivers", "staging_constructors", "staging_circuits",
    ):
        conn.execute(f"DELETE FROM {table}")


def _paginate_results(client: JolpicaClient, path: str,
                      norm: Callable[[dict], tuple[list[tuple], int]]) -> Iterator[tuple]:
    """Paginate a results/qualifying endpoint, normalizing each page. These
    endpoints page over *result rows* (not races), so we drive pagination off
    the envelope's own `total`/`limit`/`offset`."""
    offset = 0
    while True:
        mr = client.get(path, limit=client.page_size, offset=offset)
        rows, total = norm(mr)
        yield from rows
        offset += client.page_size
        if offset >= total or total == 0:
            return


def _merge_sprint_points(conn: sqlite3.Connection, sprint_rows: list[tuple]) -> int:
    """Add each driver's sprint points onto their Grand Prix result row for the
    same weekend. If the driver scored sprint points but isn't classified in the
    GP (rare), insert a non-classified row carrying only the points so the total
    is still right without registering a phantom win/podium/start. Returns the
    number of point-scoring sprint entries merged."""
    merged = 0
    for did, cid, year, rnd, circuit_id, points in sprint_rows:
        if not points:
            continue
        cur = conn.execute(
            "UPDATE staging_race_results SET points = points + ? "
            "WHERE driver_id = ? AND year = ? AND round = ?",
            (points, did, year, rnd),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO staging_race_results "
                "(driver_id, constructor_id, year, round, circuit_id, position, grid, "
                " fastest_lap, points) VALUES (?,?,?,?,?,NULL,NULL,0,?)",
                (did, cid, year, rnd, circuit_id, points),
            )
        merged += 1
    return merged


def run_etl(
    conn: sqlite3.Connection,
    client: JolpicaClient | None = None,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
) -> dict:
    """Full weekly extract: clear staging and repopulate it from the live API.

    Reference tables (drivers/constructors/circuits) plus per-season race and
    qualifying results across [start_year, end_year]. Commits and records
    freshness metadata. Returns a status dict.
    """
    client = client or JolpicaClient()
    _clear_staging(conn)

    # Reference / lookup tables.
    conn.executemany(
        "INSERT OR REPLACE INTO staging_drivers "
        "(driver_id, full_name, nationality, active_from, active_to) VALUES (?,?,?,?,?)",
        client.paginate("drivers", _norm_drivers),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO staging_constructors "
        "(constructor_id, name, nationality) VALUES (?,?,?)",
        client.paginate("constructors", _norm_constructors),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO staging_circuits (circuit_id, name, country) VALUES (?,?,?)",
        client.paginate("circuits", _norm_circuits),
    )

    # Per-season results + qualifying.
    n_races = n_quali = n_sprint = 0
    for year in range(start_year, end_year + 1):
        race_rows = list(_paginate_results(client, f"{year}/results", _norm_race_results))
        conn.executemany(
            "INSERT INTO staging_race_results "
            "(driver_id, constructor_id, year, round, circuit_id, position, grid, "
            " fastest_lap, points) VALUES (?,?,?,?,?,?,?,?,?)",
            race_rows,
        )
        n_races += len(race_rows)

        quali_rows = list(_paginate_results(client, f"{year}/qualifying", _norm_qualifying))
        conn.executemany(
            "INSERT INTO staging_qualifying_results "
            "(driver_id, constructor_id, year, round, quali_position) VALUES (?,?,?,?,?)",
            quali_rows,
        )
        n_quali += len(quali_rows)

        # Sprint points (2021+) count toward the championship: fold them into the
        # weekend's Grand Prix points so every points aggregation includes them,
        # without adding race entries that would distort win/podium/start counts.
        if year >= FIRST_SPRINT_YEAR:
            sprint_rows = list(_paginate_results(client, f"{year}/sprint", _norm_sprint))
            n_sprint += _merge_sprint_points(conn, sprint_rows)

    # Derive each driver's active span from the race log (the drivers endpoint
    # doesn't carry it; build_arcade_pair needs it for era-overlap matching).
    conn.execute(
        "UPDATE staging_drivers SET "
        "  active_from = (SELECT MIN(year) FROM staging_race_results r "
        "                 WHERE r.driver_id = staging_drivers.driver_id), "
        "  active_to   = (SELECT MAX(year) FROM staging_race_results r "
        "                 WHERE r.driver_id = staging_drivers.driver_id)"
    )

    counts = {
        "drivers": conn.execute("SELECT COUNT(*) FROM staging_drivers").fetchone()[0],
        "constructors": conn.execute("SELECT COUNT(*) FROM staging_constructors").fetchone()[0],
        "circuits": conn.execute("SELECT COUNT(*) FROM staging_circuits").fetchone()[0],
        "race_results": n_races,
        "qualifying_results": n_quali,
        "sprint_point_rows": n_sprint,
    }
    _record_metadata(conn, counts, start_year, end_year, client.stats)
    conn.commit()
    return {
        "status": "refreshed",
        "skipped": False,
        "years": [start_year, end_year],
        "counts": counts,
        "http": dict(client.stats),
    }


def _record_metadata(conn, counts, start_year, end_year, http_stats) -> None:
    meta = {
        "last_refresh": datetime.now(timezone.utc).isoformat(),
        "source": "jolpica",
        "years": f"{start_year}-{end_year}",
        "row_counts": json.dumps(counts),
        "http_stats": json.dumps(http_stats),
    }
    conn.executemany(
        "INSERT OR REPLACE INTO etl_metadata (key, value) VALUES (?, ?)",
        list(meta.items()),
    )


def last_refresh(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT value FROM etl_metadata WHERE key='last_refresh'").fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def _staging_has_rows(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM staging_race_results").fetchone()[0] > 0


def is_stale(conn: sqlite3.Connection, max_age_days: float = REFRESH_INTERVAL_DAYS) -> bool:
    """True if staging is empty or older than the weekly interval."""
    if not _staging_has_rows(conn):
        return True
    ts = last_refresh(conn)
    if ts is None:
        return True
    age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
    return age_days >= max_age_days


def refresh_if_stale(
    conn: sqlite3.Connection,
    client: JolpicaClient | None = None,
    force: bool = False,
    max_age_days: float = REFRESH_INTERVAL_DAYS,
    start_year: int = START_YEAR,
    end_year: int = END_YEAR,
) -> dict:
    """Weekly-cadence entrypoint: run the ETL only if staging is stale (or
    `force`). When fresh, returns immediately without touching the network."""
    if not force and not is_stale(conn, max_age_days):
        ts = last_refresh(conn)
        return {
            "status": "fresh",
            "skipped": True,
            "last_refresh": ts.isoformat() if ts else None,
        }
    return run_etl(conn, client=client, start_year=start_year, end_year=end_year)


if __name__ == "__main__":  # pragma: no cover - manual CLI
    import argparse

    from . import db

    ap = argparse.ArgumentParser(description="Run the Jolpica F1 ETL into staging.")
    ap.add_argument("--force", action="store_true", help="ignore the weekly freshness gate")
    ap.add_argument("--start", type=int, default=START_YEAR)
    ap.add_argument("--end", type=int, default=END_YEAR)
    args = ap.parse_args()

    conn = db.connect()
    db.init_db(conn)
    try:
        status = refresh_if_stale(
            conn, force=args.force, start_year=args.start, end_year=args.end
        )
    finally:
        conn.close()
    print("ETL status:", json.dumps(status, indent=2))
