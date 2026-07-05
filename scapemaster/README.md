# ScapeMaster

**Guess the Old School RuneScape stat, get scored on how close you are.**

ScapeMaster is a daily OSRS stats guessing game — a self-contained sibling of the
F1-themed *GridMaster* app in this repo, rebuilt around Gielinor. You're shown a
question with a single numeric answer (a High Alchemy value, a boss's combat
level, the XP required for level 92) and you guess it on a slider; the server
scores your proximity on a 0–5,000 scale with exponential decay. Answers are
**never sent to the client** — scoring is server-authoritative.

> **Fan project.** ScapeMaster is unofficial and not affiliated with or endorsed
> by Jagex. Old School RuneScape and RuneScape are trademarks of Jagex Ltd. Game
> data is drawn from the [OSRS Wiki](https://oldschool.runescape.wiki/) under
> [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) and the wiki's
> [real-time prices API](https://prices.runescape.wiki/). **No game assets, fonts
> or audio are used** — the UI is an original CSS pastiche of the classic
> interface, the pixel font is the OFL-licensed [Pixelify
> Sans](https://fonts.google.com/specimen/Pixelify+Sans) (bundled, see
> `frontend/fonts/OFL.txt`), and every jingle is synthesized in the browser.

## Modes

- **Daily Slayer Task** — 6 questions, deterministic per UTC day (everyone gets
  the same set), competitive, feeds the HiScores.
- **Training Grounds** — unlimited random questions, never recorded or ranked.
- **Duel Arena** — endless "which is greater?" head-to-heads (GE price, alch
  value, combat level, HP, Slayer XP), played for a streak.

Retention hooks: server-side daily streaks (with a one-day "Saradomin brew"
freeze), a Wordle-style spoiler-free share grid, an **Achievement Diary**
(Bronze → Dragon tiers), the **God Wars championship** (pledge to one of six gods
and pool your verified XP), daily/weekly/all-time HiScores, PWA install, and
synthesized sound.

## Quick start

```sh
cd backend
./run.sh          # installs deps, seeds the committed bank, serves on :8000
```

Then open <http://127.0.0.1:8000>. The default data source is the committed,
validated question bank (`backend/app/data/questions.json`) — instant boot, no
network.

Run the tests:

```sh
cd backend
python -m pytest -q
```

## How the data works (anti-hallucination)

No answer is hand-written or taken on trust. Every question's answer is
**recomputed from a trusted dataset (or the exact XP formula)** by a validation
step before it can ship; if the computed number doesn't match, the question is
thrown out (`app/validation.py`). The four domains:

| Domain | Source | Example question |
|---|---|---|
| **skill** | the exact RuneScape XP formula (no data at all) | "How much experience is required to reach level 92 in a skill?" |
| **item** | OSRS Wiki prices API `/mapping` + `/24h` (alch, buy limit, GE price) | "What is the High Alchemy value of the Abyssal whip?" |
| **monster** | curated from the OSRS Wiki (combat, HP, max hit, Slayer req) | "What is Zulrah's combat level?" |
| **quest** | full quest list parsed from the OSRS Wiki | "How many quests award exactly 1 Quest Point?" |

The committed datasets are built by `scripts/build_datasets.py`, which pulls from
the wiki and the prices API and cross-checks totals (e.g. the parsed quest count
and total Quest Points must match the wiki's own `{{Globals}}` counters). See
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for provenance and attribution.

### Rebuilding the bank

```sh
cd backend
# Regenerate the question bank + Duel Arena snapshot from the committed datasets:
python -m app.seed --export

# Rebuild the entity datasets from the live wiki + prices API (dev-time only):
python scripts/build_datasets.py

# Refresh just the Grand Exchange prices from the live API, then rebuild:
python -m app.seed --export --source wiki
```

`OSRS_DATA_SOURCE` selects the runtime source: `dataset` (default, committed
bank), `entities` (regenerate offline from the datasets), or `wiki` (refresh GE
prices first — rate-limited, disk-cached, weekly-gated).

## Deployment

The folder is self-contained and deploys independently of the F1 app.

- **Docker (recommended):** `docker build scapemaster/` — the `Dockerfile` builds
  the API + static UI and wraps it with Litestream for durable accounts. Works on
  Render, Railway, Fly.io, or any Docker host.
- **Render Blueprint:** Render only reads a `render.yaml` at the repo root. The
  `render.yaml` here is a ready-to-use blueprint (`rootDir: scapemaster`) — either
  deploy the folder as a standalone Docker service, or paste its `services:` block
  into the repo-root `render.yaml` (the two services are independent).

Production env: `OSRS_DATA_SOURCE=dataset`, `OSRS_DEV_TOOLS=0` (turns off the
answer-revealing proofreading endpoint), `OSRS_ANALYTICS_TOKEN` (optional, gates
the `/analytics` dashboard).

### Free durable accounts

The question bank rebuilds from the committed snapshot on every boot, but
accounts, sessions and verified play history live only in the SQLite file — which
an ephemeral free host wipes on redeploy. `start.sh` wraps uvicorn with
[Litestream](https://litestream.io/), which continuously replicates the DB to a
free S3-compatible bucket (Backblaze B2 or Cloudflare R2) and restores it on
boot. Set the `LITESTREAM_*` vars in `render.yaml` to enable it;
`LITESTREAM_REPLICA_PATH` defaults to `scapemaster` so this app and its F1 sibling
can share one bucket without colliding. Leave the bucket blank to run ephemerally.

## Weekly price refresh

`.github/workflows/scapemaster-weekly-prices.yml` (at the repo root) refreshes the
GE price snapshot from the wiki once a week, runs the sanity gate
(`scripts/verify_bank.py`) and the test suite, and commits the updated bank only
if it changed and passed. Prices reflect the latest weekly snapshot, not the live
market — the UI says so, and the static metrics (alch values, buy limits, levels,
Quest Points, XP) that make up the bulk of the bank never move.

## Layout

```
scapemaster/
├── backend/
│   ├── app/            FastAPI app, scoring, validation, generator, ETL, auth
│   │   └── data/       committed datasets + generated question bank
│   ├── scripts/        build_datasets.py, build_and_review.py, verify_bank.py
│   └── tests/          pytest suite
├── frontend/           vanilla HTML/CSS/JS SPA, PWA, bundled OFL font
├── docs/               PRD, question types, data sources + attribution
├── Dockerfile, render.yaml
└── README.md
```
