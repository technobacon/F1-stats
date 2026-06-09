# F1 StatGuesser

Website for F1 stat quizzes — a gamified Formula 1 statistics guessing platform.

This repo contains the design docs in [`docs/`](./docs) and a **runnable
prototype** that implements the defensible core of the system described there.

---

## What the prototype is

The full design (see [`docs/`](./docs)) targets Next.js + FastAPI + Postgres +
Redis + Celery + an LLM question pipeline. This prototype implements the
**three cross-cutting invariants** from [`docs/README.md`](./docs/README.md) as a
single runnable service, so the architecture can be validated and grown into the
full project:

1. **Server-authoritative scoring** — the true answer is never sent to the
   client; all scoring runs server-side via the percentage-error exponential
   decay formula (PRD §2). → `backend/app/scoring.py`
2. **Anti-hallucination validation** — no LLM-proposed answer is trusted; each
   is independently recomputed against trusted staging data before it can reach
   production (Pipeline §3). → `backend/app/validation.py`
3. **Trust boundary** — questions are served with an opaque tracking token, the
   answer is held server-side, and the score is computed on `verify`
   (Architecture §1.1, §2.2). → `backend/app/service.py`

Substitutions made to keep it runnable anywhere (each is a localized swap):

| Production (docs) | Prototype | Why |
|---|---|---|
| PostgreSQL | SQLite (schema-compatible) | zero-dependency, runs anywhere |
| Jolpica API + weekly ETL | **implemented** (`backend/app/etl.py`) — rate-limited, disk-cached, weekly; synthetic seed is the offline fallback | real data when the network allows; still runs anywhere |
| Real LLM synthesizer | `mock_llm_questions()` emitting the strict schema | deterministic, offline |
| Redis token cache | in-memory dict | single-process prototype |
| Next.js + Tailwind frontend | vanilla HTML/CSS/JS served by FastAPI | one runnable service |

### Data source: real Jolpica ETL vs. synthetic seed

The data layer can run from **real F1 history** or the **synthetic seed**, chosen
by the `F1_DATA_SOURCE` env var (default `synthetic`):

```bash
# Real data: pull from the Jolpica F1 API into staging, cache it, regenerate
# the validated question pool. Gated to a weekly cadence (the data updates once
# a week), so re-runs are cheap until the data is stale.
F1_DATA_SOURCE=jolpica python3 -m app.seed          # respects the weekly gate
F1_DATA_SOURCE=jolpica python3 -m app.seed --force  # force a fresh pull now
python3 -m app.etl --force                           # ETL-only (no question regen)
```

The ETL (`backend/app/etl.py`) implements the documented ingestion engine
(Pipeline §1): a **token-bucket rate limiter** honoring both a per-second burst
and a sustained hourly ceiling (with `429` backoff), an **on-disk cache** of
every raw API page, and a **weekly freshness gate** so we don't re-fetch data
that hasn't changed. If the API host (`api.jolpi.ca`) is unreachable and nothing
is cached, it falls back to the synthetic seed so the app always runs.

> **Network note:** pulling real data requires outbound access to
> `https://api.jolpi.ca`. In sandboxes that block it you'll see the synthetic
> fallback; add the host to the environment's network allowlist to fetch live.
> Tune the ingest with `F1_ETL_START_YEAR` / `F1_ETL_END_YEAR` (default 2004→now;
> set start to `1950` for the full archive).

---

## Run it

```bash
cd backend
./run.sh           # installs deps, seeds the DB, serves on http://127.0.0.1:8000
```

Then open <http://127.0.0.1:8000> to play the Daily Quiz, Arcade Over/Under, and
view the guest-first profile. The seed step prints the validation pipeline
summary, including the **planted hallucination that gets rejected**:

```
Seed complete. Committed 6 questions, rejected 1.
  REJECTED [wins] 'How many race wins did Michael Schumacher take with Ferrari (1996-2006)?':
    expected 72, LLM proposed 80 -- Hallucination detected ...
```

### Tests

```bash
cd backend
python3 -m pytest -q     # 52 tests: scoring, validation, API trust boundary, all modes, ETL ingestion
```

### Install on your phone (PWA)

The frontend ships a web-app manifest and iOS meta tags, so on iPhone you can
open the site in Safari → **Share** → **Add to Home Screen** to launch it
full-screen like a native app. Icons are pre-generated; regenerate with
`python3 frontend/gen_icon.py` (requires Pillow).

---

## Layout

```
backend/
  app/
    scoring.py      # exp-decay scoring engine (PRD §2)
    validation.py   # deterministic anti-hallucination layer (Pipeline §3)
    db.py           # SQLite schema mirroring the staging + production tables
    etl.py          # real Jolpica ETL: rate-limited, disk-cached, weekly (Pipeline §1)
    seed.py         # synthetic seed + data-driven generator + validation pipeline
    service.py      # deterministic per-mode provisioning, token store, scoring, arcade
    models.py       # Pydantic API contracts (no answer leaves the server)
    main.py         # FastAPI app + static frontend mount
  tests/            # pytest suite
frontend/
  index.html        # HUD, unified quiz view, arcade, profile, PWA tags
  style.css         # constructor CSS-var theming + odometer reveal + mobile
  app.js            # guest-first localStorage, server-side scoring, countdown, share
  manifest.json     # PWA manifest;  icon-*.png  generated by gen_icon.py
docs/               # original design documents
```

## API

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/api/v1/health` | liveness + active question count |
| `GET`  | `/api/v1/quiz/{mode}` | `daily` (5) / `race_week` (5) / `one_shot` (3); tracking tokens, **no answers** |
| `POST` | `/api/v1/quiz/verify` | `{tracking_token, guess}` → server-side score |
| `GET`  | `/api/v1/arcade/pair` | over/under matchup (non-competitive v1) |

The daily/race-week/one-shot selection is **deterministic per period** (seeded by
mode + UTC date), so everyone gets the same set within a period and it rotates —
the prototype's stand-in for the 00:00 UTC cron provisioning (Architecture §1.1).

## Implemented systems

- Three exact-numerical modes (Daily / Race-Week / One-Shots) + Arcade Over/Under
- Server-authoritative exp-decay scoring; answers never leave the server
- Deterministic anti-hallucination validation over **~560 generated questions**
- Guest-first localStorage: lifetime points, accuracy, streaks, achievements

### Question variety

The validation engine is metric + aggregation based, so a single deterministic
SQL path verifies many creative question shapes against a synthetic but
internally-consistent race-by-race log (grids, DNFs, points, circuits):

- **Metrics:** wins, podiums, poles, fastest laps, points, DNFs, points-finishes,
  front-row starts, net positions gained, distinct constructors, seasons, poles
  converted to wins.
- **Aggregations:** career/stint totals, single-season best, "in which year…",
  first-season milestones, percentage-of-races, and head-to-head differences.
- **Categories:** career, qualifying, reliability, racecraft, rates, milestones,
  single-season, consistency, per-circuit, head-to-head — surfaced as UI chips,
  with `answer_kind` (count / points / year / percentage) driving the input.
- Per-period play caps with a testing-replay override
- Live countdown HUD from a 2026 calendar with off-season pivot
- Constructor theming, odometer score reveal, native share, PWA/add-to-home-screen

## Next steps toward the full project

- Swap SQLite → Postgres and the in-memory token store → Redis.
- ~~Replace the seed with the real Jolpica ETL~~ — **done** (`backend/app/etl.py`:
  rate-limited token bucket, disk cache, weekly cadence, Pipeline §1.1). Remaining:
  put a real LLM synthesizer behind the same validation gate (currently the
  data-driven `mock_llm_questions` generator stands in).
- Run the weekly ETL on a real scheduler (cron / Celery beat) instead of the
  boot-time freshness gate.
- Split the frontend into the Next.js + Tailwind app (NextAuth guest-first flow,
  Framer Motion odometer).
- Add the 00:00 UTC cron provisioning, the Global Leaderboard + Constructors
  Championship (server-reconstructed totals, Architecture §2.2), and ad-network
  integration.
