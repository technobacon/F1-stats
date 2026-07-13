# GridMaster

Website for F1 stat quizzes — a gamified Formula 1 statistics guessing platform.

This repo contains the design docs in [`docs/`](./docs) and a **runnable
prototype** that implements the defensible core of the system described there.

> **Ready to ship?** [`docs/LAUNCH.md`](./docs/LAUNCH.md) is the $0 go-live
> runbook (Render + free durable accounts), ~20 minutes end to end.
>
> **Where we are & what's next:** see [`docs/STATUS.md`](./docs/STATUS.md) for a
> full status snapshot and roadmap, and [`docs/question-types.md`](./docs/question-types.md)
> for the question design.
>
> **Growth & retention:** [`docs/ENGAGEMENT.md`](./docs/ENGAGEMENT.md) covers the
> return-visit hooks (streaks + freeze, social proof, deep-linked sharing,
> achievements, sector flash) and the backlog;
> [`docs/MARKETING.md`](./docs/MARKETING.md) is the minimal-effort, no-Reddit marketing plan;
> [`docs/HANDOFF_ENGAGEMENT.md`](./docs/HANDOFF_ENGAGEMENT.md) is the engineering
> handoff for that work.

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
python3 -m pytest -q     # 167 tests (~20s): scoring, validation, trust boundary, all modes, accounts, leaderboards, analytics, ETL, security
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
  index.html        # HUD (with sound toggle), unified quiz view, arcade, profile, PWA tags
  style.css         # constructor CSS-var theming + odometer reveal + mobile
  app.js            # guest-first localStorage, server-side scoring, countdown, share, onboarding
  sound.js          # Web Audio sound effects, synthesized at runtime (zero audio assets)
  manifest.json     # PWA manifest;  icon-*.png  generated by gen_icon.py
docs/               # original design documents
```

### `scapemaster/` — a deliberate fork, not shared code

[`scapemaster/`](./scapemaster) is a second game (OSRS trivia) built by copying
this app's backend and frontend wholesale. It is a **fork by design**: every
module has since diverged (different data model, question kinds, theming), and
there is no shared package between the two. The consequence is a standing
porting tax — **any fix to the shared engine pieces (scoring, auth, service
trust boundary, db, main) must be consciously applied twice**, once here and
once under `scapemaster/`. When touching those files, check whether the sibling
needs the same change; the test suites on both sides are the safety net.

## API

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/api/v1/health` | liveness + active question count |
| `GET`  | `/api/v1/quiz/{mode}` | `daily` (6) from the general bank; tracking tokens, **no answers** |
| `GET`  | `/api/v1/practice/question` | one **random** Free Practice question; non-competitive, **no answers**; optional focus filters `?category=` and `?era=` (decade windows, e.g. `2010s` / `classic`) with a full-bank fallback flagged by `focus_matched`; rate-limited per client (60 / 10 min) so the bank can't be script-farmed |
| `POST` | `/api/v1/quiz/verify` | `{tracking_token, guess, anon_id?}` → server-side score; also **records** the scored result (to the signed-in user, or to `anon_id` for a guest) — **except Free Practice**, which is never recorded |
| `POST` | `/api/v1/quiz/hint` | **Pit Wall Radio**: `{tracking_token}` → a salted, non-revealing band guaranteed to contain the answer, always tighter than the served slider band; marks the token so `verify` takes 40% off the eventual score. Idempotent per token; the exact answer still never leaves the server |
| `GET`  | `/api/v1/quiz/daily/field` | **Today's Field** — where the caller finished among *everyone* (members **and** guests) who played today's Daily: `{players, rank, points, beat_percent}`; identity from the bearer token, else `?anon_id=` |
| `GET`  | `/api/v1/arcade/pair` | over/under matchup (non-competitive v1) |
| `POST` | `/api/v1/auth/register` | `{username, password, anon_id?}` → session token + server stats; claims guest events |
| `POST` | `/api/v1/auth/login` | `{username, password, anon_id?}` → session token + server stats; claims guest events |
| `POST` | `/api/v1/auth/logout` | revokes the bearer token (`Authorization: Bearer …`) |
| `GET`  | `/api/v1/auth/me` | current user + server-derived stats (requires bearer token) |
| `POST` | `/api/v1/sync/claim` | `{anon_id}` → merge a guest device's verified events into the account |
| `POST` | `/api/v1/profile/team` | `{selected_team}` → persist the player's constructor faction (server-side; counts in the Constructors' Championship) |
| `GET`  | `/api/v1/leaderboard` | top players by **server-verified** points; `?period=all\|weekly\|daily` for resetting windows |
| `GET`  | `/api/v1/leaderboard/teams` | **Constructors' Championship** — verified points bucketed by team faction; `?period=` as above |
| `GET`  | `/api/v1/teams/overview` | per-team **registered headcount + all-time championship points for every team** (including empty ones); powers the first-run team-picker onboarding prompt |
| `GET`  | `/api/v1/leaderboard/me` | the signed-in player's **own global rank + percentile** for a window (the "your garage" rank card); `?period=`; **auth** |
| `GET`  | `/api/v1/leaderboard/team` | the caller's **personal stake in the Constructors' Championship** — their faction's standing + a within-team leaderboard; `?period=`; **auth** |
| `GET`  | `/api/v1/user/play-history` | per-day Daily-Challenge play totals for the **streak heatmap**; `?days=`; **auth** |
| `GET`  | `/sw.js` | service worker (served at root scope) for the opt-in **local streak reminder** |
| `POST` | `/api/v1/analytics/collect` | ingest a batch of pseudonymous client events (sendBeacon-friendly); public, bounded, best-effort |
| `GET`  | `/api/v1/analytics/summary` | DAU/WAU/MAU, the play funnel, D1/D7 retention, mode mix, account growth; **token-gated** (`F1_ANALYTICS_TOKEN`); `?days=` window |
| `GET`  | `/analytics` | the analytics dashboard page (reads the gated summary; harmless without the token) |
| `GET`  | `/api/v1/dev/questions` | full bank **with answers** for proofreading ("Check the data" button), each row tagged with its review `flagged` state; **disabled in production** (`F1_DEV_TOOLS=0`, set in `render.yaml`) |
| `POST` | `/api/v1/dev/flag` | flag/unflag a question (by text) for later review — the 🚩 button in "Check the data"; same `F1_DEV_TOOLS` gate |
| `GET`  | `/api/v1/dev/flags` | the dev review queue (every flagged question); feeds a follow-up cull via `scripts/curate_questions.py` |

The daily selection is **deterministic per period** (seeded by
mode + UTC date), so everyone gets the same set within a period and it rotates —
the prototype's stand-in for the 00:00 UTC cron provisioning (Architecture §1.1).

### Accounts, saving & the trust boundary

Accounts are built directly into the FastAPI backend — no third-party auth
service, no OAuth, no extra dependencies. Passwords are hashed with PBKDF2 (from
the standard library) and never stored in plaintext; a session is an opaque
bearer token the browser keeps in `localStorage`.

Saving respects the trust boundary (Architecture §2.2): the server **never**
trusts client-supplied totals. Every guess is scored server-side and that score
is written to `play_events`; the leaderboard and your profile totals are
recomputed from those rows, so a total can't be forged by editing `localStorage`.
Play while logged out is recorded against a per-device `anon_id` and claimed into
your account when you sign in, so nothing is lost.

**Persistence (hosting):** accounts live in the SQLite file at `F1_DB_PATH`. The
question bank is wiped and reloaded on every boot, but the account tables
(`users`, `auth_sessions`, `play_events`) are deliberately preserved across that
reseed. They only survive as long as the **file** does — and on an ephemeral
free host the filesystem is wiped on every redeploy *and* cold start. The free
fix is **Litestream** (below); the heavier options remain a persistent disk or
managed Postgres (Architecture §0).

### Free durable accounts (Litestream → object storage)

[Litestream](https://litestream.io) continuously streams the SQLite DB to an
S3-compatible bucket and restores it on boot, so accounts survive the ephemeral
host with **no paid disk and no Postgres migration**. The wiring is already in
place — `backend/start.sh` wraps uvicorn, `backend/litestream.yml` is the config,
`render.yaml` fetches the binary at build time and the `Dockerfile` bakes it in.
It's **opt-in**: with no bucket configured the app just runs with an ephemeral DB
(local dev, tests and CI are untouched).

To turn it on (free, ~5 minutes):

1. Create a bucket on a free S3-compatible store — **Backblaze B2** (10 GB free,
   no credit card) or **Cloudflare R2** are both fine.
2. Make an application key with read/write access and note the **keyID** and
   **applicationKey**, plus your bucket's **endpoint** and **region** (B2 shows
   these as e.g. `s3.us-west-002.backblazeb2.com` / `us-west-002`).
3. In the Render dashboard (or your host's env), set the values `render.yaml`
   marks `sync: false`:

   | Env var | Example |
   |---|---|
   | `LITESTREAM_REPLICA_BUCKET` | `gridmaster-db` |
   | `LITESTREAM_REPLICA_ENDPOINT` | `s3.us-west-002.backblazeb2.com` |
   | `LITESTREAM_REPLICA_REGION` | `us-west-002` |
   | `LITESTREAM_ACCESS_KEY_ID` | *(your keyID)* |
   | `LITESTREAM_SECRET_ACCESS_KEY` | *(your applicationKey)* |

   (`LITESTREAM_REPLICA_PATH` defaults to `f1stats`.)

4. Redeploy. On boot you'll see `Restoring … from replica` / `Starting uvicorn
   under Litestream replication`; thereafter every write is mirrored within ~1 s
   and a final snapshot is flushed on graceful shutdown.

### Analytics

Engagement is measured **first-party** — no Google Analytics, no third-party tag,
no cross-site cookies — in keeping with the rest of the project. The frontend
batches a small set of allow-listed, pseudonymous events (keyed by the existing
guest `anon_id` + a per-tab session id) and flushes them via `navigator.sendBeacon`;
the server validates and bounds them into `analytics_events`
(`backend/app/analytics.py`). This is **aggregate telemetry only** — it never
feeds scoring or the leaderboard, which stay on `play_events` behind the trust
boundary.

- **Collection is always on** and privacy-respecting (no PII).
- **The dashboard is gated** by `F1_ANALYTICS_TOKEN`: leave it unset and
  `/analytics` + the summary API are disabled (collection still runs); set a
  strong secret and open `/analytics`, paste the token, and you get **DAU / WAU /
  MAU**, the **landing → start → complete → share → sign-up funnel** with
  conversion rates, **D1 / D7 retention**, **mode popularity**, and **account
  growth** over a 7/14/30-day window.
- Rows older than 180 days are pruned on boot to bound growth.

### Security hardening

Beyond the three core invariants, the service ships with defense-in-depth:

- **Salted slider bounds** — derived slider bands are seeded with a server-side
  secret (`F1_SLIDER_SALT`, or a random value generated once and persisted in
  the `app_kv` table), so the public bounds algorithm can't be re-run client-side
  to invert a bound back to the answer.
- **Free Practice throttle** — 60 question draws per 10 minutes per client,
  server-side, so the answer key can't be scripted out of the practice oracle.
- **Security headers** — a strict CSP (`script-src 'self'`, no inline scripts),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `X-Frame-Options`.
- **Auth hygiene** — PBKDF2 passwords with byte-length caps enforced identically
  at registration and login, a login brute-force limiter, and bulk pruning of
  expired sessions at boot.

## Implemented systems

- Polished landing page + the exact-numerical **Daily Challenge** (one general
  bank) + unlimited **Free Practice** (non-competitive, never recorded, with a
  10-second anti-scouting team penalty on low scores) + Arcade Over/Under
  (matchups biased toward close, within-30% calls)
- **Pit Wall Radio** — once per question, radio the pit wall and it narrows the
  guess to a band guaranteed to contain the answer, for 40% of whatever the
  question goes on to score. The band's width is capped below the served slider
  band (the call always buys real information) and its placement is drawn from a
  secret-salted, per-token RNG, so the answer's position inside it can't be
  re-derived client-side — the trust boundary holds
- **Today's Field** — after a Daily, the summary (and the share text) shows where
  you finished among everyone who has played that day's set so far, guests
  included: "P4 of 23 in today's field". Computed only from server-scored events
- **Free Practice focus** — optional era (decade) and topic chips narrow the
  practice draw ("2010s · Qualifying"), persisted across visits, with a graceful
  full-bank fallback when a focus matches nothing
- **Next-Daily countdown** on the capped intro and the finish summary, and an
  **Enter-key play flow** (Enter locks in a guess and advances past the reveal;
  inert while any dialog is open)
- Server-authoritative exp-decay scoring, **kind-aware** (counts/points by
  percentage error; years by years-off; percentages by points-of-percentage);
  answers never leave the server
- Deterministic anti-hallucination validation; a **committed 2,500-question bank**
  (`backend/app/data/questions.json`), sampled era-weighted from a validated,
  significance-gated pool rebuilt weekly from the full 1950→now record, with a
  **modern-era floor**: at least 55% of the bank sits in the post-2020 era
  (`seed.MODERN_MIN_SHARE`, enforced at export and gated in
  `scripts/verify_bank.py` so the weekly refresh preserves the composition)
- 35+ question types across drivers, teams and circuits — career totals, season
  spotlights ("how many races did Verstappen win in 2023?"), dominant-team
  seasons, win streaks/spans, head-to-head rivalries — era-biased toward the
  current grid, with an era-tiered driver significance gate (2020s: 50+ points ·
  2010s: 3+ wins or a title · 2000s: champions · pre-2000: multiple champions
  only). The same gate filters the Arcade Over/Under roster, so it never pits
  insignificant also-rans
- Guest-first localStorage: lifetime points, accuracy, streaks, achievements
- **User accounts** (username + PBKDF2-hashed password, server sessions) with
  server-side saving, guest→account merge, and a server-verified Global
  Leaderboard — see *Accounts, saving & the trust boundary* below
- **Daily / weekly / all-time leaderboards** (resetting windows give newcomers a
  fresh race to win) and a **Constructors' Championship** that buckets every
  player's verified points by the team faction they pledge to (PRD §5.3)
- **Server-side daily streaks** recomputed from play history, and a
  **replay-proof leaderboard**: the deterministic daily set can't be re-run to
  inflate a total — one scored row per player per question per day
- **Spoiler-free shareable result** — a Wordle-style coloured-square grid with
  the day's puzzle number, copied/shared without leaking any answers
- **Synthesized sound effects** (`frontend/sound.js`) — a feel-good audio layer
  generated at runtime with the Web Audio API, so there are **no binary audio
  assets** to ship or host (same self-contained ethos as the first-party
  analytics and built-in accounts). A spinning-wheel click tracks the guess
  slider, a riser builds under the answer reveal, F1 "lights out" starts each
  session, an engine pack thunders by on a purple sector (a single car on green),
  plus lock-in / achievement / session-complete / arcade cues. One **always-visible
  header toggle** mutes everything, persisted across visits
- **First-run onboarding** — brand-new guests are prompted to pledge a
  constructor, shown how many players back each team and how the Constructors'
  Championship is going (`/api/v1/teams/overview`), so the choice feels social
  from the first visit
- **First-party analytics** — a self-contained, pseudonymous event pipeline
  (`backend/app/analytics.py`) with a token-gated dashboard at `/analytics`
  reporting DAU/WAU/MAU, the play funnel, D1/D7 retention and mode mix; see
  *Analytics* below

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
