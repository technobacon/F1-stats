# Question types

Every question reduces to a `(domain, metric, aggregation)` triple that the
validation engine (`app/validation.py`) can recompute deterministically. The
generator (`app/seed.py`) emits these; only ones whose recomputed answer matches
are committed.

## Answer kinds

The `answer_kind` drives the client's input UI and formatting:

| kind | used for | slider |
|---|---|---|
| `count` | buy limits, HP, max hit, Quest Points, `count_where` results | linear |
| `level` | combat level, Slayer level | linear, explicit 1–126 / 1–99 bounds |
| `xp` | skill experience | **log scale**, k/m formatting |
| `coins` | GE price, High/Low Alchemy values | **log scale**, k/m/b formatting + coin-colour easter egg |
| `year` | release years | linear, 1999–present |
| `percentage` | e.g. "% of quests that are members-only" | linear, 0–100 |

`coins`/`xp` answers span five orders of magnitude, so their slider maps the
track logarithmically (equal drag = equal multiplicative step) and snaps to two
significant figures. Scoring is unaffected — the percentage-error decay is
scale-invariant.

## Domains & metrics

### skill (formula, no table)
- `xp_for_level {level}` — cumulative XP to reach a level.
- `xp_between {level_a, level_b}` — XP between two levels.

### item (`staging_items`)
Metrics: `ge_price`, `high_alch`, `low_alch`, `buy_limit`, `value`,
`release_year`.

Guard rails: GE-price questions are only emitted for items with `ge_price ≥ 500`,
`ge_volume ≥ 100` and `fame_tier ≤ 2` — a thin or obscure market makes the weekly
snapshot meaningless. No coin question is emitted whose answer is below 50
(percentage-error scoring is brutal at tiny scales).

### monster (`staging_monsters`)
Metrics: `combat_level`, `hitpoints`, `max_hit`, `slayer_level`, `slayer_xp`,
`release_year`. Plus a head-to-head `difference` on combat level between two
close bosses, and a `max_where` / `count_where` census.

### quest (`staging_quests`)
Metrics: `quest_points`, `release_year`. This dataset is **complete**, so the
aggregate questions are honest:
- `count_where` — how many quests match a filter (difficulty, members, exact QP,
  release-year band, series).
- `sum_where` — total Quest Points of a difficulty tier, or free-to-play.
- `max_where` — the most QP any single quest awards.
- `percentage_where` — what share of quests are members-only.

## Aggregations

- `identity` — the metric's value for one entity (the workhorse).
- `difference` — `entity_id` minus `entity_id_b` (head-to-head).
- `count_where` / `sum_where` / `max_where` — over rows matching `filters`.
- `percentage_where` — `100 * count(filters) / count(all)`, rounded.

`filters` is an allow-listed vocabulary (`_FILTERS` in `validation.py`); anything
outside it fails validation rather than silently computing nonsense.

## Difficulty & era weighting

Each question carries a `difficulty_weight` (base per metric plus a fame-tier
penalty) and an `era_year` (the content's RuneScape-timeline release year). The
daily set is drawn with Efraimidis–Spirakis weighted sampling biased toward
familiar, recent content (`RELEASE_WEIGHT_BANDS` in `service.py`): 2019+ →1.0,
2013–18 →0.9, 2005–07 →0.6, 2001–04 →0.4. Training Grounds draws uniformly so
every corner of Gielinor turns up.
