"""Post-refresh sanity gate for the committed question bank.

Run after a Jolpica ETL + export to confirm the freshly built staging reflects
the complete, real World Championship record before anything is committed. Exits
non-zero (failing CI, blocking the commit) if any anchor check is wrong — this is
what stops a partial extract or a synthetic-seed fallback from overwriting the
curated bank with bad numbers.

Usage:  python -m scripts.verify_bank        # uses F1_DB_PATH / default DB
"""

from __future__ import annotations

import json
import sys

from app import db, seed
from app.validation import compute_metric

# Composition gates for the exported bank itself (not just staging): the weekly
# refresh must never quietly shrink the bank or dilute its modern-era share —
# the committed 2,500-question bank guarantees at least half its questions sit
# in the post-2020 era (see seed.MODERN_MIN_SHARE; the gate here is slightly
# looser so a modest pool shift doesn't break CI, only a real regression does).
MIN_BANK_SIZE = 2400
MIN_MODERN_SHARE = 0.50


def _career_wins(conn, did: str) -> int:
    return int(compute_metric(conn, {
        "target_entity": "driver", "entity_id": did,
        "start_year": 1950, "end_year": 2026, "metric_target": "wins",
    }))


# Anchors picked across eras so a truncated or synthetic extract can't pass.
# Retired drivers use an EXACT count (their record is fixed forever). Active
# drivers use a MINIMUM (">= n") so the gate still catches a truncated history
# without breaking the automated weekly refresh every time they win again.
EXACT_WIN_ANCHORS = {
    "michael_schumacher": 91,
    "senna": 41,
    "prost": 51,
    "lauda": 25,          # spans pre-1980 — guards against the truncated-history bug
    "mario_andretti": 12,
    "lawson": 0,          # winless — guards the which_year phantom-peak fix
}
MIN_WIN_ANCHORS = {
    "hamilton": 105,      # still racing; floor guards truncation, tolerates new wins
}


def main() -> int:
    conn = db.connect()
    ok = True

    for did, expect in EXACT_WIN_ANCHORS.items():
        got = _career_wins(conn, did)
        flag = "OK " if got == expect else "FAIL"
        if got != expect:
            ok = False
        print(f"[{flag}] {did} career wins = {got} (expect {expect})")

    for did, floor in MIN_WIN_ANCHORS.items():
        got = _career_wins(conn, did)
        flag = "OK " if got >= floor else "FAIL"
        if got < floor:
            ok = False
        print(f"[{flag}] {did} career wins = {got} (expect >= {floor})")

    span = conn.execute(
        "SELECT MIN(year), MAX(year), COUNT(*) FROM staging_race_results"
    ).fetchone()
    lo, hi, n = span
    print(f"[INFO] staging span {lo}-{hi}, {n} result rows")
    if not (lo is not None and lo <= 1960 and hi is not None and hi >= 2025 and n > 20000):
        print("[FAIL] staging does not cover the full World Championship record")
        ok = False

    conn.close()

    # The exported bank itself: big enough, and modern enough.
    try:
        bank = json.loads(seed.DATASET_PATH.read_text())
    except (OSError, ValueError) as exc:
        print(f"[FAIL] cannot read the exported bank: {exc}")
        return 1
    modern = sum(1 for q in bank if (q.get("era_year") or 0) >= seed.MODERN_ERA_START)
    share = modern / len(bank) if bank else 0.0
    flag = "OK " if len(bank) >= MIN_BANK_SIZE else "FAIL"
    if len(bank) < MIN_BANK_SIZE:
        ok = False
    print(f"[{flag}] bank size = {len(bank)} (expect >= {MIN_BANK_SIZE})")
    flag = "OK " if share >= MIN_MODERN_SHARE else "FAIL"
    if share < MIN_MODERN_SHARE:
        ok = False
    print(f"[{flag}] post-{seed.MODERN_ERA_START} share = {share:.0%} ({modern} questions, "
          f"expect >= {MIN_MODERN_SHARE:.0%})")
    if not ok:
        print("SANITY GATE FAILED — refusing to commit this bank.")
        return 1
    print("SANITY GATE PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
