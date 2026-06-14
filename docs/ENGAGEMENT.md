# Engagement & Retention

How GridMaster turns a one-time visitor into a daily habit, and what's queued
next. The engine for retention was already built (daily puzzles, streaks,
replay-proof leaderboards, a Constructors' Championship, a Wordle-style share
grid). This doc covers the **loop closers** layered on top — the surfaces that
actually drag players back and turn one play into ten — and the prioritised
backlog of what's still missing.

> Companion doc: the growth side lives in [`MARKETING.md`](./MARKETING.md).

---

## Shipped in this pass

Three self-contained, high-ROI retention levers, all themed to the player's
chosen constructor and all built on existing server-authoritative data (no new
infrastructure, no third-party services).

### 1. Loud streaks + automatic streak freeze
Losing a long streak is the single moment players churn for good, so streaks are
now both **prominent** and **forgiving**.

- **Home banner** (`#streak-banner`, `renderStreakBanner()`): a tappable,
  loss-averse nudge — *"🔥 5-day streak — play today's Daily to keep it alive"*,
  or *"…secured — see you tomorrow!"* once today's Daily is done.
- **Summary callout** (`#summary-streak`): a celebratory flame after each daily
  run.
- **Streak freeze** (`auth.daily_streak`, mirrored client-side in
  `finishSession`): a single missed day inside an otherwise unbroken run is
  forgiven **once per run** (the auto-freeze Duolingo popularised — a proven
  churn reducer). A second gap, or any 2-day gap, still ends the streak; a freeze
  protects a gap *within* a live run, it never revives a dead one.
  See `STREAK_FREEZE_MAX_GAP` in `backend/app/auth.py`.

### 2. Social proof on every result
Free, server-computed dopamine that also makes the share grid worth posting.

- **Per question** (`#reveal-insight`): after the answer lands —
  *"You beat 78% of players here · avg 3,210 pts"*.
- **Per session** (`#summary-insight`): *"You beat 72% of players on average
  today."*
- Source: `auth.question_insight()` aggregates the server-scored `play_events`
  for that question — players answered, average score, and the percentile this
  guess beats. Suppressed until `_INSIGHT_MIN_SAMPLE` (5) players have answered,
  so it never reads "you beat 0%". Returned on `POST /api/v1/quiz/verify` as the
  optional `insight` field (`VerifyResponse.insight`); absent for Free Practice
  (non-recorded) by design. Backed by `idx_play_events_question`.

### 3. Clickable, deep-linked sharing
The share loop is the free growth engine — so shared results are now one-tap
invites, not bare URLs.

- **Deep links**: the share text and the new **"🏎️ Challenge a friend"** button
  emit `…/?play=daily|race|practice|arcade`. `handleDeepLink()` parses it on boot
  and drops the visitor straight into that challenge.
- **Two share psychologies**: *Share Result* (spoiler-free grid + "Beat 72% of
  players" brag, `buildShareText`) and *Challenge a friend* (a direct head-to-head
  dare). Both fall back native-share → clipboard → inline text (`shareOrCopy`).
- **Rich previews**: Open Graph + Twitter Card meta in `index.html`, so a pasted
  link unfurls into a card.

---

## Shipped in the second pass

### 4. A proper achievement system (50 badges, Rookie → Champion)
Replaces the three hard-coded `if`s with a **data-driven catalog** in
`frontend/app.js` (`ACHIEVEMENTS`). Each badge is one row — `{id, icon, tier,
name, desc, check(s)}` — with a pure predicate run against an achievement snapshot
(`achSnapshot()`). Adding a badge is a one-liner.

- **50 thematic achievements** across four difficulty tiers (10 Rookie, 15
  Midfield, 15 Podium, 10 Champion): *Lights Out, Purple Sector, Hat-Trick,
  Grand Slam, Perfect Lap, Comeback Kid, The Full Grid, Hall of Fame*…
- **Counters + flags** backing the checks live in `state.ach` (lazy `ensureAch()`),
  updated from `submitGuess` / `finishSession` / arcade / share / challenge /
  team-select / auth. Like the streak, this is **local/cosmetic** (Architecture
  §2.2) — it never touches the server-verified leaderboard.
- **Profile UI**: a filterable grid (`#ach-grid`, All / Unlocked / Locked) with
  per-tier accent colours; unlocks raise a celebratory toast and re-render.
- `evaluateAchievements()` is idempotent (skips already-earned), so it's called
  liberally — on boot, after every scoring event, and after server refresh.

### 5. Purple / Green sector flash
On each reveal, a guess is classified by percentage error (`sectorForResult`) and,
if close, an F1 timing-board scroll sweeps the screen (`flashSector`):
- **≤10% → "&lt;TEAM&gt; PURPLE SECTOR"** in F1 purple (`--f1-purple`).
- **≤25% → "&lt;TEAM&gt; GREEN SECTOR"** in F1 green (`--f1-green`).

Fixed sector colours (not the team colour) plus a white stroke + glow keep it
legible on every theme; honours `prefers-reduced-motion`. Purple hits also feed
the *Purple Sector / Reign / Machine / Grand Slam* achievements.

### 6. Team-colour text legibility (`--color-ink`)
Several brand primaries (Red Bull navy, Haas near-black, Williams/RB deep blue,
Aston dark teal) were nearly invisible as **text** on the dark UI. Fixed by
splitting the variable:
- `--color-primary` — the true brand colour, still used for fills, borders,
  buttons and swatches.
- `--color-ink` — a legibility-safe (lightened where needed) variant used for
  **text**. Set per team in `applyTeam()` from the `TEAMS[...].ink` value; all
  text-colour CSS now references it.

### 7. Optional email at registration
A new **optional** email field on the sign-up form (register only), for future
opt-in reminders. Validated loosely and normalised server-side
(`auth.normalize_email`), stored in `users.email` (nullable; migration in
`db._migrate`). Blank is fine; a malformed non-empty value returns a 400. This
lays the groundwork for the email-reminder backlog item below.

---

## Backlog (impact ÷ effort)

### Tier 1 — highest ROI

- **Reminders / notifications** — the biggest remaining gap. Nothing yet pulls a
  player back tomorrow. Opt-in **email is now collected at sign-up** (see Shipped
  §7), so the data side is ready. Two tractable paths, in order of effort:
  1. **Email reminder** (opt-in): a daily cron + the stored `users.email` —
     "today's challenge is live / your N-day streak is on the line". Needs an
     email provider/SMTP env and a scheduler.
  2. **Web Push** (PWA): a service worker + VAPID keys + a server send. Higher
     impact, more setup. Needs a decision on provider/infra (see *Open
     decisions*).
- **Streak freeze as a visible, earnable resource** — surface the freeze in the
  profile ("1 freeze available"), let streaks earn more. The mechanic already
  exists server-side; this is UI + a small economy.

### Tier 2 — strong, moderate effort

- **Surface Hardcore mode** — built but hidden in the UI today.
- **Live race calendar** — replace the hardcoded `SESSIONS_2026` with a fed
  schedule so the countdown HUD never goes stale.
- **Race-weekend event mode** — auto-themed challenge tied to the upcoming GP
  ("Monaco GP Special"), riding the sport's natural traffic spikes.
- **Shareable achievement badges** — turn the existing achievements into
  celebratory, shareable moments.

### Tier 3 — polish

- Licensed hero image (CSS placeholder today).
- Weekly recap ("Your week on the grid") email/screen.
- Constructors' Championship standings on the home page (tribal pull).
- Collapse the 4 mode cards into one dominant Daily CTA + secondary row.

---

## Open decisions

- **Reminder channel** — email (low effort, needs an SMTP/provider env) vs. Web
  Push (higher impact, needs a service worker + VAPID keys + scheduler). Affects
  what infra the free host needs.
- **Scheduler** — reminders, the weekly ETL, and event modes all want a real
  scheduler (cron / Celery beat) rather than the boot-time gate.
