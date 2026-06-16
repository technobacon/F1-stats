# Implementation Notes — Prototype As Built

This document describes the **working prototype** in this repository: how it maps
to the spec docs (PRD / Pipeline / Architecture), and the concrete shape of the
question-generation and validation systems. Where the spec describes intent,
this describes the code as it actually runs.

> **Status:** launch-ready service. The default data source is **`dataset`** — the
> committed, validated **2,000-question bank** (`data/questions.json`, spanning
> 1950–2026), served with no network. A **real Jolpica ETL** (`etl.py`,
> `F1_DATA_SOURCE=jolpica`) rebuilds that bank from live F1 history (rate-limited,
> disk-cached, weekly), and an in-code **synthetic** seed is the offline fallback.
> Every client-facing answer is recomputed from staging before it ships, so
> questions can never disagree with the data they were generated from.
>
> Beyond the question pipeline the service now also has real **accounts**,
> server-verified **leaderboards** (all-time/weekly/daily) + a **Constructors'
> Championship**, server-side **streaks**, free **durable storage** (Litestream),
> and first-party **analytics** — see §9–§11. A feel-good **sound layer** and a
> first-run **team-selection onboarding** prompt round out the client (§12).
> **139 tests pass.**

---

## 1. Module map

| File | Responsibility |
|------|----------------|
| `backend/app/db.py` | SQLite schema (staging + production + `etl_metadata`; plus the **durable** account & analytics tables) + migrations; connect/init/reset. |
| `backend/app/etl.py` | **Real Jolpica ETL** (Pipeline §1): rate-limited (token bucket), disk-cached, weekly-gated ingestion of drivers/constructors/circuits/results/qualifying into staging. |
| `backend/app/seed.py` | Pipeline orchestrator: synthetic race-log generator (offline fallback), `load_entities_from_staging` for real data, **and** `load_dataset` for the committed bank → data-driven generator → validation → production. `refresh()` selects the source. |
| `backend/app/validation.py` | The deterministic anti-hallucination engine (metric + aggregation registry). |
| `backend/app/service.py` | Quiz / Free Practice / arcade provisioning (deterministic, era-weighted sampling), tracking tokens. |
| `backend/app/scoring.py` | Server-authoritative exponential-decay scoring. |
| `backend/app/auth.py` | Accounts (PBKDF2 + sessions), `play_events`, guest→account merge, leaderboards (period-windowed), Constructors' Championship, daily streaks (§9). |
| `backend/app/analytics.py` | First-party event ingest (allow-listed, bounded) + aggregate reporting (DAU/funnel/retention) (§10). |
| `backend/app/models.py` | Pydantic request/response models. |
| `backend/app/main.py` | FastAPI routes + lifespan (self-seed, analytics prune) + static frontend mount. |
| `backend/start.sh`, `litestream.yml` | Prod entrypoint wrapping uvicorn with Litestream replication for durable accounts (§11). |
| `frontend/*` | Guest-first PWA: modes, odometer reveal, leaderboards, share grid, analytics tracker, `analytics.html` dashboard, **first-run team onboarding** (§12). |
| `frontend/sound.js` | **Web Audio sound effects**, synthesized at runtime — no audio assets shipped or fetched (§12). |

The pipeline has two interchangeable front-ends into the same staging→validation
→production path, selected by `seed.refresh(source=...)` (env `F1_DATA_SOURCE`):

```
synthetic (default, offline):
  reset_db → seed_staging → run_validation_pipeline
              (synthetic     (mock-LLM questions →
               race log)      validate each → commit survivors)

jolpica (real, cached, weekly):
  etl.refresh_if_stale → load_entities_from_staging → run_validation_pipeline
   (Jolpica API → staging,   (derive drivers/stints     (recompute every answer
    rate-limited + cached,     from real staging rows)    from staging → commit)
    skipped if <7 days old)
```

Both paths share `validation.py`, so the anti-hallucination guarantee — every
client-facing answer is recomputed from staging — holds regardless of source.
If the live API is unreachable and nothing is cached, `refresh` falls back to the
synthetic seed so the app always runs.

### Weekly ETL (etl.py)

The real ingestion engine implements the three properties Pipeline §1 calls for:

- **Rate limiting** — a `RateLimiter` token bucket enforcing *both* a per-second
  burst and a sustained per-hour ceiling at once (throttling to whichever bites
  first), with a 300s halt on `429`. Limits are env-tunable; verify Jolpica's
  current published values before a large run.
- **Caching** — every raw API page is written to an on-disk cache keyed by URL;
  re-runs reuse it, and entries older than the weekly interval are re-fetched.
  Processed rows persist in the SQLite staging tables.
- **Weekly cadence** — `etl_metadata.last_refresh` gates `refresh_if_stale`: it's
  a no-op (no network) until staging is older than `REFRESH_INTERVAL_DAYS` (7).

The Ergast/Jolpica JSON is flattened by per-entity normalizers (`_norm_*`), with
DNFs mapped from non-numeric `positionText`, poles taken from qualifying P1 (not
race grid), and each driver's active span derived from the ingested race log.

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

The default `dataset` source serves the committed, validated **2,000-question
bank** (`data/questions.json`); the synthetic in-code run commits **~560
questions**. Both span the same **10 categories**:

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
questions. Questions are spread across the exact-numerical game modes, with the
more lateral metrics weighted into **Hardcore** (internally `one_shot`).

---

## 5. The planted-hallucination demo

`mock_llm_questions()` appends exactly one question whose `proposed_answer` is
**wrong** (Schumacher/Ferrari career wins: staging says 72, the mock "remembers"
80). The real Schumacher/Ferrari wins total is intentionally excluded from normal
generation so this is the only question with that wording. Running the synthetic
pipeline (`F1_DATA_SOURCE=synthetic python -m app.seed`):

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
- Sampling is **era-weighted** (`ERA_WEIGHT_BANDS`): the mix leans modern (2014+)
  with a lean on the golden eras, older seasons appearing occasionally — so the
  1950–2026 bank still feels current.
- Mints an opaque `tracking_token` per question; the verified answer is stashed
  **server-side** in the token store and never serialized to the client.
- Slider bounds: uses `display_min/max` when present (year/percentage), otherwise
  a non-revealing band around the answer. The bounds never leak the answer.

Scoring (`scoring.py`) is server-authoritative: the client submits a guess + token,
the server looks up the trusted answer and applies the percentage-error
exponential-decay formula (PRD §2). The answer is only revealed *after* a guess is
scored. The verify endpoint also records a server-scored `play_event` (§9) —
**except Free Practice** (`service.FREE_PRACTICE_MODE`), which is a non-competitive
training mode and is never recorded.

**Free Practice** (`service.build_practice_question`) serves one random verified
question at a time, unlimited and non-competitive, behind the same era-tiered
significance gate the generator uses.

**Arcade Over/Under** compares two drivers on a career metric
(`service.ARCADE_METRICS`: wins, podiums, poles, fastest laps, points finishes,
front rows, DNFs), again computed through the same validation engine.

---

## 7. Frontend

- Each question shows a **category chip** and an **answer-kind hint** (“Enter a
  year”, “Enter a percentage 0–100”, etc.).
- **Year** questions always render a labelled slider bounded by the season range;
  Hardcore otherwise hides the slider for a harder feel.
- Guest-first: stats live in `localStorage` with no account required. Once signed
  in, the **competitive** numbers (points, accuracy, streak) come from the server
  instead; cosmetic state (team, achievements) stays local.
- Profile shows the **leaderboards** (all-time/weekly/daily tabs) and the
  **Constructors' Championship**; the summary screen shows a spoiler-free
  **Wordle-style result grid** that Share copies.
- A small pseudonymous **analytics tracker** batches funnel events and flushes via
  `sendBeacon` (§10).

---

## 8. Testing & running

```bash
cd backend
F1_DATA_SOURCE=dataset ./run.sh   # install, load the committed bank, serve :8000
python -m pytest -q               # 139 tests (offline)
```

Test coverage of note:

- `tests/test_validation.py` — **exact** assertions for placed metrics; consistency
  assertions for emergent metrics; every aggregation, per-circuit filtering, the
  poles-converted join; and the planted-hallucination rejection.
- `tests/test_api.py` — the trust boundary (no answer in any payload), all modes,
  deterministic daily, dev-tools toggle.
- `tests/test_auth.py` — accounts, sessions, guest→account merge, the replay-proof
  dedup, server-derived streaks, period & team leaderboards, reseed survival.
- `tests/test_analytics.py` — bounded ingest, the allow-list, the token gate, and
  funnel/mode aggregation.

---

## 9. Accounts, leaderboards & the trust boundary (`auth.py`)

The account layer is **first-party and dependency-free** — no OAuth, no third-party
auth. Passwords are PBKDF2-HMAC-SHA256 (stdlib `hashlib`, per-user salt); sessions
are opaque server-stored bearer tokens (logout/expiry = a row delete).

The trust boundary (Architecture §2.2) is enforced by construction: every guess is
scored server-side and written to **`play_events`** with the server's score —
never a client number. Leaderboard and profile totals are **recomputed** from
those rows, so they can't be forged from `localStorage`. Guest play is logged
against the client `anon_id` and re-keyed to the account on sign-in
(`claim_anon_events`).

- **Replay-proof:** the daily set is deterministic, so without a guard a player
  could re-run it and stack points. A partial `UNIQUE(identity_key, question_id,
  period)` index plus `INSERT OR IGNORE` pins each question to **one scored row per
  player per day** (orphan reveals with no identity are exempt).
- **Leaderboards:** `leaderboard(period=all|weekly|daily)` filters on
  `created_at`; the resetting windows give newcomers a fresh race to win.
- **Constructors' Championship:** `team_leaderboard` buckets verified points by the
  player's pledged team (persisted via `POST /api/v1/profile/team`).
- **Team overview (onboarding):** `team_overview` returns **every** team — even
  empty ones — with its registered headcount and all-time points, served at
  `GET /api/v1/teams/overview`. It backs the first-run team-selection prompt
  (§12); points still come only from `play_events`.
- **Streaks:** `daily_streak` recomputes the consecutive-day run from `play_events`
  (never trusts the client).

---

## 10. Analytics (`analytics.py`)

First-party, pseudonymous, self-contained — no third-party tag, no cross-site
cookie. The frontend batches an allow-listed set of events keyed by the guest
`anon_id` + a per-tab session id and flushes them via `sendBeacon`; the server
validates/bounds them into the persistent `analytics_events` table (pruned to 180
days on boot). This is **aggregate telemetry only** — it never feeds scoring or the
leaderboard.

- `POST /api/v1/analytics/collect` — public, best-effort batch ingest (unknown
  events dropped, batch capped, props sanitized).
- `GET /api/v1/analytics/summary` — DAU/WAU/MAU, the open→start→complete→share→
  signup funnel with conversion rates, D1/D7 retention, mode mix, account growth.
  **Token-gated** by `F1_ANALYTICS_TOKEN` (unset ⇒ disabled). `/analytics` serves
  the dashboard page (`frontend/analytics.html`, no dependencies).

---

## 11. Durability (Litestream)

Accounts/sessions/play-history/analytics live only in the SQLite file, which an
ephemeral free host wipes on redeploy/cold start. `backend/start.sh` makes it
durable for free: on boot it restores the latest snapshot from S3-compatible object
storage, then runs uvicorn under `litestream replicate`. It's **opt-in** (engaged
only when `LITESTREAM_REPLICA_BUCKET` is set) and **graceful** (final snapshot on
SIGTERM), with zero changes to application code or SQL. Setup is in HANDOFF §7.

---

## 12. Sound effects & first-run onboarding (`frontend/sound.js`, `app.js`)

Two client-only feel-good layers; neither touches the trust boundary.

### Sound (`sound.js`)

Every effect is **synthesized at runtime** with the Web Audio API — there are **no
binary audio assets** to ship, host or fetch (the same self-contained philosophy as
the first-party analytics and built-in accounts). The module is a small singleton:

- One lazily-created `AudioContext` (built on the first play, resumed inside the
  triggering user gesture to satisfy autoplay policy) feeding **one master gain**
  (`MASTER ≈ 0.5`). Each effect's own peak sits under that ceiling, so the palette
  is loud enough to satisfy but never startles or clips.
- Low-level voices: `blip` (enveloped oscillator, optional pitch glide) and
  `whoosh` (band-pass-filtered white noise with a Doppler sweep + optional stereo
  pan for the car drive-bys).
- Mapped effects: `lockIn`, `riser`, `greenSector` (one car), `purpleSector` (a
  whole pack, panned across the grid + a sparkle), `lightsOut` (the five-light F1
  start sequence then a launch surge), `achievement`, `sessionComplete`,
  `correct`/`wrong` (arcade), `uiClick`. The slider's spinning-wheel `tick` has its
  own rate limit so dragging reads as crisp detents, not a buzz.
- On/off persists in `localStorage` (`f1sg_sound_on`, default **on**) and is
  honoured before any node is built, so muting is instant and total.

`app.js` fires the cues at the natural moments (guess crossing a notch, lock-in,
the reveal slide, sector flash, session start/finish, achievement unlock, arcade
pick, navigation). A single **header toggle** (`#sound-toggle`, wired by
`SoundToggle`) flips the state and repaints the 🔊/🔇 icon.

### Onboarding (`TeamPicker` in `app.js`)

The team-picker modal doubles as a one-time welcome prompt. `TeamPicker.maybeOnboard()`
(run at boot) opens it **only** for a brand-new guest — returning guests (local
progress present) and signed-in players are silently marked done via the
`f1sg_onboarded` flag, and a `?play=` deep link suppresses it so a shared challenge
isn't interrupted. In onboarding mode the close affordance and backdrop/Escape
dismissal are disabled so a side is actually chosen.

It fetches `GET /api/v1/teams/overview` and shows, per team card, the **fan
headcount + championship points**, plus an intro line naming the current
Constructors' Championship leader and the total number of players who've picked a
side — making the choice feel social and consequential from the first visit. The
selection reuses the existing `applyTeam` / `syncTeam` path (cosmetic locally,
pledged server-side on sign-in).
