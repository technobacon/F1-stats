"""GE snapshot ETL tests — fully offline.

A fake transport returns recorded prices-API JSON, so we exercise the merge
logic, the mandatory User-Agent, the disk cache, the rate limiter, and the
weekly freshness gate without any network."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import etl


# --- Fake API ----------------------------------------------------------------
MAPPING = [
    {"id": 4151, "name": "Abyssal whip", "members": True, "lowalch": 48000,
     "highalch": 72000, "limit": 70, "value": 120001},
    {"id": 561, "name": "Nature rune", "members": False, "lowalch": 72,
     "highalch": 108, "limit": 18000, "value": 180},
    {"id": 20997, "name": "Twisted bow", "members": True, "lowalch": 1200000,
     "highalch": 2400000, "limit": 8, "value": 4000000},
]
DAY = {"data": {
    "4151": {"avgHighPrice": 1_000_000, "avgLowPrice": 980_000,
             "highPriceVolume": 600, "lowPriceVolume": 500},
    "561": {"avgHighPrice": 130, "avgLowPrice": 126,
            "highPriceVolume": 2_000_000, "lowPriceVolume": 1_500_000},
    # Twisted bow absent from /24h (thin trade day) -> falls back to /latest.
}}
LATEST = {"data": {
    "20997": {"high": 1_480_000_000, "low": 1_470_000_000},
}}

CURATED = [
    {"item_id": 4151, "name": "Abyssal whip", "release_year": 2005, "fame_tier": 1},
    {"item_id": 561, "name": "Nature rune", "release_year": 2001, "fame_tier": 1},
    {"item_id": 20997, "name": "Twisted bow", "release_year": 2017, "fame_tier": 1},
    {"item_id": 424242, "name": "Deleted item", "release_year": 2013, "fame_tier": 3},
]


def make_fake_fetch():
    calls: list[tuple[str, dict]] = []

    def fetch(url: str, headers: dict) -> bytes:
        calls.append((url, headers))
        if url.endswith("/mapping"):
            return json.dumps(MAPPING).encode()
        if url.endswith("/24h"):
            return json.dumps(DAY).encode()
        if url.endswith("/latest"):
            return json.dumps(LATEST).encode()
        raise AssertionError(f"unexpected url: {url}")

    return fetch, calls


@pytest.fixture
def client(tmp_path):
    fetch, calls = make_fake_fetch()
    cl = etl.WikiPricesClient(cache_dir=tmp_path / "cache", fetch=fetch,
                              limiter=etl.RateLimiter(rate=1e9))
    cl._calls = calls  # type: ignore[attr-defined]
    return cl


# --- User-Agent (mandatory per the wiki API rules) -----------------------------
def test_every_request_carries_a_descriptive_user_agent(client):
    client.mapping()
    client.day_prices()
    url, headers = client._calls[0]
    assert headers["User-Agent"] == etl.USER_AGENT
    # "Descriptive" per the wiki rules: says what it is and where it lives.
    assert "ScapeMaster" in etl.USER_AGENT and "github.com" in etl.USER_AGENT
    assert all(h["User-Agent"] == etl.USER_AGENT for _u, h in client._calls)


# --- Merge logic ---------------------------------------------------------------
def test_merge_joins_prices_onto_curated_allowlist():
    items = etl.merge_snapshot(CURATED, MAPPING, DAY["data"], LATEST["data"])
    by_id = {i["item_id"]: i for i in items}

    whip = by_id[4151]
    assert whip["ge_price"] == 990_000            # mid of 24h averages
    assert whip["ge_volume"] == 1100
    assert whip["high_alch"] == 72000 and whip["buy_limit"] == 70
    # Curated fields are never network-sourced.
    assert whip["release_year"] == 2005 and whip["fame_tier"] == 1

    tbow = by_id[20997]
    assert tbow["ge_price"] == 1_475_000_000      # /latest fallback when /24h is silent
    assert tbow["ge_volume"] == 0


def test_merge_drops_items_that_left_the_mapping():
    items = etl.merge_snapshot(CURATED, MAPPING, DAY["data"], LATEST["data"])
    assert {i["item_id"] for i in items} == {4151, 561, 20997}  # 424242 dropped


# --- Caching -------------------------------------------------------------------
def test_disk_cache_avoids_refetch(client):
    a = client.mapping()
    n_after_first = len(client._calls)
    b = client.mapping()
    assert a == b
    assert len(client._calls) == n_after_first  # second call served from disk


def test_stale_cache_is_refetched(tmp_path):
    fetch, calls = make_fake_fetch()
    cl = etl.WikiPricesClient(cache_dir=tmp_path / "cache", fetch=fetch,
                              limiter=etl.RateLimiter(rate=1e9), cache_ttl=3600)
    cl.mapping()
    path = cl._cache_path(f"{cl.base_url}/mapping")
    old = (datetime.now() - timedelta(hours=2)).timestamp()
    os.utime(path, (old, old))
    cl.mapping()
    assert len(calls) == 2  # refetched because stale


def test_network_error_without_cache_raises(tmp_path):
    def dead_fetch(url, headers):
        raise OSError("connection refused")

    cl = etl.WikiPricesClient(cache_dir=tmp_path / "cache", fetch=dead_fetch,
                              limiter=etl.RateLimiter(rate=1e9))
    with pytest.raises(etl.NetworkError):
        cl.mapping()


def test_network_error_with_cache_serves_stale(tmp_path):
    fetch, _calls = make_fake_fetch()
    cl = etl.WikiPricesClient(cache_dir=tmp_path / "cache", fetch=fetch,
                              limiter=etl.RateLimiter(rate=1e9), cache_ttl=0)
    good = cl.mapping()  # populates the (immediately stale) cache

    def dead_fetch(url, headers):
        raise OSError("connection refused")

    cl2 = etl.WikiPricesClient(cache_dir=tmp_path / "cache", fetch=dead_fetch,
                               limiter=etl.RateLimiter(rate=1e9), cache_ttl=0)
    assert cl2.mapping() == good  # stale cache beats no data


# --- Rate limiter ----------------------------------------------------------------
def test_rate_limiter_enforces_rate_after_burst():
    waits = []
    now = [0.0]

    def sleep(w):
        waits.append(w)
        now[0] += w

    rl = etl.RateLimiter(rate=2, burst=2, clock=lambda: now[0], sleep=sleep)
    for _ in range(5):
        rl.acquire()
    assert any(w > 0 for w in waits)  # had to throttle once the burst was spent


def test_rate_limiter_env_default(monkeypatch):
    monkeypatch.setenv("OSRS_ETL_RPS", "7")
    assert etl.RateLimiter().rate == 7.0


# --- Snapshot refresh + weekly gate ---------------------------------------------
@pytest.fixture
def snapshot_paths(tmp_path, monkeypatch):
    curated = tmp_path / "curated_items.json"
    items = tmp_path / "items.json"
    meta = tmp_path / "snapshot_meta.json"
    curated.write_text(json.dumps(CURATED))
    monkeypatch.setattr(etl, "CURATED_ITEMS_PATH", curated)
    monkeypatch.setattr(etl, "ITEMS_PATH", items)
    monkeypatch.setattr(etl, "SNAPSHOT_META_PATH", meta)
    return items, meta


def test_refresh_snapshot_writes_items_and_meta(client, snapshot_paths):
    items_path, meta_path = snapshot_paths
    status = etl.refresh_snapshot(client)
    assert status["status"] == "refreshed"
    assert status["items"] == 3 and status["dropped"] == 1
    data = json.loads(items_path.read_text())
    assert {i["name"] for i in data} == {"Abyssal whip", "Nature rune", "Twisted bow"}
    assert etl.last_refresh() is not None
    assert etl.is_stale() is False


def test_refresh_if_stale_skips_when_fresh(client, snapshot_paths):
    etl.refresh_snapshot(client)
    calls_before = len(client._calls)
    status = etl.refresh_snapshot_if_stale(client=client)
    assert status["skipped"] is True and status["status"] == "fresh"
    assert len(client._calls) == calls_before  # no network


def test_refresh_if_stale_refetches_when_old(client, snapshot_paths):
    _items, meta_path = snapshot_paths
    etl.refresh_snapshot(client)
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
    meta_path.write_text(json.dumps({"refreshed_at": old}))
    assert etl.is_stale() is True
    status = etl.refresh_snapshot_if_stale(client=client)
    assert status["status"] == "refreshed"


def test_force_overrides_the_weekly_gate(client, snapshot_paths):
    etl.refresh_snapshot(client)
    status = etl.refresh_snapshot_if_stale(force=True, client=client)
    assert status["status"] == "refreshed"
