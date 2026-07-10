# GridMaster — Game Design & Visual Design Review

A professional design pass over the shipped game (frontend `v25`, ~1,200-question
bank, accounts + leaderboards live). The brief: look at GridMaster with a game
designer's and a visual designer's eye, find what to improve, and propose new
features — fully fleshed out.

Everything here is grounded in the actual code (file references throughout).
Items already on the [`ENGAGEMENT.md`](./ENGAGEMENT.md) backlog are only repeated
where this review changes or extends them.

**Contents**

1. [What's already working](#1-whats-already-working)
2. [Game design findings](#2-game-design-findings) — mechanics, balance, loops
3. [Visual & UX design findings](#3-visual--ux-design-findings)
4. [New feature proposals](#4-new-feature-proposals) — five fleshed-out specs
5. [Prioritised roadmap](#5-prioritised-roadmap)

---

## 1. What's already working

Credit first, because the foundation is genuinely strong and the
recommendations below build on it rather than replace it:

- **The core mechanic is distinctive.** "Guess the number, get scored on
  closeness" is a better fit for stats than multiple choice — it rewards domain
  intuition, produces a continuous skill curve, and makes every reveal a
  micro-drama. The curved race-line slider with the team-liveried car is a
  memorable, ownable interaction.
- **The trust architecture is real.** Server-authoritative scoring, replay-proof
  leaderboards, validated answers. Most trivia games fake all three.
- **The retention skeleton exists**: daily cadence, streak + freeze, share grid,
  achievements, Constructors' Championship, social proof percentiles.
- **The craft level is high for a prototype**: design tokens, reduced-motion
  path, focus rings, skeletons, empty states, synthesized audio, first-run
  explainer. The visual system (Titillium/JetBrains Mono, constructor theming,
  timing-board mono layer) reads coherent and on-brand.

The gaps are therefore not "add polish" — they are **balance flaws in the
scoring math, a difficulty/dramaturgy gap in the session design, one
under-developed mode, and a share loop that stops one step short of being a
game**.

---

## 2. Game design findings

### G1 — Year questions are nearly free points *(balance bug — fix first)*

`verify_guess` (`backend/app/service.py`) scores **every** answer kind through
the same curve: `5000 · e^(−3 · |guess − actual| / actual)`
(`backend/app/scoring.py`). Percentage error is the right yardstick for counts
and points totals, but for a **year** the denominator is ~2000, so the curve
degenerates:

| Guess vs actual (year 2000) | % error | Score |
|---|---|---|
| Off by 5 years | 0.25% | **4,963** |
| Off by 20 years | 1.0% | **4,852** |
| Off by 50 years (guessing 1950 for 2000) | 2.5% | **4,639** |

Any wild guess inside the slider bounds banks ≥90% of the maximum. Players who
notice will feel the scoring is broken; players who don't are still having
their Daily totals distorted by which days happen to include year questions
(the set is deterministic per day, so it's "fair" within a day but noisy across
days and inflationary on all-time boards).

**Recommendation.** Score kinds on the error metric that matches their scale.
The `answer_kind` is already on every question row, and the token store already
knows the question — this is a small, contained change in `verify_guess`:

- **`year`** — absolute error against a fixed tolerance, not percentage:
  `5000 · e^(−|guess − actual| / τ)` with `τ ≈ 3` years. Exact year = 5,000;
  ±3 years ≈ 1,840; ±10 years ≈ 178. Tune τ so the median player's error
  lands mid-curve.
- **`percentage`** — absolute error in points-of-percentage over a τ of ~8
  (a 0–100 bounded scale makes percentage-of-percentage double-punishing for
  small actuals: actual 4%, guess 6% is a 50% "error" today).
- **`count` / `points`** — keep the current curve; it's well-behaved there.

Also re-check the purple/green sector thresholds (`sectorForResult`,
`frontend/app.js`) for these kinds — "within 10% of the year 2000" is a
200-year window. Sector classification should reuse the same per-kind error.

### G2 — `actual == 0` is all-or-nothing

`score_guess` gives 0 points for *any* non-zero guess when the answer is 0
(e.g. "How many DNFs did X have in 2016?" → answer 0, guess 1 → 0 points,
while a 100%-wrong guess on a non-zero answer still earns ~249). A player who
was off by one is scored the same as one who was off by fifty.

**Recommendation.** For zero answers, fall back to an absolute-error curve
scaled to the slider span: `5000 · e^(−|guess| / max(1, span/10))`. Guessing
1 on a 0-answer with a 0–10 slider then scores ~1,840 — wrong, but proportional.
This also removes the temptation to filter zero-answer questions out of the
bank (they're often the most interesting: "how many DNFs did the champion
have?").

### G3 — The slider bounds leak where the answer is *(exploit)*

`_slider_bounds` (`backend/app/service.py:133`) doubles an upper bound until it
exceeds `2 × answer`. Consequence: for every count/points question using
derived bounds, **the answer always lies between 25% and 50% of the slider
range** (`upper ∈ [2a, 4a)` ⇒ `a ∈ (upper/4, upper/2]`). A player who parks the
car at ~35% of every slider is guaranteed a small percentage error forever — it
quietly defeats the server-side answer secrecy the architecture works hard for.

**Recommendation.** Randomise the band deterministically per question so the
answer's position within the slider is ~uniform: seed an RNG with the question
id (the `_deterministic_rng` helper already exists), pick a position `p ∈
[0.15, 0.85]`, set `upper = round_nice(answer / p)` and optionally a non-zero
lower bound the same way. Same UX, no leak, and it costs one line of
explanation in `docs/question-types.md`.

### G4 — The Daily has no dramaturgy

Six questions, all worth 5,000, in random order. There's no rising tension, no
decision to make, no moment the session design itself creates — all the drama
comes from the reveal animation. Compare the reference dailies (Wordle's
escalating board, Immaculate Grid's scarcity): the *structure* creates the
story.

**Recommendation** (composable, pick incrementally):

1. **Difficulty ramp.** Order the six by empirical difficulty — the server
   already has per-question average scores in `play_events`
   (`auth.question_insight`). Serve easiest → hardest. Costs a sort; instantly
   gives sessions an arc and makes Q6 feel like a final lap. New questions
   without data slot in by `difficulty_weight`.
2. **The Overtake (final-question wager).** Before Q6, offer a one-tap choice:
   *"Push — double points if you land green or better, half if you don't."*
   One decision, made at maximum tension, converts spectators into
   participants. Server-side: a `wager` flag on the verify call for the last
   token of the day's set; the multiplier is applied server-side so the
   leaderboard stays trustworthy. The share grid gets a ⚡ marker on a won
   overtake — free share-bait.
3. **Purple-sector chain bonus.** Back-to-back purples add a small,
   *displayed* bonus (+250/chain step, server-computed). Rewards hot hands and
   gives the sector system a cumulative meaning beyond the flash.

Keep the base 5,000 curve untouched — these are layers, and all three remain
server-authoritative.

### G5 — Arcade is a prototype next to the rest of the game

Arcade (`frontend/app.js` `loadArcade`/`pick`, ~45 lines) is the only mode with
no economy, no server verification, no leaderboard presence, no session shape —
a localStorage streak counter with a fixed 1.4 s delay between rounds. Yet
over/under is the most casual-friendly, most snackable mode in the game, and
the natural thing to funnel players into after the Daily is capped (see G7).

**Recommendation.** Rebuild it as **Qualifying** — a proper session (full spec
in [F3](#f3--arcade-20-qualifying)). Headlines: three lives, escalating
difficulty (the value gap between the pair shrinks as the streak grows —
`ARCADE_MAX_GAP` already exists server-side to tune), a run token so the streak
is server-verified, a daily arcade leaderboard, and a share card.

### G6 — Free Practice's penalty punishes learners, not scouts

The 10-second "stewards' penalty" (`startPracticePenalty`) fires on any score
under 1,000 — but a newcomer who is *honestly bad* at F1 stats scores under
1,000 constantly. Their experience of Practice: guess → wrong → sit through a
lecture about cheating → repeat. The mechanic aims at scouts and hits
beginners; a scout, meanwhile, only pays 10 s per farmed answer.

**Recommendation.** Two changes:

- **Grace, then escalate.** No penalty on the first two low scores per
  session; then 5 s; then 10 s. Casual learners rarely trip it, scouting gets
  *more* expensive over a session, not less.
- **Reframe the copy.** Drop the accusatory framing on first trigger:
  *"Tough one — the stewards give you 5 seconds to study the result."* Keep the
  anti-scouting explanation behind a "why?" disclosure. The current wall of
  bold caps reads as a telling-off.

The stronger structural fix (worth doing later): practice from a *rotating
daily subset* of the bank, so memorising answers has bounded value — then the
penalty can shrink further or disappear.

### G7 — After the Daily, the game dead-ends

Once the Daily is capped, the intro card says "come back next period" —
correct, but it's a stop sign at the exact moment the player is warmed up and
willing. The summary screen's only forward paths are Share and Back.

**Recommendation — the "After the flag" funnel.** On the summary and on the
capped intro card, present the next actions in priority order:

1. **Compare** — "See how the grid did" (today's leaderboard + your rank, one
   tap; the API already exists: `/leaderboard?period=daily`, `/leaderboard/me`).
2. **Continue** — "Keep your eye in: Free Practice" / "One more streak:
   Qualifying".
3. **Commit** — the streak-secured banner, and (if guest) the account prompt —
   this is the highest-intent moment for the sign-up ask, far better than the
   profile page.

This is pure re-plumbing of existing surfaces — no new backend — and it's the
cheapest DAU→engagement multiplier available.

### G8 — Points accumulate, but nothing *progresses*

Lifetime points is an unbounded odometer. There is no level, rank, or title; a
10-million-point veteran and a first-week player differ only in digits, and
achievements are the only long-arc goal (locked ones aren't goal-shaped — see
V8). Progression is the strongest free retention mechanic the game doesn't
have.

**Recommendation.** A **Super Licence** ladder (full spec in
[F4](#f4--super-licence-progression--livery-garage)) — named tiers from F4 to
World Champion driven by server-verified points, surfaced next to the username
everywhere (leaderboard, profile, share text), with cosmetic car liveries as
tier unlocks. Costs one derived field; the leaderboard, share card and profile
all get richer at once.

### G9 — Onboarding demands loyalty before delivering value

`TeamPicker.maybeOnboard()` blocks a brand-new visitor with a mandatory
constructor pledge (close/escape disabled) before they have answered a single
question. The pledge matters more *after* the first session, when the player
knows what points are and has some to donate; asked cold, it's a speed bump
that costs first-play conversion (measurable in the analytics funnel:
`landing → start`).

**Recommendation.** Let the first Daily start unthemed (default McLaren).
Trigger the pledge on the **first summary screen** — "Your 21,400 pts need a
home. Pick your constructor" — where the same social-proof overview lands with
actual stakes. Keep the modal exactly as built; only the trigger moves. A/B via
the existing analytics events if in doubt.

### G10 — "Challenge a friend" doesn't carry a challenge

The challenge button shares a static deep link (`?play=daily`) plus the score
as *text*. The recipient plays the same set, but the game never knows the two
are connected — no comparison screen, no closure for either player, no reason
for the challenger to return. This is the single biggest missed loop in the
product: the share infrastructure (deterministic daily set, per-question server
scores) makes a real head-to-head almost free. Full spec in
[F1 — Ghost Race](#f1--ghost-race-asynchronous-head-to-head).

---

## 3. Visual & UX design findings

### V1 — `hero.jpg` 404s on every page load

`style.css` (lines 79, 226) backgrounds the hero with `url('/static/hero.jpg')`
— the file doesn't exist, so every visit fires a failed request (and any future
file dropped there will surprise-restyle the hero). Either ship an original
asset (an SVG scene in the existing hero-track style would be on-brand and
weightless) or gate the `url()` behind a `.has-hero` class set only when the
asset exists.

### V2 — Two constructor themes break at the edges

- **Cadillac** (`--color-primary:#FFFFFF`): in the light theme, white fills and
  borders sit on white surfaces — the active mode tab, progress bars, timeline
  fill and heatmap cells lose contrast almost entirely. `--btn-text:#000` on a
  white button also produces a ghost-looking primary CTA in dark mode.
- **Audi** (`#8E8E8E`): a mid-grey accent reads as *disabled UI*, not as a
  brand — every "active" state looks inactive.

**Recommendation.** These two need per-theme overrides the same way `ink` was
split from `primary`: give Cadillac a functional accent (its secondary near-
black on light; keep white for swatches/livery only) and shift Audi's accent to
its secondary red for interactive states. A 10-minute audit page that renders
every component in all 11 teams × 2 themes would catch the next one of these
before players do.

### V3 — Arcade looks like the wireframe of the game around it

The quiz mode gets an immersive fullscreen treatment, themed cars, sector
flashes, and audio staging; Arcade is a flat card with two grey buttons and a
"VS". The visual gap makes it feel like a placeholder even though it works.
Cheap wins even before the F3 rebuild: driver cards with era/team accent
striping, the streak counter styled like the timing-tower rows, the reveal
animating the two values counting up (reusing `countUp`), and entering the same
`in-game` fullscreen mode as the quiz.

### V4 — Mixed icon languages

The app has a proper SVG icon set (`icons.js`) but still speaks emoji in
several places: achievement icons, `✕ Exit` / `✕ Close`, the 🚩 flag button,
the 🔥/🏎️ in share text (fine — that's clipboard content), and the summary
grid squares (fine — that's the share format). On-screen chrome should be one
language: swap achievement icons and the ✕/🚩 buttons to `icons.js` glyphs.
Emoji render differently per platform and fight the otherwise tight
timing-board aesthetic.

### V5 — Precision entry on the slider is fiddly

For a 0–8192 points question, one pixel of drag ≈ 20+ units, and the number
input below the curve is small and easy to miss (it also starts at the slider
minimum, so a player who taps "Lock In" without touching anything submits the
minimum silently — worth requiring an interaction before enabling the button).
Add stepper chips flanking the readout (−10 · −1 · +1 · +10, long-press to
repeat), sized for thumbs. Keyboard already steps at 1% of range; pointer users
deserve the same granularity.

### V6 — Exiting mid-run is silent and lossy

`✕ Exit` (`#game-exit`, wired through the generic `data-view` navigation)
abandons a Daily run with no confirmation. Because verified questions are
already recorded server-side one-by-one, the player has *spent* questions but
banks no summary, no streak credit, no share. One `confirm`-style dialog
("Retire from the session? Your scored answers stand.") prevents the game's
single most destructive misclick.

### V7 — The back button doesn't work

Navigation is `data-view` buttons mutating classes; the only URL state is the
`?play=` deep link. On mobile (and especially as an installed PWA), pressing
back from the quiz exits the site instead of returning home. Push a history
entry per view (`history.pushState({view}, "", "#daily")`) and handle
`popstate` in `navigate()` — small change, large perceived-quality gain, and it
makes views shareable/bookmarkable for free.

### V8 — Locked achievements aren't goal-shaped

`achProgress` already computes progress toward threshold badges, and the
garage shows the three closest — but the profile's locked grid renders only a
greyed icon + description. Add the progress bar (`gb-bar` already exists) to
locked cards in the grid. A locked badge with "37/50" is a goal; a grey box is
wallpaper.

### V9 — Reveal timeline labels collide on close guesses

When guess ≈ actual (the *best* outcome), the "You" and "Actual" labels and
the two car sprites overlap into an unreadable clump — the moment of triumph is
the moment the UI is messiest. Detect proximity (<8% of track width) and
offset: ghost car below the line, guess car above, labels stacked. Also
consider celebrating the overlap explicitly — when within purple range, snap
the cars nose-to-tail like a photo finish.

### V10 — Small legibility/consistency notes

- **Share-grid colour scale** (🟦🟩🟨🟧⬛, `closenessSquare`): blue-as-best is
  unconventional (Wordle trained green-as-best) and the green/orange midpoints
  are hard to rank for deuteranopes. Consider 🟪🟩🟨⬜⬛ — purple-as-best
  matches the game's own "purple sector" language, which is a teaching
  opportunity, and value-ramps better in greyscale.
- **Hero italic-uppercase** is used for the hero title, section titles, card
  titles, verdicts, readouts and buttons; when everything is emphatic, nothing
  is. Reserve the italic for the hero + one level of section heading.
- **`hud-points` vs `game-points`** show different numbers (lifetime vs
  session) in the same visual voice; a first-session player can read the
  session bar as their "balance" resetting. Label the in-game one ("session").

---

## 4. New feature proposals

Five features, spec'd to be buildable on the existing architecture (SQLite +
FastAPI + vanilla frontend, no new services). Ordered by expected impact.

---

### F1 — Ghost Race (asynchronous head-to-head)

**One-liner.** A challenge link that carries the challenger's run, so the
recipient races their *ghost* through the same six questions — per-question,
car-vs-car, with a verdict screen both players can see.

**Why this one first.** It converts the existing share loop from "text with a
URL" into a game two people are playing together; K-factor mechanics beat any
retention tweak. Every ingredient exists: the daily set is deterministic, per-
question scores are server-recorded, the timeline UI already renders two cars.

**Player story.**
1. A finishes the Daily → taps **Challenge a friend** → gets
   `…/?race=8f3ka2` (a share token) with the existing brag text.
2. B opens it → hero shows *"A ran 21,430 today — beat them."* → plays the
   normal Daily, but each reveal shows a third marker: A's guess as a ghost car
   with their per-question points.
3. Finish → verdict screen: two summary grids side by side, total vs total,
   winner banner ("You got them by 1,240 — send it back?").
4. A gets closure on their next visit: the garage card shows "B accepted your
   challenge — you won/lost" (polled from the same token).

**Mechanics & rules.**
- A ghost race is only valid **same UTC day** (the set rotates); an expired
  link falls back to the current plain deep link behaviour.
- Ghosts show the challenger's guess *after* the recipient locks in — the
  server returns the ghost data on `verify`, never before, so it can't be used
  as a hint. (This is the key trust-boundary detail.)
- Works guest-to-guest: the token references `play_events` rows by `anon_id`
  or user, no account needed on either side.

**Backend.** One table `challenges (token PK, owner_user_or_anon, period,
mode, created_at)`; `POST /api/v1/challenge` creates a token after a finished
run; `verify` accepts an optional `challenge_token` and adds
`ghost: {guess, score}` for the matching question to its response; `GET
/api/v1/challenge/{token}` returns the summary comparison (both totals, both
grids) once the recipient finishes. Prune rows > 7 days.

**Frontend.** Parse `?race=`; a ghost banner on the intro card; the third
timeline node (the grey `car-ghost` sprite exists); the verdict screen is a
variant of the summary card. Achievement hooks: *Wheel to Wheel* (first ghost
race), *Divebomb* (win one by <500).

**Effort.** ~2–3 days. **Risk.** Low — additive, no scoring changes.
**Success metric.** `challenge_created → challenge_completed` conversion, and
D1 retention of challenge recipients vs organic visitors (analytics events
already flow).

---

### F2 — Grand Prix Weekend (event mode)

**One-liner.** On race weekends, the Daily gets a themed companion: six
questions about the weekend's circuit, its history and its heroes — riding the
sport's built-in traffic spikes. (Extends the ENGAGEMENT backlog item into a
concrete spec.)

**Design.**
- **Availability window:** FP1 Friday → midnight Sunday UTC, driven by the
  schedule the countdown HUD already has (`SESSIONS_2026` /
  `schedule-2026.json`). The HUD countdown becomes a tap-through to the event
  when live — the two features finally connect.
- **Content:** the question generator already tags per-circuit questions
  (`per-circuit` category); provisioning filters the validated bank by the
  weekend's `circuit_id`, topped up with drivers who starred there. Thin
  circuits (new venues) fall back to country/era-themed questions — the
  deterministic provisioning path (`build_quiz`) just takes a different filter.
- **Reward:** a per-event badge (*Monaco '26 — P1/podium/finisher* by
  percentile), rendered as a collectible sticker wall on the profile — 24
  scheduled chances a year to re-engage lapsed players, each individually
  shareable.
- **Leaderboard:** the event is its own `game_mode`, so the existing
  period-window leaderboard machinery gives an event board for free.

**Effort.** ~3–4 days (mostly provisioning + badge art).
**Risk.** Content thinness at new circuits — mitigated by the fallback.
**Metric.** Weekend DAU uplift vs non-race weekends; event completion rate.

---

### F3 — Arcade 2.0: "Qualifying"

**One-liner.** Rebuild over/under as a session with stakes: three lives, a
closing gap, a server-verified best streak, and a daily board.

**Session shape.**
- **Q1 (streak 0–4):** pairs with a generous value gap — warm-up.
- **Q2 (5–9):** gap tightens (`ARCADE_MAX_GAP` steps down; the close-pair
  picker already biases by gap, so this is a parameter, not new logic).
- **Q3 (10+):** knife-edge pairs (<10% apart) and mixed metrics ("poles" vs
  "wins" comparisons for the brave).
- **Three lives** per run; a wrong pick costs one and *shows the margin* ("you
  were 3 wins off"). Run ends at zero lives → summary card with best-streak,
  share text, and "again?" (instant restart — this mode's whole point is one
  more go).
- **Timer pressure, opt-in:** after streak 10, a 10-second shot clock appears
  (with the existing tick sound). Keeps the accessibility default calm.

**Trust.** Streaks become comparable, so they need the same treatment as the
Daily: `GET /arcade/run` issues a run token; each `POST /arcade/pick` sends the
pick, the server returns correct/margin and increments the streak server-side;
final streak lands in `play_events` (new `game_mode="arcade"`), which gives the
daily/weekly arcade leaderboard through the existing window machinery.

**Visual.** Full `in-game` treatment (V3): two driver cards slide in from
opposite grid slots, values `countUp` on reveal, lives shown as three tyre
icons that blister away, streak counter in the timing-tower style.

**Effort.** ~3 days backend-light, frontend-medium. **Risk.** Low.
**Metric.** Arcade runs/DAU, share rate of arcade cards, post-Daily
continuation rate (pairs with G7).

---

### F4 — Super Licence progression + livery garage

**One-liner.** A named rank ladder over server-verified lifetime points, with
cosmetic car liveries as unlocks — permanent goals that survive any single bad
day.

**Ladder.** Karting → F4 → F3 → F2 → Rookie → Midfielder → Points Scorer →
Podium Finisher → Race Winner → World Champion (10 tiers, exponential
thresholds tuned so a daily player hits a new tier roughly monthly for the
first half-year). Rank is **derived** from `play_events` totals — one SQL
expression in `auth.me`/leaderboard queries, no new state, impossible to forge.

**Surfaces.**
- Tier chip beside the username on the leaderboard, profile and share text
  ("🏁 GridMaster Daily #191 · *Race Winner*").
- Tier-up moment: full-screen flash (the sector-flash pattern) + a dedicated
  share card — a second share trigger unrelated to today's score, which
  matters on bad-score days.
- Garage card on home shows progress to the next tier ("2,140 pts to F2").

**Livery garage (the cosmetic layer).** The slider/reveal car
(`F1_CAR_SHAPES`) currently paints in team colours; let tiers and select
achievements unlock alternate liveries — chrome, gulf-style, chequered,
carbon — as a `data-livery` attribute swapping the CSS fills. Purely local
cosmetics (Architecture §2.2-consistent), but they make the most-touched object
in the game a status symbol. The achievements system gains its missing
extrinsic reward without touching the points economy.

**Effort.** ~2 days for the ladder, +2 for liveries.
**Risk.** Threshold tuning; start generous, tighten for new tiers later
(never retroactively demote). **Metric.** W4 retention of players who reach
tier 3+ vs those who don't.

---

### F5 — "The story behind the number" (reveal enrichment)

**One-liner.** After the odometer settles, one line of verified context turns
each answer from a number into a piece of F1 lore — the game starts *teaching*,
which is why stats fans show up.

**Design.** The reveal card gains a single muted line under the insight:
- Count/points: a 12-season **sparkline** of the metric ("his wins peaked in
  2004 — 13 of them"), rendered as a tiny inline SVG from per-season values.
- Year: what bracketed it ("…two years before the team's last title").
- Head-to-head: the season the gap flipped.

**Data path.** The ETL already stages race-by-race results
(`staging_race_results`); at question-generation time, precompute a
`context: {seasons: [[year, value]…], note}` blob per question and store it
alongside the verified answer. The verify response returns it *after* scoring
(same trust posture as the answer itself). No LLM in the loop — the note is
templated from the same aggregations the validator runs, so it inherits the
anti-hallucination guarantee.

**Why it matters for retention.** "I learned something" is the sharable,
tellable outcome for the audience that *loses*. Every other hook in the game
pays winners; this one pays everyone.

**Effort.** ~2–3 days (generator templates + sparkline).
**Risk.** Template blandness — ship the sparkline first, notes second.
**Metric.** Session length / practice continuation after reveals with context
vs without (A/B by question).

---

## 5. Prioritised roadmap

| # | Item | Type | Impact | Effort | Notes |
|---|---|---|---|---|---|
| 1 | G1/G2/G3 scoring & bounds fixes | Balance | ★★★★★ | S | Correctness of the core economy; do before anything amplifies sharing |
| 2 | G7 After-the-flag funnel | Loop | ★★★★ | S | Re-plumb existing surfaces |
| 3 | F1 Ghost Race | Feature | ★★★★★ | M | The share loop becomes a game |
| 4 | V1/V2/V4/V6/V8 visual fixes | Polish | ★★★ | S | One pass, half a day total |
| 5 | G4 Daily dramaturgy (ramp → Overtake) | Mechanic | ★★★★ | S–M | Ramp first; wager once G1 lands |
| 6 | F4 Super Licence + liveries | Progression | ★★★★ | M | Ladder first, liveries second |
| 7 | F3 Arcade "Qualifying" | Mode | ★★★ | M | Pairs with #2's funnel |
| 8 | G9 onboarding timing, G6 practice penalty | Funnel | ★★★ | S | Measure via existing analytics |
| 9 | F2 GP Weekend events | Feature | ★★★ | M | Needs the schedule feed (backlog Tier 2) |
| 10 | F5 Reveal enrichment | Content | ★★★ | M | Best paired with the ETL/LLM roadmap step |
| 11 | V7 history routing, V5 steppers, V9 collisions | Polish | ★★ | S | Quality-of-life batch |

**The order in one sentence:** first make the scoring economy sound (1), then
make finishing the Daily lead somewhere (2), then make sharing a two-player
game (3) — everything after that compounds on a loop that is finally airtight.
