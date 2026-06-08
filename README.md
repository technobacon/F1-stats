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
| Jolpica API + weekly ETL | offline seed of illustrative stints | PRD forbids live API calls during gameplay |
| Real LLM synthesizer | `mock_llm_questions()` emitting the strict schema | deterministic, offline |
| Redis token cache | in-memory dict | single-process prototype |
| Next.js + Tailwind frontend | vanilla HTML/CSS/JS served by FastAPI | one runnable service |

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
python3 -m pytest -q     # 23 tests: scoring math, validation layer, API trust boundary
```

---

## Layout

```
backend/
  app/
    scoring.py      # exp-decay scoring engine (PRD §2)
    validation.py   # deterministic anti-hallucination layer (Pipeline §3)
    db.py           # SQLite schema mirroring the staging + production tables
    seed.py         # offline ETL + mock-LLM + validation pipeline
    service.py      # daily-quiz token store, scoring, arcade pairing
    models.py       # Pydantic API contracts (no answer leaves the server)
    main.py         # FastAPI app + static frontend mount
  tests/            # pytest suite
frontend/
  index.html        # HUD, daily quiz, arcade, profile
  style.css         # constructor CSS-var theming + odometer reveal
  app.js            # guest-first localStorage, server-side scoring calls
docs/               # original design documents
```

## API

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/api/v1/health` | liveness + active question count |
| `GET`  | `/api/v1/quiz/daily` | 5 questions, tracking tokens, **no answers** |
| `POST` | `/api/v1/quiz/daily/verify` | `{tracking_token, guess}` → server-side score |
| `GET`  | `/api/v1/arcade/pair` | over/under matchup (non-competitive v1) |

## Next steps toward the full project

- Swap SQLite → Postgres and the in-memory token store → Redis.
- Replace the seed with the real Jolpica ETL (rate-limited token bucket,
  Pipeline §1.1) and a real LLM synthesizer behind the same validation gate.
- Split the frontend into the Next.js + Tailwind app (NextAuth guest-first flow,
  Framer Motion odometer).
- Add Race-Week / One-Shot modes, the 00:00 UTC cron provisioning, the
  Constructors Championship leaderboard, and ad-network integration.
