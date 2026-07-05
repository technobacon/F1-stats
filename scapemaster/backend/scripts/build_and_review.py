"""Rebuild the committed bank from the entity datasets and review the mix.

Regenerates the full validated pool, exports the sampled bank + Duel Arena
snapshot, and prints a breakdown by category / answer-kind / era so the balance
can be eyeballed.

    python -m scripts.build_and_review
"""
from __future__ import annotations

import json
from collections import Counter

from app import db, seed


def main() -> None:
    # Load the committed entity datasets into a throwaway in-memory staging DB.
    conn = db.connect(":memory:")
    db.init_db(conn)
    seed.seed_staging(conn)

    conn.execute("DELETE FROM production_trivia_questions")
    summary = seed.run_validation_pipeline(conn, planted=False)
    pool = conn.execute(
        "SELECT COUNT(*) AS n FROM production_trivia_questions"
    ).fetchone()["n"]
    print(f"Validated pool: {pool} questions "
          f"(committed={summary['committed']}, rejected={summary['rejected']})")

    d = seed.export_dataset(conn, n=1200)
    a = seed.export_arcade(conn)
    print(f"Exported bank: {d['written']} questions (pool {d['pool']}) -> {d['path']}")
    print(f"Exported arcade: {a['items']} items + {a['monsters']} monsters")

    bank = json.loads(seed.DATASET_PATH.read_text())
    print(f"\nBank size: {len(bank)}")

    def era_band(y):
        if y is None:
            return "unknown"
        for lo, hi, label in [(2019, 9999, "2019+"), (2013, 2018, "2013-18"),
                              (2005, 2007, "2005-07"), (2001, 2004, "2001-04")]:
            if lo <= y <= hi:
                return label
        return "other"

    print("By era:", dict(Counter(era_band(q.get("era_year")) for q in bank)))
    print("By kind:", dict(Counter(q["answer_kind"] for q in bank)))
    print("By category:", dict(Counter(q["category"] for q in bank)))

    conn.close()


if __name__ == "__main__":
    main()
