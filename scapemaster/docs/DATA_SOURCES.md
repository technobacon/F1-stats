# Data sources & attribution

ScapeMaster's questions are generated from three kinds of source, all built into
committed datasets at dev time so gameplay never touches the network.

## Sources

### 1. The skill-XP formula (no data)
Every skill-XP answer is computed from the exact RuneScape experience formula:

```
XP(n) = floor( (1/4) * sum_{L=1}^{n-1} floor(L + 300 * 2^(L/7)) )
```

This is pure arithmetic — `app/validation.py:xp_for_level`. It is the anchor of
the anti-hallucination design: an infinite supply of questions whose answers
cannot be wrong. `xp_for_level(99) == 13,034,431`.

### 2. OSRS Wiki real-time prices API
<https://prices.runescape.wiki/> — used for the **item** domain.

- `/mapping` → item id, name, members flag, High/Low Alchemy values, buy limit,
  base value.
- `/24h` → 24-hour average high/low prices and volumes. We take
  `ge_price = round((avgHigh + avgLow) / 2)` and
  `ge_volume = highPriceVolume + lowPriceVolume`. `/latest` is used only as a
  fallback when a thinly-traded item is absent from `/24h`.

Per the wiki's API guidelines we send a **descriptive User-Agent** on every
request, use only the bulk endpoints (never per-item calls), rate-limit
conservatively, and cache responses to disk. Prices are refreshed weekly, never
during gameplay.

### 3. OSRS Wiki page content
<https://oldschool.runescape.wiki/> — used for the **monster** and **quest**
domains, and for item release years.

- **Quests**: the complete list is parsed from the rendered `Quests/Free-to-play`
  and `Quests/Members` tables (Quest Points, difficulty, series, release year).
  The parsed count and total Quest Points are cross-checked against the wiki's own
  `{{Globals|quests}}` / `{{Globals|quest points}}` counters — a mismatch fails
  the build.
- **Monsters**: combat level, hitpoints, max hit, Slayer level/XP and release
  year are read from each curated monster's infobox. A page whose versioned
  infoboxes disagree on a field (e.g. multiple forms with different combat levels)
  **drops that field, or the whole monster**, rather than guessing.
- **Items**: release years are read from each item's infobox.

## What is and isn't hand-authored

The curated allowlists in `scripts/build_datasets.py` carry **only names and an
editorial fame tier** (1 = iconic, 2 = staple, 3 = known-to-regulars) — no
numbers. Every stat, price, level, count and year is fetched from the wiki or the
prices API at build time. The generator's proposed answers are then independently
recomputed by the validation layer before anything ships.

## Attribution & licensing

- Content from the **OSRS Wiki** is available under
  [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). The datasets
  derived from it (`app/data/monsters.json`, `quests.json`, and the curated
  fields of `items.json`) are likewise shared under CC BY-SA 3.0. Attribution to
  the OSRS Wiki appears in the app's About page and this repository.
- **Old School RuneScape** and **RuneScape** are trademarks of **Jagex Ltd.**
  ScapeMaster is an unofficial fan project, not affiliated with or endorsed by
  Jagex. No game assets, fonts, sprites or audio are used.
- The bundled pixel font is **Pixelify Sans**, licensed under the
  [SIL Open Font License 1.1](https://scripts.sil.org/OFL) — see
  `frontend/fonts/OFL.txt`.

## Curation convention: `release_year`

`release_year` (the `era_year` used for era-weighted serving) is the year the
content **first existed in the RuneScape timeline**, not the OSRS re-release. The
Abyssal whip is 2005 (RS2), Zulrah is 2015 (an OSRS original). This is what
"familiarity weighting" means — older iconic content still reads as old.
