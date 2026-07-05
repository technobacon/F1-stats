"""Grand Exchange snapshot ETL — the only network-touching module.

Refreshes the price fields of the committed items dataset from the OSRS Wiki
real-time prices API (https://prices.runescape.wiki). Everything else in the
entity datasets (names, release years, fame tiers, quest/monster facts) is
build-time curated and NEVER touched here — see scripts/build_datasets.py.

Rules of engagement (per the wiki's API guidelines):
  * a descriptive User-Agent on every request (mandatory);
  * bulk endpoints only (/mapping, /24h, /latest) — never per-item calls;
  * a token-bucket rate limit as belt-and-braces (OSRS_ETL_RPS, default 1);
  * a disk cache so a re-run within the TTL never re-fetches (/mapping is ~4MB);
  * a weekly freshness gate — gameplay always serves from the committed
    snapshot, so there is no reason to refresh more often.

Refresh bookkeeping lives in a sidecar file next to the snapshot itself
(snapshot_meta.json), so it survives the boot-time database reseed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
CURATED_ITEMS_PATH = DATA_DIR / "curated_items.json"
ITEMS_PATH = DATA_DIR / "items.json"
SNAPSHOT_META_PATH = DATA_DIR / "snapshot_meta.json"
CACHE_DIR = DATA_DIR / ".http_cache"

BASE_URL = "https://prices.runescape.wiki/api/v1/osrs"
USER_AGENT = "ScapeMaster fan quiz weekly GE snapshot (github.com/technobacon/F1-stats)"

# The GE snapshot is weekly by design; anything younger than this is "fresh".
REFRESH_MAX_AGE = timedelta(days=6)
# Disk-cache TTL: a crashed/re-run refresh within the hour reuses the download.
CACHE_TTL_SECONDS = 3600


class NetworkError(Exception):
    """Raised when the prices API is unreachable and nothing is cached."""


class RateLimiter:
    """Token bucket: at most `rate` requests per second, with a small burst.
    `clock`/`sleep` are injectable so tests can drive it without real waits."""

    def __init__(self, rate: float | None = None, burst: int = 2,
                 clock=time.monotonic, sleep=time.sleep):
        self.rate = rate if rate is not None else float(os.environ.get("OSRS_ETL_RPS", "1"))
        self.burst = burst
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(burst)
        self._last = clock()

    def acquire(self) -> None:
        while True:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            self._sleep((1 - self._tokens) / self.rate)


def _urllib_fetch(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


class WikiPricesClient:
    """Minimal client for the wiki prices API with UA, rate limit + disk cache.
    `fetch(url, headers) -> bytes` is injectable so tests run fully offline."""

    def __init__(self, base_url: str = BASE_URL, cache_dir: Path | None = None,
                 cache_ttl: int = CACHE_TTL_SECONDS, limiter: RateLimiter | None = None,
                 fetch=None):
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir or CACHE_DIR)
        self.cache_ttl = cache_ttl
        self.limiter = limiter or RateLimiter()
        self._fetch_fn = fetch or _urllib_fetch

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def _fetch(self, path: str) -> dict | list:
        url = f"{self.base_url}{path}"
        cache = self._cache_path(url)
        if cache.exists() and time.time() - cache.stat().st_mtime < self.cache_ttl:
            return json.loads(cache.read_text())
        self.limiter.acquire()
        try:
            body = self._fetch_fn(url, {"User-Agent": USER_AGENT})
        except OSError as exc:
            if cache.exists():  # stale cache beats no data for a snapshot refresh
                return json.loads(cache.read_text())
            raise NetworkError(f"prices API unreachable: {exc}") from exc
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(body)
        return json.loads(body)

    def mapping(self) -> list[dict]:
        return self._fetch("/mapping")

    def day_prices(self) -> dict:
        return self._fetch("/24h")["data"]

    def latest_prices(self) -> dict:
        return self._fetch("/latest")["data"]


def merge_snapshot(curated: list[dict], mapping: list[dict],
                   day: dict, latest: dict) -> list[dict]:
    """Join the curated allowlist onto fresh mapping + price data.

    Curated fields (name, release_year, fame_tier) always win — they are never
    network-sourced. Price/metadata fields come only from the API. An item that
    has vanished from the mapping is dropped (with the caller logging it) rather
    than served with stale facts."""
    by_id = {row["id"]: row for row in mapping}
    items = []
    for cur in curated:
        m = by_id.get(cur["item_id"])
        if m is None:
            continue
        d = day.get(str(cur["item_id"]), {})
        l = latest.get(str(cur["item_id"]), {})
        hi = d.get("avgHighPrice") or l.get("high")
        lo = d.get("avgLowPrice") or l.get("low")
        ge_price = round((hi + lo) / 2) if hi and lo else (hi or lo)
        items.append({
            "item_id": cur["item_id"],
            "name": cur["name"],
            "members": 1 if m.get("members") else 0,
            "buy_limit": m.get("limit"),
            "value": m.get("value"),
            "low_alch": m.get("lowalch"),
            "high_alch": m.get("highalch"),
            "ge_price": ge_price,
            "ge_volume": (d.get("highPriceVolume") or 0) + (d.get("lowPriceVolume") or 0),
            "release_year": cur.get("release_year"),
            "fame_tier": cur.get("fame_tier", 2),
        })
    return items


def last_refresh() -> datetime | None:
    """When the GE snapshot was last refreshed from the live API, or None if it
    has only ever been built by scripts/build_datasets.py."""
    try:
        meta = json.loads(SNAPSHOT_META_PATH.read_text())
        return datetime.fromisoformat(meta["refreshed_at"])
    except (OSError, ValueError, KeyError):
        return None


def is_stale() -> bool:
    ts = last_refresh()
    return ts is None or datetime.now(timezone.utc) - ts > REFRESH_MAX_AGE


def refresh_snapshot(client: WikiPricesClient | None = None) -> dict:
    """Refresh items.json price fields from the live API and stamp the sidecar
    meta. Raises NetworkError if the API is unreachable and nothing is cached."""
    client = client or WikiPricesClient()
    curated = json.loads(CURATED_ITEMS_PATH.read_text())
    items = merge_snapshot(curated, client.mapping(), client.day_prices(),
                           client.latest_prices())
    dropped = len(curated) - len(items)
    ITEMS_PATH.write_text(json.dumps(items, indent=1, ensure_ascii=False))
    now = datetime.now(timezone.utc)
    SNAPSHOT_META_PATH.write_text(json.dumps(
        {"refreshed_at": now.isoformat(timespec="seconds")}, indent=1))
    return {"status": "refreshed", "items": len(items), "dropped": dropped,
            "refreshed_at": now.isoformat(timespec="seconds")}


def refresh_snapshot_if_stale(force: bool = False,
                              client: WikiPricesClient | None = None) -> dict:
    """Honor the weekly cadence: skip cheaply when the snapshot is still fresh."""
    if not force and not is_stale():
        ts = last_refresh()
        return {"status": "fresh", "skipped": True,
                "refreshed_at": ts.isoformat(timespec="seconds") if ts else None}
    return refresh_snapshot(client)
