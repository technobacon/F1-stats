/* ScapeMaster — prototype frontend.
 * Guest-first: all progress lives in localStorage.
 * Scoring is NEVER computed here — guesses go to the server, which returns the score. */

const API = "/api/v1";
// Build identifier, surfaced in the footer as a real "this is shipped software"
// signal. Bump alongside the asset version when cutting a release.
const APP_VERSION = "2026.07.05";
// Distinct prefix so running ScapeMaster and its F1 sibling on the same origin
// (localhost dev) never cross-contaminates saved progress or auth tokens.
const STORAGE_KEY = "scapemaster_user_state";
const TOKEN_KEY = "scapemaster_auth_token";
const ANON_KEY = "scapemaster_anon_id";

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
const ANALYTICS_SESSION_KEY = "sm_session_id";
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
    title: "Daily Slayer Task",
    desc: "Six questions spanning all of Gielinor — items, monsters, quests and the XP table. The closer your guess, the more of the 5,000 XP per question you keep.",
    capKey: () => utcDate(), capLabel: "today's Slayer Task", slider: true,
  },
  free_practice: {
    title: "Training Grounds",
    desc: "Unlimited random questions to sharpen your instincts. Your score is shown here " +
      "but never saved or ranked — it's pure training. To keep it fair, scoring under " +
      "1,000 XP on a question puts you on a 10-second cooldown before the next one " +
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
  is_guest: true, selected_god: "saradomin",
  lifetime_points: 0, games_played: 0, average_closeness: 0,
  daily_streak: 0, last_played_date: null, unlocked_achievements: [],
  played_dates: [],                 // UTC dates with a Daily run, for the heatmap
  last_daily_percentile: null,      // {pct, date} — echoed on the home garage
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
  if (!Array.isArray(state.ach.gods_used)) state.ach.gods_used = [];
  return state.ach;
}
function achFlag(name) { ensureAch().flags[name] = true; }
function recordGodUse(god) {
  const a = ensureAch();
  if (god && !a.gods_used.includes(god)) { a.gods_used.push(god); }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...defaultState(), ...JSON.parse(raw).user_state } : defaultState();
  } catch { return defaultState(); }
}
function saveState(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ user_state: s }));
  document.getElementById("hud-points").textContent = `${s.lifetime_points.toLocaleString()} xp`;
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

/* ---- The six god factions ----
 * Each god has a main (primary) colour and a secondary accent. Main buttons render
 * solid in the primary with a thin secondary stripe along the bottom edge.
 * `ink` is a legibility-safe variant of the colour used for TEXT on the dark UI:
 * dark primaries (Zamorak crimson, Zaros void-purple) are lightened so they don't
 * vanish against the background. Fills/borders keep the true `primary`. */
const TEAMS = {
  saradomin: { name: "Saradomin", primary: "#2f6bd8", secondary: "#f2c14b", text: "#fff", ink: "#6f9ff0", inkLight: "#2455b8" },
  zamorak:   { name: "Zamorak",   primary: "#c22f2f", secondary: "#1c1010", text: "#fff", ink: "#f06060", inkLight: "#a32020" },
  guthix:    { name: "Guthix",    primary: "#3f9b45", secondary: "#173a1a", text: "#fff", ink: "#66cc6d", inkLight: "#2c7a32" },
  armadyl:   { name: "Armadyl",   primary: "#6fc2e0", secondary: "#e9eef2", text: "#062733", ink: "#8fd4ee", inkLight: "#2f7f9e" },
  bandos:    { name: "Bandos",    primary: "#8a7a3b", secondary: "#3c2f16", text: "#fff", ink: "#c2ae62", inkLight: "#6d5f2b" },
  zaros:     { name: "Zaros",     primary: "#7a3bd6", secondary: "#171026", text: "#fff", ink: "#a97ae8", inkLight: "#5e28ab" },
};

/* ---- Theming ---- */
function applyTeam(god) {
  const t = TEAMS[god] || TEAMS.saradomin;
  const root = document.documentElement;
  root.setAttribute("data-god", god);
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
  state.selected_god = god;
}

/* ---- Toast ---- */
let toastTimer;
function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

/* ===================== COUNTDOWN HUD ===================== */
/* The next Daily Slayer Task drops at 00:00 UTC, same moment for everyone.
 * The HUD counts down to it; the home task board mirrors the timer. */
function nextTaskTime() {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
}

function tickCountdown() {
  const now = new Date();
  const when = nextTaskTime();
  const diff = Math.max(0, when - now);
  const h = Math.floor(diff / 36e5), m = Math.floor(diff % 36e5 / 6e4), sec = Math.floor(diff % 6e4 / 1e3);
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("countdown-label").textContent = "NEW TASK IN";
  document.getElementById("countdown-timer").textContent = `${pad(h)}:${pad(m)}:${pad(sec)}`;

  // Mirror the live countdown onto the home-page task board.
  const rwTimer = document.getElementById("rw-next-timer");
  if (rwTimer) {
    rwTimer.textContent = `${pad(h)}:${pad(m)}:${pad(sec)}`;
    document.getElementById("rw-next-name").textContent = `Slayer Task #${dailyNumber() + 1}`;
  }
}

/* ===================== TASK BOARD PANEL ===================== */
/* The home-page "task board": today's Slayer Task status plus the evergreen
 * modes, styled like a noticeboard. Uses the shared rw-* row classes. */
function renderRaceWeek() {
  const list = document.getElementById("rw-list");
  if (!list) return;
  document.getElementById("rw-title").textContent = `Daily Slayer Task #${dailyNumber()}`;
  const done = isCapped("daily");
  const duelBest = +localStorage.getItem("arcade_best") || 0;
  const rows = [
    {
      cls: done ? "is-past" : "is-next", icon: done ? "✓" : "!",
      title: "Daily Slayer Task", kindClass: "race",
      kind: done ? "Task complete — new one at 00:00 UTC" : "Assignment waiting",
      right: done ? "Done" : "6 questions",
    },
    {
      cls: "", icon: "∞", title: "Training Grounds", kindClass: "",
      kind: "Unlimited practice, never recorded", right: "Open",
    },
    {
      cls: "", icon: "⚔", title: "Duel Arena", kindClass: "sprint",
      kind: duelBest ? `Best streak: ${duelBest}` : "Which is greater?", right: "Open",
    },
  ];
  list.innerHTML = rows.map((r) => `<li class="rw-row ${r.cls}">
      <span class="rw-day"><span class="dow">&nbsp;</span><span class="dnum">${r.icon}</span></span>
      <span class="rw-meta"><span class="rw-session">${r.title}</span>
        <span class="rw-kind ${r.kindClass}">${r.kind}</span></span>
      <span class="rw-time">${r.right}</span>
    </li>`).join("");
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
    if (match) t.setAttribute("aria-current", "page");
    else t.removeAttribute("aria-current");
  });
  if (view === "quiz") { currentMode = mode || currentMode; renderQuizIntro(); }
  if (view === "arcade") loadArcade();
  if (view === "profile") renderProfile();
  if (view === "home") { renderStreakBanner(); loadHomeTower(); renderGarage(); }
  window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
}
document.querySelectorAll("[data-view]").forEach((el) => {
  el.addEventListener("click", (e) => {
    e.preventDefault();
    navigate(el.dataset.view, el.dataset.mode);
    // Footer links deep-link into the About page: after navigating, bring the
    // requested section into view (honouring reduced motion).
    const anchor = el.dataset.scroll;
    if (anchor && anchor !== "about-top") {
      const target = document.getElementById(anchor);
      if (target) target.scrollIntoView({
        behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
    }
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

document.getElementById("start-quiz").addEventListener("click", () => {
  // First-timers see the scoring-curve explainer; the run starts when they dismiss it.
  if (!ScoringIntro.maybeShow(() => startQuiz())) startQuiz();
});
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
    status.textContent = "Couldn't load the quiz. Tap to retry.";
    toast("Couldn't reach the server. Check your connection and try again.");
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
    toast("Couldn't reach the server. Check your connection and try again.");
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
  const godName = (TEAMS[state.selected_god] || TEAMS.saradomin).name;
  btn.disabled = true;
  note.classList.remove("hidden");
  const paint = () => {
    note.innerHTML =
      `${Icons.svg("flag")} <strong>${PRACTICE_PENALTY_SECONDS}-SECOND COOLDOWN — ${escapeHtml(godName.toUpperCase())} IS UNIMPRESSED.</strong> ` +
      `You scored under ${PRACTICE_PENALTY_THRESHOLD.toLocaleString()} XP, so the tutors ` +
      `hold you at the Training Grounds for ${remaining}s. This is necessary to discourage ` +
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
  let logScale = false, coinColors = false;
  let len = 0, samples = [], dragging = false, built = false;
  const $ = (id) => document.getElementById(id);
  const clamp01 = (t) => Math.min(1, Math.max(0, t));

  /* Format a value for the readout/endpoints: OSRS-style k/m/b suffixes once
   * the numbers get big, exact numerals below that. */
  function fmt(v) {
    const n = Math.round(v);
    const abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(abs >= 1e10 ? 1 : 2).replace(/\.?0+$/, "") + "b";
    if (abs >= 1e6) return (n / 1e6).toFixed(abs >= 1e7 ? 1 : 2).replace(/\.?0+$/, "") + "m";
    if (abs >= 100000) return Math.round(n / 1000).toLocaleString() + "k";
    return n.toLocaleString();
  }

  /* value <-> track parameter. coins/xp questions span several orders of
   * magnitude, so their track is logarithmic: equal drag distance = equal
   * multiplicative step. Linear kinds keep the plain mapping. */
  function tForValue(v) {
    if (!logScale) return clamp01((v - min) / ((max - min) || 1));
    const lo = Math.log(Math.max(min, 1)), hi = Math.log(Math.max(max, 2));
    return clamp01((Math.log(Math.max(v, 1)) - lo) / ((hi - lo) || 1));
  }
  function valueForT(t) {
    if (!logScale) return min + t * (max - min);
    const lo = Math.log(Math.max(min, 1)), hi = Math.log(Math.max(max, 2));
    const raw = Math.exp(lo + clamp01(t) * (hi - lo));
    // Snap to 2 significant figures so the readout lands on human numbers
    // (1.2m, 340k) instead of 1,183,447.
    const mag = Math.pow(10, Math.max(0, Math.floor(Math.log10(raw)) - 1));
    return Math.round(raw / mag) * mag;
  }

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

  /* The in-game coin-stack text colours: yellow under 100k, white to 10m,
   * green beyond. A tiny easter egg on coin questions. */
  function paintCoinColor() {
    const el = $("curve-val");
    el.classList.remove("coins-yellow", "coins-white", "coins-green");
    if (!coinColors) return;
    el.classList.add(value >= 1e7 ? "coins-green" : value >= 1e5 ? "coins-white" : "coins-yellow");
  }

  function place() {
    if (!built) build();
    const t = tForValue(value);
    const p = ptAtT(t), box = $("curve-slider").getBoundingClientRect();
    const car = $("car-thumb");
    car.style.left = (p.x / 1000) * 100 + "%";
    // Ride a touch above the line rather than sitting on it — the lift is in
    // screen px so it's unaffected by the tangent rotate.
    car.style.top = `calc(${(p.y / 280) * 100}% - 9px)`;
    // Rotate the marker to the curve tangent (viewBox delta -> screen delta).
    const a = ptAtT(t - 0.012), b = ptAtT(t + 0.012);
    const dx = (b.x - a.x) * (box.width / 1000), dy = (b.y - a.y) * (box.height / 280);
    const ang = Math.atan2(dy, dx) * 180 / Math.PI;
    car.style.transform = `translate(-50%,-50%) rotate(${ang}deg)`;
    car.setAttribute("aria-valuenow", Math.round(value));
    const fill = $("curve-fill");
    fill.style.strokeDasharray = len;
    fill.style.strokeDashoffset = len * (1 - t);
    $("curve-val").textContent = fmt(value);
    paintCoinColor();
  }
  function setValue(v, fire) {
    const prev = value;
    value = Math.min(max, Math.max(min, Math.round(v)));
    place();
    if (fire) {
      // A notch click on each value the guess crosses (rate-limited in
      // Sound.tick), so dragging the marker feels satisfyingly tactile.
      if (value !== prev) Sound.tick();
      if (onChange) onChange(value);
    }
  }
  function valueFromX(clientX) {
    const box = $("curve-slider").getBoundingClientRect();
    const vx = Math.min(1000, Math.max(0, ((clientX - box.left) / box.width) * 1000));
    let best = samples[0], bd = Infinity;
    for (const smp of samples) { const d = Math.abs(smp.x - vx); if (d < bd) { bd = d; best = smp; } }
    return valueForT(best.t);
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
      // Keyboard: linear kinds step 1/100th of the range; log kinds step ~5%
      // multiplicatively so arrows stay useful across the decades.
      if (e.key === "ArrowRight" || e.key === "ArrowUp") {
        setValue(logScale ? Math.max(value + 1, value * 1.05) : value + Math.max(1, Math.round((max - min) / 100)), true);
        e.preventDefault();
      }
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
        setValue(logScale ? Math.min(value - 1, value / 1.05) : value - Math.max(1, Math.round((max - min) / 100)), true);
        e.preventDefault();
      }
    });
    window.addEventListener("resize", () => visible && place());
  }
  function configure(opts) {
    min = opts.min; max = opts.max; visible = opts.visible !== false; onChange = opts.onChange || null;
    logScale = !!opts.log; coinColors = !!opts.coins;
    $("curve-wrap").classList.toggle("no-curve", !visible);
    $("curve-min").textContent = fmt(min);
    $("curve-max").textContent = fmt(max);
    const car = $("car-thumb");
    car.setAttribute("aria-valuemin", Math.round(min));
    car.setAttribute("aria-valuemax", Math.round(max));
    setValue(opts.value, false);
  }
  return { init, configure, setValue: (v) => setValue(v, false), get: () => value, fmt };
})();

const KIND_HINT = {
  count: "Enter a whole number.",
  level: "Enter a level.",
  xp: "Enter an experience amount — the slider steps in k/m.",
  coins: "Enter a coin value — the slider steps in k/m/b, like a proper coin stack.",
  year: "Enter a year.",
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
    log: kind === "coins" || kind === "xp",   // huge ranges ride a log track
    coins: kind === "coins",                  // coin-stack colour easter egg
    onChange: (v) => { input.value = v; },
  });
  input.oninput = () => { if (useSlider) CurveSlider.setValue(parseFloat(input.value) || q.slider_min); };

  // Advance the immersive progress bar to reflect questions completed. Free
  // Practice is endless, so the bar simply stays full rather than tracking an end.
  const fill = document.getElementById("game-progress-fill");
  if (fill) fill.style.width = quiz.free ? "100%" : `${(qPos / quiz.questions.length) * 100}%`;
  const gp = document.getElementById("game-points");
  if (gp) gp.textContent = `${sessionScore.toLocaleString()} xp`;

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

/* Stylized scimitar, reused for the slider thumb (inline in index.html) and the
 * two reveal markers. Blade takes the god's primary colour, the guard its
 * secondary; pass "car-ghost" for the translucent grey actual marker. Original
 * art — no game sprites. Keep in sync with the inline #car-thumb SVG. */
const MARKER_SHAPES =
  '<circle class="car-hub" cx="12" cy="42" r="5.5"/>' +
  '<rect class="car-dark" x="13" y="38" width="18" height="8" rx="3"/>' +
  '<rect class="car-wing" x="30" y="30" width="7" height="21" rx="2"/>' +
  '<path class="car-body" d="M38 44 C64 41 94 31 116 13 ' +
    'C121 9 124 15 120 20 C101 36 68 46 40 48 C37 48 36 45 38 44 Z"/>' +
  '<path class="car-cockpit" d="M44 42 C68 39 94 30 112 17 L114 19 C96 32 70 41 45 44 Z"/>';
function markerSVG(extraClass = "") {
  return `<svg class="car-sprite ${extraClass}" viewBox="0 0 128 52" aria-hidden="true">${MARKER_SHAPES}</svg>`;
}
// Drop a full (locked-in guess) and a ghost (actual answer) marker into the reveal.
document.getElementById("node-guess").insertAdjacentHTML("afterbegin", markerSVG());
document.getElementById("node-actual").insertAdjacentHTML("afterbegin", markerSVG("car-ghost"));

/* Odometer Score Reveal (Architecture §3.2) */
function revealScore(q, result) {
  hide("quiz-play"); show("quiz-reveal");
  const gp = document.getElementById("game-points");
  if (gp) gp.textContent = `${sessionScore.toLocaleString()} xp`;
  const lo = +q.slider_min, hi = +q.slider_max, span = (hi - lo) || 1;
  const clampPct = (v) => Math.min(100, Math.max(0, ((v - lo) / span) * 100));
  const guessNode = document.getElementById("node-guess");
  const actualNode = document.getElementById("node-actual");
  const actualText = document.getElementById("reveal-actual");

  // Reset: park both markers at the start, keep the answer hidden for now.
  guessNode.style.left = "0%";
  actualNode.style.left = "0%";
  document.getElementById("reveal-guess").textContent = CurveSlider.fmt(result.guess);
  actualText.textContent = "?";
  actualText.classList.remove("revealed");
  actualText.classList.add("pending");
  document.getElementById("odometer").textContent = "0";
  document.getElementById("reveal-chatline")?.classList.add("hidden");
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
  // Arrived: reveal the answer, run the score odometer, fire the sector cue.
  const arrive = () => {
    node.style.left = targetPct + "%";
    textEl.textContent = CurveSlider.fmt(result.actual);
    textEl.classList.remove("pending");
    textEl.classList.add("revealed");
    tickOdometer(result.score);
    renderRevealInsight(result);
    setVerdict(result);
    setChatline(result);
    if (result._sector) {
      // A purple (≤10%) gets the jackpot jingle; a green (≤25%) a bright chime.
      Sound.play(result._sector === "purple" ? "purpleSector" : "greenSector");
    }
  };
  // Reduced motion: skip the long slide and land on the answer straight away.
  if (prefersReducedMotion()) { arrive(); return; }
  const dur = 2600, start = performance.now();
  // Cubic ease-in-out: accelerate away from 0, decelerate into the target.
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  (function step(now) {
    const t = Math.min(1, (now - start) / dur);
    node.style.left = targetPct * easeInOut(t) + "%";
    if (t < 1) requestAnimationFrame(step);
    else arrive();
  })(start);
}

/* Persistent loot verdict banner shown at the top of the reveal once the answer
 * lands, classifying the guess as a purple, a green, or a plain finish. */
function setVerdict(result) {
  const el = document.getElementById("reveal-verdict");
  const txt = document.getElementById("reveal-verdict-text");
  if (!el || !txt) return;
  el.classList.remove("purple", "green", "neutral");
  let label;
  if (result._sector === "purple") { el.classList.add("purple"); label = "A purple!"; }
  else if (result._sector === "green") { el.classList.add("green"); label = "Solid loot"; }
  else { el.classList.add("neutral"); label = "Task done"; }
  txt.textContent = label;
  el.hidden = false;
}

/* Chatbox flavour line under the score — the game-message voice of Gielinor. */
function setChatline(result) {
  const el = document.getElementById("reveal-chatline");
  if (!el) return;
  let msg;
  if (result.score >= result.max_score) {
    msg = "Congratulations — an exact hit! Your Guessing level is now immeasurable.";
  } else if (result._sector === "purple") {
    msg = "The chest creaks open… it's a purple! Within 10% of the true value.";
  } else if (result._sector === "green") {
    msg = "You feel a strange wisdom wash over you. Within 25% — solid loot.";
  } else if (result.score >= 1500) {
    msg = "Close, but the RNG giveth not. The wiki gnomes note your effort.";
  } else {
    msg = "You have been awarded some XP anyway. Gielinor is merciful.";
  }
  el.textContent = msg;
  el.classList.remove("hidden");
}

/* Classify a guess by percentage error, loot-tier style:
 *   purple  — within 10% (raid-chest jackpot)
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

/* True when motion should be minimized — either the OS "reduce motion" setting is
 * on, or the player has flipped the in-app override in Settings. Honoured
 * everywhere: the CSS clamps transitions/animations (see the reduced-motion media
 * query) and the JS count-ups + answer-slide snap straight to their end state.
 * The override also drives a `data-reduce-motion` attribute on <html> so the CSS
 * can react to the in-app choice, not just the system one. */
const MOTION_KEY = "sm_reduce_motion";
function motionOverride() { return localStorage.getItem(MOTION_KEY) === "1"; }
function prefersReducedMotion() {
  return motionOverride() ||
    !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}
function applyMotionPref() {
  document.documentElement.toggleAttribute("data-reduce-motion", motionOverride());
}

/* Count an element up to `target` with an ease-out, or snap to it under reduced
 * motion. Shared by the per-question score odometer and the session total. */
function countUp(el, target, dur = 900) {
  if (!el) return;
  if (prefersReducedMotion()) { el.textContent = Math.round(target).toLocaleString(); return; }
  const start = performance.now();
  (function step(now) {
    const p = Math.min(1, (now - start) / dur);
    el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3))).toLocaleString();
    if (p < 1) requestAnimationFrame(step);
  })(performance.now());
}

function tickOdometer(target) {
  countUp(document.getElementById("odometer"), target);
  // Float an in-game-style "+N xp" drop off the counter (skipped under reduced
  // motion — the CSS also disables the animation belt-and-braces).
  const wrap = document.querySelector(".odometer-wrap");
  if (!wrap || prefersReducedMotion()) return;
  const drop = document.createElement("span");
  drop.className = "xp-drop";
  drop.textContent = `+${Math.round(target).toLocaleString()} xp`;
  wrap.appendChild(drop);
  setTimeout(() => drop.remove(), 1600);
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
  countUp(document.getElementById("summary-score"), sessionScore, 1100);
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
    // Local per-day history for the guest streak heatmap (signed-in players use
    // the server's /user/play-history instead). Keep ~13 months, deduped.
    if (!Array.isArray(state.played_dates)) state.played_dates = [];
    if (!state.played_dates.includes(today)) state.played_dates.push(today);
    if (state.played_dates.length > 400) state.played_dates = state.played_dates.slice(-400);
  }

  // Session-level achievement stats (competitive modes only).
  const a = ensureAch();
  a.sessions = (a.sessions || 0) + 1;
  a.questions = (a.questions || 0) + quiz.questions.length;
  if (currentMode === "daily") a.daily_sessions = (a.daily_sessions || 0) + 1;
  a.best_session = Math.max(a.best_session || 0, sessionScore);
  a.max_purple_in_session = Math.max(a.max_purple_in_session || 0, sessionPurpleCount);
  a.max_streak = Math.max(a.max_streak || 0, state.daily_streak);
  const h = new Date().getUTCHours();
  if (h >= 22 || h < 5) achFlag("night_owl");
  if (h >= 5 && h < 8) achFlag("early_bird");

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
      streakEl.innerHTML = `<span class="flame">${Icons.svg("flame")}</span> <strong>${state.daily_streak}-day streak</strong>` +
        ` <span class="muted">Back tomorrow to keep it alive.</span>`;
      streakEl.classList.remove("hidden");
    } else {
      streakEl.classList.add("hidden");
    }
  }
  const insEl = document.getElementById("summary-insight");
  if (insEl) {
    if (sessionInsights.length) {
      const avg = Math.round(sessionInsights.reduce((a, b) => a + b, 0) / sessionInsights.length);
      insEl.innerHTML = `${Icons.svg("chart")} You beat <strong>${avg}%</strong> of players on average today.`;
      insEl.classList.remove("hidden");
      // Stash it so the home "garage" can echo it back next visit ("Last Daily…").
      if (currentMode === "daily") {
        state.last_daily_percentile = { pct: avg, date: utcDate() };
        saveState(state);
      }
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
  const mode = currentMode === "free_practice" ? "practice" : "daily";
  return `${location.origin}/?play=${mode}`;
}

function buildShareText() {
  const grid = sessionResults.map(closenessSquare).join("");
  const max = quiz.questions.length * 5000;
  const tag = currentMode === "daily" ? `Slayer Task #${dailyNumber()}` : "Training Grounds";
  // Optional brag line from the per-question percentiles (social proof in shares
  // is a strong pull — "beat 72% of players" invites a comeback).
  let brag = "";
  if (sessionInsights.length) {
    const avg = Math.round(sessionInsights.reduce((a, b) => a + b, 0) / sessionInsights.length);
    brag = `\nBeat ${avg}% of players`;
  }
  // Spoiler-free: shares the closeness pattern and total, never the answers.
  return `⚔️ ScapeMaster — ${tag}\n${grid}\n${sessionScore.toLocaleString()} / ${max.toLocaleString()} xp${brag}` +
    `\nCan you beat me? ${shareLink()}`;
}

/* Try the native share sheet, fall back to clipboard, then to inline text. */
async function shareOrCopy(text, copiedMsg) {
  if (navigator.share) {
    try { await navigator.share({ title: "ScapeMaster", text }); return; } catch { /* cancelled */ }
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
  await shareOrCopy(buildShareText(), "Result copied — paste it anywhere to share.");
});

/* Direct "I dare you" invite — same deep link, framed as a head-to-head challenge
 * rather than a result post. The two have different psychology, so we offer both. */
document.getElementById("challenge-friend").addEventListener("click", async () => {
  track("challenge_friend", { mode: currentMode });
  const a = ensureAch(); a.challenges = (a.challenges || 0) + 1; saveState(state);
  evaluateAchievements();
  const tag = currentMode === "daily" ? "Daily Slayer Task" : "ScapeMaster run";
  const text = `⚔️ I just banked ${sessionScore.toLocaleString()} xp on today's ScapeMaster ${tag}. ` +
    `Think you can beat me?\n${shareLink()}`;
  await shareOrCopy(text, "Challenge copied — send it to a friend.");
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
  } catch { toast("Couldn't load the matchup. Try again."); return; }
  document.getElementById("arcade-metric").textContent = `Which has the higher ${arcade.metric_label}?`;
  a.querySelector(".name").textContent = arcade.entity_a.full_name;
  b.querySelector(".name").textContent = arcade.entity_b.full_name;
}

function pick(which) {
  if (locked || !arcade) return;
  locked = true;
  track("arcade_play");
  const a = arcade.entity_a, b = arcade.entity_b;
  document.querySelector("#arcade-a .val").textContent = CurveSlider.fmt(a.value);
  document.querySelector("#arcade-b .val").textContent = CurveSlider.fmt(b.value);
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
    pickedHigher ? "Correct — next up…" : "Wrong — streak reset. Next up…";
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
    ? `<span class="flame">${Icons.svg("flame")}</span> <span><strong>${n}-day streak</strong> secured. See you tomorrow.</span>`
    : `<span class="flame">${Icons.svg("flame")}</span> <span><strong>${n}-day streak</strong> — play today's Slayer Task to keep it alive</span>`;
  el.classList.remove("hidden");
}

function renderProfile() {
  const signedIn = isSignedIn();
  document.getElementById("p-team").textContent = (TEAMS[state.selected_god] || TEAMS.saradomin).name;

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
  renderHeatmap("profile-heatmap", 26);

  document.getElementById("guest-badge").textContent = signedIn ? "member" : "guest";
  document.getElementById("account-guest").classList.toggle("hidden", signedIn);
  document.getElementById("account-member").classList.toggle("hidden", !signedIn);
  if (signedIn) {
    document.getElementById("account-username").textContent =
      (serverStats && serverStats.username) || localStorage.getItem("scapemaster_username") || "you";
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
    localStorage.setItem("scapemaster_username", me.username);
  } catch { /* offline — keep whatever we have */ }
}

/* Leaderboard window shared by the HiScores and the God Wars
 * championship: 'all' | 'weekly' | 'daily'. Daily/weekly reset, so there's
 * always a fresh race to win — the reason to come back tomorrow. */
let lbPeriod = "all";

async function loadLeaderboard() {
  const list = document.getElementById("leaderboard-list");
  if (!list) return;
  try {
    const res = await fetch(`${API}/leaderboard?period=${lbPeriod}`);
    const entries = (await res.json()).entries || [];
    const myName = localStorage.getItem("scapemaster_username");
    list.innerHTML = entries.length
      ? entries.map((e) => `<li class="${e.username === myName ? "me" : ""}">
            <span class="lb-rank">${e.rank}</span>
            <span class="lb-name">${escapeHtml(e.username)} <em class="lb-team">${(TEAMS[e.selected_god] || {}).name || ""}</em></span>
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
    const res = await fetch(`${API}/leaderboard/gods?period=${lbPeriod}`);
    const entries = (await res.json()).entries || [];
    const mine = state.selected_god;
    list.innerHTML = entries.length
      ? entries.map((e) => {
          const t = TEAMS[e.god] || { name: e.god, primary: "#888" };
          return `<li class="${e.god === mine ? "me" : ""}">
            <span class="lb-rank">${e.rank}</span>
            <span class="ctc-swatch" style="background:${t.primary}"></span>
            <span class="lb-name">${escapeHtml(t.name)}
              <em class="lb-team">${e.members} follower${e.members === 1 ? "" : "s"} · ${e.avg_per_member.toLocaleString()} avg</em></span>
            <span class="lb-points">${e.points.toLocaleString()}</span>
          </li>`;
        }).join("")
      : `<li class="muted">No god has scored ${lbPeriod === "all" ? "yet" : "in this window"}.</li>`;
  } catch {
    list.innerHTML = `<li class="muted">Standings unavailable right now.</li>`;
  }
}

/* Home-page God Wars tower — the championship pulled onto
 * the landing page. Same /leaderboard/gods data as the profile board, but its own
 * period state (towerPeriod) so the two boards browse independently. */
let towerPeriod = "all";
function fmtCompact(n) {
  return n >= 10000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : Number(n).toLocaleString();
}
async function loadHomeTower() {
  const list = document.getElementById("home-tower-list");
  if (!list) return;
  try {
    const res = await fetch(`${API}/leaderboard/gods?period=${towerPeriod}`);
    const entries = (await res.json()).entries || [];
    const mine = state.selected_god;
    list.innerHTML = entries.length
      ? entries.slice(0, 8).map((e) => {
          const t = TEAMS[e.god] || { name: e.god, primary: "#888" };
          const you = e.god === mine ? ` <span class="tt-you">\u00b7 YOUR GOD</span>` : "";
          return `<li class="${e.god === mine ? "me" : ""}">
            <span class="tt-rank">${e.rank}</span>
            <span class="tt-bar" style="background:${t.primary}"></span>
            <span class="tt-name">${escapeHtml(t.name)}${you}</span>
            <span class="tt-pts">${fmtCompact(e.points)}</span>
          </li>`;
        }).join("")
      : `<li class="muted">No god has scored ${towerPeriod === "all" ? "yet" : "in this window"} \u2014 pledge a god and post the first.</li>`;
  } catch {
    list.innerHTML = `<li class="muted">Standings unavailable right now.</li>`;
  }
}
/* ===================== YOUR GARAGE ===================== *
 * The personalized home strip: who you are, where you rank, your stake in the
 * God Wars championship, the badges you're closest to, and your streak
 * heatmap. Everything works for guests off local state; signing in upgrades the
 * rank + team-contribution cards to server-authoritative numbers. */

/* Per-day Daily play, for the heatmap. Signed-in players get the authoritative
 * server history; guests fall back to the locally-recorded played_dates. Returns
 * a map of 'YYYY-MM-DD' -> intensity level 0..4. */
async function heatmapLevels(days) {
  const level = {};
  if (isSignedIn()) {
    try {
      const r = await fetch(`${API}/user/play-history?days=${days}`, { headers: authHeaders() });
      if (r.ok) {
        for (const d of (await r.json()).days || []) {
          const q = d.questions || 0;
          level[d.date] = q >= 6 ? 4 : q >= 4 ? 3 : q >= 2 ? 2 : q >= 1 ? 1 : 0;
        }
        return level;
      }
    } catch { /* fall through to local */ }
  }
  for (const d of (state.played_dates || [])) level[d] = 3;  // guest: played = solid
  return level;
}

/* A GitHub-style contribution grid of the last `weeks` weeks of Daily play,
 * coloured in the team's primary. Columns are Mon→Sun weeks ending today. */
async function renderHeatmap(containerId, weeks = 18) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const level = await heatmapLevels(weeks * 7);
  const today = new Date(utcDate() + "T00:00:00Z");
  const start = new Date(today);
  start.setUTCDate(start.getUTCDate() - (weeks * 7 - 1));
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));  // back to Monday
  const cells = [];
  for (let d = new Date(start); d <= today; d.setUTCDate(d.getUTCDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    const lv = level[iso] || 0;
    const label = lv ? `${iso} · played` : `${iso} · no Daily`;
    cells.push(`<i class="hm-cell l${lv}" title="${label}"></i>`);
  }
  el.innerHTML =
    `<div class="hm-grid">${cells.join("")}</div>` +
    `<div class="hm-legend"><span>Less</span><i class="hm-cell l0"></i><i class="hm-cell l1"></i>` +
    `<i class="hm-cell l2"></i><i class="hm-cell l3"></i><i class="hm-cell l4"></i><span>More</span></div>`;
}

/* Rank movement since the last day you checked. Snapshots your rank once per UTC
 * day in localStorage, so the arrow means "vs. yesterday", not "vs. last click". */
function rankMovement(period, rank) {
  if (!rank) return "";
  const key = `sm_lastrank_${period}`;
  let prev = null;
  try { prev = JSON.parse(localStorage.getItem(key) || "null"); } catch { /* ignore */ }
  const today = utcDate();
  let html = `<span class="g-move flat">—</span>`;
  if (prev && prev.date !== today && Number.isFinite(prev.rank)) {
    const up = prev.rank - rank;                 // smaller rank number = moved up
    html = up > 0 ? `<span class="g-move up">▲${up}</span>`
         : up < 0 ? `<span class="g-move down">▼${-up}</span>`
         : `<span class="g-move flat">—</span>`;
  }
  if (!prev || prev.date !== today) localStorage.setItem(key, JSON.stringify({ rank, date: today }));
  return html;
}

function garageBadgesCard() {
  const near = closestAchievements(achSnapshot(), 3);
  const rows = near.length
    ? near.map(({ ach, p }) => `
        <div class="gb-row tier-${ach.tier}">
          <span class="gb-icon">${ach.icon}</span>
          <div class="gb-main">
            <div class="gb-name">${escapeHtml(ach.name)} <span class="gb-frac">${fmtCompact(p.cur)}/${fmtCompact(p.target)}</span></div>
            <div class="gb-bar"><i style="width:${Math.round(p.pct * 100)}%"></i></div>
          </div>
        </div>`).join("")
    : `<p class="muted g-empty">Every badge unlocked — you're a Hall of Famer. 🏛️</p>`;
  return `<div class="g-card g-badges">
      <p class="g-card-label">Almost there</p>
      ${rows}
      <button class="g-link" data-view="profile">All badges →</button>
    </div>`;
}

async function renderGarage() {
  const el = document.getElementById("garage");
  if (!el) return;
  const signedIn = isSignedIn();
  const team = TEAMS[state.selected_god] || TEAMS.saradomin;
  const streak = currentStreak();
  const name = (serverStats && serverStats.username) ||
    localStorage.getItem("scapemaster_username") || "adventurer";

  const welcome = signedIn
    ? `Welcome back, ${escapeHtml(name)}`
    : `Your bank`;
  const streakBit = streak > 0
    ? ` <span class="g-flame">${Icons.svg("flame")} ${streak}-day streak</span>` : "";
  const sub = signedIn
    ? `Sworn to ${escapeHtml(team.name)}.`
    : `Sign in to save your progress, pledge to a god and climb the HiScores.`;
  const last = state.last_daily_percentile;
  const lastBit = (last && last.pct != null)
    ? `<p class="g-last">${Icons.svg("chart")} Last Daily — you beat <strong>${last.pct}%</strong> of players.</p>` : "";

  // Card shells (rank + team fill in async; badges + heatmap are local/instant).
  el.innerHTML = `
    <div class="garage-head">
      <h2 class="garage-title">${welcome}${streakBit}</h2>
      <p class="garage-sub">${sub}</p>
    </div>
    <div class="garage-grid">
      <div class="g-card g-rank" id="g-rank">
        <p class="g-card-label">Your rank</p>
        ${signedIn
          ? `<span class="sk-line sk-h" style="width:52%"></span>
             <span class="sk-line sk-b" style="width:78%;margin-top:.2rem"></span>`
          : `<p class="g-big">—</p><p class="g-note">Sign in to rank on the leaderboard.</p>
             <button class="g-link" data-auth="open">Create an account →</button>`}
      </div>
      <div class="g-card g-team" id="g-team" style="--g-accent:${team.primary}">
        <p class="g-card-label">God Wars</p>
        <p class="g-team-name">Sworn to <strong>${escapeHtml(team.name)}</strong></p>
        ${signedIn
          ? `<span class="sk-line sk-b" style="width:65%;margin-top:.35rem"></span>
             <span class="sk-line sk-sm" style="width:88%"></span>`
          : `<button class="g-link" data-view="profile">Pledge a god →</button>`}
      </div>
      ${garageBadgesCard()}
    </div>
    ${lastBit}
    <div class="g-heatwrap">
      <div class="g-heat-head"><span class="g-card-label">Daily streak history</span>
        <button class="g-remind ${remindEnabled() ? "on" : ""}" id="g-remind" title="Streak reminder">${Icons.svg("bell")} ${remindEnabled() ? "Reminders on" : "Remind me"}</button></div>
      <div class="heatmap" id="garage-heatmap"></div>
    </div>`;

  renderHeatmap("garage-heatmap", 18);

  if (!signedIn) return;
  // Rank card (global, all-time) + team stake — server-authoritative.
  try {
    const r = await fetch(`${API}/leaderboard/me?period=all`, { headers: authHeaders() });
    if (r.ok) {
      const d = await r.json();
      const box = document.getElementById("g-rank");
      if (box) box.innerHTML = d.rank
        ? `<p class="g-card-label">Your rank</p>
           <p class="g-big">#${d.rank} ${rankMovement("all", d.rank)}</p>
           <p class="g-note">Top ${100 - d.percentile}% of ${fmtCompact(d.total_ranked)} ranked · ${fmtCompact(d.points)} pts</p>`
        : `<p class="g-card-label">Your rank</p>
           <div class="empty-state"><p>Play your first Daily to appear on the leaderboard.</p></div>`;
    }
  } catch { /* offline — leave the loading state */ }
  try {
    const r = await fetch(`${API}/leaderboard/god?period=all`, { headers: authHeaders() });
    if (r.ok) {
      const d = await r.json();
      const box = document.getElementById("g-team");
      const t = TEAMS[d.god] || team;
      if (box) box.innerHTML = `<p class="g-card-label">God Wars</p>
        <p class="g-team-name">Sworn to <strong>${escapeHtml(t.name)}</strong>${d.god_rank ? ` · <span class="g-pos">#${d.god_rank}</span>` : ""}</p>
        ${d.your_rank_in_god
          ? `<p class="g-note">You've banked <strong>${fmtCompact(d.your_points)}</strong> xp — #${d.your_rank_in_god} of ${d.members} in your faction.</p>`
          : `<p class="g-note muted">Play today's Slayer Task to score for your god.</p>`}`;
    }
  } catch { /* offline */ }
}

/* ===================== STREAK REMINDER (local, opt-in) ===================== *
 * There is no push server (and the free host is ephemeral), so this is entirely
 * local: a service worker is registered only so notifications can be shown — and,
 * where the browser supports Notification Triggers, scheduled for the evening.
 * Everything degrades gracefully to an on-reopen nudge. */
const REMIND_KEY = "sm_streak_remind";
let swReg = null;

function remindEnabled() {
  return localStorage.getItem(REMIND_KEY) === "1" &&
    "Notification" in window && Notification.permission === "granted";
}

async function initServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  try { swReg = await navigator.serviceWorker.register("/sw.js"); }
  catch { /* the app works fine without it */ }
}

function notifyStreak(streak) {
  const body = `Your ${streak}-day streak ends at midnight UTC — play today's Slayer Task to keep it alive.`;
  const opts = { body, icon: "/static/icon-180.png", badge: "/static/icon-180.png", tag: "streak" };
  try {
    if (swReg && swReg.showNotification) swReg.showNotification("ScapeMaster", opts);
    else new Notification("ScapeMaster", opts);
  } catch { /* best effort */ }
}

/* Schedule an evening nudge via Notification Triggers (Chromium-only,
 * experimental). Where unsupported this is a no-op and the on-open path covers it. */
function scheduleStreakReminder() {
  if (!remindEnabled() || !swReg || typeof window.TimestampTrigger === "undefined") return;
  const now = new Date();
  const target = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 20, 0, 0);
  if (target <= Date.now()) return;  // past 20:00 UTC already — on-open nudge handles it
  try {
    swReg.showNotification("ScapeMaster", {
      tag: "streak-scheduled", icon: "/static/icon-180.png",
      body: "Don't lose your streak — today's Slayer Task is waiting.",
      showTrigger: new window.TimestampTrigger(target),
    });
  } catch { /* triggers unsupported -> on-open reminder only */ }
}

/* On app open: if reminders are on, the streak is live, today's Daily isn't done,
 * and it's already late in the UTC day, nudge immediately; always (re)schedule. */
function maybeRemindOnOpen() {
  if (!remindEnabled()) return;
  const streak = currentStreak();
  if (streak < 1 || isCapped("daily")) return;
  if (new Date().getUTCHours() >= 18) notifyStreak(streak);
  scheduleStreakReminder();
}

async function toggleReminder() {
  if (!("Notification" in window)) { toast("Notifications aren't supported on this device."); return; }
  if (remindEnabled()) {
    localStorage.setItem(REMIND_KEY, "0");
    toast("Streak reminders turned off.");
  } else {
    let perm = Notification.permission;
    if (perm !== "granted") perm = await Notification.requestPermission();
    if (perm === "granted") {
      localStorage.setItem(REMIND_KEY, "1");
      toast("Streak reminders on — we'll nudge you before midnight UTC.");
      scheduleStreakReminder();
    } else {
      toast("Allow notifications in your browser to enable reminders.");
    }
  }
  renderGarage();
}
document.addEventListener("click", (e) => {
  if (e.target.closest("#g-remind")) toggleReminder();
});

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

function resetLocalProgress() {
  localStorage.removeItem(STORAGE_KEY);
  ["arcade_streak", "arcade_best", "played_daily"].forEach((k) => localStorage.removeItem(k));
  state = defaultState(); applyTeam(state.selected_god); saveState(state);
  toast("Local progress reset.");
}
document.getElementById("reset-btn").addEventListener("click", resetLocalProgress);

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
      const payload = { username, password, anon_id: anonId(), selected_god: state.selected_god };
      if (mode === "register" && email) payload.email = email;
      const res = await fetch(`${API}/auth/${mode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        // Pledge the locally-chosen god on sign-up so the new account joins that
        // faction in the God Wars championship from its first point.
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
      localStorage.setItem("scapemaster_username", body.username);
      serverStats = { ...body.stats, username: body.username };
      // Adopt the god the server has on file for this account (it persists the
      // faction across devices, unlike the local-only guest choice).
      if (body.selected_god) applyTeam(body.selected_god);
      state.is_guest = false;
      recordGodUse(body.selected_god);  // the pledged god counts toward collection badges
      saveState(state);
      evaluateAchievements();  // "Contract Signed" + any points-based unlocks from the merge
      close();
      toast(body.claimed_events
        ? `Signed in as ${body.username} — ${body.claimed_events} guest result${body.claimed_events === 1 ? "" : "s"} saved to your account.`
        : `Signed in as ${body.username}.`);
      renderProfile();
      renderGarage();
    } catch {
      showError("Couldn't reach the server. Check your connection and try again.");
    } finally {
      btn.disabled = false;
    }
  }

  async function logout() {
    try { await fetch(`${API}/auth/logout`, { method: "POST", headers: authHeaders() }); }
    catch { /* best effort */ }
    setAuthToken(null);
    localStorage.removeItem("scapemaster_username");
    serverStats = null;
    state.is_guest = true; saveState(state);
    toast("Logged out.");
    renderProfile();
    renderGarage();
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
    // Garage's "Create an account →" CTA (rendered dynamically) opens the modal.
    document.addEventListener("click", (e) => {
      if (e.target.closest('[data-auth="open"]')) open();
    });
  }
  return { init, open };
})();

/* Persist the chosen god to the account so it counts in the God Wars
 * championship and follows the player across devices. No-op for guests (their
 * choice stays local until they sign in, which pledges it then). */
async function syncTeam(god) {
  if (!isSignedIn()) return;
  try {
    await fetch(`${API}/profile/god`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ selected_god: god }),
    });
    loadTeamLeaderboard();  // reflect the switch in the standings
  } catch { /* best effort — local choice already applied */ }
}

/* ===================== TEAM PICKER + FIRST-RUN ONBOARDING ===================== *
 * The same modal serves two jobs: the header's "change my colours" picker, and a
 * one-time welcome prompt that asks a brand-new player to pick a side. Both show
 * how many players have pledged to each team and how the Constructors'
 * Championship is going (from /api/v1/gods/overview), so the choice feels social
 * and consequential rather than purely cosmetic. */
const ONBOARD_KEY = "sm_onboarded";
const TeamPicker = (() => {
  let onboarding = false;       // true while the forced first-run prompt is open
  let overview = null;          // cached {teams, total_players} from the server

  // Per-team {members, points, rank}, keyed by team id, for the card footers and
  // the intro line. Best-effort: if the fetch fails we just render bare cards.
  async function loadOverview() {
    try {
      const res = await fetch(`${API}/gods/overview`);
      if (!res.ok) throw new Error();
      overview = await res.json();
    } catch { overview = null; }
    return overview;
  }

  function statFor(key) {
    if (!overview) return null;
    return overview.gods.find((t) => t.god === key) || { members: 0, points: 0, rank: null };
  }

  // The welcome blurb: total players who've picked a side + the current
  // God Wars leader, so a newcomer knows what they're joining.
  function introHtml() {
    if (!overview || !overview.gods.length) {
      return "Pick the god you'll fight for. Your XP feeds your faction's " +
             "war effort in the God Wars championship.";
    }
    const total = overview.total_players;
    const leader = overview.gods.find((t) => t.points > 0);
    const players = total === 1 ? "1 player has" : `${total.toLocaleString()} players have`;
    const lead = leader
      ? ` <strong>${escapeHtml((TEAMS[leader.god] || {}).name || leader.god)}</strong> ` +
        `lead the God Wars with ${leader.points.toLocaleString()} xp.`
      : " No god has scored yet — be the first to put XP on the board.";
    return `${players} already picked a side.${lead} Every question you answer adds your ` +
           `XP to your god's war effort — choose yours:`;
  }

  function cardFooter(key) {
    const s = statFor(key);
    if (!s) return "";
    const fans = s.members === 1 ? "1 follower" : `${s.members.toLocaleString()} followers`;
    return `<span class="team-card-stats">${fans} · ${s.points.toLocaleString()} xp</span>`;
  }

  function render() {
    const grid = document.getElementById("team-picker-grid");
    grid.innerHTML = Object.entries(TEAMS).map(([key, t]) => {
      const sel = key === state.selected_god;
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

  function pickTeam(god) {
    track("god_select", { god, onboarding });
    applyTeam(god);
    recordGodUse(god);  // for the pantheon-collection achievements
    saveState(state);
    evaluateAchievements();
    syncTeam(god);       // persist server-side when signed in
    if (onboarding) {
      localStorage.setItem(ONBOARD_KEY, "1");
      const name = (TEAMS[god] || {}).name || god;
      toast(`Now sworn to ${name}. Your XP feeds its war effort.`);
    }
    onboarding = false;
    close();
  }

  async function open(opts = {}) {
    onboarding = !!opts.onboarding;
    const title = document.getElementById("team-panel-title");
    const intro = document.getElementById("team-panel-intro");
    const closeBtn = document.getElementById("team-panel-close");
    title.textContent = onboarding ? "Pledge Your God" : "Choose Your God";
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
const ACH_TIERS = { bronze: 1, adamant: 2, rune: 3, dragon: 4 };
const ACH_TIER_LABEL = { bronze: "Bronze", adamant: "Adamant", rune: "Rune", dragon: "Dragon" };

const ACHIEVEMENTS = [
  // ── Bronze ──────────────────────────────────────────────────────────────
  { id: "tutorial_island", icon: "🏝️", tier: "bronze", name: "Tutorial Island",   desc: "Answer your very first question.",           check: (s) => s.questions >= 1 || s.practiceQuestions >= 1 },
  { id: "first_task",      icon: "📜", tier: "bronze", name: "First Assignment",  desc: "Complete your first full Slayer Task.",      check: (s) => s.sessions >= 1 },
  { id: "solid_loot",      icon: "🟢", tier: "bronze", name: "Solid Loot",        desc: "Land within 25% of an answer.",              check: (s) => s.green >= 1 },
  { id: "ten_kc",          icon: "➕", tier: "bronze", name: "10 KC",             desc: "Answer 10 questions.",                       check: (s) => s.questions >= 10 },
  { id: "training_dummy",  icon: "🎯", tier: "bronze", name: "Training Dummy",    desc: "Try a Training Grounds question.",           check: (s) => s.practiceQuestions >= 1 },
  { id: "sound_of_duel",   icon: "⚔️", tier: "bronze", name: "Duel Accepted",     desc: "Play a round in the Duel Arena.",            check: (s) => s.flags.arcade_played === true },
  { id: "on_the_board",    icon: "📈", tier: "bronze", name: "On the HiScores",   desc: "Bank 5,000 lifetime XP.",                    check: (s) => s.points >= 5000 },
  { id: "pick_a_god",      icon: "🛡️", tier: "bronze", name: "Take a Side",       desc: "Pledge to a god.",                           check: (s) => s.godsUsed >= 1 },
  { id: "town_crier",      icon: "📣", tier: "bronze", name: "Town Crier",        desc: "Share a result.",                            check: (s) => s.shares >= 1 },
  { id: "account_made",    icon: "✍️", tier: "bronze", name: "Character Created", desc: "Create an account.",                         check: (s) => s.signedIn === true },
  { id: "quick_study",     icon: "📚", tier: "bronze", name: "Wiki Browser",      desc: "Answer 25 questions.",                       check: (s) => s.questions >= 25 },
  { id: "first_coins",     icon: "🪙", tier: "bronze", name: "First Coin Stack",  desc: "Bank 1,000 lifetime XP.",                    check: (s) => s.points >= 1000 },

  // ── Adamant ─────────────────────────────────────────────────────────────
  { id: "a_purple",        icon: "🟣", tier: "adamant", name: "A Purple!",        desc: "Land within 10% of an answer.",              check: (s) => s.purple >= 1 },
  { id: "three_streak",    icon: "🔥", tier: "adamant", name: "On a Task Streak", desc: "Reach a 3-day streak.",                      check: (s) => s.maxStreak >= 3 },
  { id: "the_ton",         icon: "💯", tier: "adamant", name: "100 KC",           desc: "Answer 100 questions.",                      check: (s) => s.questions >= 100 },
  { id: "big_session",     icon: "⏱️", tier: "adamant", name: "Efficient Grinder", desc: "Bank 22,000+ XP in a single task.",         check: (s) => s.bestSession >= 22000 },
  { id: "quarter_stack",   icon: "🏆", tier: "adamant", name: "Growing Bank",     desc: "Bank 25,000 lifetime XP.",                   check: (s) => s.points >= 25000 },
  { id: "ten_tasks",       icon: "🗡️", tier: "adamant", name: "Task Done, Next",  desc: "Complete 10 sessions.",                      check: (s) => s.sessions >= 10 },
  { id: "dummy_whisperer", icon: "🎯", tier: "adamant", name: "Dummy Whisperer",  desc: "Answer 50 Training Grounds questions.",      check: (s) => s.practiceQuestions >= 50 },
  { id: "duel_ace",        icon: "⚡", tier: "adamant", name: "Duel Arena Ace",   desc: "Reach a Duel Arena streak of 10.",           check: (s) => s.arcadeBest >= 10 },
  { id: "night_owl",       icon: "🦉", tier: "adamant", name: "Midnight Grinder", desc: "Finish a session after midnight (UTC).",     check: (s) => s.flags.night_owl === true },
  { id: "early_bird",      icon: "🐦", tier: "adamant", name: "Dawn Patrol",      desc: "Finish a session at sunrise (UTC).",         check: (s) => s.flags.early_bird === true },
  { id: "two_gods",        icon: "🤝", tier: "adamant", name: "Godless No More",  desc: "Pledge to two different gods.",              check: (s) => s.godsUsed >= 2 },
  { id: "double_stack",    icon: "💰", tier: "adamant", name: "Double Stack",     desc: "Bank 50,000 lifetime XP.",                   check: (s) => s.points >= 50000 },
  { id: "sharp_training",  icon: "🏹", tier: "adamant", name: "Sharp in Training", desc: "Hit a purple in the Training Grounds.",     check: (s) => s.flags.practice_purple === true },
  { id: "five_streak",     icon: "📆", tier: "adamant", name: "Daily Scaper",     desc: "Reach a 5-day streak.",                      check: (s) => s.maxStreak >= 5 },
  { id: "regular",         icon: "🔄", tier: "adamant", name: "Regular",          desc: "Complete 25 sessions.",                      check: (s) => s.sessions >= 25 },
  { id: "training_arc",    icon: "🧪", tier: "adamant", name: "The Training Arc", desc: "Answer 200 Training Grounds questions.",     check: (s) => s.practiceQuestions >= 200 },
  { id: "social_butterfly",icon: "🦋", tier: "adamant", name: "Falador Party",    desc: "Share 10 results.",                          check: (s) => s.shares >= 10 },

  // ── Rune ────────────────────────────────────────────────────────────────
  { id: "bullseye",        icon: "🎯", tier: "rune", name: "Dead Shot",           desc: "Nail an answer exactly.",                    check: (s) => s.perfect >= 1 },
  { id: "triple_purple",   icon: "🥇", tier: "rune", name: "Triple Purple",       desc: "Hit 3 purples in one task.",                 check: (s) => s.maxPurpleInSession >= 3 },
  { id: "purple_reign",    icon: "🟣", tier: "rune", name: "Purple Reign",        desc: "Hit 25 purples.",                            check: (s) => s.purple >= 25 },
  { id: "week_streak",     icon: "📆", tier: "rune", name: "A Week in Gielinor",  desc: "Reach a 7-day streak.",                      check: (s) => s.maxStreak >= 7 },
  { id: "maximum_attack",  icon: "🚀", tier: "rune", name: "Maximum Efficiency",  desc: "Bank 27,000+ XP in a single task.",          check: (s) => s.bestSession >= 27000 },
  { id: "veteran",         icon: "🎖️", tier: "rune", name: "Veteran Cape",        desc: "Answer 500 questions.",                      check: (s) => s.questions >= 500 },
  { id: "centurion",       icon: "💪", tier: "rune", name: "The 100k Stack",      desc: "Bank 100,000 lifetime XP.",                  check: (s) => s.points >= 100000 },
  { id: "comeback_kid",    icon: "🧪", tier: "rune", name: "Saradomin Brew",      desc: "Save a streak with the one-day freeze.",     check: (s) => s.flags.comeback === true },
  { id: "gauntlet",        icon: "🤜", tier: "rune", name: "Duel Me, Coward",     desc: "Send 5 challenges to friends.",              check: (s) => s.challenges >= 5 },
  { id: "wanderer",        icon: "🌍", tier: "rune", name: "God Hopper",          desc: "Pledge to 5 different gods.",                check: (s) => s.godsUsed >= 5 },
  { id: "iron_scaper",     icon: "🦾", tier: "rune", name: "Iron Discipline",     desc: "Complete 50 sessions.",                      check: (s) => s.sessions >= 50 },
  { id: "duel_legend",     icon: "👾", tier: "rune", name: "Duel Arena Legend",   desc: "Reach a Duel Arena streak of 25.",           check: (s) => s.arcadeBest >= 25 },
  { id: "daily_devotee",   icon: "☀️", tier: "rune", name: "Slayer Devotee",      desc: "Complete 25 Daily Slayer Tasks.",            check: (s) => s.dailySessions >= 25 },
  { id: "daily_grinder",   icon: "🔁", tier: "rune", name: "Slayer Grandmaster",  desc: "Complete 100 Daily Slayer Tasks.",           check: (s) => s.dailySessions >= 100 },
  { id: "sharpshooter",    icon: "🏹", tier: "rune", name: "Sharpshooter",        desc: "Nail 10 exact answers.",                     check: (s) => s.perfect >= 10 },
  { id: "the_grind",       icon: "⚙️", tier: "rune", name: "The Grind",           desc: "Answer 250 questions.",                      check: (s) => s.questions >= 250 },
  { id: "purple_patch",    icon: "🔮", tier: "rune", name: "Purple Patch",        desc: "Hit 50 purples.",                            check: (s) => s.purple >= 50 },
  { id: "fortnight",       icon: "🗓️", tier: "rune", name: "Fortnight of Focus",  desc: "Reach a 14-day streak.",                     check: (s) => s.maxStreak >= 14 },

  // ── Dragon ──────────────────────────────────────────────────────────────
  { id: "grand_slam",      icon: "🏆", tier: "dragon", name: "Six Purples",       desc: "All six questions purple in one task.",      check: (s) => s.maxPurpleInSession >= 6 },
  { id: "perfect_task",    icon: "💎", tier: "dragon", name: "The Perfect Task",  desc: "A flawless 30,000-XP session.",              check: (s) => s.bestSession >= 30000 },
  { id: "unbeatable",      icon: "🔥", tier: "dragon", name: "Never Log Out",     desc: "Reach a 30-day streak.",                     check: (s) => s.maxStreak >= 30 },
  { id: "max_cape_fund",   icon: "👑", tier: "dragon", name: "Max Cape Fund",     desc: "Bank 500,000 lifetime XP.",                  check: (s) => s.points >= 500000 },
  { id: "purple_machine",  icon: "🟪", tier: "dragon", name: "Purple Machine",    desc: "Hit 100 purples.",                           check: (s) => s.purple >= 100 },
  { id: "completionist",   icon: "🏃", tier: "dragon", name: "Completionist",     desc: "Answer 2,000 questions.",                    check: (s) => s.questions >= 2000 },
  { id: "duel_immortal",   icon: "🌟", tier: "dragon", name: "Duel Immortal",     desc: "Reach a Duel Arena streak of 50.",           check: (s) => s.arcadeBest >= 50 },
  { id: "pantheon",        icon: "🏛️", tier: "dragon", name: "The Full Pantheon", desc: "Pledge to all six gods.",                    check: (s) => s.godsUsed >= 6 },
  { id: "dead_eye",        icon: "🦅", tier: "dragon", name: "Dead-Eye",          desc: "Nail 50 exact answers.",                     check: (s) => s.perfect >= 50 },
  { id: "high_roller",     icon: "💸", tier: "dragon", name: "Party Hat Money",   desc: "Bank 250,000 lifetime XP.",                  check: (s) => s.points >= 250000 },
  { id: "millionaire",     icon: "🤑", tier: "dragon", name: "XP Millionaire",    desc: "Bank 1,000,000 lifetime XP.",                check: (s) => s.points >= 1000000 },
  { id: "hall_of_fame",    icon: "🏰", tier: "dragon", name: "Hall of Legends",   desc: "Unlock 40 other diary entries.",             check: (s) => s.unlocked >= 40 },
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
    perfect: a.perfect || 0,
    purple: a.purple || 0,
    green: a.green || 0,
    bestSession: a.best_session || 0,
    maxPurpleInSession: a.max_purple_in_session || 0,
    maxStreak: Math.max(a.max_streak || 0, currentStreak() || 0),
    shares: a.shares || 0,
    challenges: a.challenges || 0,
    godsUsed: (a.gods_used || []).length,
    arcadeBest: +localStorage.getItem("arcade_best") || 0,
    signedIn: isSignedIn(),
    flags: a.flags || {},
    unlocked: (state.unlocked_achievements || []).length,
  };
}

/* Progress toward a badge, derived from its check's single numeric threshold
 * (e.g. "s.purple >= 25"). Returns null for compound or flag checks (zero or
 * multiple thresholds), which the nudge skips. Deriving it from the check keeps
 * the check the single source of truth — there's no separate target to drift. */
const _ACH_THRESHOLD = /s\.(\w+)\s*>=\s*(\d+)/g;
function achProgress(ach, s) {
  const hits = [...ach.check.toString().matchAll(_ACH_THRESHOLD)];
  if (hits.length !== 1) return null;            // compound/boolean -> no simple bar
  const metric = hits[0][1], target = Number(hits[0][2]);
  const cur = Number(s[metric]) || 0;
  return { metric, target, cur: Math.min(cur, target), pct: Math.max(0, Math.min(cur / target, 1)) };
}

/* The locked badges closest to unlocking, for the garage "almost there" nudge.
 * Ranks by how far along you are, then by the smaller target so a brand-new
 * player still sees the easiest next goals rather than an empty card. */
function closestAchievements(s, n = 3) {
  const unlocked = new Set(state.unlocked_achievements || []);
  return ACHIEVEMENTS
    .filter((a) => !unlocked.has(a.id))
    .map((a) => ({ ach: a, p: achProgress(a, s) }))
    .filter((x) => x.p && x.p.pct < 1)
    .sort((a, b) => (b.p.pct - a.p.pct) || (a.p.target - b.p.target))
    .slice(0, n);
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
        <span class="ach-icon">${got ? a.icon : Icons.svg("lock")}</span>
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
      `${view.length} / ${rows.length}${flagged ? ` · ${flagged} ${Icons.svg("flag")}` : ""}`;
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
              aria-pressed="${r.flagged}">${Icons.svg("flag")}</button></td>
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
    daily: ["quiz", "daily"],
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
    icon.innerHTML = Icons.svg(on ? "volume-2" : "volume-x");
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
  return { init, refresh: paint };
})();

/* ---- Theme toggle (dark default / light opt-in) ----
 * Mirrors SoundToggle: flips data-theme="light" on <html>, persists to localStorage,
 * so it sticks across visits. The team colour still comes from applyTeam(); only the
 * neutral surfaces + the legible-on-white --color-ink swap (handled in CSS). */
const THEME_KEY = "sm_theme";
const ThemeToggle = (() => {
  function current() { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; }
  function paint(theme) {
    const btn = document.getElementById("theme-toggle");
    const icon = document.getElementById("theme-icon");
    if (!btn || !icon) return;
    const light = theme === "light";
    icon.innerHTML = Icons.svg(light ? "sun" : "moon");
    btn.setAttribute("aria-pressed", String(light));
    btn.title = light ? "Parchment mode \u2014 click for dark" : "Dark mode \u2014 click for parchment";
  }
  function apply(theme) {
    const root = document.documentElement;
    if (theme === "light") root.setAttribute("data-theme", "light");
    else root.removeAttribute("data-theme");
    paint(theme);
    const t = TEAMS[state.selected_god] || TEAMS.saradomin;
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

/* ===================== SETTINGS ===================== *
 * A real preferences surface. It doesn't own any state — each row reads and
 * writes the existing source of truth (Sound, ThemeToggle, the motion + remind
 * prefs, the team, local progress) and keeps the quick header toggles in sync.
 * Reuses the modal pattern: backdrop + Escape close, and focus returns to the
 * gear button afterwards. */
const Settings = (() => {
  let lastFocus = null;
  const setSwitch = (id, on) =>
    document.getElementById(id)?.setAttribute("aria-checked", String(!!on));

  function paint() {
    setSwitch("set-sound", Sound.isOn());
    setSwitch("set-theme", ThemeToggle.current() === "light");
    setSwitch("set-motion", motionOverride());
    setSwitch("set-remind", remindEnabled());
    const tn = document.getElementById("set-team-name");
    if (tn) tn.textContent = (TEAMS[state.selected_god] || TEAMS.saradomin).name;
    const ver = document.getElementById("settings-version");
    if (ver) ver.textContent = `ScapeMaster · Build ${APP_VERSION}`;
  }
  function open() {
    lastFocus = document.activeElement;
    paint();
    show("settings-overlay");
    document.getElementById("settings-close")?.focus();
  }
  function close() {
    hide("settings-overlay");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function init() {
    const btn = document.getElementById("settings-btn");
    if (!btn) return;
    btn.addEventListener("click", open);
    document.getElementById("settings-close")?.addEventListener("click", close);
    document.getElementById("settings-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "settings-overlay") close();
    });

    document.getElementById("set-sound")?.addEventListener("click", () => {
      const on = Sound.toggle(); SoundToggle.refresh();
      if (on) Sound.play("uiClick");
      track("sound_toggle", { on, from: "settings" });
      paint();
    });
    document.getElementById("set-theme")?.addEventListener("click", () => {
      const next = ThemeToggle.current() === "light" ? "dark" : "light";
      localStorage.setItem(THEME_KEY, next); ThemeToggle.apply(next);
      track("theme_toggle", { theme: next, from: "settings" });
      Sound.play("uiClick"); paint();
    });
    document.getElementById("set-motion")?.addEventListener("click", () => {
      const next = motionOverride() ? "0" : "1";
      localStorage.setItem(MOTION_KEY, next); applyMotionPref();
      track("motion_toggle", { reduced: next === "1" });
      paint();
    });
    document.getElementById("set-remind")?.addEventListener("click", async () => {
      await toggleReminder(); paint();
    });
    document.getElementById("set-team")?.addEventListener("click", () => {
      close(); TeamPicker.open();
    });
    document.getElementById("set-reset")?.addEventListener("click", () => {
      resetLocalProgress(); renderProfile(); renderGarage(); paint();
    });
  }
  return { init, close, isOpen: () => !document.getElementById("settings-overlay")?.classList.contains("hidden") };
})();

/* ===================== FIRST-RUN SCORING EXPLAINER ===================== *
 * Shown once — the first time a player opens any challenge — so the closeness
 * curve is taught before they're scored on it. The promise to start the run is
 * deferred until they dismiss it. */
const SCORING_SEEN_KEY = "sm_seen_scoring";
const ScoringIntro = (() => {
  let onDone = null;
  function finish() {
    hide("scoring-overlay");
    localStorage.setItem(SCORING_SEEN_KEY, "1");
    const cb = onDone; onDone = null;
    if (cb) cb();
  }
  /* Returns true if it showed (caller should wait); false if already seen. */
  function maybeShow(done) {
    if (localStorage.getItem(SCORING_SEEN_KEY) === "1") return false;
    onDone = done || null;
    show("scoring-overlay");
    document.getElementById("scoring-go")?.focus();
    track("scoring_intro_shown");
    return true;
  }
  function init() {
    document.getElementById("scoring-go")?.addEventListener("click", finish);
    document.getElementById("scoring-close")?.addEventListener("click", finish);
    document.getElementById("scoring-overlay")?.addEventListener("click", (e) => {
      if (e.target.id === "scoring-overlay") finish();
    });
  }
  return { init, maybeShow };
})();

/* One global Escape handler for the lightweight dialogs that don't manage their
 * own (the team picker stays open during forced onboarding, so it's excluded). */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (Settings.isOpen()) { Settings.close(); return; }
  const so = document.getElementById("scoring-overlay");
  if (so && !so.classList.contains("hidden")) { so.classList.add("hidden"); return; }
});

/* ---- Boot ---- */
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
applyTeam(state.selected_god);
saveState(state);
renderQuizIntro();
renderStreakBanner();
CurveSlider.init();
DataCheck.init();
TeamPicker.init();
SoundToggle.init();
ThemeToggle.init();
Settings.init();
ScoringIntro.init();
applyMotionPref();
Auth.init();
document.querySelectorAll(".lb-period-tab").forEach((t) =>
  t.addEventListener("click", () => setLeaderboardPeriod(t.dataset.period)));
document.querySelectorAll(".tt-period-tab").forEach((t) =>
  t.addEventListener("click", () => setTowerPeriod(t.dataset.towerPeriod)));
loadHomeTower();
renderGarage();
loadDataStatus();
{ const verEl = document.getElementById("app-version");
  if (verEl) verEl.textContent = `Build ${APP_VERSION}`; }
document.querySelectorAll(".ach-filter-tab").forEach((t) =>
  t.addEventListener("click", () => setAchFilter(t.dataset.filter)));
renderAchievements();
evaluateAchievements();  // catch anything already earned (e.g. from a prior visit)
track("app_open", { signed_in: isSignedIn() });  // open the analytics session
// If a session token is present, pull the authoritative server stats, then
// repaint the profile so it shows the signed-in totals.
refreshMe().then(() => { renderProfile(); renderStreakBanner(); renderGarage(); evaluateAchievements(); });
initServiceWorker().then(maybeRemindOnOpen);
tickCountdown(); setInterval(tickCountdown, 1000);
renderRaceWeek(); setInterval(renderRaceWeek, 60000); // refresh past/next state each minute
// A shared "?play=" link drops the player straight into a challenge; don't
// interrupt that with the onboarding prompt. Otherwise, first-run guests are
// asked to pick a team (with live headcounts + championship standings).
if (!handleDeepLink()) TeamPicker.maybeOnboard();
