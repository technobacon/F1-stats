# FULL ARCHITECTURE BLUEPRINT
## Game Logic, Frontend Stack & Monetization Strategy
**Module:** Full-Stack Deployment
**Target Audience:** Full-Stack Developers / DevOps Engineers

### 1. Game Logic & API State Architecture
To prevent client-side tampering and ensure a unified competitive experience, core score calculations and round caching configurations must remain isolated to secure server environments.

#### 1.1 Global Daily Synchronization Architecture
A cron service running daily at 00:00 UTC queries the master database to provision the global challenges. It selects five verified entries, writing them into a high-speed caching registry or structured database look-up layer.

- **Data Payload Security:** When the endpoint `GET /api/v1/quiz/daily` is targeted by the client engine, the server returns the question string, its metadata descriptors, and an abstract question tracking token. The true numerical statistical answer is explicitly omitted from this payload block to prevent cheat mechanisms via inspector tool tracing.
- **Verification Evaluation Routing:** Guesses are forwarded securely to `POST /api/v1/quiz/daily/verify` containing the tracking token and user guess. The server evaluates accuracy server-side using the percentage-error exponential decay engine and stores logs before returning final score maps.

#### 1.2 High-Performance Arcade Querying Engine
To preserve sub-second response parameters during heavy concurrent scaling phases, database queries mapping driver matchups must never employ arbitrary `ORDER BY RANDOM()` filters. Engineers should use index-based random offset targeting to fetch two competing entities within an intersecting historical runtime threshold.

### 2. Frontend Infrastructure & Frictionless Client State
The client-side infrastructure leverages frameworks equipped for performant edge rendering (such as Next.js, React, or Nuxt.js) alongside optimized localized object management models.

#### 2.1 Guest-First Local Storage Schema
Before requiring account registration blocks, client states must write immediately to local container strings. The frontend tracks achievements and gameplay streaks transparently using a unified `localStorage` key configuration:

```json
{
  "user_state": {
    "is_guest": true,
    "selected_team": "mclaren",
    "lifetime_points": 24500,
    "games_played": 12,
    "average_closeness": 0.934,
    "daily_streak": 4,
    "last_played_date": "2026-06-07",
    "unlocked_achievements": ["monaco_master", "podium_streak"]
  }
}
```

#### 2.2 Anonymous Data Synchronization (Account Merging)
When an unauthenticated player requests an explicit login upgrade to secure global scoreboard indexing, a migration controller handles syncing.

> **Trust Boundary (Critical):** The backend must **never** trust client-supplied aggregate totals (`lifetime_points`, `average_closeness`, streak counters) when populating the Global Leaderboard or Constructors Championship standings. The `localStorage` blob is fully editable by the user; accepting it verbatim would allow a player to inject an arbitrary score (e.g., editing `lifetime_points` to `24500`) directly onto the leaderboard. This is consistent with the server-side scoring stance in §1.1.

Two acceptable migration strategies:

1. **Verified-event merge (preferred):** Only individually server-verified round events (each carrying its tracking token and server-computed score) are accepted from the guest history. Aggregate totals are then recomputed server-side from those trusted rows.
2. **Cosmetic-only merge:** Non-competitive state (e.g., `selected_team`, unlocked achievement flags that do not affect ranking) may be carried over from `localStorage` directly, while all leaderboard-affecting totals are reconstructed exclusively from server-side logs.

### 3. Tribal Personalization & Animation Engineering

#### 3.1 Tailwind CSS Variable Dynamic Theming
F1 fan bases are inherently fragmented across constructor lines. Custom UI dynamic configurations inject distinct root color properties based on a subscriber's chosen profile affinity alignment:

```css
/* Dynamic Constructor Theme Mappings */
:root[data-team="ferrari"] {
  --color-primary: #EF3829;     /* Maranello Red */
  --color-accent: #F9C900;      /* Modena Yellow */
}
:root[data-team="mercedes"] {
  --color-primary: #6CD3BF;     /* Petronas Turquoise */
  --color-accent: #000000;      /* Mercedes Black (modern livery) */
}
:root[data-team="mclaren"] {
  --color-primary: #FF8700;     /* Papaya Orange */
  --color-accent: #1B2425;      /* Anthracite Grey */
}
```

#### 3.2 The Odometer Score Reveal Animation
To maintain elevated behavioral dopamine responses during question results evaluation loops, score readouts must be metered. Using keyframe primitives or interpolation libraries (like Framer Motion), the view executes a step sequence:

1. Renders a normalized horizontal timeline scaling between localized data spectrum limits.
2. Animates a user-controlled node vector sliding across the axis until intersecting their guess placement.
3. Drops a target marker from the upper boundary signifying true statistical reality.
4. Triggers a fast text counter string ticking rapidly from 0 upwards to their calculated point score.

### 4. Monetization Integration & Ad Network Management

#### 4.1 Lazy-Loading Intersection Observer Implementations
To prevent ad blockers from restricting core canvas loading times, display ad scripts must remain decoupled from the critical initialization loop. Ads inject specifically inside interstitial placeholder frames using an Intersection Observer interface, meaning code is queried only as the user concludes gameplay actions and transfers onto summary cards.

#### 4.2 Programmatic Yield Optimization
Developers must swap standard ad code modules for specialized HTML5 gaming programmatic SDK layers (e.g., AdInPlay or Playwire). These libraries support real-time header bidding, ensuring top-tier advertisers actively bid against alternative inventories for high-value player demographics. An internal Boolean parameter flag checking `is_premium == true` will clear all ad frames from the layout configuration dynamically.
