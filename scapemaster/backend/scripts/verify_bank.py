"""Post-refresh sanity gate for the committed question bank.

Run after a dataset build / GE-price refresh + export to confirm the freshly
built staging reflects real, sane OSRS numbers before anything is committed.
Exits non-zero (failing CI, blocking the commit) if any anchor check is wrong —
this is what stops a corrupted extract or a bad price snapshot from overwriting
the curated bank.

Usage:  python -m scripts.verify_bank        # uses OSRS_DB_PATH / default DB
"""

from __future__ import annotations

import json
import sys

from app import db, seed
from app.validation import xp_for_level


def _item(conn, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM staging_items WHERE name = ?", (name,)
    ).fetchone()
    return dict(row) if row else None


def main() -> int:
    # Rebuild staging from the committed datasets so the checks see what will ship.
    conn = db.connect(":memory:")
    db.init_db(conn)
    seed.seed_staging(conn)
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        if not passed:
            ok = False
        print(f"[{'OK ' if passed else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")

    # ── XP formula anchors (the immovable ground truth) ──
    check("xp_for_level(99) == 13,034,431", xp_for_level(99) == 13_034_431)
    check("xp_for_level(92) == 6,517,253", xp_for_level(92) == 6_517_253)

    # ── Dataset population ──
    counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM staging_{t}").fetchone()["n"]
              for t in ("items", "monsters", "quests", "skills")}
    print(f"[INFO] staging rows: {counts}")
    check("items dataset non-trivial", counts["items"] >= 150, f"{counts['items']} items")
    check("monsters dataset non-trivial", counts["monsters"] >= 40, f"{counts['monsters']} monsters")
    check("quests dataset complete-ish", counts["quests"] >= 150, f"{counts['quests']} quests")
    check("skills present", counts["skills"] >= 23, f"{counts['skills']} skills")

    # ── No missing prices for tradeables; alch/limit populated ──
    null_price = conn.execute(
        "SELECT COUNT(*) AS n FROM staging_items WHERE ge_price IS NULL"
    ).fetchone()["n"]
    check("no null GE prices", null_price == 0, f"{null_price} items missing a price")
    # A few items legitimately can't be alchemised (e.g. the Old school bond).
    # The generator already skips a null metric, so a handful is fine; a flood
    # signals a broken mapping join.
    null_alch = conn.execute(
        "SELECT COUNT(*) AS n FROM staging_items WHERE high_alch IS NULL"
    ).fetchone()["n"]
    check("high-alch populated (allowing a few non-alchable items)", null_alch <= 5,
          f"{null_alch} items missing high_alch")

    # ── Anchor sanity: famous items land in a plausible band ──
    whip = _item(conn, "Abyssal whip")
    check("Abyssal whip high-alch == 72,000", bool(whip) and whip["high_alch"] == 72000,
          str(whip["high_alch"]) if whip else "missing")
    nature = _item(conn, "Nature rune")
    check("Nature rune price in 50–1,000 gp", bool(nature) and 50 <= (nature["ge_price"] or 0) <= 1000,
          str(nature["ge_price"]) if nature else "missing")
    tbow = _item(conn, "Twisted bow")
    check("Twisted bow price > 100m", bool(tbow) and (tbow["ge_price"] or 0) > 100_000_000,
          str(tbow["ge_price"]) if tbow else "missing")

    # ── No >3× price swing vs the previously committed snapshot ──
    try:
        prev = {i["item_id"]: i for i in json.loads(seed.ITEMS_PATH.read_text())}
        swings = []
        for row in conn.execute("SELECT item_id, name, ge_price FROM staging_items"):
            old = prev.get(row["item_id"], {}).get("ge_price")
            new = row["ge_price"]
            if old and new and (new > old * 3 or new * 3 < old):
                swings.append(f"{row['name']}: {old}->{new}")
        # Comparing staging (built from the same committed file) to that file is a
        # no-op here; the check has teeth when run after a live --source wiki
        # refresh has rewritten items.json under this staging build.
        check("no >3x GE price swings vs previous snapshot", not swings,
              "; ".join(swings[:5]))
    except (OSError, ValueError):
        print("[INFO] no previous items.json to diff against — skipping swing check")

    # ── The committed bank exists, is sizeable, and rejects the hallucination ──
    if seed.DATASET_PATH.exists():
        bank = json.loads(seed.DATASET_PATH.read_text())
        check("committed bank >= 800 questions", len(bank) >= 800, f"{len(bank)} questions")
        kinds = {q["answer_kind"] for q in bank}
        check("answer kinds all known",
              kinds <= {"count", "level", "xp", "coins", "year", "percentage"},
              str(kinds))
    summary = seed.run_validation_pipeline(conn, planted=True)
    check("planted hallucination is rejected", summary["rejected"] == 1,
          f"rejected={summary['rejected']}")

    conn.close()
    if not ok:
        print("SANITY GATE FAILED — refusing to commit this bank.")
        return 1
    print("SANITY GATE PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
