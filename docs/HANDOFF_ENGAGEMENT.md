# Handoff — Engagement & Retention work

_Last updated: 2026-06-14_

Engineering handoff for the engagement/retention + marketing batch built on the
`claude/quiz-game-engagement-marketing-dgiqu5` branch (now merged to `main`).
Companion docs: [`ENGAGEMENT.md`](./ENGAGEMENT.md) (feature rationale + backlog),
[`MARKETING.md`](./MARKETING.md) (growth plan), [`HANDOFF.md`](./HANDOFF.md)
(original full engineering handoff), [`STATUS.md`](./STATUS.md).

## TL;DR

Shipped, in two passes, with **no new runtime dependencies and no required env
vars**:

1. Loud daily **streaks + an automatic streak freeze** (one missed day forgiven).
2. **Social proof** on results ("you beat 78% of players"), server-computed.
3. **Deep-linked sharing** (`?play=…`), a *Challenge a friend* button, and rich
   link previews (Open Graph / Twitter Card).
4. A **50-badge achievement system** (Rookie → Champion), data-driven.
5. **Purple / Green sector flash** on close guesses (F1 timing-board style).
6. **Team-colour text legibility** fix (`--color-ink`).
7. **Optional email** at registration (capture only; sending is a later task).

All 123 backend tests pass; `node --check` clean; verified with a live server
smoke test.

---

## What changed, by file

### Backend (`backend/app/`)
- **`auth.py`**
  - `daily_streak()` — now forgives a single one-day gap once per run
    (`STREAK_FREEZE_MAX_GAP`). A second gap, or a 2-day lapse, still resets to 0.
  - `question_insight(conn, question_id, score)` — aggregate social proof
    (players answered, average score, percentile beaten) from server-scored
    `play_events`; suppressed under `_INSIGHT_MIN_SAMPLE` (5) players.
  - `normalize_email()` + `create_user(..., email=None)` — optional, validated,
    lower-cased; stored on `users.email`.
- **`models.py`** — `QuestionInsight`, `VerifyResponse.insight` (optional),
  `RegisterRequest.email` (optional).
- **`main.py`** — `quiz_verify` attaches `insight` after recording the event;
  `auth_register` passes the email through.
- **`db.py`** — `users.email` column + `idx_play_events_question`; both handled by
  `_migrate()` so **existing databases upgrade automatically** on boot.

### Frontend (`frontend/`)
- **`index.html`** — OG/Twitter meta; `#streak-banner`, `#reveal-insight`,
  `#summary-streak`/`#summary-insight`, `#challenge-friend`, optional
  `#auth-email` field, the achievements card (`#ach-grid` + filter tabs), and the
  `#sector-flash` overlay. **Asset cache-bust bumped to `?v=14`.**
- **`app.js`** — streak banner + freeze, social-proof rendering, sector
  classification/flash (`sectorForResult`, `flashSector`), deep links
  (`handleDeepLink`), share/challenge, the achievement engine (`ACHIEVEMENTS`,
  `achSnapshot`, `evaluateAchievements`, `renderAchievements`), `ink` per team in
  `applyTeam`, and email submit.
- **`style.css`** — `--color-ink` (text) split from `--color-primary` (fills),
  `--f1-purple` / `--f1-green`, and styles for the engagement hooks, achievements
  grid, and sector flash. All text-colour usages now use `--color-ink`.

### Docs
- New: `ENGAGEMENT.md`, `MARKETING.md`, this handoff. README links all three.

---

## Architecture notes for the next engineer

- **Trust boundary intact.** Streaks, achievements and the sector flash are
  **local/cosmetic** (localStorage), exactly like the pre-existing streak. Only
  server-scored `play_events` feed points/leaderboards. Achievement unlocks can't
  be used to inflate the leaderboard.
- **Achievements are data-driven.** Add a badge = add one row to `ACHIEVEMENTS`
  with a pure `check(snapshot)` predicate. If a predicate needs a new signal, add
  a counter/flag to `state.ach` (via `ensureAch()`) and a field to `achSnapshot()`.
  `evaluateAchievements()` is idempotent — call it after any state change.
- **Social-proof insight** is best-effort: a failure never blocks scoring, and
  it's omitted for Free Practice (non-recorded) and new questions.

## Deploy / ops
- **No migration step needed** — `db._migrate()` adds the `email` column and the
  new index on boot; safe and idempotent on the live SQLite DB.
- **No new env vars, no new dependencies.** Litestream durability is unchanged.
- Bumped `?v=` query strings so clients pick up the new CSS/JS.

## Known pending / next
- **Email reminders** — capture is done (`users.email`); **sending is not built**.
  Needs an email provider/SMTP env + a scheduler (cron / Celery beat). Web Push is
  the higher-impact alternative. See `ENGAGEMENT.md` → *Backlog / Open decisions*.
- Streak freeze as a visible, earnable resource; surface Hardcore mode; live race
  calendar; race-weekend event mode (all in the `ENGAGEMENT.md` backlog).
