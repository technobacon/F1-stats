# Project status & roadmap

_Last updated: 2026-06-10_

A snapshot of where **F1 Stat Guesser** is, how it fits together, and what could
come next. For the question design specifically, see
[`question-types.md`](./question-types.md).

---

## What it is

A guest-first Formula 1 trivia game. You guess a numeric stat (e.g. "How many
career wins does Lewis Hamilton have?") and are scored on how close you get, with
an exponential-decay curve. Three timed quiz modes plus an endless arcade.

Live deploy target: **Render** (free tier), auto-deploying the `main` branch.

---

## Current state (shipped)

### Game
- **Daily General Challenge** — 10 questions across all of F1 history.
- **Daily Race Challenge** — 10 questions on teams, circuits and race-day feats.
- **Hardcore** — 3 brutal questions, no slider (type-only).
- **Arcade Over/Under** — endless "who has more?" head-to-heads, streak-based.
- Server-authoritative scoring: the answer never reaches the client; guesses are
  scored server-side (`backend/app/scoring.py`).
- Per-period caps (one run per day per daily mode), with a testing replay.

### Questions & data
- **1,000-question curated bank** committed at `backend/app/data/questions.json`,
  served with no network (`F1_DATA_SOURCE=dataset`). Companion
  `arcade.json` powers Over/Under offline.
- **20 question types** across **drivers, teams (constructors), and circuits** —
  wins/podiums/poles/points, comebacks, average finish, distinct circuits won,
  team 1-2s, venue facts, head-to-head differences, and more.
- **Anti-hallucination validation**: every answer is recomputed from staging
  data by `validation.compute_metric` before it can ship — nothing is
  hand-written or trusted from text.
- **Era-biased serving**: the mix leans modern (2014+) with a boost on the
  Senna/Prost/Mansell/Piquet (1984–93) and Schumacher (1994–2006) eras; older
  eras appear occasionally (`service.ERA_WEIGHT_BANDS`).
- **Real data source**: a rate-limited, disk-cached, weekly-gated Jolpica
  (Ergast) ETL (`backend/app/etl.py`) covering 1980→present can rebuild the bank
  (`python -m app.seed --export`). The synthetic in-code seed is the offline
  fallback.

### Frontend
- Polished **landing page**: hero ("Welcome to F1 Stat Guesser") in Titillium
  Web, race-themed background that is **photo-ready** (drop `frontend/hero.jpg`),
  four mode cards, feature strip.
- Sticky blurred top bar, pill nav, single `navigate()` router, team-colour
  theming, live countdown HUD, share, PWA / add-to-home-screen.

### Quality
- **64 tests passing** (`cd backend && python3 -m pytest -q`): scoring,
  validation (incl. every new metric/aggregation), API trust boundary, all
  modes, ETL ingestion, and the dataset export→load→serve round-trip.

---

## Architecture

```
backend/app/
  validation.py  anti-hallucination engine: (metric, aggregation) over
                 entity × years × constructor/circuit -> recomputed answer
  seed.py        question generator + dataset export/load + data-source router
  etl.py         Jolpica ETL: token-bucket rate limit, disk cache, weekly gate
  service.py     quiz provisioning (era-weighted), token store, scoring, arcade
  scoring.py     exponential-decay percentage-error scoring
  db.py          SQLite schema + lightweight migrations
  main.py        FastAPI app; self-seeds on boot; serves the static frontend
  data/          questions.json (the bank) + arcade.json (offline arcade)
frontend/        index.html, style.css, app.js (guest-first, localStorage)
docs/            design docs, question-types.md, this file
```

Data flow: **Jolpica API → staging tables → generator → validation → production
questions → (snapshot) questions.json → served to the client**. The site
normally serves the committed snapshot; the ETL path is for rebuilding it.

### Substitutions vs. the full design (each a localized swap)
| Production target | Today | Why |
|---|---|---|
| PostgreSQL | SQLite | zero-dependency, runs anywhere |
| Redis token cache | in-memory dict | single-process |
| Real LLM synthesizer | data-driven `mock_llm_questions` | deterministic, offline |
| Next.js + Tailwind | vanilla HTML/CSS/JS | one runnable service |

---

## How to run / rebuild

```bash
# Run locally (serves the committed bank, no network):
cd backend && F1_DATA_SOURCE=dataset ./run.sh        # http://127.0.0.1:8000

# Rebuild the 1,000-question bank from live F1 data:
cd backend && F1_DATA_SOURCE=jolpica F1_ETL_START_YEAR=1980 python3 -m app.seed --export

# Tests:
cd backend && python3 -m pytest -q
```

Key env vars: `F1_DATA_SOURCE` (`dataset` | `jolpica` | `synthetic`),
`F1_DB_PATH`, `F1_ETL_START_YEAR` / `F1_ETL_END_YEAR`.

---

## Deployment

- `render.yaml` deploys **`main`**, `F1_DATA_SOURCE=dataset` (instant boot, no
  network), DB on `/tmp` (ephemeral — rebuilt from the committed bank each boot).
- Workflow: branch → PR into `main` → merge → Render auto-deploys.
- **Manual step**: confirm the service's **Branch = `main`** in the Render
  dashboard (Render stores it there too, not only in `render.yaml`).

---

## Known gaps / caveats
- **No hero photo bundled** — copyright. Add a licensed image at
  `frontend/hero.jpg` to use one (the CSS scene shows until then).
- **Accounts / sync / Global Leaderboard are stubs** — progress is local-only
  (`localStorage`); the "Sync" button is a placeholder.
- **No real LLM** in the loop yet — questions come from the deterministic
  generator behind the validation gate (the gate is the point; the LLM is a
  drop-in).
- **Branch hygiene**: this work has lived on one long-running branch that's been
  squash-merged repeatedly, which causes merge-conflict reconciliation. Prefer a
  fresh branch off `main` per task going forward.
- Pre-1994 **qualifying** data is sparse upstream, so pole/front-row questions
  skew to 1994+ (race-result questions still cover 1980+).
- Countdown calendar is a hardcoded 2026 schedule (`app.js`), not a live feed.

---

## What could be next

**Content & gameplay**
- Add a licensed hero image; per-mode card artwork.
- More question types: teammate head-to-heads, podium/points streaks,
  nationality cuts, "which driver/team" multiple-choice, decade rounds.
- Difficulty calibration / adaptive difficulty from answer telemetry.
- Expand achievements; daily-streak rewards; weekly themed rounds.

**Platform**
- Global Leaderboard + accounts with **server-reconstructed** totals (never
  trust the client blob), per the trust-boundary design.
- Migrate SQLite → Postgres and the in-memory token store → Redis.
- Run the weekly ETL on a real scheduler (cron / Celery beat) instead of the
  boot-time gate; keep `questions.json` refreshed automatically.
- Real LLM question synthesizer behind the existing validation gate.

**Product**
- Analytics + the ad slots already stubbed in the reveal/summary views.
- Next.js + Tailwind frontend (NextAuth guest-first, Framer Motion reveal).
- Accessibility pass, i18n, social share cards.
- A persistent disk or paid Render tier to avoid cold-start rebuilds (minor,
  since the bank load is fast).
