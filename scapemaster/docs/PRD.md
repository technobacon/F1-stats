# ScapeMaster — product overview

A daily Old School RuneScape stats guessing game, built as a self-contained,
re-themed sibling of the F1 *GridMaster* app in the same repository.

## The core loop

The player is shown a question with a single numeric answer and guesses it on a
slider. The server scores proximity on a 0–5,000 scale with exponential decay on
percentage error:

```
Score = 5000 · e^(−3 · |guess − actual| / actual)
```

A perfect guess banks 5,000 XP; the score falls off gently at first, then
steeply. Within 10% is "a purple" (raid-chest jackpot); within 25% is "a green"
(solid loot). **The answer is never sent to the client** — it's stashed
server-side against an opaque token and only revealed after the guess is scored,
so HiScores totals can't be forged from the browser (`app/service.py`,
`app/scoring.py`).

## Modes

- **Daily Slayer Task** — 6 questions, deterministic per UTC day via a
  SHA-256-seeded RNG, the same set for everyone. The ranked, competitive mode.
- **Training Grounds** — unlimited random questions, uniformly drawn, never
  recorded or ranked, with a short cooldown after a wild guess to deter
  answer-scouting.
- **Duel Arena** — endless "which is greater?" head-to-heads between two items or
  two monsters on a shared metric, biased toward close calls, played for a streak.

## Retention

- **Daily streaks**, recomputed server-side from verified play, with a one-day
  "Saradomin brew" freeze that forgives a single missed day once per run.
- **Share grids** — a spoiler-free Wordle-style coloured-square result.
- **Achievement Diary** — a Bronze → Adamant → Rune → Dragon catalog, evaluated
  client-side.
- **God Wars championship** — the player pledges to one of six gods (Saradomin,
  Zamorak, Guthix, Armadyl, Bandos, Zaros) and their verified XP pools into that
  faction's standing.
- **HiScores** — daily / weekly / all-time boards, ranked only from
  server-verified play (`app/auth.py`, the trust boundary).
- PWA install and synthesized sound.

## Trust boundary

Every number that gates competition comes only from `play_events`, whose `score`
is always the value the server computed — never a client-supplied total. Guest
play is recorded against a device `anon_id` and claimed into the account on
sign-in. A partial unique index on `(identity_key, question_id, period)` pins each
question to one scored row per player per day, so replaying the deterministic
daily set can't inflate a total.

## Data integrity

See [`DATA_SOURCES.md`](DATA_SOURCES.md) and
[`question-types.md`](question-types.md). The headline: no answer is hand-written
— each is recomputed from a committed dataset (OSRS Wiki, CC BY-SA) or the exact
XP formula before it can ship, and the pipeline demonstrably rejects a planted
wrong answer.

## Non-goals (prototype)

Server-validated Duel Arena picks (v1 evaluates client-side), real-time GE prices
(the snapshot is weekly by design), and a production datastore (SQLite stands in
for Postgres; the schema is written to port mechanically).
