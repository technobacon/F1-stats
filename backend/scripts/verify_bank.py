"""Post-refresh sanity gate for the committed question bank.

Run after a Jolpica ETL + export to confirm the freshly built staging reflects
the complete, real World Championship record before anything is committed. Exits
non-zero (failing CI, blocking the commit) if any anchor check is wrong — this is
what stops a partial extract or a synthetic-seed fallback from overwriting the
curated bank with bad numbers.

Usage:  python -m scripts.verify_bank        # uses F1_DB_PATH / default DB
"""

from __future__ import annotations

import sys

from app import db
from app.validation import compute_metric


def _career_wins(conn, did: str) -> int:
    return int(compute_metric(conn, {
        "target_entity": "driver", "entity_id": did,
        "start_year": 1950, "end_year": 2026, "metric_target": "wins",
    }))


# Anchors picked across eras so a truncated or synthetic extract can't pass.
WIN_ANCHORS = {
    "michael_schumacher": 91,
    "hamilton": 105,
    "senna": 41,
    "prost": 51,
    "lauda": 25,          # spans pre-1980 — guards against the truncated-history bug
    "mario_andretti": 12,
    "lawson": 0,          # winless — guards the which_year phantom-peak fix
}


def main() -> int:
    conn = db.connect()
    ok = True

    for did, expect in WIN_ANCHORS.items():
        got = _career_wins(conn, did)
        flag = "OK " if got == expect else "FAIL"
        if got != expect:
            ok = False
        print(f"[{flag}] {did} career wins = {got} (expect {expect})")

    span = conn.execute(
        "SELECT MIN(year), MAX(year), COUNT(*) FROM staging_race_results"
    ).fetchone()
    lo, hi, n = span
    print(f"[INFO] staging span {lo}-{hi}, {n} result rows")
    if not (lo is not None and lo <= 1960 and hi is not None and hi >= 2025 and n > 20000):
        print("[FAIL] staging does not cover the full World Championship record")
        ok = False

    conn.close()
    if not ok:
        print("SANITY GATE FAILED — refusing to commit this bank.")
        return 1
    print("SANITY GATE PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
