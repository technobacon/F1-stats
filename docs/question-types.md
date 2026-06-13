# Question types

Every question reduces to a `(metric, aggregation)` over a scope of
`entity × years × (optional constructor/circuit)`, and the answer is **always
recomputed from the staging data** by `validation.compute_metric` — no answer is
ever hand-written or trusted from text. Adding a "type" means adding a metric or
aggregation here, not writing answers.

The bank is era-biased toward the modern era with a lean on the
Senna/Prost/Mansell/Piquet (1984–93) and Schumacher (1994–2006) eras, and spans
three entity kinds: **drivers, teams (constructors), and circuits (venues)**.

## Driver significance gate

A driver may only *feature* in a question when they matter for the era the
question is scoped to (the mid-point of its season span). The further back the
question reaches, the bigger the name has to be:

| Question era | Featured driver must have… |
|---|---|
| 2020s | ≥ 50 career points |
| 2010s | at least one race win |
| 2000s | multiple race wins (3+) |
| pre-2000 | a World Championship |

Head-to-head questions apply the gate to **both** drivers. Team and venue
questions are unaffected (they don't feature a driver by name). Champions are
matched by full name (`seed.WORLD_CHAMPIONS`), so the gate works for both the
synthetic seed and the real ETL ids.

## Driver questions
1. **Career wins / podiums / poles / fastest laps / points** — the staples.
2. **Runner-up (P2) / third-place (P3) finishes** — the "nearly" stats.
3. **Comeback drives** — races where the driver climbed **10+ places** off the grid.
4. **Biggest single-race climb** — most positions made up grid→flag in one race.
5. **Net positions gained** — total places gained from the grid across a stint.
6. **Average finishing position** — mean classified finish (rewards consistency).
7. **Different circuits won at** — breadth of winning, not just volume.
8. **Winning seasons** — in how many separate seasons they won at least once.
9. **Average points per season** — normalizes across short vs long careers.
10. **Best track** — most wins at their single most successful circuit.
11. **Pole-to-win conversion** — poles that became wins (cross-table join).
12. **DNF count & DNF rate** — reliability/attrition (rate = % of starts).
13. **Win % / podium %** — strike rate over races entered.
14. **Peak season & first-win year** — *which* season they won most / won first.
15. **Head-to-head difference** — "how many more X does A have than B?", only
    generated when the two tallies are close (within ~20–30%) and substantial,
    so it can't be eyeballed.
16. **Hat-trick weekends** — pole, the win *and* the fastest lap in one Grand Prix.
17. **Wins from off pole** — career wins that didn't start from P1.
18. **Deepest winning grid slot** — the furthest back they started a race they won.
19. **Longest top-10 / podium streaks** — consecutive-finish runs, in race order.
20. **Team-mate count** — how many different drivers shared their car.
21. **Most recent win year** — the latest season with a victory.
22. **Average grid position** — the qualifying counterpart to average finish.

## Team (constructor) questions
23. **Team wins / podiums / poles / points per era window** (1980–93, 94–06,
    07–13, 14–26) — so each lands in a real period.
24. **Team 1-2 finishes** — races where a team locked out P1 **and** P2.
25. **Front-row lockouts** — both cars on row one of the grid, per era window.
26. **Team best season / which season** — peak win haul and the year it happened.
27. **Distinct winning drivers** — how many different drivers have won for the team.

## Circuit (venue) questions
28. **Different winners at a circuit** — how many distinct drivers have won there.
29. **Races a circuit has hosted** — venue longevity.
30. **Wins from pole at a venue** — how often the polesitter converted there.
31. **Venue win record** — the most wins any single driver has taken there.

## Building the bank
```bash
cd backend
# Rebuild questions.json (+ arcade.json) from live data, era-weighted to ~1000:
F1_DATA_SOURCE=jolpica F1_ETL_START_YEAR=1950 python3 -m app.seed --export
```
The site serves this committed bank with `F1_DATA_SOURCE=dataset` (instant boot,
no network).
