# Question types

Every question reduces to a `(metric, aggregation)` over a scope of
`entity × years × (optional constructor/circuit)`, and the answer is **always
recomputed from the staging data** by `validation.compute_metric` — no answer is
ever hand-written or trusted from text. Adding a "type" means adding a metric or
aggregation here, not writing answers.

The bank is era-biased toward the modern era with a lean on the
Senna/Prost/Mansell/Piquet (1984–93) and Schumacher (1994–2006) eras, and spans
three entity kinds: **drivers, teams (constructors), and circuits (venues)**.

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

## Team (constructor) questions
16. **Team wins / podiums / poles / points per era window** (1980–93, 94–06,
    07–13, 14–26) — so each lands in a real period.
17. **Team 1-2 finishes** — races where a team locked out P1 **and** P2.
18. **Team best season / which season** — peak win haul and the year it happened.

## Circuit (venue) questions
19. **Different winners at a circuit** — how many distinct drivers have won there.
20. **Races a circuit has hosted** — venue longevity.

## Building the bank
```bash
cd backend
# Rebuild questions.json (+ arcade.json) from live data, era-weighted to ~1000:
F1_DATA_SOURCE=jolpica F1_ETL_START_YEAR=1980 python3 -m app.seed --export
```
The site serves this committed bank with `F1_DATA_SOURCE=dataset` (instant boot,
no network).
