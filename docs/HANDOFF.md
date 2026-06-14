# GridMaster — Engineering Handoff

_Last updated: 2026-06-13 · Branch of record: `main` · Live target: Render (free tier)_

This is the single document to read first when picking up the project. It covers
what the game is, exactly where it stands, how it's built, how to run/test/deploy
it, and what's left to do. For deeper design intent see the spec docs
([PRD](./PRD.md), [Architecture Blueprint](./ARCHITECTURE_BLUEPRINT.md),
[Technical Pipeline Specs](./TECHNICAL_PIPELINE_SPECS.md)); for the as-built
mapping see [Implementation Notes](./IMPLEMENTATION_NOTES.md); for question design
see [question-types.md](./question-types.md); for the running snapshot see
[STATUS.md](./STATUS.md).

---

## 1. TL;DR — current state

GridMaster is a **guest-first Formula 1 trivia game**: you guess a numeric
stat (e.g. "career wins for Hamilton") and are scored by how close you are, on an
exponential-decay curve. It is a **single runnable FastAPI service** that serves a
vanilla HTML/CSS/JS frontend plus a JSON API, backed by SQLite.

It is **feature-complete for a launch** and past prototype stage:

- Five game modes, server-authoritative scoring, a 1,000-question validated bank.
- Real accounts (first-party, no OAuth), server-verified leaderboards (all-time /
  weekly / daily), a Constructors' Championship, server-side daily streaks.
- A Wordle-style spoiler-free share result (the growth lever).
- Free, durable accounts via **Litestream** replication (opt-in via env).
- First-party, privacy-respecting **analytics** + token-gated dashboard.
- **124 backend tests passing.**

**Two manual steps remain to be fully live** (see §3). Nothing in the code blocks
launch; both are dashboard/env settings.

---

## 2. What it is (product)

| Mode | What | Cap | Competitive? |
|---|---|---|---|
| **Daily General Challenge** | 6 questions across all F1 history | 1×/UTC day | Yes (leaderboard) |
| **Daily Race Challenge** | 6 questions on teams/circuits/race-day feats | 1×/UTC day | Yes |
| **Hardcore** | 3 brutal questions, type-only (no slider) | — | Yes |
| **Free Practice** | endless single questions, with an anti-scouting penalty | unlimited | **No** (never recorded) |
| **Arcade Over/Under** | endless "who has more?" driver head-to-heads | unlimited | No (local streak) |

The daily/race/hardcore selection is **deterministic per (mode, UTC day)** — like
Wordle, everyone gets the same set, and it rotates daily. Scoring is always
server-side; the true answer never reaches the client until after a guess.

---

## 3. Go-live checklist (manual steps)

The code is deployed by merging to `main` (Render auto-deploys). Two settings are
**not** in the repo and must be set in the Render dashboard:

1. **Confirm the service branch is `main`.** Render stores the deploy branch in
   its own dashboard, not only in `render.yaml`. Verify it says `main`.
2. **Set `F1_ANALYTICS_TOKEN`** to a strong secret to enable the `/analytics`
   dashboard. (Event *collection* runs regardless; this only gates *viewing*.)
3. **(Recommended) Enable durable accounts** by creating a free object-storage
   bucket and setting the five `LITESTREAM_*` env vars (see §7). Until then,
   accounts/leaderboards are wiped on every redeploy and cold start.

Already handled in `render.yaml`: `F1_DEV_TOOLS=0` (answer-key endpoint off),
`F1_DATA_SOURCE=dataset` (instant offline boot), Litestream binary fetched at
build, `start.sh` entrypoint.

---

## 4. Architecture at a glance

Single FastAPI app (`backend/app/main.py`) that both serves the static frontend
and exposes the `/api/v1` JSON API. SQLite via stdlib `sqlite3` (WAL mode, one
connection per request).

```
Client (frontend/app.js, localStorage guest-first)
  │  GET /api/v1/quiz/{mode}  → questions + opaque tracking tokens (NO answers)
  │  POST /api/v1/quiz/verify → server scores the guess, records a play_event
  ▼
FastAPI (main.py)
  ├─ service.py    quiz/practice/arcade provisioning, in-memory token store
  ├─ scoring.py    exponential-decay percentage-error scoring (the only scorer)
  ├─ auth.py       accounts, sessions, play_events, leaderboards, streaks
  ├─ analytics.py  first-party event ingest + aggregate reporting
  ├─ validation.py anti-hallucination metric/aggregation recomputation engine
  ├─ seed.py       question generator + dataset load/export + source router
  ├─ etl.py        real Jolpica ETL (rate-limited, disk-cached, weekly)
  └─ db.py         SQLite schema + migrations
```

### Three cross-cutting invariants (do not break these)

1. **Server-authoritative scoring.** The verified answer is computed/held
   server-side and returned only *after* a guess. No client-facing payload carries
   the answer. (`models.py` deliberately omits it; `service.build_quiz` stashes it
   in the token store.)
2. **Anti-hallucination validation.** Every question's answer is independently
   recomputed from staging data (`validation.compute_metric`) before it can ship;
   a planted wrong answer is rejected as a regression test.
3. **Trust boundary.** Leaderboards/totals are rebuilt only from server-scored
   `play_events` — never from client-supplied aggregates. A `(identity, question,
   day)` unique index makes the deterministic daily un-replayable for points.

---

## 5. Feature inventory (what's built, and where)

| Area | Status | Key code |
|---|---|---|
| Exp-decay scoring (server-only) | ✅ | `scoring.py` |
| 5 game modes + deterministic daily | ✅ | `service.py`, `frontend/app.js` (`MODES`) |
| 1,000-question validated bank (1950–2026) | ✅ | `data/questions.json`, `seed.py`, `validation.py` |
| Anti-hallucination gate | ✅ | `validation.py` |
| Real Jolpica ETL (weekly, cached) | ✅ | `etl.py`, `.github/workflows/weekly-data-refresh.yml` |
| Accounts (PBKDF2 + sessions), guest→account merge | ✅ | `auth.py` |
| Replay-proof leaderboard | ✅ | `auth.record_event` + `uq_play_events_dedup` (`db.py`) |
| All-time / weekly / daily leaderboards | ✅ | `auth.leaderboard`, `/api/v1/leaderboard?period=` |
| Constructors' Championship | ✅ | `auth.team_leaderboard`, `/api/v1/leaderboard/teams` |
| First-run team-selection onboarding | ✅ | `auth.team_overview`, `/api/v1/teams/overview`, `frontend/app.js` (`TeamPicker`) |
| Server-side daily streaks | ✅ | `auth.daily_streak` |
| Wordle-style spoiler-free share | ✅ | `frontend/app.js` (`buildShareText`) |
| Synthesized sound effects + header toggle | ✅ | `frontend/sound.js`, `app.js` (`SoundToggle`) |
| Constructor theming (11 teams) | ✅ | `frontend/app.js` (`TEAMS`), `style.css` |
| Durable accounts (Litestream) | ✅ (opt-in) | `start.sh`, `litestream.yml`, `Dockerfile`, `render.yaml` |
| First-party analytics + dashboard | ✅ (token-gated) | `analytics.py`, `frontend/analytics.html` |
| Live countdown HUD + race-week panel | ✅ (hardcoded 2026 cal) | `frontend/app.js` (`SESSIONS_2026`) |
| PWA / add-to-home-screen | ✅ | `frontend/manifest.json`, iOS meta tags |
| Ad slots | ⛔ stubbed (placeholders only) | `frontend/index.html` (`.ad-slot`) |
| Real LLM question synthesizer | ⛔ data-driven stand-in behind the gate | `seed.mock_llm_questions` |

---

## 6. Environment variables

| Var | Default | Purpose |
|---|---|---|
| `F1_DATA_SOURCE` | `dataset` | `dataset` (committed bank, offline) · `jolpica` (real ETL) · `synthetic` (in-code fallback) |
| `F1_DB_PATH` | `backend/f1stats.db` (`/tmp/f1stats.db` on Render) | SQLite file location |
| `F1_DEV_TOOLS` | `1` (set to `0` in prod) | `/api/v1/dev/questions` answer-key endpoint on/off |
| `F1_ANALYTICS_TOKEN` | unset | Gates the `/analytics` dashboard + summary API; unset = disabled |
| `F1_ETL_START_YEAR` / `F1_ETL_END_YEAR` | `2004`/current (CI uses `1950`) | ETL ingest span |
| `PORT` | `8000` | Server port (Render injects it) |
| `LITESTREAM_REPLICA_BUCKET` | unset | Object-storage bucket; **unset = ephemeral DB** |
| `LITESTREAM_REPLICA_ENDPOINT` / `_REGION` / `_PATH` | — / — / `f1stats` | S3-compatible target |
| `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` | unset | Bucket credentials |

---

## 7. Durability (Litestream) — how it works

Render's free filesystem is wiped on every redeploy **and** cold start, so the
SQLite accounts/sessions/play-history would vanish. Rather than a paid disk or a
Postgres migration, `start.sh` wraps uvicorn with **Litestream**: it restores the
latest DB snapshot from object storage on boot, then streams every change back to
the bucket while the app runs (final flush on graceful SIGTERM).

- **Opt-in & graceful:** with `LITESTREAM_REPLICA_BUCKET` unset (local dev, tests,
  Docker hosts with their own disk) it just runs uvicorn directly. The application
  code and SQL are unchanged — durability is pure orchestration.
- **Setup (~5 min, free):** create a Backblaze B2 (10 GB free, no card) or
  Cloudflare R2 bucket, make an app key, and set the five `LITESTREAM_*` vars (see
  README → *Free durable accounts* for exact values).
- **Caveat:** protects against *graceful* shutdown (Render's default). An
  ungraceful kill could lose ~1 s of writes. Fine "for now"; a paid disk removes it.

Verified end-to-end (file replica): register a user → delete the DB → next boot
restores it → login still works.

---

## 8. Analytics — how it works

First-party and self-contained (no Google Analytics, no third-party tag, no
cross-site cookies), consistent with the project's own-auth/own-scoring design.

- **Collection (always on, pseudonymous):** `frontend/app.js` batches allow-listed
  events keyed by the existing guest `anon_id` + a per-tab session id, flushed via
  `navigator.sendBeacon` on page hide. Server validates/bounds them into the
  persistent `analytics_events` table (`analytics.py`), pruned to 180 days on boot.
- **Aggregate-only:** this never touches scoring or the leaderboard.
- **Dashboard (token-gated):** `GET /analytics` (page) + `GET /api/v1/analytics/summary`
  (data, requires `F1_ANALYTICS_TOKEN`). Reports DAU/WAU/MAU, the
  open→start→complete→share→signup funnel with conversion rates, D1/D7 retention,
  mode mix and account growth over a 7/14/30-day window.

---

## 9. Run / test / deploy

```bash
# Run locally (committed bank, no network):
cd backend && F1_DATA_SOURCE=dataset ./run.sh          # http://127.0.0.1:8000

# Tests (124):
cd backend && python3 -m pytest -q

# Rebuild the 1,000-question bank from live F1 data (1950→present):
cd backend && F1_DATA_SOURCE=jolpica F1_ETL_START_YEAR=1950 python3 -m app.seed --export
```

Deploy: branch → merge to `main` → Render auto-deploys via `render.yaml`
(`buildCommand` installs deps + Litestream; `startCommand` runs `backend/start.sh`).
A GitHub Action (`weekly-data-refresh.yml`) rebuilds the bank from Jolpica every
Monday behind a sanity gate.

---

## 10. Known gaps / caveats

- **Ephemeral DB unless Litestream is configured** — see §3/§7. The single most
  important go-live setting.
- **In-memory token store & login rate-limiter** are per-process — correct for one
  free instance, but break across multiple workers/instances. Move to Redis before
  scaling horizontally.
- **Hardcoded 2026 countdown calendar** (`SESSIONS_2026` in `app.js`), not a live
  feed; update or wire to a schedule source for 2027.
- **No bundled hero image** (copyright) — drop a licensed `frontend/hero.jpg`; a
  CSS scene shows until then.
- **Ads are stubbed** — `.ad-slot` placeholders exist; no network wired.
- **No real LLM** — the data-driven generator stands in behind the validation gate
  (the gate is the point; the LLM is a drop-in).
- **Pre-1994 qualifying data is sparse upstream**, so pole/front-row questions skew
  to 1994+ (race-result questions cover the full span).
- **Asset cache-buster:** `index.html` references `app.js?v=N` / `style.css?v=N`.
  **Bump `N` whenever you change those files** or returning users get stale assets.

---

## 11. Roadmap (prioritized for "alive, trendy, daily return")

**Highest leverage next:**
1. **Post-race recap quizzes** — a fresh set the morning after each real Grand Prix
   (makes it feel alive week to week).
2. **PWA push reminders** — streak-about-to-break / new-daily nudges (manifest
   already shipped; push is within reach).
3. **Wire the ad slots** (analytics is now in place to measure RPM/impact).

**Platform hardening:**
4. SQLite → Postgres and in-memory token store → Redis (when scaling past one
   instance).
5. Run the weekly ETL on a real scheduler instead of the boot-time gate.
6. Real LLM synthesizer behind the existing validation gate.

**Content & polish:**
7. More question types (teammate H2H, streaks, "which driver/team" multiple-choice,
   decade rounds); adaptive difficulty from answer telemetry.
8. Licensed hero/card art; accessibility pass; i18n; social share images.

---

## 12. Repo map

```
backend/
  app/
    main.py        FastAPI routes + lifespan (self-seeds, prunes analytics) + static mount
    scoring.py     exp-decay scoring (the only scorer)
    service.py     quiz/practice/arcade provisioning, token store, era weighting
    auth.py        accounts, sessions, play_events, leaderboards, streaks, teams
    analytics.py   event ingest (allow-listed, bounded) + aggregate reporting
    validation.py  anti-hallucination metric+aggregation engine
    seed.py        question generator, dataset load/export, source router
    etl.py         real Jolpica ETL (token bucket, disk cache, weekly gate)
    db.py          SQLite schema + migrations (data tables vs durable account/analytics tables)
    models.py      Pydantic API contracts (no answer leaves the server)
    data/          questions.json (the bank) + arcade.json (offline arcade)
  tests/           pytest suite (124): scoring, validation, api, auth, analytics, etl, dataset
  start.sh         prod entrypoint: Litestream restore+replicate around uvicorn (opt-in)
  litestream.yml   S3 replica config (env-driven)
  run.sh           local dev runner
frontend/
  index.html       HUD (+ sound toggle), modes, quiz, arcade, profile, leaderboards, modals, PWA tags
  app.js           guest-first state, server scoring, analytics tracker, share grid, team onboarding
  sound.js         Web Audio sound effects, synthesized at runtime (no audio assets)
  style.css        constructor CSS-var theming + odometer reveal + responsive
  analytics.html   token-gated analytics dashboard (no deps)
  manifest.json    PWA manifest; icon-*.png generated by gen_icon.py
docs/              HANDOFF (this) · STATUS · IMPLEMENTATION_NOTES · PRD · ARCHITECTURE_BLUEPRINT
                   · TECHNICAL_PIPELINE_SPECS · question-types · README (index)
Dockerfile         portable container (bakes in Litestream)
render.yaml        Render blueprint (build/start/env)
```

---

## 13. Gotchas for the next developer

- **Don't trust the client for anything competitive.** Scores, totals, streaks,
  team standings are all server-recomputed from `play_events`. Keep new
  competitive features on that side of the boundary.
- **The daily set is deterministic** (seeded by mode + UTC date). Tests rely on
  this; don't make it random.
- **Account/analytics tables survive the reseed.** `reset_db()` drops only the
  staging + question tables; `users`, `auth_sessions`, `play_events`,
  `analytics_events` are deliberately excluded. Don't add them to the drop list.
- **Schema changes need a migration.** `CREATE TABLE IF NOT EXISTS` won't alter an
  existing table; add columns in `db._migrate` before the SCHEMA's indexes run
  (see how `identity_key`/`period` were added).
- **Bump the `?v=N` asset version** in `index.html` when changing `app.js`/`style.css`.
- **Long-running branch hygiene:** historically this work lived on one branch that
  was squash-merged repeatedly, causing merge reconciliations. Prefer a fresh
  branch off `main` per task.
