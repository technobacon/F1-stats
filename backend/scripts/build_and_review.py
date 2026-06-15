"""One-off: rebuild the committed bank from a prebuilt staging DB and review it.

Run AFTER staging is populated (F1_DB_PATH points at the full-history ETL DB):
    F1_DB_PATH=.../f1stats_full.db python -m scripts.build_and_review
"""
from __future__ import annotations

from collections import Counter

from app import db, seed
from app.validation import compute_metric


def main() -> None:
    conn = db.connect()

    # Full validated pool (before sampling), so we can see how much we have.
    drivers = seed.load_entities_from_staging(conn)
    conn.execute("DELETE FROM production_trivia_questions")
    summary = seed.run_validation_pipeline(conn, drivers=drivers, planted=False)
    pool = conn.execute(
        "SELECT COUNT(*) AS n FROM production_trivia_questions"
    ).fetchone()["n"]
    print(f"Validated pool: {pool} questions "
          f"(committed={summary['committed']}, rejected={summary['rejected']})")

    # Export the sampled bank (writes questions.json + dataset_meta.json) + arcade.
    d = seed.export_dataset(conn, n=2000)
    a = seed.export_arcade(conn)
    print(f"Exported bank: {d['written']} questions (pool {d['pool']}) -> {d['path']}")
    print(f"Exported arcade: {a['drivers']} drivers")

    import json
    bank = json.loads(seed.DATASET_PATH.read_text())
    print(f"\nBank size: {len(bank)}")

    def era_band(y):
        if y is None:
            return "unknown"
        for lo, hi, label in [(2014, 9999, "2014+"), (2007, 2013, "2007-13"),
                              (1994, 2006, "1994-06"), (1980, 1993, "1980-93"),
                              (0, 1979, "pre-1980")]:
            if lo <= y <= hi:
                return label
        return "unknown"

    print("By era:", dict(Counter(era_band(q.get("era_year")) for q in bank)))
    print("By mode:", dict(Counter(q["game_mode"] for q in bank)))
    print("By kind:", dict(Counter(q["answer_kind"] for q in bank)))
    print("By category:", dict(Counter(q["category"] for q in bank)))

    conn.close()


if __name__ == "__main__":
    main()
