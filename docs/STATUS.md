# Project status & roadmap

_Last updated: 2026-07-12_

A snapshot of where **GridMaster** is, how it fits together, and what could
come next. New here? Read [`HANDOFF.md`](./HANDOFF.md) first — it's the full
engineering handoff. For question design see [`question-types.md`](./question-types.md).

> **Latest work (security & robustness pass + refactor):** a fresh-eyes code
> review, fixed and shipped. **(1) Trust boundary** — derived slider bounds are
> now seeded with a **server-side secret** (`F1_SLIDER_SALT` or a persisted
> random salt in the new `app_kv` table), closing an exploit where the public
> bounds algorithm could be inverted to recover the answer within a few percent;
> **Free Practice is rate-limited server-side** (60 draws / 10 min / client) so
> the bank can't be script-farmed for answers. **(2) Auth** — password length
> caps are enforced in **bytes, identically at registration and login** (a
> multibyte password >1024 bytes could previously register but never log in);
> expired sessions are bulk-pruned at boot. **(3) Hardening** — a security-headers
> middleware (strict CSP with `script-src 'self'`, nosniff, Referrer-Policy,
> X-Frame-Options); the analytics dashboard's inline script moved to
> `frontend/analytics.js` to comply. **(4) Plumbing** — one request-scoped DB
> connection shared via a FastAPI dependency (`main.db_conn`) replaces the
> per-endpoint open/close boilerplate *and* the per-request schema DDL;
> score-recording failures are logged instead of silently swallowed; test runs
> no longer dirty the working tree (`dataset_meta.json` stamps next to the
> export target). **(5) Tests** — the suite runs in ~20s (was ~2.5 min) via a
> once-per-session seeded template DB + test-only PBKDF2 rounds, with new
> regression tests for the salt, password symmetry, throttle, headers and
> session pruning. The same pass was ported to the `scapemaster/` fork (the
> fork policy is now documented in the root README).
>
> **Earlier (professional-polish passes):** six back-to-back design
> passes to shed the "AI-generated" tells. **(1) Visual identity** — a
> documented `:root` **design-token** system (neutral ramp, type scale, radii,
> elevation, motion), a real **type pairing** (Titillium Web for UI + headings,
> **JetBrains Mono** for the timing-board data/labels), a dependency-free inline
> **SVG icon set** (`frontend/icons.js`) replacing the UI-chrome emoji, flatter
> surfaces, a consistent keyboard focus ring, and a vector **favicon**.
> **(2) Layout & hierarchy** — radii tightened and every hard-coded corner moved
> onto the `--r-*` tokens, padding/gaps normalized onto the `--sp-*` grid, glass
> backgrounds made solid (blur kept only on the sticky bar), denser data rows
> against the airy hero, plus **loading skeletons** and **designed empty states**
> for the garage cards. **(3) Copy & tone** — a de-exclaimed, F1-broadcast voice:
> peppy enthusiasm and stray emoji pulled from toasts/banners, and the jokey
> network-failure line replaced with calm, branded edge-case microcopy.
> **(4) Motion & polish** — every transition consolidated onto one easing curve
> (`--ease`) and two interaction durations (`--t-fast`/`--t-med`), with the
> deliberately-weighted reveal animations (the answer car's drive, the score
> count-up) kept distinct; the per-question and session-total scores now both
> count up; and a full **`prefers-reduced-motion`** path (CSS clamps motion,
> the JS count-ups and answer-slide snap to their end state).
> **(5) Trust signals** — the "this is a real, shipped product" scaffolding: a
> proper **About / methodology page** (how scoring works, where the data comes
> from, and — the real differentiator — *how every answer is recomputed and
> verified* before it ships), plus a privacy section; a restructured **footer**
> with About / scoring / privacy / contact links + a build version; a branded
> **404 page** (served as HTML to browsers, JSON kept for API clients); and
> aligned `theme-color` across the head + manifest.
> **(6) Product depth** — the "shipped software" surfaces: a real **Settings**
> dialog (sound, light theme, a manual **reduce-motion** override, streak
> reminders, constructor, reset — opened from a header gear, consolidating the
> scattered toggles), a one-time **first-run scoring explainer** that teaches the
> closeness curve before you're scored on it, and an **accessibility** pass
> (`role="switch"` toggles, `aria-current` on the active tab, Escape/backdrop
> close + focus-return on the dialogs, a polite-live toast). The curve slider
> already had arrow-key support; fonts already ship `display=swap`.
> The Wordle share-grid squares and achievement-badge medallions intentionally
> stay emoji (the share squares must survive as plain text; bespoke badge art is
> its own pass).
>
> **Earlier (feel like home):** a personalized **"your garage"** home for
> signed-in players (global rank + day-over-day movement, personal Constructors'
> Championship stake + within-team board, closest-badge progress, a streak
> heatmap, last-Daily percentile), an opt-in **local streak reminder** (root-scope
> service worker), a tightened **race-week panel**, and a team-coloured
> **Hungaroring** hero outline.
>
> **Earlier (feel + onboarding):** a synthesized **Web Audio sound layer**
> (`frontend/sound.js`, zero audio assets) with a header on/off toggle, and a
> **first-run team-selection prompt** that shows per-team headcounts + the
> Constructors' Championship standings.
>
> **Earlier (engagement & retention):** streaks + freeze, social proof,
> deep-linked sharing, a 59-badge achievement system, purple/green sector flash,
> team-colour legibility, and optional sign-up email. See
> [`HANDOFF_ENGAGEMENT.md`](./HANDOFF_ENGAGEMENT.md), with rationale in
> [`ENGAGEMENT.md`](./ENGAGEMENT.md) and growth in [`MARKETING.md`](./MARKETING.md).

---

## What it is

A guest-first Formula 1 trivia game. You guess a numeric stat (e.g. "How many
career wins does Lewis Hamilton have?") and are scored on how close you get, with
an exponential-decay curve. Three daily quiz modes, an endless Free Practice mode,
and an endless arcade.

Live deploy target: **Render** (free tier), auto-deploying the `main` branch.

---

## Current state (shipped)

### Game
- **Daily Challenge** — 6 questions drawn from one general bank spanning all of
  F1 history (drivers, teams, circuits and race-day feats). The separate Race
  Challenge has been merged back into this general bank for now; the race-week
  framework will be revisited later.
- **Free Practice** — endless single questions with an anti-scouting penalty;
  non-competitive, **never recorded** (no totals/leaderboard write).
- **Arcade Over/Under** — endless "who has more?" head-to-heads, streak-based.
- Server-authoritative scoring: the answer never reaches the client; guesses are
  scored server-side (`backend/app/scoring.py`).
- Per-period caps (one run per day per daily mode), with a testing replay.

### Accounts, leaderboards & retention
- **Accounts** (PBKDF2 + server sessions), guest→account merge, server-side
  saving — totals are rebuilt from server-scored `play_events`, never the client.
- **Replay-proof leaderboard**: a `(player, question, day)` unique guard means the
  deterministic daily set can't be re-run to inflate a total.
- **Daily / weekly / all-time** leaderboards (resetting windows) and a
  **Constructors' Championship** bucketing verified points by team faction.
- **Server-side daily streaks**, recomputed from play history.
- **"Your garage" home** (signed-in): a personalized strip with your **global rank
  + day-over-day movement + percentile** (`/leaderboard/me`), your **personal stake
  in the Constructors' Championship** and a **within-team leaderboard**
  (`/leaderboard/team`), the **badges you're closest to** (progress bars), a
  **streak heatmap** (`/user/play-history`, GitHub-style), and an "I beat X%"
  echo of your last Daily. Guests see a lighter version with sign-in CTAs.
- **Opt-in local streak reminder**: a root-scope service worker (`/sw.js`) fires a
  local notification when you reopen with a streak at risk, and schedules an
  evening nudge where the browser supports Notification Triggers. No push server.
- **Spoiler-free Wordle-style share grid** with the day's puzzle number.
- **First-party analytics** (`analytics.py`): pseudonymous, self-contained event
  pipeline + token-gated `/analytics` dashboard (DAU/WAU/MAU, play funnel, D1/D7
  retention, mode mix). Aggregate-only; never feeds scoring/leaderboard.
- `/api/v1/dev/questions` (answer key) is **off in production** (`F1_DEV_TOOLS=0`).

### Questions & data
- **~1,200-question curated bank** committed at `backend/app/data/questions.json`
  (modern-era curation via `backend/scripts/curate_questions.py`; ~70% from 2020s
  regulars), served with no network (`F1_DATA_SOURCE=dataset`). Companion
  `arcade.json` powers Over/Under offline (same driver-eligibility filter).
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
  (Ergast) ETL (`backend/app/etl.py`) covering the full **1950→present** World
  Championship can rebuild the bank (`python -m app.seed --export`); the committed
  bank spans 1950–2026. The synthetic in-code seed is the offline fallback.

### Frontend
- Polished **landing page**: hero ("Welcome to GridMaster") in Titillium
  Web, race-themed background that is **photo-ready** (drop `frontend/hero.jpg`),
  four mode cards, feature strip.
- Sticky blurred top bar, pill nav, single `navigate()` router, team-colour
  theming, live countdown HUD, share, PWA / add-to-home-screen.
- **Synthesized sound effects** (`frontend/sound.js`, Web Audio, no audio assets):
  slider click, answer-reveal riser, F1 "lights out" session start, purple/green
  sector drive-bys, lock-in / achievement / session-complete / arcade cues — with
  an always-visible **header on/off toggle** (persisted).
- **First-run onboarding**: brand-new guests are prompted to pledge a constructor,
  shown each team's fan headcount and the Constructors' Championship leader
  (`/api/v1/teams/overview`).

### Quality
- **167 tests passing in ~20s** (`cd backend && python3 -m pytest -q`): scoring,
  validation (incl. every metric/aggregation), API trust boundary, all modes,
  accounts/leaderboards/streaks, the personal rank / team-stake / play-history
  garage endpoints, the replay-proof dedup, analytics ingest + reporting + token
  gate, ETL ingestion, the dataset export→load→serve round-trip, and the
  security regressions (salted bounds, password byte-caps, practice throttle,
  security headers, session pruning).

---

## Architecture

```
backend/app/
  validation.py  anti-hallucination engine: (metric, aggregation) over
                 entity × years × constructor/circuit -> recomputed answer
  seed.py        question generator + dataset export/load + data-source router
  etl.py         Jolpica ETL: token-bucket rate limit, disk cache, weekly gate
  service.py     quiz/practice/arcade provisioning (era-weighted), token store
  scoring.py     exponential-decay percentage-error scoring
  auth.py        accounts, sessions, play_events, leaderboards, streaks, teams
  analytics.py   first-party event ingest (allow-listed) + aggregate reporting
  db.py          SQLite schema + lightweight migrations
  main.py        FastAPI app; self-seeds on boot; serves the static frontend
  data/          questions.json (the bank) + arcade.json (offline arcade)
backend/start.sh   prod entrypoint: Litestream restore+replicate around uvicorn
frontend/        index.html, style.css, app.js (guest-first, localStorage),
                 analytics.html (token-gated dashboard)
docs/            HANDOFF, design docs, question-types.md, this file
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

# Rebuild the 2,000-question bank from live F1 data:
cd backend && F1_DATA_SOURCE=jolpica F1_ETL_START_YEAR=1980 python3 -m app.seed --export

# Tests:
cd backend && python3 -m pytest -q
```

Key env vars: `F1_DATA_SOURCE` (`dataset` | `jolpica` | `synthetic`),
`F1_DB_PATH`, `F1_ETL_START_YEAR` / `F1_ETL_END_YEAR`, `F1_DEV_TOOLS` (`0` in
prod), `F1_ANALYTICS_TOKEN`, `F1_SLIDER_SALT` (optional; else auto-generated and
persisted in `app_kv`), and the `LITESTREAM_*` replica vars. Full table in
[`HANDOFF.md`](./HANDOFF.md) §6.

---

## Deployment

- `render.yaml` deploys **`main`**, `F1_DATA_SOURCE=dataset` (instant boot, no
  network), `F1_DEV_TOOLS=0` (answer-key endpoint off). The build fetches the
  Litestream binary; `backend/start.sh` is the entrypoint.
- DB lives at `/tmp/f1stats.db`; **set the `LITESTREAM_*` vars** to make accounts
  durable across the free host's redeploys/cold starts (see HANDOFF §7), else
  they're rebuilt empty each boot.
- Workflow: branch → merge to `main` → Render auto-deploys.
- **Manual steps** (dashboard, not in repo): confirm **Branch = `main`**; set
  `F1_ANALYTICS_TOKEN` to view `/analytics`; set the `LITESTREAM_*` vars for
  durable accounts.

---

## Known gaps / caveats
- **No hero photo bundled** — copyright. Add a licensed image at
  `frontend/hero.jpg` to use one (the CSS scene shows until then).
- **Account durability on the free host** — accounts, sessions and verified play
  history live in the SQLite file at `F1_DB_PATH`, which the free host wipes on
  redeploy/cold start. Fixed for free with **Litestream**: it replicates the DB
  to an S3-compatible bucket and restores on boot (opt-in via env; see README →
  *Free durable accounts*). A persistent disk or Postgres remain heavier options.
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

**Platform** (accounts, the server-verified leaderboards, the Constructors'
Championship, durable storage and analytics are all **done** — see *Current
state*)
- Migrate SQLite → Postgres and the in-memory token store → Redis (needed only to
  scale past one instance).
- Run the weekly ETL on a real scheduler (cron / Celery beat) instead of the
  boot-time gate; keep `questions.json` refreshed automatically.
- Real LLM question synthesizer behind the existing validation gate.

**Product**
- Post-race recap quizzes; PWA push reminders (streak/new-daily nudges).
- Wire the ad slots already stubbed in the reveal/summary views.
- Next.js + Tailwind frontend (NextAuth guest-first, Framer Motion reveal).
- Accessibility pass, i18n, social share cards.
