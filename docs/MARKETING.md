# Marketing Plan — minimal effort, maximum results

A solo-operator growth plan for GridMaster. The strategy in one line: **let the
product do the marketing** and concentrate the little manual effort there is on
the F1 race calendar.

> Companion doc: the retention features that make this traffic *stick* live in
> [`ENGAGEMENT.md`](./ENGAGEMENT.md). Send traffic to a game with no return-hook
> and it leaks — ship the Tier-1 retention levers first.

---

## Why this works

- **The audience is huge, online, and tribal.** F1 has one of the largest, most
  engaged fanbases in sport, and they argue about stats for fun.
- **The format is inherently viral.** A daily puzzle + a spoiler-free share grid
  is the Wordle growth model. GridMaster already has both, now with **clickable
  deep links and social-proof brag lines** in every share (see ENGAGEMENT §3), so
  each player can recruit the next.
- **Traffic is free and recurring.** ~24 race weekends a year are built-in,
  high-intent spikes. You don't manufacture attention; you catch it.

---

## The one principle: ride the race calendar

F1 traffic lives and dies by the schedule (Thu–Sun of a race weekend). **One
concentrated push per race weekend beats constant low-level effort.** Outside race
weekends, rest — don't burn energy or community goodwill on dead days.

The live schedule is already in the app (`SESSIONS_2026` /
`schedule-2026.json`) — use it as your own posting calendar.

---

## One-time setup (a few hours, then done)

1. **Rich share previews** — ✅ shipped (OG + Twitter Card meta). Verify the link
   unfurls with [opengraph.xyz](https://www.opengraph.xyz/) once deployed.
2. **Product Hunt launch** — one good day. "Wordle for F1 stats" is a strong,
   legible pitch.
3. **List on Wordle-alike directories** ("games like Wordle" roundups,
   puzzle-game aggregators) — passive long-tail traffic forever.
4. **One brand account each on X/Twitter + TikTok/Reels** — that's enough.
5. **A clean, memorable URL** with the deep-link params working
   (`/?play=daily`).

---

## The repeatable weekly loop (~30–60 min per race weekend)

This is the entire ongoing plan. Do only this and the share loop compounds the
rest.

1. **Reddit, timed right.** r/formula1 (millions of members) is the prize but
   strict on self-promo — engage genuinely, don't drop bare links. The
   meme/share-grid angle fits r/formuladank; r/F1Technical, country and team
   subreddits are more permissive. Post the day's grid or a teaser stat into the
   live race-week discussion as a free, no-signup fan game.
2. **X/Twitter during the session.** Post the day's share grid + a teaser
   question while the race is live and the GP hashtag is trending. One well-timed
   reply under a big F1 account beats 100 cold tweets.
3. **One short vertical video**, repurposed across TikTok / Reels / Shorts:
   *"Can you guess how many wins Schumacher had at Ferrari?"* → reveal. Same clip,
   three platforms. Highest-leverage reach for zero spend.
4. **Seed F1 Discords.** Drop the daily challenge once into the off-topic/games
   channel of a few large F1 servers; let members start their own streak
   competitions.

---

## The compounding engine (passive)

Every finished run offers a spoiler-free grid and a one-tap "Challenge a friend",
both carrying a clickable deep link back into today's exact puzzle and a
*"Beat 72% of players"* brag line. That's the Wordle recruit-the-next-player loop.
Your job is mostly to **light the fuse each weekend**; the share mechanics carry
it from there. Watch it in the token-gated `/analytics` dashboard (the funnel
already tracks `share` and now `challenge_friend` / `deeplink` events).

---

## Deliberately NOT doing (to keep effort minimal)

- ❌ **Paid ads** — terrible ROI for a free game pre-virality.
- ❌ **Daily posting on non-race days** — saves energy and subreddit goodwill.
- ❌ **A big content calendar** — one repurposed clip + one timed post per race
  weekend.

---

## Sequencing

1. Ship Tier-1 retention (streaks ✅, social proof ✅, clickable shares ✅;
   **reminders** next — see ENGAGEMENT backlog).
2. Do the one-time setup.
3. Run the weekly loop every race weekend.
4. Read `/analytics` after each weekend; double down on whichever channel moved
   the funnel.
