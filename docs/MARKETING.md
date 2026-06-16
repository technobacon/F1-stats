# Marketing Plan — minimal effort, no Reddit

A solo-operator growth plan for GridMaster. The strategy in one line: **let the
product do the marketing** and concentrate the little manual effort there is on
the F1 race calendar — **without ever cold-posting to Reddit.**

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
  each player can recruit the next — **the share grid is the marketing channel.**
- **Traffic is free and recurring.** ~24 race weekends a year are built-in,
  high-intent spikes. You don't manufacture attention; you catch it.

---

## Why no Reddit

r/formula1 and the big F1 subs are strict on self-promotion, the goodwill cost of
getting it wrong is high, and it's exactly the "shilling" grind we want to avoid.
**We skip Reddit entirely.** Everything below is either *passive* (the share loop,
directories, SEO) or *push-based on channels you own* (your own X/TikTok/Discord),
so growth never depends on talking your way past a community's spam filter.

---

## The one principle: ride the race calendar

F1 traffic lives and dies by the schedule (Thu–Sun of a race weekend). **One
concentrated push per race weekend beats constant low-level effort.** Outside race
weekends, rest — don't burn energy on dead days.

The live schedule is already in the app (`SESSIONS_2026` /
`schedule-2026.json`) — use it as your own posting calendar.

---

## One-time setup (a few hours, then done)

1. **Rich share previews** — ✅ shipped (OG + Twitter Card meta). Verify the link
   unfurls with [opengraph.xyz](https://www.opengraph.xyz/) once deployed.
2. **A clean, memorable URL** with the deep-link params working (`/?play=daily`).
3. **One brand account each on X/Twitter + TikTok** (Reels/Shorts are the same
   clip reposted) — that's enough. Put the URL in every bio.
4. **Launch posts (one good day each), in order of fit:**
   - **Product Hunt** — "Wordle for F1 stats" is a strong, legible pitch.
   - **Hacker News "Show HN"** — the server-authoritative / anti-hallucination
     engineering angle plays well to that crowd; lead with how it's built.
   - **Hacker News / IndieHackers** follow-up if the launch lands.
5. **List on directories — passive long-tail traffic forever:**
   - "Games like Wordle" / daily-puzzle roundups and aggregators.
   - F1 link directories and fan-site link lists.
   - These keep sending trickle traffic with zero ongoing effort.
6. **SEO basics (set once):** a descriptive `<title>`/meta description targeting
   "F1 trivia / daily F1 quiz / Formula 1 stats game", a sitemap, and a short,
   indexable landing description. Long-tail search is free recurring traffic.
7. **Wire a reminder hook** — see ENGAGEMENT backlog (PWA push / optional email
   for streak + new-daily nudges). This is the single highest-leverage retention
   lever and it markets *to people who already opted in*.

---

## The repeatable weekly loop (~30–60 min per race weekend)

This is the entire ongoing plan. Do only this and the share loop compounds the
rest. **None of it is Reddit.**

1. **X/Twitter during the session.** Post the day's share grid + a teaser
   question while the race is live and the GP hashtag is trending. One well-timed
   reply under a big F1 account beats 100 cold tweets. Posting on your *own*
   account under a trending hashtag is not shilling — it's being in the room.
2. **One short vertical video**, repurposed across TikTok / Reels / Shorts:
   *"Can you guess how many wins Schumacher had at Ferrari?"* → reveal. Same clip,
   three platforms. Highest-leverage reach for zero spend.
3. **Seed F1 Discords (invited, not spammed).** Drop the daily challenge once into
   the off-topic/games channel of a few large F1 servers where game links are
   welcome; let members start their own streak competitions. Read each server's
   rules first — if links aren't allowed, skip it.
4. **Threads / Instagram (optional).** The same teaser-stat carousel or clip,
   reposted. Pure upside if the clip already exists.

---

## The compounding engine (passive — this is the real plan)

Every finished run offers a spoiler-free grid and a one-tap "Challenge a friend",
both carrying a clickable deep link back into today's exact puzzle and a
*"Beat 72% of players"* brag line. That's the Wordle recruit-the-next-player loop:
**players market to other players.** Your weekend posts only light the fuse; the
share mechanics carry it from there. Watch it in the token-gated `/analytics`
dashboard (the funnel already tracks `share` and `challenge_friend` / `deeplink`
events).

---

## Deliberately NOT doing (to keep effort minimal)

- ❌ **Reddit self-promo** — high goodwill cost, the "shilling" grind we're avoiding.
- ❌ **Paid ads** — terrible ROI for a free game pre-virality.
- ❌ **Daily posting on non-race days** — saves energy; the share loop runs itself.
- ❌ **A big content calendar** — one repurposed clip + one timed post per race
  weekend.

---

## Sequencing

1. Ship Tier-1 retention (streaks ✅, social proof ✅, clickable shares ✅;
   **reminders** next — see ENGAGEMENT backlog).
2. Do the one-time setup (directories + SEO + launch days are the durable wins).
3. Run the weekly loop every race weekend.
4. Read `/analytics` after each weekend; double down on whichever channel moved
   the funnel.
