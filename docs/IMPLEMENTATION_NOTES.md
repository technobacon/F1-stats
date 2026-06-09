# Implementation Notes — Prototype As Built

This document describes the **working prototype** in this repository: how it maps
to the spec docs (PRD / Pipeline / Architecture), and the concrete shape of the
question-generation and validation systems. Where the spec describes intent,
this describes the code as it actually runs.

> **Status:** runnable offline prototype. The staging data is **synthetic and
> illustrative** (a self-consistent stand-in for the Jolpica ETL extract), not a
> historical import. The guarantee the prototype enforces is *internal*: every
> client-facing answer is recomputed from the staging tables, so questions can
> never disagree with the data they were generated from.

---

## 1. Module map

| File | Responsibility |
|------|----------------|
| `backend/app/db.py` | SQLite schema (staging + production tables), connect/init/reset. |
| `backend/app/seed.py` | Offline pipeline: synthetic race-log generator → mock-LLM question generator → validation → production. |
| `backend/app/validation.py` | The deterministic anti-hallucination engine (metric + aggregation registry). |
| `backend/app/service.py` | Quiz provisioning (deterministic question sampling), tracking tokens, arcade. |
| `backend/app/scoring.py` | Server-authoritative exponential-decay scoring. |
| `backend/app/models.py` | Pydantic request/response models. |
| `backend/app/main.py` | FastAPI routes + static frontend mount. |
| `frontend/*` | Guest-first PWA: modes, odometer reveal, localStorage stats. |

The end-to-end offline pipeline is `seed.seed_all()`:

```
reset_db → seed_staging → run_validation_pipeline
            (synthetic     (mock-LLM questions →
             race log)      validate each → commit survivors)
```

---

## 2. Data model

### 2.1 Staging tables (trusted source of truth)

Stand-ins for the weekly Jolpica ETL extract.

- **`staging_race_results`** — one row per driver per race:
  `driver_id, constructor_id, year, round, circuit_id, position (NULL = DNF),
  grid, fastest_lap (0/1), points (REAL)`.
- **`staging_qualifying_results`** — one row per driver per qualifying session:
  `driver_id, constructor_id, year, round, quali_position (1 = pole)`.
  Poles are sourced **here**, never from race `grid`, because grid penalties make
  the two diverge.
- **`staging_drivers`**, **`staging_constructors`**, **`staging_circuits`** —
  lookup/reference data.

### 2.2 Synthetic race-log generator (`seed._generate_log`)

Each driver is described by one or more **stints** (driver + constructor + year
range + headline aggregates). For every stint the generator lays out a realistic
race-by-race calendar and assigns outcomes:

- **Targeted aggregates are placed exactly.** `wins`, `podiums`, `poles`, and
  `fastest_laps` are dealt to specific rounds so the recomputed totals equal the
  stint's declared numbers.
- **Everything else emerges from the log.** Points (via an illustrative points
  table), DNFs (era-dependent rate), grids (qualifying ± occasional penalty),
  positions gained, and per-circuit distribution all fall out of the generated
  rows — so derived metrics are internally consistent rather than hand-set.
- **Deterministic.** The per-stint RNG is seeded from a `hashlib.md5` of the
  stint key (not Python's salted `hash()`), so the generated log — and therefore
  every answer — is byte-stable across processes and redeploys.

This is what lets the validation layer be meaningful: the answers are *computed
from* this log, and the log is reproducible.

### 2.3 Production table

`production_trivia_questions` holds only **verified, client-facing** questions:

```
id, question_string, verified_answer (REAL),
answer_kind   -- 'count' | 'points' | 'year' | 'percentage'
category      -- UI grouping, e.g. 'reliability'
display_min, display_max  -- optional slider bounds (year/percentage)
difficulty_weight, game_mode, is_active, scheduled_date, created_at
```

`verified_answer` is always the **independently recomputed** value, never the
number the LLM proposed.

---

## 3. Validation engine (`validation.py`)

The anti-hallucination invariant (Pipeline §3): **the integer the LLM supplies in
`proposed_answer` is never trusted.** For every question the engine reads the
structural `validation_parameters`, recomputes the answer from staging via its
own SQL, and only accepts the question if the two match.

### 3.1 `validation_parameters` schema

```jsonc
{
  "target_entity": "driver",
  "entity_id": "schumacher",       // subject
  "entity_id_b": "hamilton",       // only for aggregation = "difference"
  "metric_target": "wins",         // see registry below
  "aggregation": "total",          // optional, defaults to "total"
  "start_year": 1991, "end_year": 1995,
  "filter_constructor_id": "benetton",  // OPTIONAL — omit for career totals
  "filter_circuit_id": "monza"          // OPTIONAL — per-circuit questions
}
```

### 3.2 Metric registry

Each metric is a SQL aggregate over a scoped row set. Source table is implied by
the metric.

| Source | Metrics |
|--------|---------|
| `staging_race_results` | `wins`, `podiums`, `fastest_laps`, `points`, `dnfs`, `points_finishes`, `positions_gained`, `distinct_constructors`, `seasons_active`, `starts` |
| `staging_qualifying_results` | `poles`, `front_rows` |
| cross-table join | `poles_converted` (pole *and* win in the same round) |

### 3.3 Aggregation registry

| Aggregation | Meaning | Answer kind |
|-------------|---------|-------------|
| `total` (default) | straight total over the year range | count/points |
| `best_season` | max of the metric in any single season | count |
| `which_year` | the season in which the metric peaked (earliest on ties) | **year** |
| `first_season` | earliest season the metric was non-zero | **year** |
| `percentage_of_races` | `round(100 · metric / starts)` | **percentage** |
| `difference` | `entity_id` total − `entity_id_b` total (head-to-head) | count |

Unsupported metrics **or** unsupported aggregations are rejected (the question
never reaches production) rather than silently passing.

### 3.4 Entry points

- `compute_metric(conn, params) -> Decimal` — independent recomputation. `Decimal`
  preserves half-points.
- `validate_ai_question(conn, llm_output) -> ValidationResult` — recomputes and
  compares to `proposed_answer`; returns `ok`, `expected`, `proposed`, `reason`.

---

## 4. Question generation (`seed.generate_questions`)

A data-driven generator stands in for the LLM: it queries the seeded staging data
and emits questions in the strict schema, each with the **true** answer as
`proposed_answer` (exactly what an accurate model would produce). The pipeline
still routes every one through the validator before committing — so the
*mechanism* is exercised on all questions, and the rejection path is proven by a
deliberately planted wrong answer (§5).

Current run commits **~560 questions** across **10 categories**:

| Category | Examples |
|----------|----------|
| `career` | career/stint wins, podiums, points, distinct constructors, seasons |
| `qualifying` | poles, front-row starts |
| `single_season` | most wins in a single season for a team |
| `milestone` | *"in which year…"* first win / peak season (**year** answers) |
| `rates` | podium percentage, poles converted to wins (**percentage**/count) |
| `reliability` | DNF counts |
| `racecraft` | net positions gained from the grid |
| `consistency` | points-scoring (top-10) finishes |
| `circuit` | wins at a specific track (each driver's three best) |
| `head_to_head` | *"how many more career X does A have than B?"* (curated rivalries) |

`answer_kind` drives the client input (count / points / **year** / **percentage**);
`display_min`/`display_max` carry sensible slider bounds for year and percentage
questions. Questions are spread across the three exact-numerical game modes, with
the more lateral metrics weighted into **One-Shots**.

---

## 5. The planted-hallucination demo

`mock_llm_questions()` appends exactly one question whose `proposed_answer` is
**wrong** (Schumacher/Ferrari career wins: staging says 72, the mock "remembers"
80). The real Schumacher/Ferrari wins total is intentionally excluded from normal
generation so this is the only question with that wording. Running the pipeline:

```
Seed complete. Committed 560 questions, rejected 1.
  REJECTED [wins] 'How many race wins did Michael Schumacher take with Ferrari
  (1996-2006)?': expected 72, LLM proposed 80 -- Hallucination detected
```

The rejected question never reaches `production_trivia_questions`. This is the
concrete proof of the anti-hallucination invariant.

---

## 6. Serving path

`service.build_quiz(mode, period)`:

- Deterministically samples N questions for `(mode, period)` so the set is stable
  for everyone within a period and rotates the next period (mirrors the 00:00 UTC
  cron provisioning, Architecture §1.1).
- Mints an opaque `tracking_token` per question; the verified answer is stashed
  **server-side** in the token store and never serialized to the client.
- Slider bounds: uses `display_min/max` when present (year/percentage), otherwise
  a non-revealing band around the answer. The bounds never leak the answer.

Scoring (`scoring.py`) is server-authoritative: the client submits a guess + token,
the server looks up the trusted answer and applies the percentage-error
exponential-decay formula (PRD §2). The answer is only revealed *after* a guess is
scored.

**Arcade Over/Under** compares two drivers on a career metric
(`service.ARCADE_METRICS`: wins, podiums, poles, fastest laps, points finishes,
front rows, DNFs), again computed through the same validation engine.

---

## 7. Frontend

- Each question shows a **category chip** and an **answer-kind hint** (“Enter a
  year”, “Enter a percentage 0–100”, etc.).
- **Year** questions always render a labelled slider bounded by the season range;
  One-Shots otherwise hides the slider for a harder feel.
- Stats (lifetime points, accuracy, streaks, achievements) live in `localStorage`
  — guest-first, no account required.

---

## 8. Testing & running

```bash
cd backend
python -m app.seed         # rebuild + seed + run the validation pipeline
python -m pytest -q        # 42 tests
python -m uvicorn app.main:app --reload
```

Test coverage of note (`tests/test_validation.py`):

- **Exact** assertions for placed metrics (wins, podiums, poles).
- **Consistency** assertions for emergent metrics (points/DNFs/positions-gained
  equal an independent direct query).
- Every aggregation (`best_season`, `which_year`, `first_season`,
  `percentage_of_races`, `difference`), per-circuit filtering, and the
  poles-converted join.
- The pipeline commits all valid questions and rejects exactly the one planted
  hallucination, which is verified absent from production.
