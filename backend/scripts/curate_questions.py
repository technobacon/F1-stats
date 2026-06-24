"""Era/relevance curation pass for the committed question bank.

Test-group feedback: very old and obscure (pre-2010, feeder-era) questions turn
players off. This script culls backend/app/data/questions.json down to a modern,
driver-relevant bank under these rules:

DRIVER QUESTIONS (career, qualifying, milestone, ... — anything naming a driver)
  KEEP a question only if EVERY driver it names is eligible, where a driver is
  eligible iff:
    * they were active in 2010 or later (active_to >= 2010), OR
    * they are a multiple World Champion from an older era (>= 2 WDCs) — the
      Sennas / Prosts / Schumachers / Fangios that everyone still knows.
  Otherwise the question is culled (single-title pre-2010 names, pre-2010
  non-champions, feeder-era one-offs).

NON-DRIVER QUESTIONS (team, venue — name a constructor/circuit, no driver)
  KEEP only the modern ones (era_year >= 2010); the pre-2010 constructor- and
  circuit-era questions are exactly the "very old" content that tested badly.

ERA BIAS (the headline requirement)
  At least 70% of the surviving bank must come from drivers who raced multiple
  seasons in the 2020s. 2020s regulars and the protected multiple-champions are
  always kept; the remaining "eligible but not a 2020s regular" pool (older
  single-era-2010s drivers + modern team/venue) is then trimmed newest-first
  until the 2020s share reaches the 70% target.

The driver facts (career span + WDC count) are encoded below. Spans for the 56
drivers in arcade.json are taken from that snapshot; the rest (and all WDC
counts, which are fixed historical facts) are listed explicitly.

Usage:
    python -m scripts.curate_questions            # write + report
    python -m scripts.curate_questions --dry-run  # report only, no write
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
QUESTIONS_PATH = DATA / "questions.json"
ARCADE_PATH = DATA / "arcade.json"

TARGET_2020S_SHARE = 0.70

# Career span (active_from, active_to). Loaded from arcade.json for the 56
# drivers it covers; the dozen drivers below appear in questions but not arcade.
EXTRA_SPANS = {
    "Alberto Ascari": (1950, 1955),
    "Andrea Kimi Antonelli": (2025, 2026),
    "Daniil Kvyat": (2014, 2020),
    "Eddie Irvine": (1993, 2002),
    "Heinz-Harald Frentzen": (1994, 2003),
    "Isack Hadjar": (2025, 2026),
    "Jarno Trulli": (1997, 2011),
    "Johnny Herbert": (1989, 2000),
    "Kevin Magnussen": (2014, 2024),
    "Nico Hülkenberg": (2010, 2026),
    "Nino Farina": (1950, 1955),
    "Oliver Bearman": (2024, 2026),
}

# World Drivers' Championships won (fixed historical fact). Anyone absent counts
# as 0. Only the >= 2 entries actually change a keep/cull decision (they protect
# pre-2010 legends), but the full list is kept for auditability.
WDC = {
    "Juan Fangio": 5, "Lewis Hamilton": 7, "Michael Schumacher": 7,
    "Alain Prost": 4, "Sebastian Vettel": 4, "Max Verstappen": 4,
    "Ayrton Senna": 3, "Jack Brabham": 3, "Niki Lauda": 3,
    "Nelson Piquet": 3, "Jackie Stewart": 3,
    "Fernando Alonso": 2, "Alberto Ascari": 2, "Jim Clark": 2,
    "Emerson Fittipaldi": 2, "Mika Häkkinen": 2, "Graham Hill": 2,
    "Jenson Button": 1, "Damon Hill": 1, "Phil Hill": 1, "Mike Hawthorn": 1,
    "James Hunt": 1, "Alan Jones": 1, "Nigel Mansell": 1, "Mario Andretti": 1,
    "Keke Rosberg": 1, "Nico Rosberg": 1, "Jody Scheckter": 1, "John Surtees": 1,
    "Jacques Villeneuve": 1, "Kimi Räikkönen": 1, "Denny Hulme": 1,
    "Jochen Rindt": 1, "Nino Farina": 1,
}

DRIVER_CATEGORIES = {
    "career", "circuit", "consistency", "feats", "head_to_head", "milestone",
    "qualifying", "racecraft", "rates", "reliability", "single_season",
}


def load_spans() -> dict[str, tuple[int, int]]:
    spans = dict(EXTRA_SPANS)
    arcade = json.loads(ARCADE_PATH.read_text())
    for d in arcade["drivers"]:
        spans[d["full_name"]] = (d["active_from"], d["active_to"])
    return spans


def seasons_in_2020s(span: tuple[int, int]) -> int:
    lo, hi = max(span[0], 2020), min(span[1], 2026)
    return max(0, hi - lo + 1)


def build_index(spans):
    """Sort names longest-first so 'Nico Rosberg' wins over a bare 'Rosberg'
    and two-driver head-to-heads resolve both names."""
    return sorted(spans, key=len, reverse=True)


def drivers_in(text, names):
    found, rest = [], text
    for n in names:
        if n in rest:
            found.append(n)
            rest = rest.replace(n, "")
    return found


def classify(questions, spans):
    """Tag every question: ('cull', reason) or ('keep', bucket).

    Buckets: '2020s' (>=2 seasons in the 2020s), 'legend' (>=2 WDC protected),
    'eligible' (active 2010+ but not a 2020s regular — the trimmable pool)."""
    names = build_index(spans)

    def eligible(n):
        return spans[n][1] >= 2010 or WDC.get(n, 0) >= 2

    def is_2020s(n):
        return seasons_in_2020s(spans[n]) >= 2

    tagged = []
    for q in questions:
        cat = q.get("category", "")
        if cat in DRIVER_CATEGORIES:
            ds = drivers_in(q["question_string"], names)
            if not ds:
                tagged.append((q, "cull", "no-driver-found"))
            elif not all(eligible(d) for d in ds):
                bad = [d for d in ds if not eligible(d)]
                tagged.append((q, "cull", "ineligible-driver:" + ",".join(bad)))
            elif any(is_2020s(d) for d in ds):
                tagged.append((q, "keep", "2020s"))
            elif any(WDC.get(d, 0) >= 2 for d in ds):
                tagged.append((q, "keep", "legend"))
            else:
                tagged.append((q, "keep", "eligible"))
        else:  # team / venue — names no driver
            if (q.get("era_year") or 0) >= 2010:
                tagged.append((q, "keep", "eligible"))  # modern, trimmable
            else:
                tagged.append((q, "cull", "old-non-driver"))
    return tagged


def curate(questions, spans):
    tagged = classify(questions, spans)
    kept_2020s = [q for q, k, b in tagged if k == "keep" and b == "2020s"]
    kept_legend = [q for q, k, b in tagged if k == "keep" and b == "legend"]
    pool = [(q, b) for q, k, b in tagged if k == "keep" and b == "eligible"]

    # Enforce the 70% floor: keep all 2020s + protected legends, then admit as
    # many of the trimmable pool as the target allows, newest (highest era_year)
    # first so the oldest survivors are the ones dropped.
    twenty, legend = len(kept_2020s), len(kept_legend)
    max_total = int(twenty / TARGET_2020S_SHARE)  # floor
    pool_budget = max(0, max_total - twenty - legend)
    pool_sorted = sorted(
        pool, key=lambda qb: (-(qb[0].get("era_year") or 0), qb[0]["question_string"])
    )
    kept_pool = [q for q, _ in pool_sorted[:pool_budget]]
    trimmed_pool = [q for q, _ in pool_sorted[pool_budget:]]

    keep_set = id_set(kept_2020s) | id_set(kept_legend) | id_set(kept_pool)
    final = [q for q in questions if id(q) in keep_set]
    report = {
        "tagged": tagged,
        "kept_2020s": twenty,
        "kept_legend": legend,
        "pool_total": len(pool),
        "pool_kept": len(kept_pool),
        "pool_trimmed_for_target": len(trimmed_pool),
        "final": final,
    }
    return final, report


def id_set(items):
    return {id(x) for x in items}


def main(argv):
    dry = "--dry-run" in argv
    spans = load_spans()
    questions = json.loads(QUESTIONS_PATH.read_text())
    final, rep = curate(questions, spans)

    culled = [(q, why) for q, k, why in rep["tagged"] if k == "cull"]
    from collections import Counter
    cull_reasons = Counter(why.split(":")[0] for _, why in culled)
    cull_by_driver = Counter()
    for _, why in culled:
        if why.startswith("ineligible-driver:"):
            for d in why.split(":", 1)[1].split(","):
                cull_by_driver[d] += 1

    n = len(final)
    twenty = rep["kept_2020s"]
    by_mode = Counter(q["game_mode"] for q in final)
    print("=" * 64)
    print(f"START:  {len(questions)} questions")
    print(f"FINAL:  {n} questions  ({len(questions) - n} culled)")
    print(f"  from 2020s-regular drivers: {twenty}  ({100*twenty/n:.1f}%)")
    print(f"  protected multi-WDC legends: {rep['kept_legend']}")
    print(f"  modern eligible (2010s drivers + modern team/venue): {rep['pool_kept']}"
          f"  (trimmed {rep['pool_trimmed_for_target']} to hit the 70% floor)")
    print(f"  per game_mode: {dict(by_mode)}")
    print("-" * 64)
    print("Culled by reason:")
    for r, c in cull_reasons.most_common():
        print(f"  {c:5d}  {r}")
    print("Culled driver questions, by ineligible driver:")
    for d, c in cull_by_driver.most_common():
        print(f"  {c:5d}  {d}")
    print("=" * 64)

    if dry:
        print("dry-run: questions.json NOT modified")
        return 0
    QUESTIONS_PATH.write_text(json.dumps(final, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {n} questions -> {QUESTIONS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
