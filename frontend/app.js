/* GridMaster — prototype frontend.
 * Guest-first: all progress lives in localStorage (PRD §5.2, Architecture §2.1).
 * Scoring is NEVER computed here — guesses go to the server, which returns the score. */

const API = "/api/v1";
// NOTE: these localStorage keys keep the legacy "f1statguesser_" prefix on
// purpose — the product is now GridMaster, but renaming the keys would orphan
// every existing player's saved progress, session and guest id. Leave them.
const STORAGE_KEY = "f1statguesser_user_state";
const TOKEN_KEY = "f1statguesser_auth_token";
const ANON_KEY = "f1statguesser_anon_id";

/* ---- Auth/session helpers ----
 * The session token is an opaque bearer credential from the server. The anon id
 * is a stable per-device id so guesses made while logged out are recorded
 * server-side and can be claimed on sign-in (Architecture §2.2). */
function authToken() { return localStorage.getItem(TOKEN_KEY); }
function setAuthToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}
function anonId() {
  let id = localStorage.getItem(ANON_KEY);
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID()) ||
         `anon-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(ANON_KEY, id);
  }
  return id;
}
function authHeaders(extra = {}) {
  const t = authToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : { ...extra };
}
function isSignedIn() { return !!authToken(); }

/* ---- First-party analytics ----
 * Pseudonymous, self-contained: events are keyed by the existing guest anon_id
 * plus a per-tab session id, queued client-side and flushed in small batches
 * (via sendBeacon on page hide so nothing is lost on exit). No third-party tag,
 * no extra cookies. Scoring/leaderboard never depend on this — it's telemetry. */
const ANALYTICS_SESSION_KEY = "f1sg_session_id";
function sessionId() {
  let id = sessionStorage.getItem(ANALYTICS_SESSION_KEY);
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID()) || `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem(ANALYTICS_SESSION_KEY, id);
  }
  return id;
}
let _evQueue = [];
function track(event, props) {
  _evQueue.push({ event, props: props || {}, t: Date.now() });
  if (_evQueue.length >= 20) flushAnalytics(false);
}
function flushAnalytics(useBeacon) {
  if (!_evQueue.length) return;
  const body = JSON.stringify({ anon_id: anonId(), session_id: sessionId(), events: _evQueue.splice(0) });
  const url = `${API}/analytics/collect`;
  try {
    if (useBeacon && navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    } else {
      fetch(url, { method: "POST", headers: authHeaders({ "Content-Type": "application/json" }), body, keepalive: true })
        .catch(() => {});
    }
  } catch { /* telemetry must never throw into the app */ }
}
setInterval(() => flushAnalytics(false), 15000);
addEventListener("pagehide", () => flushAnalytics(true));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") flushAnalytics(true);
});

/* ---- Mode metadata ---- */
const MODES = {
  daily: {
    title: "Daily General Challenge",
    desc: "Six questions spanning all of F1 history. The closer your guess, the more of the 5,000 points per question you keep.",
    capKey: () => utcDate(), capLabel: "today's Daily General Challenge", slider: true,
  },
  race_week: {
    title: "Daily Race Challenge",
    desc: "Six questions on teams, circuits and race-day feats from across the eras. The closer your guess, the bigger the score.",
    capKey: () => utcDate(), capLabel: "today's Daily Race Challenge", slider: true,
  },
  free_practice: {
    title: "Free Practice",
    desc: "Unlimited random questions to sharpen your instincts. Your score is shown here " +
      "but never saved or ranked — it's pure practice. To keep it fair, scoring under " +
      "1,000 points on a question hands your team a 10-second penalty before the next one " +
      "(an anti-scouting measure, explained when it happens).",
    capKey: null, capLabel: "", slider: true, free: true,
  },
};

/* Free Practice anti-scouting rule: a question scored under the threshold blocks
 * the Next button for a few seconds. This deters "quiz-scouting" — burning through
 * questions with throwaway guesses just to reveal and memorise the answers. The
 * wait is framed as a stewards' time penalty handed to the player's chosen team. */
const PRACTICE_PENALTY_THRESHOLD = 1000; // out of 5,000 per question
const PRACTICE_PENALTY_SECONDS = 10;

/* ---- Guest-first local state (Architecture §2.1 schema) ---- */
const defaultState = () => ({
  is_guest: true, selected_team: "mclaren",
  lifetime_points: 0, games_played: 0, average_closeness: 0,
  daily_streak: 0, last_played_date: null, unlocked_achievements: [],
  // Streak freeze: one missed day inside a run is forgiven once (re-armed when a
  // new run starts). Mirrors the server rule in auth.daily_streak.
  streak_freeze_available: true,
  // Counters + flags backing the achievement catalog (see ACHIEVEMENTS). Lazily
  // filled by ensureAch(); kept local/cosmetic like the streak (Architecture §2.2).
  ach: {},
  _closeness_sum: 0, _q_count: 0,
});

/* The achievement stat bucket, ensured to exist (older saved states predate it). */
function ensureAch() {
  if (!state.ach || typeof state.ach !== "object") state.ach = {};
  if (!state.ach.flags || typeof state.ach.flags !== "object") state.ach.flags = {};
  if (!Array.isArray(state.ach.teams_used)) state.ach.teams_used = [];
  return state.ach;
}
function achFlag(name) { ensureAch().flags[name] = true; }
function recordTeamUse(team) {
  const a = ensureAch();
  if (team && !a.teams_used.includes(team)) { a.teams_used.push(team); }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...defaultState(), ...JSON.parse(raw).user_state } : defaultState();
  } catch { return defaultState(); }
}
function saveState(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ user_state: s }));
  document.getElementById("hud-points").textContent = `${s.lifetime_points.toLocaleString()} pts`;
  renderProfile();
}
let state = loadState();

/* ---- Date helpers (cap periods) ---- */
function utcDate() { return new Date().toISOString().slice(0, 10); }
function isoWeek() {
  const d = new Date();
  const day = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - day + 3);
  const firstThu = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  const week = 1 + Math.round(((d - firstThu) / 864e5 - 3 + ((firstThu.getUTCDay() + 6) % 7)) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

/* ---- All 2026 F1 constructor colour schemes ----
 * Each team has a main (primary) colour and a secondary accent. Main buttons render
 * solid in the primary with a thin secondary stripe along the bottom edge.
 * `ink` is a legibility-safe variant of the colour used for TEXT on the dark UI:
 * for most teams it equals the primary, but dark primaries (Red Bull navy, Haas
 * near-black, Williams/RB deep blue, Aston dark teal) are lightened so they don't
 * vanish against the background. Fills/borders keep the true `primary`. */
const TEAMS = {
  mclaren:      { name: "McLaren",      primary: "#FF8000", secondary: "#1B2425", text: "#000", ink: "#FF8000", inkLight: "#C95E00" },
  ferrari:      { name: "Ferrari",      primary: "#DC0000", secondary: "#FFEB00", text: "#fff", ink: "#FF3232", inkLight: "#D10000" },
  mercedes:     { name: "Mercedes",     primary: "#00D2BE", secondary: "#0A0A0A", text: "#000", ink: "#00D2BE", inkLight: "#00897B" },
  red_bull:     { name: "Red Bull",     primary: "#1E1B4B", secondary: "#DC0000", text: "#fff", ink: "#7E7CFF", inkLight: "#2A2570" },
  aston_martin: { name: "Aston Martin", primary: "#006F62", secondary: "#CEDC00", text: "#fff", ink: "#1FC8AC", inkLight: "#006F62" },
  alpine:       { name: "Alpine",       primary: "#FF87BC", secondary: "#0090FF", text: "#000", ink: "#FF87BC", inkLight: "#D63384" },
  williams:     { name: "Williams",     primary: "#0064FF", secondary: "#FFFFFF", text: "#fff", ink: "#4D8DFF", inkLight: "#0056D6" },
  rb:           { name: "Racing Bulls", primary: "#1634CE", secondary: "#FFFFFF", text: "#fff", ink: "#5A77FF", inkLight: "#1634CE" },
  haas:         { name: "Haas",         primary: "#1A1A1A", secondary: "#E8002D", text: "#fff", ink: "#FF4D67", inkLight: "#1A1A1A" },
  audi:         { name: "Audi",         primary: "#8E8E8E", secondary: "#CC0000", text: "#000", ink: "#C7CCD4", inkLight: "#5A5A5A" },
  cadillac:     { name: "Cadillac",     primary: "#FFFFFF", secondary: "#0D0D0D", text: "#000", ink: "#FFFFFF", inkLight: "#0D0D0D" },
};

/* ---- Theming (Architecture §3.1) ---- */
function applyTeam(team) {
  const t = TEAMS[team] || TEAMS.mclaren;
  const root = document.documentElement;
  root.setAttribute("data-team", team);
  root.style.setProperty("--color-primary", t.primary);
  root.style.setProperty("--color-secondary", t.secondary);
  root.style.setProperty("--team-ink-dark", t.ink || t.primary);
  root.style.setProperty("--team-ink-light", t.inkLight || t.ink || t.primary);
  root.style.setProperty("--btn-text", t.text);
  /* Header dot: solid main colour with a secondary accent ring */
  const swatch = document.getElementById("team-btn-swatch");
  const label  = document.getElementById("team-btn-label");
  if (swatch) { swatch.style.background = t.primary; swatch.style.boxShadow = `inset 0 0 0 2px ${t.secondary}`; }
  if (label)  label.textContent = t.name;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t.primary);
  state.selected_team = team;
}

/* ---- Toast ---- */
let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

/* ===================== COUNTDOWN HUD (PRD §5.1) ===================== */
/* Real remaining 2026 F1 sessions (all UTC), mirrored in /static/schedule-2026.json.
 * Every FP1/FP2/FP3, Sprint Qualifying, Sprint, Qualifying and Grand Prix after
 * 2026-06-08. The HUD counts down to whichever of these is next. */
const SESSIONS_2026 = [
  ["2026-06-12T11:30:00Z", "Spanish GP", "FP1"], ["2026-06-12T15:00:00Z", "Spanish GP", "FP2"],
  ["2026-06-13T10:30:00Z", "Spanish GP", "FP3"], ["2026-06-13T14:00:00Z", "Spanish GP", "Qualifying"],
  ["2026-06-14T13:00:00Z", "Spanish GP", "Race"],
  ["2026-06-26T11:30:00Z", "Austrian GP", "FP1"], ["2026-06-26T15:00:00Z", "Austrian GP", "FP2"],
  ["2026-06-27T10:30:00Z", "Austrian GP", "FP3"], ["2026-06-27T14:00:00Z", "Austrian GP", "Qualifying"],
  ["2026-06-28T13:00:00Z", "Austrian GP", "Race"],
  ["2026-07-03T11:30:00Z", "British GP", "FP1"], ["2026-07-03T15:30:00Z", "British GP", "Sprint Qualifying"],
  ["2026-07-04T11:00:00Z", "British GP", "Sprint"], ["2026-07-04T15:00:00Z", "British GP", "Qualifying"],
  ["2026-07-05T14:00:00Z", "British GP", "Race"],
  ["2026-07-17T11:30:00Z", "Belgian GP", "FP1"], ["2026-07-17T15:00:00Z", "Belgian GP", "FP2"],
  ["2026-07-18T10:30:00Z", "Belgian GP", "FP3"], ["2026-07-18T14:00:00Z", "Belgian GP", "Qualifying"],
  ["2026-07-19T13:00:00Z", "Belgian GP", "Race"],
  ["2026-07-24T11:30:00Z", "Hungarian GP", "FP1"], ["2026-07-24T15:00:00Z", "Hungarian GP", "FP2"],
  ["2026-07-25T10:30:00Z", "Hungarian GP", "FP3"], ["2026-07-25T14:00:00Z", "Hungarian GP", "Qualifying"],
  ["2026-07-26T13:00:00Z", "Hungarian GP", "Race"],
  ["2026-08-21T10:30:00Z", "Dutch GP", "FP1"], ["2026-08-21T14:30:00Z", "Dutch GP", "Sprint Qualifying"],
  ["2026-08-22T10:00:00Z", "Dutch GP", "Sprint"], ["2026-08-22T14:00:00Z", "Dutch GP", "Qualifying"],
  ["2026-08-23T13:00:00Z", "Dutch GP", "Race"],
  ["2026-09-04T10:30:00Z", "Italian GP", "FP1"], ["2026-09-04T14:00:00Z", "Italian GP", "FP2"],
  ["2026-09-05T10:30:00Z", "Italian GP", "FP3"], ["2026-09-05T14:00:00Z", "Italian GP", "Qualifying"],
  ["2026-09-06T13:00:00Z", "Italian GP", "Race"],
  ["2026-09-11T11:30:00Z", "Madrid GP", "FP1"], ["2026-09-11T15:00:00Z", "Madrid GP", "FP2"],
  ["2026-09-12T10:30:00Z", "Madrid GP", "FP3"], ["2026-09-12T14:00:00Z", "Madrid GP", "Qualifying"],
  ["2026-09-13T13:00:00Z", "Madrid GP", "Race"],
  ["2026-09-24T08:30:00Z", "Azerbaijan GP", "FP1"], ["2026-09-24T12:00:00Z", "Azerbaijan GP", "FP2"],
  ["2026-09-25T08:30:00Z", "Azerbaijan GP", "FP3"], ["2026-09-25T12:00:00Z", "Azerbaijan GP", "Qualifying"],
  ["2026-09-26T11:00:00Z", "Azerbaijan GP", "Race"],
  ["2026-10-09T08:30:00Z", "Singapore GP", "FP1"], ["2026-10-09T12:30:00Z", "Singapore GP", "Sprint Qualifying"],
  ["2026-10-10T09:00:00Z", "Singapore GP", "Sprint"], ["2026-10-10T13:00:00Z", "Singapore GP", "Qualifying"],
  ["2026-10-11T12:00:00Z", "Singapore GP", "Race"],
  ["2026-10-23T17:30:00Z", "United States GP", "FP1"], ["2026-10-23T21:00:00Z", "United States GP", "FP2"],
  ["2026-10-24T17:30:00Z", "United States GP", "FP3"], ["2026-10-24T21:00:00Z", "United States GP", "Qualifying"],
  ["2026-10-25T20:00:00Z", "United States GP", "Race"],
  ["2026-10-30T18:30:00Z", "Mexico City GP", "FP1"], ["2026-10-30T22:00:00Z", "Mexico City GP", "FP2"],
  ["2026-10-31T17:30:00Z", "Mexico City GP", "FP3"], ["2026-10-31T21:00:00Z", "Mexico City GP", "Qualifying"],
  ["2026-11-01T20:00:00Z", "Mexico City GP", "Race"],
  ["2026-11-06T15:30:00Z", "São Paulo GP", "FP1"], ["2026-11-06T19:00:00Z", "São Paulo GP", "Sprint Qualifying"],
  ["2026-11-07T14:30:00Z", "São Paulo GP", "Sprint"], ["2026-11-07T18:00:00Z", "São Paulo GP", "Qualifying"],
  ["2026-11-08T17:00:00Z", "São Paulo GP", "Race"],
  ["2026-11-20T00:30:00Z", "Las Vegas GP", "FP1"], ["2026-11-20T04:00:00Z", "Las Vegas GP", "FP2"],
  ["2026-11-21T00:30:00Z", "Las Vegas GP", "FP3"], ["2026-11-21T04:00:00Z", "Las Vegas GP", "Qualifying"],
  ["2026-11-22T04:00:00Z", "Las Vegas GP", "Race"],
  ["2026-11-27T13:30:00Z", "Qatar GP", "FP1"], ["2026-11-27T17:00:00Z", "Qatar GP", "FP2"],
  ["2026-11-28T14:30:00Z", "Qatar GP", "FP3"], ["2026-11-28T18:00:00Z", "Qatar GP", "Qualifying"],
  ["2026-11-29T16:00:00Z", "Qatar GP", "Race"],
  ["2026-12-04T09:30:00Z", "Abu Dhabi GP", "FP1"], ["2026-12-04T13:00:00Z", "Abu Dhabi GP", "FP2"],
  ["2026-12-05T10:30:00Z", "Abu Dhabi GP", "FP3"], ["2026-12-05T14:00:00Z", "Abu Dhabi GP", "Qualifying"],
  ["2026-12-06T13:00:00Z", "Abu Dhabi GP", "Race"],
];
const SEASON_2027_OPENER = ["Australian GP", "2027-03-08T04:00:00Z"]; // off-season pivot target

const SESSIONS = SESSIONS_2026
  .map(([iso, name, kind]) => ({ when: new Date(iso), name, kind, text: `${name} · ${kind}` }))
  .sort((a, b) => a.when - b.when);

function tickCountdown() {
  const now = new Date();
  let target = SESSIONS.find((s) => s.when > now);
  let label = target ? `NEXT · ${target.text}` : `SEASON 2027 · ${SEASON_2027_OPENER[0]}`;
  let when = target ? target.when : new Date(SEASON_2027_OPENER[1]);

  const diff = Math.max(0, when - now);
  const d = Math.floor(diff / 864e5), h = Math.floor(diff % 864e5 / 36e5),
        m = Math.floor(diff % 36e5 / 6e4), s = Math.floor(diff % 6e4 / 1e3);
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("countdown-label").textContent = label;
  document.getElementById("countdown-timer").textContent = `${pad(d)}:${pad(h)}:${pad(m)}:${pad(s)}`;

  // Mirror the live countdown onto the home-page race-week panel.
  const rwTimer = document.getElementById("rw-next-timer");
  if (rwTimer) {
    rwTimer.textContent = `${pad(d)}:${pad(h)}:${pad(m)}:${pad(s)}`;
    document.getElementById("rw-next-name").textContent = target ? target.text : SEASON_2027_OPENER[0];
  }
}

/* ===================== NEXT RACE WEEKEND PANEL ===================== */
/* Renders every session of the upcoming Grand Prix weekend as a modern list,
 * marking the next session and dimming any that have already run. */
function renderRaceWeek() {
  const list = document.getElementById("rw-list");
  if (!list) return;
  const now = new Date();
  const next = SESSIONS.find((s) => s.when > now);
  if (!next) {
    document.getElementById("rw-title").textContent = `${SEASON_2027_OPENER[0]} · 2027`;
    list.innerHTML = `<li class="muted" style="padding:.6rem .2rem">Season complete — see you in 2027.</li>`;
    return;
  }
  const weekend = SESSIONS.filter((s) => s.name === next.name);
  document.getElementById("rw-title").textContent = next.name;

  const localZone = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
    .formatToParts(now).find((p) => p.type === "timeZoneName")?.value || "local";
  const dow = (d) => d.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase();
  const time = (d) => d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const sprintKinds = ["Sprint", "Sprint Qualifying"];

  list.innerHTML = weekend.map((s) => {
    const isNext = s === next;
    const isPast = s.when <= now;
    const kindClass = sprintKinds.includes(s.kind) ? "sprint" : (s.kind === "Race" ? "race" : "");
    const cls = isNext ? "is-next" : (isPast ? "is-past" : "");
    return `<li class="rw-row ${cls}">
        <span class="rw-day"><span class="dow">${dow(s.when)}</span><span class="dnum">${s.when.getDate()}</span></span>
        <span class="rw-meta"><span class="rw-session">${s.kind}</span>
          <span class="rw-kind ${kindClass}">${isNext ? "Up next" : (isPast ? "Finished" : "Upcoming")}</span></span>
        <span class="rw-time">${time(s.when)}<small>${localZone}</small></span>
      </li>`;
  }).join("");
}

/* ===================== VIEW SWITCHING ===================== */
/* One router for everything that carries data-view: the top-nav tabs, the brand,
 * the hero buttons and the landing-page mode cards. */
let currentMode = "daily";
function navigate(view, mode) {
  track("view", { view, mode: mode || null });
  Sound.play("uiClick");   // subtle tap so the chrome feels responsive
  // Leaving the quiz (or re-entering its intro) always exits immersive mode and
  // cancels any in-flight Free Practice penalty countdown.
  if (view !== "quiz") { document.body.classList.remove("in-game"); clearInterval(practiceTimer); }
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  document.getElementById("view-" + view).classList.add("active");
  // Sync the top-nav highlight (tabs only; cards/buttons aren't tabs).
  document.querySelectorAll(".mode-tab").forEach((t) => {
    const match = t.dataset.view === view && (t.dataset.mode || null) === (mode || null);
    t.classList.toggle("active", match);
  });
  if (view === "quiz") { currentMode = mode || currentMode; renderQuizIntro(); }
  if (view === "arcade") loadArcade();
  if (view === "profile") renderProfile();
  if (view === "home") { renderStreakBanner(); loadHomeTower(); }
  window.scrollTo({ top: 0, behavior: "smooth" });
}
document.querySelectorAll("[data-view]").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    navigate(el.dataset.view, el.dataset.mode);
  });
});

/* ===================== QUIZ (Daily / Race-Week / One-Shots) ===================== */
let quiz = null, qPos = 0, sessionScore = 0, sessionCloseness = 0;
// Per-question scores for this run, used to build the spoiler-free share grid.
let sessionResults = [];
// Per-question "beat X% of players" percentiles (social proof), when available.
let sessionInsights = [];
// Purple sectors (guesses within 10%) hit this session — for the Grand Slam etc.
let sessionPurpleCount = 0;
let practiceCount = 0, practiceTimer = null; // Free Practice: questions answered + penalty countdown

function playedKey(mode) { return `played_${mode}`; }
function isCapped(mode) {
  const cfg = MODES[mode];
  if (!cfg.capKey) return false;
  return localStorage.getItem(playedKey(mode)) === cfg.capKey();
}

function renderQuizIntro() {
  const cfg = MODES[currentMode];
  document.getElementById("quiz-title").textContent = cfg.title;
  document.getElementById("quiz-desc").textContent = cfg.desc;
  document.body.classList.remove("in-game"); // intro is not part of the immersive run
  show("quiz-intro"); hide("quiz-play"); hide("quiz-reveal"); hide("quiz-summary");
  document.getElementById("quiz-status").textContent = "";

  const capNote = document.getElementById("quiz-cap");
  const startBtn = document.getElementById("start-quiz");
  const replayBtn = document.getElementById("replay-quiz");
  if (isCapped(currentMode)) {
    capNote.textContent = `✓ You've completed ${cfg.capLabel}. Come back next period for a fresh set.`;
    capNote.classList.remove("hidden");
    startBtn.classList.add("hidden");
    replayBtn.classList.remove("hidden");
  } else {
    capNote.classList.add("hidden");
    startBtn.classList.remove("hidden");
    startBtn.textContent = "Start Session";
    replayBtn.classList.add("hidden");
  }
}

document.getElementById("start-quiz").addEventListener("click", () => startQuiz());
document.getElementById("replay-quiz").addEventListener("click", () => startQuiz());

async function startQuiz() {
  if (MODES[currentMode].free) return startFreePractice();
  const status = document.getElementById("quiz-status");
  status.textContent = "Loading questions…";
  try {
    const res = await fetch(`${API}/quiz/${currentMode}`);
    if (!res.ok) throw new Error(await res.text());
    quiz = await res.json();
    track("quiz_start", { mode: currentMode });
    qPos = 0; sessionScore = 0; sessionCloseness = 0; sessionResults = []; sessionInsights = [];
    sessionPurpleCount = 0;
    document.getElementById("q-total").textContent = quiz.questions.length;
    document.getElementById("q-mode-badge").textContent = currentMode.replace("_", "-");
    hide("quiz-intro"); show("quiz-play"); hide("quiz-summary"); hide("quiz-reveal");
    document.body.classList.add("in-game"); // go full-screen immersive
    Sound.play("lightsOut");                // lights out and away we go
    window.scrollTo({ top: 0 });
    renderQuestion();
  } catch (e) {
    status.textContent = "Could not load quiz. Tap to retry.";
    toast("Network error — is the server awake?");
  }
}

/* ===================== FREE PRACTICE (unlimited, non-competitive) ===================== *
 * Pulls one random question at a time from /practice/question. The score is shown
 * for feedback but is NEVER recorded (the server skips persistence for this mode),
 * so there is no summary, cap or leaderboard write — just a rolling session tally. */
async function fetchPracticeQuestion() {
  const res = await fetch(`${API}/practice/question`);
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()).question;
}

async function startFreePractice() {
  const status = document.getElementById("quiz-status");
  status.textContent = "Loading question…";
  clearInterval(practiceTimer);
  practiceCount = 0; sessionScore = 0; sessionCloseness = 0; sessionPurpleCount = 0;
  try {
    const q = await fetchPracticeQuestion();
    track("practice_start");
    quiz = { questions: [q], free: true };
    qPos = 0;
    document.getElementById("q-total").textContent = "∞";
    document.getElementById("q-mode-badge").textContent = "practice";
    hide("quiz-intro"); show("quiz-play"); hide("quiz-summary"); hide("quiz-reveal");
    document.body.classList.add("in-game");
    Sound.play("lightsOut");                // lights out and away we go
    window.scrollTo({ top: 0 });
    renderQuestion();
  } catch (e) {
    status.textContent = "Could not load a question. Tap to retry.";
    toast("Network error — is the server awake?");
  }
}

async function nextPracticeQuestion() {
  const btn = document.getElementById("next-question");
  btn.disabled = true; btn.textContent = "Loading…";
  try {
    const q = await fetchPracticeQuestion();
    quiz.questions = [q]; qPos = 0;
    practiceCount += 1;
  } catch {
    toast("Couldn't load the next question — tap to retry.");
    btn.disabled = false; btn.textContent = "Next question";
    return;
  }
  hide("quiz-reveal"); show("quiz-play");
  renderQuestion();
}

/* After a Free Practice reveal, gate the Next button: scores at or above the
 * threshold proceed freely; lower scores trigger a short, explained countdown. */
function startPracticePenalty(score) {
  const btn = document.getElementById("next-question");
  const note = document.getElementById("practice-penalty");
  clearInterval(practiceTimer);

  if (score >= PRACTICE_PENALTY_THRESHOLD) {
    note.classList.add("hidden");
    btn.disabled = false; btn.textContent = "Next question";
    return;
  }

  let remaining = PRACTICE_PENALTY_SECONDS;
  const teamName = (TEAMS[state.selected_team] || TEAMS.mclaren).name;
  btn.disabled = true;
  note.classList.remove("hidden");
  const paint = () => {
    note.innerHTML =
      `🏴 <strong>${PRACTICE_PENALTY_SECONDS} SECONDS PENALTY TO ${escapeHtml(teamName.toUpperCase())}.</strong> ` +
      `You scored under ${PRACTICE_PENALTY_THRESHOLD.toLocaleString()} points, so the stewards ` +
      `hold you on the grid for ${remaining}s. This is necessary to discourage ` +
      `<em>quiz-scouting</em> — guessing wildly just to reveal and memorise answers — ` +
      `which would let players farm the bank and spoil the challenge for everyone. ` +
      `Take a breath and read the result.`;
    btn.textContent = `Next question in ${remaining}s`;
  };
  paint();
  practiceTimer = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(practiceTimer);
      note.classList.add("hidden");
      btn.disabled = false; btn.textContent = "Next question";
    } else {
      paint();
    }
  }, 1000);
}

/* ===================== CURVED RACE-LINE SLIDER ===================== *
 * A custom slider whose thumb is an F1 car riding an SVG curve. The viewBox
 * (1000×280) maps linearly onto the box via preserveAspectRatio="none", so a
 * path point (x,y) places the car at (x/1000, y/280) of the container. The
 * curve is monotonic in x, so the pointer's x-position resolves to a value. */
const CurveSlider = (() => {
  let min = 0, max = 100, value = 0, visible = true, onChange = null;
  let len = 0, samples = [], dragging = false, built = false;
  const $ = (id) => document.getElementById(id);
  const clamp01 = (t) => Math.min(1, Math.max(0, t));
  const fmt = (v) => Math.round(v).toLocaleString();

  function build() {
    const path = $("curve-track");
    len = path.getTotalLength();
    samples = [];
    const N = 240;
    for (let i = 0; i <= N; i++) {
      const p = path.getPointAtLength((len * i) / N);
      samples.push({ t: i / N, x: p.x });
    }
    built = true;
  }
  function ptAtT(t) { return $("curve-track").getPointAtLength(len * clamp01(t)); }

  function place() {
    if (!built) build();
    const t = clamp01((value - min) / ((max - min) || 1));
    const p = ptAtT(t), box = $("curve-slider").getBoundingClientRect();
    const car = $("car-thumb");
    car.style.left = (p.x / 1000) * 100 + "%";
    // Ride a touch above the race line rather than sitting on it (wheels just clear
    // the curve) — the lift is in screen px so it's unaffected by the tangent rotate.
    car.style.top = `calc(${(p.y / 280) * 100}% - 9px)`;
    // Rotate the car to the curve tangent (convert viewBox delta to screen delta).
    const a = ptAtT(t - 0.012), b = ptAtT(t + 0.012);
    const dx = (b.x - a.x) * (box.width / 1000), dy = (b.y - a.y) * (box.height / 280);
    const ang = Math.atan2(dy, dx) * 180 / Math.PI;
    car.style.transform = `translate(-50%,-50%) rotate(${ang}deg)`;
    car.setAttribute("aria-valuenow", Math.round(value));
    const fill = $("curve-fill");
    fill.style.strokeDasharray = len;
    fill.style.strokeDashoffset = len * (1 - t);
    $("curve-val").textContent = fmt(value);
  }
  function setValue(v, fire) {
    const prev = value;
    value = Math.min(max, Math.max(min, Math.round(v)));
    place();
    if (fire) {
      // A spinning-wheel click on each notch the guess crosses (rate-limited in
      // Sound.tick), so dragging the car along the line feels satisfyingly tactile.
      if (value !== prev) Sound.tick();
      if (onChange) onChange(value);
    }
  }
  function valueFromX(clientX) {
    const box = $("curve-slider").getBoundingClientRect();
    const vx = Math.min(1000, Math.max(0, ((clientX - box.left) / box.width) * 1000));
    let best = samples[0], bd = Infinity;
    for (const s of samples) { const d = Math.abs(s.x - vx); if (d < bd) { bd = d; best = s; } }
    return min + best.t * (max - min);
  }

  function init() {
    const slider = $("curve-slider"), car = $("car-thumb");
    const onMove = (e) => { if (!dragging) return; setValue(valueFromX(e.clientX), true); e.preventDefault(); };
    slider.addEventListener("pointerdown", (e) => {
      if (!visible) return;
      dragging = true; car.classList.add("dragging");
      try { slider.setPointerCapture(e.pointerId); } catch {}
      setValue(valueFromX(e.clientX), true);
    });
    slider.addEventListener("pointermove", onMove);
    const end = () => { dragging = false; car.classList.remove("dragging"); };
    slider.addEventListener("pointerup", end);
    slider.addEventListener("pointercancel", end);
    car.addEventListener("keydown", (e) => {
      const step = Math.max(1, Math.round((max - min) / 100));
      if (e.key === "ArrowRight" || e.key === "ArrowUp") { setValue(value + step, true); e.preventDefault(); }
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") { setValue(value - step, true); e.preventDefault(); }
    });
    window.addEventListener("resize", () => visible && place());
  }
  function configure(opts) {
    min = opts.min; max = opts.max; visible = opts.visible !== false; onChange = opts.onChange || null;
    $("curve-wrap").classList.toggle("no-curve", !visible);
    $("curve-min").textContent = fmt(min);
    $("curve-max").textContent = fmt(max);
    const car = $("car-thumb");
    car.setAttribute("aria-valuemin", Math.round(min));
    car.setAttribute("aria-valuemax", Math.round(max));
    setValue(opts.value, false);
  }
  return { init, configure, setValue: (v) => setValue(v, false), get: () => value };
})();

const KIND_HINT = {
  count: "Enter a whole number.",
  points: "Enter a championship points total.",
  year: "Enter a year (season).",
  percentage: "Enter a percentage from 0 to 100.",
};

function renderQuestion() {
  const q = quiz.questions[qPos];
  const kind = q.answer_kind || "count";
  document.getElementById("q-index").textContent = quiz.free ? practiceCount + 1 : qPos + 1;
  document.getElementById("q-text").textContent = q.question_text;
  document.getElementById("q-cat").textContent = (q.category || "").replace(/_/g, " ");
  document.getElementById("q-hint").textContent = KIND_HINT[kind] || "";

  const input = document.getElementById("q-input");
  // Year answers always use the curved slider (the scope is in the question anyway);
  // other modes fall back to whatever the mode config specifies.
  const useSlider = kind === "year" || MODES[currentMode].slider;
  input.step = "1"; input.min = q.slider_min;
  input.max = kind === "percentage" ? 100 : q.slider_max;
  input.value = useSlider ? q.slider_min : "";
  CurveSlider.configure({
    min: +q.slider_min, max: +q.slider_max, value: +q.slider_min, visible: useSlider,
    onChange: (v) => { input.value = v; },
  });
  input.oninput = () => { if (useSlider) CurveSlider.setValue(parseFloat(input.value) || q.slider_min); };

  // Advance the immersive progress bar to reflect questions completed. Free
  // Practice is endless, so the bar simply stays full rather than tracking an end.
  const fill = document.getElementById("game-progress-fill");
  if (fill) fill.style.width = quiz.free ? "100%" : `${(qPos / quiz.questions.length) * 100}%`;
  const gp = document.getElementById("game-points");
  if (gp) gp.textContent = `${sessionScore.toLocaleString()} pts`;

  const btn = document.getElementById("submit-guess");
  btn.disabled = false; btn.textContent = "Lock In Guess";
}

const submitBtn = document.getElementById("submit-guess");
submitBtn.addEventListener("click", submitGuess);

async function submitGuess() {
  const q = quiz.questions[qPos];
  const guess = parseFloat(document.getElementById("q-input").value) || 0;
  Sound.play("lockIn");   // confident confirm — the guess is committed
  submitBtn.disabled = true; submitBtn.textContent = "Scoring…";
  try {
    const res = await fetch(`${API}/quiz/verify`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ tracking_token: q.tracking_token, guess, anon_id: anonId() }),
    });
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    sessionScore += result.score;
    sessionCloseness += result.score / result.max_score;
    sessionResults.push(result.score / result.max_score);  // 0..1 per question
    if (result.insight) sessionInsights.push(result.insight.beat_percent);

    // Sector + achievement bookkeeping (all modes). Purple ≤10%, green ≤25%.
    const sector = sectorForResult(result);
    result._sector = sector;
    const a = ensureAch();
    if (result.score >= result.max_score) a.perfect = (a.perfect || 0) + 1;  // exact hit
    if (sector === "purple") a.purple = (a.purple || 0) + 1;
    if (sector === "purple" || sector === "green") a.green = (a.green || 0) + 1;
    if (quiz && quiz.free) {
      a.practice_questions = (a.practice_questions || 0) + 1;
      if (sector === "purple") achFlag("practice_purple");
    } else if (sector === "purple") {
      sessionPurpleCount += 1;
    }
    evaluateAchievements();
    revealScore(q, result);
  } catch (e) {
    toast("Couldn't score that — try again.");
    submitBtn.disabled = false; submitBtn.textContent = "Lock In Guess";
  }
}

/* Simplistic side-view F1 car, reused for the slider thumb (inline in index.html)
 * and the two reveal markers. Body + wings are the team's primary colour, the
 * visor its secondary; pass "car-ghost" for the translucent grey actual marker.
 * Keep this in sync with the inline #car-thumb SVG in index.html. */
const F1_CAR_SHAPES =
  '<rect class="car-body" x="8" y="9" width="19" height="4" rx="1.5"/>' +
  '<rect class="car-body" x="17" y="11" width="5" height="18" rx="1.5"/>' +
  '<rect class="car-body" x="104" y="39" width="22" height="4" rx="1.5"/>' +
  '<rect class="car-body" x="116" y="36" width="3" height="5"/>' +
  '<path class="car-body" d="M12 41 L15 28 C19 24 29 24 38 25 L52 25 ' +
    'C55 17 65 16 70 20 L76 23 L100 27 L123 36 C125 37 125 40 122 41 L40 43 C20 43 12 43 12 41 Z"/>' +
  '<path class="car-dark" d="M55 24 C57 17 66 16 70 21 L65 24 Z"/>' +
  '<rect class="car-dark" x="60" y="13" width="3" height="11" rx="1.5"/>' +
  '<circle class="car-cockpit" cx="63" cy="20" r="3.2"/>' +
  '<rect class="car-wing" x="64" y="18" width="3.4" height="3" rx="1"/>' +
  '<circle class="car-tyre" cx="34" cy="40" r="12"/><circle class="car-hub" cx="34" cy="40" r="5"/>' +
  '<circle class="car-tyre" cx="100" cy="40" r="12"/><circle class="car-hub" cx="100" cy="40" r="5"/>';
function f1CarSVG(extraClass = "") {
  return `<svg class="car-sprite ${extraClass}" viewBox="0 0 128 52" aria-hidden="true">${F1_CAR_SHAPES}</svg>`;
}
// Drop a full (locked-in guess) and a ghost (actual answer) car into the reveal markers.
document.getElementById("node-guess").insertAdjacentHTML("afterbegin", f1CarSVG());
document.getElementById("node-actual").insertAdjacentHTML("afterbegin", f1CarSVG("car-ghost"));

/* Odometer Score Reveal (Architecture §3.2) */
function revealScore(q, result) {
  hide("quiz-play"); show("quiz-reveal");
  const gp = document.getElementById("game-points");
  if (gp) gp.textContent = `${sessionScore.toLocaleString()} pts`;
  const lo = +q.slider_min, hi = +q.slider_max, span = (hi - lo) || 1;
  const clampPct = (v) => Math.min(100, Math.max(0, ((v - lo) / span) * 100));
  const guessNode = document.getElementById("node-guess");
  const actualNode = document.getElementById("node-actual");
  const actualText = document.getElementById("reveal-actual");

  // Reset: park both markers at the start, keep the answer hidden for now.
  guessNode.style.left = "0%";
  actualNode.style.left = "0%";
  document.getElementById("reveal-guess").textContent = result.guess;
  actualText.textContent = "?";
  actualText.classList.remove("revealed");
  actualText.classList.add("pending");
  document.getElementById("odometer").textContent = "0";
  document.getElementById("reveal-insight").classList.add("hidden");
  document.getElementById("timeline-fill").style.width = "0%";
  document.getElementById("reveal-verdict").hidden = true;

  // Drop the guess marker in promptly, then slide the answer bar across slowly
  // with an ease-in-out (speeds up, then eases into the answer — anticipation).
  // The number and score stay hidden until the bar reaches its destination.
  requestAnimationFrame(() => {
    guessNode.style.left = clampPct(result.guess) + "%";
    document.getElementById("timeline-fill").style.width = clampPct(result.guess) + "%";
    setTimeout(() => {
      Sound.play("riser");   // anticipation build, timed to land as the answer arrives
      slideToAnswer(actualNode, actualText, clampPct(result.actual), result);
    }, 500);
  });

  // Free Practice gates the Next button (anti-scouting penalty); every other mode
  // keeps the plain, always-available "Next".
  if (quiz && quiz.free) {
    startPracticePenalty(result.score);
  } else {
    const nextBtn = document.getElementById("next-question");
    nextBtn.disabled = false; nextBtn.textContent = "Next";
    document.getElementById("practice-penalty").classList.add("hidden");
  }
}

function slideToAnswer(node, textEl, targetPct, result) {
  const dur = 2600, start = performance.now();
  // Cubic ease-in-out: accelerate away from 0, decelerate into the target.
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  (function step(now) {
    const t = Math.min(1, (now - start) / dur);
    node.style.left = targetPct * easeInOut(t) + "%";
    if (t < 1) {
      requestAnimationFrame(step);
    } else {
      // Arrived: now reveal the answer and run the score odometer.
      textEl.textContent = result.actual;
      textEl.classList.remove("pending");
      textEl.classList.add("revealed");
      tickOdometer(result.score);
      renderRevealInsight(result);
      setVerdict(result);
      if (result._sector) {
        // Purple (≤10%) = a whole pack thunders by; green (≤25%) = a single car.
        Sound.play(result._sector === "purple" ? "purpleSector" : "greenSector");
      }
    }
  })(start);
}

/* Persistent sector verdict banner shown at the top of the reveal once the answer
 * lands, classifying the guess as a purple/green sector or a plain chequered flag. */
function setVerdict(result) {
  const el = document.getElementById("reveal-verdict");
  const txt = document.getElementById("reveal-verdict-text");
  if (!el || !txt) return;
  el.classList.remove("purple", "green", "neutral");
  let label;
  if (result._sector === "purple") { el.classList.add("purple"); label = "Purple sector"; }
  else if (result._sector === "green") { el.classList.add("green"); label = "Green sector"; }
  else { el.classList.add("neutral"); label = "Chequered flag"; }
  txt.textContent = label;
  el.hidden = false;
}

/* Classify a guess by percentage error, F1-timing style:
 *   purple  — within 10% (a "purple sector", the fastest)
 *   green   — within 25%
 *   null    — outside both. */
function sectorForResult(result) {
  const actual = Math.abs(result.actual);
  if (actual === 0) return result.guess === result.actual ? "purple" : null;
  const err = Math.abs(result.guess - result.actual) / actual;
  if (err <= 0.10) return "purple";
  if (err <= 0.25) return "green";
  return null;
}

/* Social proof under the score: how this guess compares to everyone who has
 * answered the same question. Server-computed (auth.question_insight), shown only
 * once a small sample exists so it never reads "you beat 0%". */
function renderRevealInsight(result) {
  const el = document.getElementById("reveal-insight");
  if (!el) return;
  const ins = result && result.insight;
  if (!ins || ins.players_answered < 5) { el.classList.add("hidden"); return; }
  el.innerHTML = `You beat <strong>${ins.beat_percent}%</strong> of players here · ` +
    `avg ${ins.average_score.toLocaleString()} pts`;
  el.classList.remove("hidden");
}

function tickOdometer(target) {
  const el = document.getElementById("odometer");
  const start = performance.now(), dur = 900;
  (function step(now) {
    const p = Math.min(1, (now - start) / dur);
    el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
  })(performance.now());
}

document.getElementById("next-question").addEventListener("click", () => {
  if (quiz && quiz.free) { nextPracticeQuestion(); return; }
  qPos++;
  if (qPos < quiz.questions.length) { hide("quiz-reveal"); show("quiz-play"); renderQuestion(); }
  else finishSession();
});

function finishSession() {
  hide("quiz-reveal"); show("quiz-summary");
  Sound.play("sessionComplete");   // chequered-flag fanfare
  const maxPossible = quiz.questions.length * 5000;
  document.getElementById("summary-score").textContent = sessionScore.toLocaleString();
  const acc = Math.round((sessionCloseness / quiz.questions.length) * 100);
  track("quiz_complete", { mode: currentMode, score: sessionScore, accuracy: acc });
  document.getElementById("accuracy-row").textContent = `Accuracy: ${acc}% · ${sessionScore.toLocaleString()} / ${maxPossible.toLocaleString()}`;
  // Spoiler-free result grid (same squares the Share button copies).
  const gridEl = document.getElementById("summary-grid");
  if (gridEl) gridEl.textContent = sessionResults.map(closenessSquare).join("");

  // Mark the period as played (cap), then update guest-first local stats.
  const cfg = MODES[currentMode];
  if (cfg.capKey) localStorage.setItem(playedKey(currentMode), cfg.capKey());

  state.lifetime_points += sessionScore;
  state.games_played += 1;
  state._closeness_sum += sessionCloseness;
  state._q_count += quiz.questions.length;
  state.average_closeness = +(state._closeness_sum / state._q_count).toFixed(3);

  if (currentMode === "daily") {
    const today = utcDate();
    const daysSince = state.last_played_date
      ? Math.round((new Date(today) - new Date(state.last_played_date)) / 864e5) : null;
    if (daysSince === 0) { /* replay same day — keep streak */ }
    else if (daysSince === 1) state.daily_streak += 1;
    else if (daysSince === 2 && state.streak_freeze_available) {
      // Streak freeze: forgive a single missed day, once per run.
      state.daily_streak += 1;
      state.streak_freeze_available = false;
      ensureAch().comeback_freezes = (ensureAch().comeback_freezes || 0) + 1;
      achFlag("comeback");
    } else {
      state.daily_streak = 1;            // run reset
      state.streak_freeze_available = true;  // re-arm the freeze for the new run
    }
    state.last_played_date = today;
  }

  // Session-level achievement stats (competitive modes only).
  const a = ensureAch();
  a.sessions = (a.sessions || 0) + 1;
  a.questions = (a.questions || 0) + quiz.questions.length;
  if (currentMode === "daily") a.daily_sessions = (a.daily_sessions || 0) + 1;
  if (currentMode === "race_week") a.race_sessions = (a.race_sessions || 0) + 1;
  a.best_session = Math.max(a.best_session || 0, sessionScore);
  a.max_purple_in_session = Math.max(a.max_purple_in_session || 0, sessionPurpleCount);
  a.max_streak = Math.max(a.max_streak || 0, state.daily_streak);
  const h = new Date().getUTCHours();
  if (h >= 22 || h < 5) achFlag("night_owl");
  if (h >= 5 && h < 8) achFlag("early_bird");
  if (localStorage.getItem("played_daily") === utcDate() &&
      localStorage.getItem("played_race_week") === utcDate()) achFlag("double_header");

  saveState(state);
  evaluateAchievements();

  // Loud streak callout + session social proof on the summary, plus refresh the
  // home banner and (signed-in) the authoritative server streak.
  renderSummaryEngagement();
  renderStreakBanner();
  if (isSignedIn()) refreshMe().then(renderProfile);
}

/* The summary's streak flame and "you beat X% of players" line — the two hooks
 * that turn one finished run into a reason to come back and to share. */
function renderSummaryEngagement() {
  const streakEl = document.getElementById("summary-streak");
  if (streakEl) {
    if (currentMode === "daily" && state.daily_streak > 0) {
      streakEl.innerHTML = `<span class="flame">🔥</span> <strong>${state.daily_streak}-day streak!</strong>` +
        ` <span class="muted">Come back tomorrow to keep it alive.</span>`;
      streakEl.classList.remove("hidden");
    } else {
      streakEl.classList.add("hidden");
    }
  }
  const insEl = document.getElementById("summary-insight");
  if (insEl) {
    if (sessionInsights.length) {
      const avg = Math.round(sessionInsights.reduce((a, b) => a + b, 0) / sessionInsights.length);
      insEl.innerHTML = `📊 You beat <strong>${avg}%</strong> of players on average today.`;
      insEl.classList.remove("hidden");
    } else {
      insEl.classList.add("hidden");
    }
  }
}

document.getElementById("summary-back").addEventListener("click", () => navigate("home"));

/* Map a per-question closeness (0..1) to a coloured square — the spoiler-free
 * Wordle-style result. No numbers that reveal the answer, just how close. */
function closenessSquare(c) {
  if (c >= 0.999) return "🟦";  // bullseye
  if (c >= 0.80) return "🟩";   // very close
  if (c >= 0.50) return "🟨";   // in the ballpark
  if (c >= 0.20) return "🟧";   // miles off
  return "⬛";                   // way off
}

/* A stable daily puzzle number: whole UTC days since the game's launch epoch.
 * Gives every shared result the same "#NNN" for the day, like Wordle. */
function dailyNumber() {
  const epoch = Date.UTC(2026, 0, 1);             // 2026-01-01
  return Math.floor((Date.now() - epoch) / 864e5) + 1;
}

/* A clickable deep link back into the exact mode played, so a shared result is a
 * one-tap invite to the same challenge rather than just the homepage. Parsed on
 * boot by handleDeepLink(). */
function shareLink() {
  const mode = currentMode === "race_week" ? "race"
             : currentMode === "free_practice" ? "practice" : "daily";
  return `${location.origin}/?play=${mode}`;
}

function buildShareText() {
  const grid = sessionResults.map(closenessSquare).join("");
  const max = quiz.questions.length * 5000;
  const tag = currentMode === "daily" ? `Daily #${dailyNumber()}`
            : currentMode === "race_week" ? `Race Challenge #${dailyNumber()}`
            : "Free Practice";
  // Optional brag line from the per-question percentiles (social proof in shares
  // is a strong pull — "beat 72% of players" invites a comeback).
  let brag = "";
  if (sessionInsights.length) {
    const avg = Math.round(sessionInsights.reduce((a, b) => a + b, 0) / sessionInsights.length);
    brag = `\nBeat ${avg}% of players`;
  }
  // Spoiler-free: shares the closeness pattern and total, never the answers.
  return `🏁 GridMaster — ${tag}\n${grid}\n${sessionScore.toLocaleString()} / ${max.toLocaleString()} pts${brag}` +
    `\nCan you beat me? ${shareLink()}`;
}

/* Try the native share sheet, fall back to clipboard, then to inline text. */
async function shareOrCopy(text, copiedMsg) {
  if (navigator.share) {
    try { await navigator.share({ title: "GridMaster", text }); return; } catch { /* cancelled */ }
  }
  try {
    await navigator.clipboard.writeText(text);
    document.getElementById("share-status").textContent = copiedMsg;
  } catch {
    document.getElementById("share-status").textContent = text;
  }
}

document.getElementById("share-result").addEventListener("click", async () => {
  track("share", { mode: currentMode });
  const a = ensureAch(); a.shares = (a.shares || 0) + 1; saveState(state);
  evaluateAchievements();
  await shareOrCopy(buildShareText(), "Result copied — paste it to share!");
});

/* Direct "I dare you" invite — same deep link, framed as a head-to-head challenge
 * rather than a result post. The two have different psychology, so we offer both. */
document.getElementById("challenge-friend").addEventListener("click", async () => {
  track("challenge_friend", { mode: currentMode });
  const a = ensureAch(); a.challenges = (a.challenges || 0) + 1; saveState(state);
  evaluateAchievements();
  const tag = currentMode === "race_week" ? "Daily Race Challenge"
            : currentMode === "daily" ? "Daily Challenge" : "GridMaster run";
  const text = `🏁 I just scored ${sessionScore.toLocaleString()} on today's GridMaster ${tag}. ` +
    `Think you can beat me?\n${shareLink()}`;
  await shareOrCopy(text, "Challenge copied — send it to a friend!");
});

/* ===================== ARCADE OVER/UNDER ===================== */
let arcade = null, locked = false;

async function loadArcade() {
  locked = false;
  document.getElementById("arcade-result").textContent = "";
  const a = document.getElementById("arcade-a"), b = document.getElementById("arcade-b");
  [a, b].forEach((c) => c.classList.remove("correct", "wrong"));
  a.querySelector(".val").textContent = "?"; b.querySelector(".val").textContent = "?";
  document.getElementById("arcade-best").textContent = localStorage.getItem("arcade_best") || 0;
  try {
    const res = await fetch(`${API}/arcade/pair`);
    arcade = await res.json();
  } catch { toast("Couldn't load matchup."); return; }
  document.getElementById("arcade-metric").textContent = `Who has more ${arcade.metric_label}?`;
  a.querySelector(".name").textContent = arcade.entity_a.full_name;
  b.querySelector(".name").textContent = arcade.entity_b.full_name;
}

function pick(which) {
  if (locked || !arcade) return;
  locked = true;
  track("arcade_play");
  const a = arcade.entity_a, b = arcade.entity_b;
  document.querySelector("#arcade-a .val").textContent = a.value;
  document.querySelector("#arcade-b .val").textContent = b.value;
  const pickedHigher = which === "a" ? a.value >= b.value : b.value >= a.value;
  const card = document.getElementById("arcade-" + which);
  card.classList.add(pickedHigher ? "correct" : "wrong");
  Sound.play(pickedHigher ? "correct" : "wrong");
  let streak = +localStorage.getItem("arcade_streak") || 0;
  streak = pickedHigher ? streak + 1 : 0;
  localStorage.setItem("arcade_streak", streak);
  let best = +localStorage.getItem("arcade_best") || 0;
  if (streak > best) { best = streak; localStorage.setItem("arcade_best", best); }
  document.getElementById("arcade-streak").textContent = streak;
  document.getElementById("arcade-best").textContent = best;
  achFlag("arcade_played");
  evaluateAchievements();
  document.getElementById("arcade-result").textContent =
    pickedHigher ? "Correct! Loading next…" : "Streak reset. Loading next…";
  setTimeout(loadArcade, 1400);
}
document.getElementById("arcade-a").addEventListener("click", () => pick("a"));
document.getElementById("arcade-b").addEventListener("click", () => pick("b"));

/* ===================== PROFILE ===================== */
/* Server-derived stats for the signed-in user (null when a guest). Points and
 * accuracy shown in the profile come from here when present — the authoritative,
 * server-scored totals (Architecture §2.2) — falling back to local stats for a
 * guest. Streak / achievements / team stay local (cosmetic). */
let serverStats = null;

/* The current daily streak to display: the authoritative server value when signed
 * in, otherwise the guest-local one. */
function currentStreak() {
  return (isSignedIn() && serverStats && serverStats.daily_streak != null)
    ? serverStats.daily_streak : state.daily_streak;
}

/* Home-page streak hook. Loud and loss-averse: losing a long streak is the moment
 * players churn, so we remind them it's on the line every time they land on home. */
function renderStreakBanner() {
  const el = document.getElementById("streak-banner");
  if (!el) return;
  const n = currentStreak();
  if (!n || n < 1) { el.classList.add("hidden"); return; }
  const playedToday = isCapped("daily");
  el.innerHTML = playedToday
    ? `<span class="flame">🔥</span> <span><strong>${n}-day streak</strong> secured — see you tomorrow!</span>`
    : `<span class="flame">🔥</span> <span><strong>${n}-day streak</strong> — play today's Daily to keep it alive</span>`;
  el.classList.remove("hidden");
}

function renderProfile() {
  const signedIn = isSignedIn();
  document.getElementById("p-team").textContent = (TEAMS[state.selected_team] || TEAMS.mclaren).name;

  // Competitive numbers: server values when signed in, local otherwise.
  const points = signedIn && serverStats ? serverStats.lifetime_points : state.lifetime_points;
  document.getElementById("p-points").textContent = points.toLocaleString();
  document.getElementById("p-games").textContent =
    signedIn && serverStats ? serverStats.questions_answered : state.games_played;
  document.getElementById("p-accuracy").textContent =
    signedIn && serverStats
      ? (serverStats.questions_answered ? `${Math.round(serverStats.average_accuracy * 100)}%` : "—")
      : (state._q_count ? `${Math.round(state.average_closeness * 100)}%` : "—");

  // Streak: server-derived when signed in (authoritative across devices),
  // local otherwise.
  document.getElementById("p-streak").textContent =
    signedIn && serverStats && serverStats.daily_streak != null
      ? serverStats.daily_streak : state.daily_streak;
  const achHave = (state.unlocked_achievements || []).filter(
    (id) => ACHIEVEMENTS.some((a) => a.id === id)).length;
  document.getElementById("p-achievements").textContent = `${achHave} / ${ACHIEVEMENTS.length} unlocked`;
  renderAchievements();

  document.getElementById("guest-badge").textContent = signedIn ? "member" : "guest";
  document.getElementById("account-guest").classList.toggle("hidden", signedIn);
  document.getElementById("account-member").classList.toggle("hidden", !signedIn);
  if (signedIn) {
    document.getElementById("account-username").textContent =
      (serverStats && serverStats.username) || localStorage.getItem("f1statguesser_username") || "you";
  }
  loadLeaderboard();
  loadTeamLeaderboard();
}

async function refreshMe() {
  if (!isSignedIn()) { serverStats = null; return; }
  try {
    const res = await fetch(`${API}/auth/me`, { headers: authHeaders() });
    if (res.status === 401) { setAuthToken(null); serverStats = null; return; }
    if (!res.ok) return;
    const me = await res.json();
    serverStats = { ...me.stats, username: me.username };
    localStorage.setItem("f1statguesser_username", me.username);
  } catch { /* offline — keep whatever we have */ }
}

/* Leaderboard window shared by the global board and the Constructors'
 * Championship: 'all' | 'weekly' | 'daily'. Daily/weekly reset, so there's
 * always a fresh race to win — the reason to come back tomorrow. */
let lbPeriod = "all";

async function loadLeaderboard() {
  const list = document.getElementById("leaderboard-list");
  if (!list) return;
  try {
    const res = await fetch(`${API}/leaderboard?period=${lbPeriod}`);
    const entries = (await res.json()).entries || [];
    const myName = localStorage.getItem("f1statguesser_username");
    list.innerHTML = entries.length
      ? entries.map((e) => `<li class="${e.username === myName ? "me" : ""}">
            <span class="lb-rank">${e.rank}</span>
            <span class="lb-name">${escapeHtml(e.username)} <em class="lb-team">${(TEAMS[e.selected_team] || {}).name || ""}</em></span>
            <span class="lb-points">${e.lifetime_points.toLocaleString()}</span>
          </li>`).join("")
      : `<li class="muted">No scores ${lbPeriod === "all" ? "yet" : "in this window"} — be the first to post one.</li>`;
  } catch {
    list.innerHTML = `<li class="muted">Leaderboard unavailable right now.</li>`;
  }
}

async function loadTeamLeaderboard() {
  const list = document.getElementById("team-leaderboard-list");
  if (!list) return;
  try {
    const res = await fetch(`${API}/leaderboard/teams?period=${lbPeriod}`);
    const entries = (await res.json()).entries || [];
    const mine = state.selected_team;
    list.innerHTML = entries.length
      ? entries.map((e) => {
          const t = TEAMS[e.team] || { name: e.team, primary: "#888" };
          return `<li class="${e.team === mine ? "me" : ""}">
            <span class="lb-rank">${e.rank}</span>
            <span class="ctc-swatch" style="background:${t.primary}"></span>
            <span class="lb-name">${escapeHtml(t.name)}
              <em class="lb-team">${e.members} fan${e.members === 1 ? "" : "s"} · ${e.avg_per_member.toLocaleString()} avg</em></span>
            <span class="lb-points">${e.points.toLocaleString()}</span>
          </li>`;
        }).join("")
      : `<li class="muted">No team has scored ${lbPeriod === "all" ? "yet" : "in this window"}.</li>`;
  } catch {
    list.innerHTML = `<li class="muted">Standings unavailable right now.</li>`;
  }
}

/* Home-page Timing Tower (Variation B) — the Constructors' Championship pulled onto
 * the landing page. Same /leaderboard/teams data as the profile board, but its own
 * period state (towerPeriod) so the two boards browse independently. */
let towerPeriod = "all";
function fmtCompact(n) {
  return n >= 10000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : Number(n).toLocaleString();
}
async function loadHomeTower() {
  const list = document.getElementById("home-tower-list");
  if (!list) return;
  try {
    const res = await fetch(`${API}/leaderboard/teams?period=${towerPeriod}`);
    const entries = (await res.json()).entries || [];
    const mine = state.selected_team;
    list.innerHTML = entries.length
      ? entries.slice(0, 8).map((e) => {
          const t = TEAMS[e.team] || { name: e.team, primary: "#888" };
          const you = e.team === mine ? ` <span class="tt-you">\u00b7 YOUR TEAM</span>` : "";
          return `<li class="${e.team === mine ? "me" : ""}">
            <span class="tt-rank">${e.rank}</span>
            <span class="tt-bar" style="background:${t.primary}"></span>
            <span class="tt-name">${escapeHtml(t.name)}${you}</span>
            <span class="tt-pts">${fmtCompact(e.points)}</span>
          </li>`;
        }).join("")
      : `<li class="muted">No team has scored ${towerPeriod === "all" ? "yet" : "in this window"} \u2014 pledge a team and post the first.</li>`;
  } catch {
    list.innerHTML = `<li class="muted">Standings unavailable right now.</li>`;
  }
}
/* Footer data-provenance line: when the F1 data behind the questions was last
 * refreshed (from /data/status). Reassures players the stats are current and
 * dated. Silent on failure — it's a non-essential, informational line. */
async function loadDataStatus() {
  const el = document.getElementById("data-status");
  if (!el) return;
  try {
    const res = await fetch(`${API}/data/status`);
    if (!res.ok) return;
    const s = await res.json();
    if (!s.refreshed_at) { el.textContent = ""; return; }
    const d = new Date(s.refreshed_at + "T00:00:00Z");
    const when = isNaN(d) ? s.refreshed_at
      : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    el.textContent = `· Data refreshed ${when}`;
  } catch { /* informational only — leave the footer as-is on error */ }
}

function setTowerPeriod(period) {
  towerPeriod = period;
  document.querySelectorAll(".tt-period-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.towerPeriod === period));
  loadHomeTower();
}

function setLeaderboardPeriod(period) {
  lbPeriod = period;
  document.querySelectorAll(".lb-period-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.period === period));
  loadLeaderboard();
  loadTeamLeaderboard();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

document.getElementById("reset-btn").addEventListener("click", () => {
  localStorage.removeItem(STORAGE_KEY);
  ["arcade_streak", "arcade_best", "played_daily", "played_race_week"].forEach((k) => localStorage.removeItem(k));
  state = defaultState(); applyTeam(state.selected_team); saveState(state);
  toast("Local progress reset.");
});

/* ===================== ACCOUNTS ===================== */
const Auth = (() => {
  let mode = "register";  // 'register' | 'login'

  function open() {
    track("signup_open");
    setMode("register");
    document.getElementById("auth-error").classList.add("hidden");
    document.getElementById("auth-form").reset();
    show("auth-overlay");
    document.getElementById("auth-username").focus();
  }
  function close() { hide("auth-overlay"); }

  function setMode(m) {
    mode = m;
    const register = m === "register";
    document.getElementById("auth-title").textContent = register ? "Create your account" : "Sign in";
    document.getElementById("auth-submit").textContent = register ? "Create account" : "Sign in";
    document.getElementById("auth-switch-text").textContent =
      register ? "Already have an account?" : "Need an account?";
    document.getElementById("auth-switch-btn").textContent =
      register ? "Sign in instead" : "Create one instead";
    document.getElementById("auth-password").setAttribute(
      "autocomplete", register ? "new-password" : "current-password");
    // Email is only collected at sign-up — hide it on the login form.
    document.getElementById("auth-email-row").classList.toggle("hidden", !register);
  }

  function showError(msg) {
    const el = document.getElementById("auth-error");
    el.textContent = msg;
    el.classList.remove("hidden");
  }

  async function submit(e) {
    e.preventDefault();
    const username = document.getElementById("auth-username").value.trim();
    const password = document.getElementById("auth-password").value;
    // Email is optional and register-only; omit it entirely when blank or on login.
    const email = document.getElementById("auth-email").value.trim();
    const btn = document.getElementById("auth-submit");
    btn.disabled = true;
    try {
      const payload = { username, password, anon_id: anonId(), selected_team: state.selected_team };
      if (mode === "register" && email) payload.email = email;
      const res = await fetch(`${API}/auth/${mode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        // Pledge the locally-chosen team on sign-up so the new account joins that
        // faction in the Constructors' Championship from its first point.
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        showError(detail.detail || "Something went wrong. Please try again.");
        return;
      }
      const body = await res.json();
      track(mode === "register" ? "signup_success" : "login_success");
      setAuthToken(body.token);
      localStorage.setItem("f1statguesser_username", body.username);
      serverStats = { ...body.stats, username: body.username };
      // Adopt the team the server has on file for this account (it persists the
      // faction across devices, unlike the local-only guest choice).
      if (body.selected_team) applyTeam(body.selected_team);
      state.is_guest = false;
      recordTeamUse(body.selected_team);  // the pledged team counts toward collection badges
      saveState(state);
      evaluateAchievements();  // "Contract Signed" + any points-based unlocks from the merge
      close();
      toast(body.claimed_events
        ? `Welcome, ${body.username}! ${body.claimed_events} guest result${body.claimed_events === 1 ? "" : "s"} saved to your account.`
        : `Welcome, ${body.username}!`);
      renderProfile();
    } catch {
      showError("Network error — is the server awake?");
    } finally {
      btn.disabled = false;
    }
  }

  async function logout() {
    try { await fetch(`${API}/auth/logout`, { method: "POST", headers: authHeaders() }); }
    catch { /* best effort */ }
    setAuthToken(null);
    localStorage.removeItem("f1statguesser_username");
    serverStats = null;
    state.is_guest = true; saveState(state);
    toast("Logged out.");
    renderProfile();
  }

  function init() {
    document.getElementById("open-auth-btn").addEventListener("click", open);
    document.getElementById("auth-close").addEventListener("click", close);
    document.getElementById("logout-btn").addEventListener("click", logout);
    document.getElementById("auth-form").addEventListener("submit", submit);
    document.getElementById("auth-switch-btn").addEventListener("click", () =>
      setMode(mode === "register" ? "login" : "register"));
    document.getElementById("auth-overlay").addEventListener("click", (e) => {
      if (e.target.id === "auth-overlay") close();
    });
  }
  return { init };
})();

/* Persist the chosen team to the account so it counts in the Constructors'
 * Championship and follows the player across devices. No-op for guests (their
 * choice stays local until they sign in, which pledges it then). */
async function syncTeam(team) {
  if (!isSignedIn()) return;
  try {
    await fetch(`${API}/profile/team`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ selected_team: team }),
    });
    loadTeamLeaderboard();  // reflect the switch in the standings
  } catch { /* best effort — local choice already applied */ }
}

/* ===================== TEAM PICKER + FIRST-RUN ONBOARDING ===================== *
 * The same modal serves two jobs: the header's "change my colours" picker, and a
 * one-time welcome prompt that asks a brand-new player to pick a side. Both show
 * how many players have pledged to each team and how the Constructors'
 * Championship is going (from /api/v1/teams/overview), so the choice feels social
 * and consequential rather than purely cosmetic. */
const ONBOARD_KEY = "f1sg_onboarded";
const TeamPicker = (() => {
  let onboarding = false;       // true while the forced first-run prompt is open
  let overview = null;          // cached {teams, total_players} from the server

  // Per-team {members, points, rank}, keyed by team id, for the card footers and
  // the intro line. Best-effort: if the fetch fails we just render bare cards.
  async function loadOverview() {
    try {
      const res = await fetch(`${API}/teams/overview`);
      if (!res.ok) throw new Error();
      overview = await res.json();
    } catch { overview = null; }
    return overview;
  }

  function statFor(key) {
    if (!overview) return null;
    return overview.teams.find((t) => t.team === key) || { members: 0, points: 0, rank: null };
  }

  // The welcome blurb: total players who've picked a side + the current
  // Constructors' Championship leader, so a newcomer knows what they're joining.
  function introHtml() {
    if (!overview || !overview.teams.length) {
      return "Pick the constructor you'll race for. Your points feed your team's " +
             "tally in the Constructors' Championship.";
    }
    const total = overview.total_players;
    const leader = overview.teams.find((t) => t.points > 0);
    const players = total === 1 ? "1 player has" : `${total.toLocaleString()} players have`;
    const lead = leader
      ? ` <strong>${escapeHtml((TEAMS[leader.team] || {}).name || leader.team)}</strong> ` +
        `lead the Constructors' Championship with ${leader.points.toLocaleString()} pts.`
      : " No team has scored yet — be the first to put points on the board.";
    return `${players} already picked a side.${lead} Every question you answer adds your ` +
           `score to your team's tally — choose yours:`;
  }

  function cardFooter(key) {
    const s = statFor(key);
    if (!s) return "";
    const fans = s.members === 1 ? "1 fan" : `${s.members.toLocaleString()} fans`;
    return `<span class="team-card-stats">${fans} · ${s.points.toLocaleString()} pts</span>`;
  }

  function render() {
    const grid = document.getElementById("team-picker-grid");
    grid.innerHTML = Object.entries(TEAMS).map(([key, t]) => {
      const sel = key === state.selected_team;
      return `<button class="team-card${sel ? " selected" : ""}" data-team="${key}"
                      aria-pressed="${sel}" style="${sel ? `border-color:${t.primary}` : ""}">
                <span class="team-card-swatch"
                      style="background:${t.primary}; box-shadow:inset 0 -8px 0 ${t.secondary}"></span>
                <span class="team-card-name">${t.name}<span class="team-card-check">✓</span></span>
                ${cardFooter(key)}
              </button>`;
    }).join("");
    grid.querySelectorAll(".team-card").forEach((card) => {
      card.addEventListener("click", () => pickTeam(card.dataset.team));
    });
  }

  function pickTeam(team) {
    track("team_select", { team, onboarding });
    applyTeam(team);
    recordTeamUse(team);  // for the constructor-collection achievements
    saveState(state);
    evaluateAchievements();
    syncTeam(team);       // persist server-side when signed in
    if (onboarding) {
      localStorage.setItem(ONBOARD_KEY, "1");
      const name = (TEAMS[team] || {}).name || team;
      toast(`🏎️ You're racing for ${name}! Your points now feed its championship.`);
    }
    onboarding = false;
    close();
  }

  async function open(opts = {}) {
    onboarding = !!opts.onboarding;
    const title = document.getElementById("team-panel-title");
    const intro = document.getElementById("team-panel-intro");
    const closeBtn = document.getElementById("team-panel-close");
    title.textContent = onboarding ? "Pick Your Team" : "Choose Your Team";
    // During the forced first-run prompt the player must choose, so hide the
    // close affordance (backdrop/Escape are also ignored — see close()).
    closeBtn.classList.toggle("hidden", onboarding);
    intro.classList.add("hidden");
    show("team-overlay");
    render();                       // bare cards immediately…
    await loadOverview();           // …then enrich with live counts + standings
    render();
    if (onboarding || overview) {
      intro.innerHTML = introHtml();
      intro.classList.toggle("hidden", !onboarding && !overview);
    }
  }

  // Ignore close requests mid-onboarding so a brand-new player can't skip the
  // choice without picking. The normal picker closes freely.
  function close() {
    if (onboarding) return;
    hide("team-overlay");
  }

  // Show the welcome prompt once, to brand-new guests only. Returning guests (who
  // already have local progress) and signed-in players are silently marked done.
  function maybeOnboard() {
    if (localStorage.getItem(ONBOARD_KEY)) return;
    if (isSignedIn() || state.games_played > 0) {
      localStorage.setItem(ONBOARD_KEY, "1");
      return;
    }
    open({ onboarding: true });
  }

  function init() {
    document.getElementById("team-select-btn").addEventListener("click", () => open());
    document.getElementById("team-panel-close").addEventListener("click", close);
    document.getElementById("team-overlay").addEventListener("click", (e) => {
      if (e.target.id === "team-overlay") close();
    });
  }

  return { init, open, maybeOnboard };
})();

/* ===================== ACHIEVEMENTS ===================== *
 * A data-driven catalog spanning Rookie → Champion difficulty. Each entry has a
 * pure check(s) predicate run against an achievement snapshot (achSnapshot), so
 * adding a new badge is just one row. Unlock state is local/cosmetic (like the
 * streak, Architecture §2.2) — it never touches the server-verified leaderboard.
 * Counters that back the checks live in state.ach (see ensureAch + the updates in
 * submitGuess / finishSession / arcade / share / team-select / auth). */
const ACH_TIERS = { rookie: 1, midfield: 2, podium: 3, champion: 4 };
const ACH_TIER_LABEL = { rookie: "Rookie", midfield: "Midfield", podium: "Podium", champion: "Champion" };

const ACHIEVEMENTS = [
  // ── Rookie ──────────────────────────────────────────────────────────────
  { id: "lights_out",     icon: "🚦", tier: "rookie", name: "Lights Out",        desc: "Answer your very first question.",           check: (s) => s.questions >= 1 || s.practiceQuestions >= 1 },
  { id: "formation_lap",  icon: "🏁", tier: "rookie", name: "Formation Lap",     desc: "Complete your first full session.",          check: (s) => s.sessions >= 1 },
  { id: "green_sector",   icon: "🟢", tier: "rookie", name: "Green Sector",      desc: "Land within 25% of an answer.",              check: (s) => s.green >= 1 },
  { id: "in_the_points",  icon: "➕", tier: "rookie", name: "In the Points",     desc: "Answer 10 questions.",                       check: (s) => s.questions >= 10 },
  { id: "warm_up",        icon: "🛞", tier: "rookie", name: "Warm-Up Lap",       desc: "Try a Free Practice question.",              check: (s) => s.practiceQuestions >= 1 },
  { id: "insert_coin",    icon: "🕹️", tier: "rookie", name: "Insert Coin",       desc: "Play a round of Arcade.",                    check: (s) => s.flags.arcade_played === true },
  { id: "on_the_board",   icon: "📈", tier: "rookie", name: "On the Board",      desc: "Bank 5,000 lifetime points.",                check: (s) => s.points >= 5000 },
  { id: "pick_a_side",    icon: "🎽", tier: "rookie", name: "Pick a Side",       desc: "Pledge to a constructor.",                   check: (s) => s.teamsUsed >= 1 },
  { id: "spread_word",    icon: "📣", tier: "rookie", name: "Spread the Word",   desc: "Share a result.",                            check: (s) => s.shares >= 1 },
  { id: "contract_signed",icon: "✍️", tier: "rookie", name: "Contract Signed",   desc: "Create an account.",                         check: (s) => s.signedIn === true },

  // ── Midfield ────────────────────────────────────────────────────────────
  { id: "purple_sector",  icon: "🟣", tier: "midfield", name: "Purple Sector",   desc: "Land within 10% of an answer.",              check: (s) => s.purple >= 1 },
  { id: "hat_trick",      icon: "🎩", tier: "midfield", name: "Hat-Trick",       desc: "Reach a 3-day streak.",                      check: (s) => s.maxStreak >= 3 },
  { id: "the_ton",        icon: "💯", tier: "midfield", name: "The Ton",         desc: "Answer 100 questions.",                      check: (s) => s.questions >= 100 },
  { id: "fastest_lap",    icon: "⏱️", tier: "midfield", name: "Fastest Lap",     desc: "Score 22,000+ in a single session.",         check: (s) => s.bestSession >= 22000 },
  { id: "podium_points",  icon: "🏆", tier: "midfield", name: "Podium Finish",   desc: "Bank 25,000 lifetime points.",               check: (s) => s.points >= 25000 },
  { id: "double_header",  icon: "📅", tier: "midfield", name: "Double Header",   desc: "Finish the Daily and Race challenge in one day.", check: (s) => s.flags.double_header === true },
  { id: "box_box",        icon: "🔧", tier: "midfield", name: "Box, Box!",       desc: "Complete 10 sessions.",                      check: (s) => s.sessions >= 10 },
  { id: "tyre_whisperer", icon: "🛞", tier: "midfield", name: "Tyre Whisperer",  desc: "Answer 50 Free Practice questions.",         check: (s) => s.practiceQuestions >= 50 },
  { id: "arcade_ace",     icon: "⚡", tier: "midfield", name: "Arcade Ace",      desc: "Reach an Arcade streak of 10.",              check: (s) => s.arcadeBest >= 10 },
  { id: "night_owl",      icon: "🦉", tier: "midfield", name: "Night Owl",       desc: "Finish a session after midnight (UTC).",     check: (s) => s.flags.night_owl === true },
  { id: "box_at_dawn",    icon: "🐦", tier: "midfield", name: "Box at Dawn",     desc: "Finish a session at sunrise (UTC).",         check: (s) => s.flags.early_bird === true },
  { id: "teammate_battle",icon: "🤝", tier: "midfield", name: "Teammate Battle", desc: "Run two different constructors.",            check: (s) => s.teamsUsed >= 2 },
  { id: "double_points",  icon: "💰", tier: "midfield", name: "Double Points",   desc: "Bank 50,000 lifetime points.",               check: (s) => s.points >= 50000 },
  { id: "sharp_practice", icon: "🎯", tier: "midfield", name: "Sharp in Practice", desc: "Score a purple sector in Free Practice.",  check: (s) => s.flags.practice_purple === true },
  { id: "race_ritual",    icon: "📆", tier: "midfield", name: "Race Week Ritual", desc: "Reach a 5-day streak.",                     check: (s) => s.maxStreak >= 5 },

  // ── Podium ──────────────────────────────────────────────────────────────
  { id: "bullseye",       icon: "🎯", tier: "podium", name: "Bullseye",          desc: "Nail an answer exactly.",                    check: (s) => s.perfect >= 1 },
  { id: "qualifying_ace", icon: "🥇", tier: "podium", name: "Qualifying Ace",    desc: "Hit 3 purple sectors in one session.",       check: (s) => s.maxPurpleInSession >= 3 },
  { id: "purple_reign",   icon: "🟣", tier: "podium", name: "Purple Reign",      desc: "Score 25 purple sectors.",                   check: (s) => s.purple >= 25 },
  { id: "consistency",    icon: "📆", tier: "podium", name: "Consistency is King", desc: "Reach a 7-day streak.",                    check: (s) => s.maxStreak >= 7 },
  { id: "maximum_attack", icon: "🚀", tier: "podium", name: "Maximum Attack",    desc: "Score 27,000+ in a single session.",         check: (s) => s.bestSession >= 27000 },
  { id: "race_veteran",   icon: "🎖️", tier: "podium", name: "Race Veteran",      desc: "Answer 500 questions.",                      check: (s) => s.questions >= 500 },
  { id: "centurion",      icon: "💪", tier: "podium", name: "Centurion",         desc: "Bank 100,000 lifetime points.",              check: (s) => s.points >= 100000 },
  { id: "comeback_kid",   icon: "🛡️", tier: "podium", name: "Comeback Kid",      desc: "Save a streak with a freeze.",               check: (s) => s.flags.comeback === true },
  { id: "gauntlet",       icon: "🤜", tier: "podium", name: "Throw Down the Gauntlet", desc: "Send 5 challenges to friends.",        check: (s) => s.challenges >= 5 },
  { id: "paddock_regular",icon: "🌍", tier: "podium", name: "Paddock Regular",   desc: "Run 5 different constructors.",              check: (s) => s.teamsUsed >= 5 },
  { id: "iron_driver",    icon: "🦾", tier: "podium", name: "Iron Driver",       desc: "Complete 50 sessions.",                      check: (s) => s.sessions >= 50 },
  { id: "arcade_legend",  icon: "👾", tier: "podium", name: "Arcade Legend",     desc: "Reach an Arcade streak of 25.",              check: (s) => s.arcadeBest >= 25 },
  { id: "daily_devotee",  icon: "☀️", tier: "podium", name: "Daily Devotee",     desc: "Complete 25 Daily challenges.",              check: (s) => s.dailySessions >= 25 },
  { id: "race_specialist",icon: "🏎️", tier: "podium", name: "Race Specialist",   desc: "Complete 25 Race challenges.",               check: (s) => s.raceSessions >= 25 },
  { id: "sharpshooter",   icon: "🔫", tier: "podium", name: "Sharpshooter",      desc: "Nail 10 exact answers.",                     check: (s) => s.perfect >= 10 },

  // ── Champion ────────────────────────────────────────────────────────────
  { id: "grand_slam",     icon: "🏆", tier: "champion", name: "Grand Slam",      desc: "All six sectors purple in one session.",     check: (s) => s.maxPurpleInSession >= 6 },
  { id: "perfect_lap",    icon: "💎", tier: "champion", name: "The Perfect Lap", desc: "A flawless 30,000-point session.",           check: (s) => s.bestSession >= 30000 },
  { id: "unbeatable",     icon: "🔥", tier: "champion", name: "Unbeatable",      desc: "Reach a 30-day streak.",                     check: (s) => s.maxStreak >= 30 },
  { id: "world_champion", icon: "👑", tier: "champion", name: "World Champion",  desc: "Bank 500,000 lifetime points.",              check: (s) => s.points >= 500000 },
  { id: "purple_machine", icon: "🟪", tier: "champion", name: "Purple Machine",  desc: "Score 100 purple sectors.",                  check: (s) => s.purple >= 100 },
  { id: "lights_to_flag", icon: "🏃", tier: "champion", name: "Lights to Flag",  desc: "Answer 2,000 questions.",                    check: (s) => s.questions >= 2000 },
  { id: "arcade_immortal",icon: "🌟", tier: "champion", name: "Arcade Immortal", desc: "Reach an Arcade streak of 50.",              check: (s) => s.arcadeBest >= 50 },
  { id: "full_grid",      icon: "🏟️", tier: "champion", name: "The Full Grid",   desc: "Run all eleven constructors.",               check: (s) => s.teamsUsed >= 11 },
  { id: "dead_eye",       icon: "🦅", tier: "champion", name: "Dead-Eye",        desc: "Nail 50 exact answers.",                     check: (s) => s.perfect >= 50 },
  { id: "hall_of_fame",   icon: "🏛️", tier: "champion", name: "Hall of Fame",    desc: "Unlock 40 other achievements.",              check: (s) => s.unlocked >= 40 },
];

/* A read-only snapshot of everything the achievement checks can inspect. */
function achSnapshot() {
  const a = ensureAch();
  return {
    points: (isSignedIn() && serverStats ? serverStats.lifetime_points : state.lifetime_points) || 0,
    questions: a.questions || 0,
    practiceQuestions: a.practice_questions || 0,
    sessions: a.sessions || 0,
    dailySessions: a.daily_sessions || 0,
    raceSessions: a.race_sessions || 0,
    perfect: a.perfect || 0,
    purple: a.purple || 0,
    green: a.green || 0,
    bestSession: a.best_session || 0,
    maxPurpleInSession: a.max_purple_in_session || 0,
    maxStreak: Math.max(a.max_streak || 0, currentStreak() || 0),
    shares: a.shares || 0,
    challenges: a.challenges || 0,
    teamsUsed: (a.teams_used || []).length,
    arcadeBest: +localStorage.getItem("arcade_best") || 0,
    signedIn: isSignedIn(),
    flags: a.flags || {},
    unlocked: (state.unlocked_achievements || []).length,
  };
}

/* Evaluate the catalog, unlock anything newly earned, celebrate it, and repaint.
 * Safe to call as often as we like — already-unlocked badges are skipped. */
function evaluateAchievements() {
  const s = achSnapshot();
  const newly = [];
  for (const ach of ACHIEVEMENTS) {
    if (state.unlocked_achievements.includes(ach.id)) continue;
    let ok = false;
    try { ok = !!ach.check(s); } catch { ok = false; }
    if (ok) { state.unlocked_achievements.push(ach.id); newly.push(ach); }
  }
  if (newly.length) {
    saveState(state);
    Sound.play("achievement");   // celebratory chime for the unlock(s)
    newly.forEach((ach, i) => setTimeout(() => toast(`${ach.icon} Achievement unlocked: ${ach.name}`), i * 1300));
    track("achievement", { ids: newly.map((ach) => ach.id) });
    renderAchievements();
  }
  return newly;
}

/* Render the profile achievement grid (unlocked in colour, locked dimmed). */
let achFilter = "all";
function renderAchievements() {
  const grid = document.getElementById("ach-grid");
  if (!grid) return;
  const unlocked = new Set(state.unlocked_achievements || []);
  const total = ACHIEVEMENTS.length;
  const have = ACHIEVEMENTS.filter((a) => unlocked.has(a.id)).length;
  const countEl = document.getElementById("ach-count");
  if (countEl) countEl.textContent = `${have} / ${total}`;

  const tierRank = (a) => ACH_TIERS[a.tier] || 0;
  const list = ACHIEVEMENTS
    .filter((a) => achFilter === "all" || (achFilter === "unlocked") === unlocked.has(a.id))
    .sort((a, b) => tierRank(a) - tierRank(b));

  grid.innerHTML = list.length ? list.map((a) => {
    const got = unlocked.has(a.id);
    return `<div class="ach-card tier-${a.tier} ${got ? "got" : "locked"}" title="${escapeHtml(a.desc)}">
        <span class="ach-icon">${got ? a.icon : "🔒"}</span>
        <span class="ach-body">
          <span class="ach-name">${escapeHtml(a.name)}</span>
          <span class="ach-desc">${escapeHtml(a.desc)}</span>
        </span>
        <span class="ach-tier">${ACH_TIER_LABEL[a.tier]}</span>
      </div>`;
  }).join("") : `<p class="muted">Nothing here yet.</p>`;
}

function setAchFilter(filter) {
  achFilter = filter;
  document.querySelectorAll(".ach-filter-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.filter === filter));
  renderAchievements();
}

/* ===================== DEV DATA CHECK (proofreading) ===================== */
/* Renders the full question bank WITH verified answers in a filterable, sortable
 * table so the underlying stats can be eyeballed against the record books.
 * Backed by /api/v1/dev/questions — a development tool (disable: F1_DEV_TOOLS=0). */
const DataCheck = (() => {
  let rows = null, sortKey = "category", sortAsc = true;
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const fmtAnswer = (v) => (v % 1 === 0 ? (+v).toLocaleString() : (+v).toLocaleString(undefined, { minimumFractionDigits: 1 }));

  function render() {
    const needle = document.getElementById("data-search").value.trim().toLowerCase();
    const mode = document.getElementById("data-mode-filter").value;
    const flaggedOnly = document.getElementById("data-flagged-only").checked;
    const view = rows.filter((r) =>
      (!mode || r.game_mode === mode) &&
      (!flaggedOnly || r.flagged) &&
      (!needle || `${r.question_string} ${r.category}`.toLowerCase().includes(needle)));
    view.sort((a, b) => {
      const va = a[sortKey] ?? "", vb = b[sortKey] ?? "";
      const cmp = (typeof va === "number" && typeof vb === "number")
        ? va - vb : String(va).localeCompare(String(vb));
      return sortAsc ? cmp : -cmp;
    });
    const flagged = rows.filter((r) => r.flagged).length;
    document.getElementById("data-count").textContent =
      `${view.length} / ${rows.length}${flagged ? ` · ${flagged} 🚩` : ""}`;
    document.getElementById("data-rows").innerHTML = view.map((r) => `
      <tr class="${r.flagged ? "dt-flagged" : ""}">
        <td class="dt-q">${esc(r.question_string)}</td>
        <td class="num dt-a">${fmtAnswer(r.verified_answer)}</td>
        <td>${esc(r.answer_kind)}</td>
        <td>${esc((r.category || "").replace(/_/g, " "))}</td>
        <td>${esc(r.game_mode.replace("_", "-"))}</td>
        <td class="num">${r.era_year ?? "—"}</td>
        <td><button class="dt-flag-btn ${r.flagged ? "on" : ""}"
              data-q="${esc(r.question_string)}"
              title="${r.flagged ? "Flagged — click to clear" : "Flag for review"}"
              aria-pressed="${r.flagged}">🚩</button></td>
      </tr>`).join("");
    document.querySelectorAll(".data-table th").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === sortKey);
      th.classList.toggle("desc", th.dataset.sort === sortKey && !sortAsc);
    });
  }

  /* Toggle a flag server-side, then reflect it locally without a full refetch. */
  async function toggleFlag(qs) {
    const row = rows.find((r) => r.question_string === qs);
    if (!row) return;
    const next = !row.flagged;
    try {
      const res = await fetch(`${API}/dev/flag`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_string: qs, flagged: next }),
      });
      if (!res.ok) throw new Error(await res.text());
      row.flagged = next;
      track("dev_flag", { flagged: next });
      render();
    } catch {
      toast("Could not save the flag (dev tools disabled?).");
    }
  }

  async function open() {
    show("data-overlay");
    if (rows) { render(); return; }
    try {
      const res = await fetch(`${API}/dev/questions`);
      if (!res.ok) throw new Error(await res.text());
      rows = (await res.json()).questions;
      render();
    } catch {
      hide("data-overlay");
      toast("Data check is unavailable (disabled on this server).");
    }
  }
  const close = () => hide("data-overlay");

  function init() {
    document.getElementById("data-check").addEventListener("click", open);
    document.getElementById("data-check-reveal").addEventListener("click", open);
    document.getElementById("data-close").addEventListener("click", close);
    document.getElementById("data-overlay").addEventListener("click", (e) => {
      if (e.target.id === "data-overlay") close();
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    document.getElementById("data-search").addEventListener("input", () => rows && render());
    document.getElementById("data-mode-filter").addEventListener("change", () => rows && render());
    document.getElementById("data-flagged-only").addEventListener("change", () => rows && render());
    // Delegated so the per-row 🚩 buttons keep working after every re-render.
    document.getElementById("data-rows").addEventListener("click", (e) => {
      const btn = e.target.closest(".dt-flag-btn");
      if (btn) toggleFlag(btn.dataset.q);
    });
    document.querySelectorAll(".data-table th").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        sortAsc = sortKey === key ? !sortAsc : true;
        sortKey = key;
        if (rows) render();
      });
    });
  }
  return { init };
})();

/* A shared "?play=<mode>" link opens straight into that challenge — the payoff of
 * the deep links the share/challenge buttons emit. Unknown values are ignored. */
function handleDeepLink() {
  const play = new URLSearchParams(location.search).get("play");
  if (!play) return false;
  const map = {
    daily: ["quiz", "daily"], race: ["quiz", "race_week"],
    practice: ["quiz", "free_practice"], arcade: ["arcade", null],
  };
  const dest = map[play];
  if (!dest) return false;
  track("deeplink", { play });
  navigate(dest[0], dest[1]);
  return true;
}

/* ===================== SOUND TOGGLE ===================== *
 * A single, always-visible header control mutes/unmutes every effect. The choice
 * persists (Sound.setOn writes localStorage), so it sticks across visits. */
const SoundToggle = (() => {
  function paint() {
    const btn = document.getElementById("sound-toggle");
    const icon = document.getElementById("sound-icon");
    if (!btn || !icon) return;
    const on = Sound.isOn();
    icon.textContent = on ? "🔊" : "🔇";
    btn.setAttribute("aria-pressed", String(on));
    btn.classList.toggle("muted", !on);
    btn.title = on ? "Sound on — click to mute" : "Sound off — click to enable";
  }
  function init() {
    const btn = document.getElementById("sound-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const on = Sound.toggle();
      paint();
      track("sound_toggle", { on });
      if (on) Sound.play("uiClick");   // confirm it's back (silent when muting)
    });
    paint();
  }
  return { init };
})();

/* ---- Theme toggle (dark default / light opt-in) ----
 * Mirrors SoundToggle: flips data-theme="light" on <html>, persists to localStorage,
 * so it sticks across visits. The team colour still comes from applyTeam(); only the
 * neutral surfaces + the legible-on-white --color-ink swap (handled in CSS). */
const THEME_KEY = "gm_theme";
const ThemeToggle = (() => {
  function current() { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; }
  function paint(theme) {
    const btn = document.getElementById("theme-toggle");
    const icon = document.getElementById("theme-icon");
    if (!btn || !icon) return;
    const light = theme === "light";
    icon.textContent = light ? "\u2600" : "\u263e";   // ☀ / ☾
    btn.setAttribute("aria-pressed", String(light));
    btn.title = light ? "Light mode \u2014 click for dark" : "Dark mode \u2014 click for light";
  }
  function apply(theme) {
    const root = document.documentElement;
    if (theme === "light") root.setAttribute("data-theme", "light");
    else root.removeAttribute("data-theme");
    paint(theme);
    const t = TEAMS[state.selected_team] || TEAMS.mclaren;
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "light" ? "#ffffff" : t.primary);
  }
  function init() {
    apply(current());
    const btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", () => {
      const next = current() === "light" ? "dark" : "light";
      localStorage.setItem(THEME_KEY, next);
      apply(next);
      track("theme_toggle", { theme: next });
      Sound.play("uiClick");
    });
  }
  return { init, apply, current };
})();

/* ---- Boot ---- */
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
applyTeam(state.selected_team);
saveState(state);
renderQuizIntro();
renderStreakBanner();
CurveSlider.init();
DataCheck.init();
TeamPicker.init();
SoundToggle.init();
ThemeToggle.init();
Auth.init();
document.querySelectorAll(".lb-period-tab").forEach((t) =>
  t.addEventListener("click", () => setLeaderboardPeriod(t.dataset.period)));
document.querySelectorAll(".tt-period-tab").forEach((t) =>
  t.addEventListener("click", () => setTowerPeriod(t.dataset.towerPeriod)));
loadHomeTower();
loadDataStatus();
document.querySelectorAll(".ach-filter-tab").forEach((t) =>
  t.addEventListener("click", () => setAchFilter(t.dataset.filter)));
renderAchievements();
evaluateAchievements();  // catch anything already earned (e.g. from a prior visit)
track("app_open", { signed_in: isSignedIn() });  // open the analytics session
// If a session token is present, pull the authoritative server stats, then
// repaint the profile so it shows the signed-in totals.
refreshMe().then(() => { renderProfile(); renderStreakBanner(); evaluateAchievements(); });
tickCountdown(); setInterval(tickCountdown, 1000);
renderRaceWeek(); setInterval(renderRaceWeek, 60000); // refresh past/next state each minute
// A shared "?play=" link drops the player straight into a challenge; don't
// interrupt that with the onboarding prompt. Otherwise, first-run guests are
// asked to pick a team (with live headcounts + championship standings).
if (!handleDeepLink()) TeamPicker.maybeOnboard();
