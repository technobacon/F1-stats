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

## Backlog (impact ÷ effort)

### Tier 1 — highest ROI

- **Reminders / notifications** — the biggest remaining gap. Nothing yet pulls a
  player back tomorrow. Two tractable paths, in order of effort:
  1. **Email reminder** (opt-in): a daily cron + the accounts we already have —
     "today's challenge is live / your N-day streak is on the line".
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
