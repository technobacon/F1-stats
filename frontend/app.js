/* F1 StatGuesser — prototype frontend.
 * Guest-first: all progress lives in localStorage (PRD §5.2, Architecture §2.1).
 * Scoring is NEVER computed here — guesses go to the server, which returns the score. */

const API = "/api/v1";
const STORAGE_KEY = "f1statguesser_user_state";

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
  one_shot: {
    title: "Hardcore",
    desc: "Three brutal questions. No slider, no safety net — type your answer and commit.",
    capKey: null, capLabel: "", slider: false,
  },
};

/* ---- Guest-first local state (Architecture §2.1 schema) ---- */
const defaultState = () => ({
  is_guest: true, selected_team: "mclaren",
  lifetime_points: 0, games_played: 0, average_closeness: 0,
  daily_streak: 0, last_played_date: null, unlocked_achievements: [],
  _closeness_sum: 0, _q_count: 0,
});

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

/* ---- All 2026 F1 constructor colour schemes ---- */
const TEAMS = {
  mclaren:      { name: "McLaren",      primary: "#FF8000", secondary: "#FF8000", text: "#000" },
  ferrari:      { name: "Ferrari",      primary: "#DC0000", secondary: "#DC0000", text: "#fff" },
  mercedes:     { name: "Mercedes",     primary: "#00D2BE", secondary: "#00D2BE", text: "#000" },
  red_bull:     { name: "Red Bull",     primary: "#1E1B4B", secondary: "#FFC906", tertiary: "#DC0000", text: "#fff" },
  aston_martin: { name: "Aston Martin", primary: "#00B140", secondary: "#00B140", text: "#000" },
  alpine:       { name: "Alpine",       primary: "#FF87BC", secondary: "#0090FF", text: "#000" },
  williams:     { name: "Williams",     primary: "#0064FF", secondary: "#0064FF", text: "#fff" },
  rb:           { name: "Racing Bulls", primary: "#FFFFFF", secondary: "#5BA4CF", text: "#000" },
  haas:         { name: "Haas",         primary: "#1A1A1A", secondary: "#E8002D", text: "#fff" },
  audi:         { name: "Audi",         primary: "#8E8E8E", secondary: "#CC0000", text: "#000" },
  cadillac:     { name: "Cadillac",     primary: "#FFFFFF", secondary: "#0D0D0D", text: "#000" },
};

/* ---- Theming (Architecture §3.1) ---- */
function applyTeam(team) {
  const t = TEAMS[team] || TEAMS.mclaren;
  const root = document.documentElement;
  root.setAttribute("data-team", team);
  root.style.setProperty("--color-primary", t.primary);
  root.style.setProperty("--color-secondary", t.secondary);
  root.style.setProperty("--btn-text", t.text);
  /* Three-colour teams get an explicit gradient override; others fall back to CSS default */
  if (t.tertiary) {
    root.style.setProperty("--btn-gradient", `linear-gradient(135deg, ${t.primary}, ${t.secondary}, ${t.tertiary})`);
  } else {
    root.style.removeProperty("--btn-gradient");
  }
  /* Update header button swatch */
  const swatch = document.getElementById("team-btn-swatch");
  const label  = document.getElementById("team-btn-label");
  if (swatch) swatch.style.background = buildTeamSwatch(t);
  if (label)  label.textContent = t.name;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t.primary);
  state.selected_team = team;
}

function buildTeamSwatch(t) {
  if (t.tertiary) return `linear-gradient(135deg, ${t.primary}, ${t.secondary}, ${t.tertiary})`;
  if (t.secondary !== t.primary) return `linear-gradient(135deg, ${t.primary}, ${t.secondary})`;
  return t.primary;
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
  // Leaving the quiz (or re-entering its intro) always exits immersive mode.
  if (view !== "quiz") document.body.classList.remove("in-game");
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
  const status = document.getElementById("quiz-status");
  status.textContent = "Loading questions…";
  try {
    const res = await fetch(`${API}/quiz/${currentMode}`);
    if (!res.ok) throw new Error(await res.text());
    quiz = await res.json();
    qPos = 0; sessionScore = 0; sessionCloseness = 0;
    document.getElementById("q-total").textContent = quiz.questions.length;
    document.getElementById("q-mode-badge").textContent = currentMode.replace("_", "-");
    hide("quiz-intro"); show("quiz-play"); hide("quiz-summary"); hide("quiz-reveal");
    document.body.classList.add("in-game"); // go full-screen immersive
    window.scrollTo({ top: 0 });
    renderQuestion();
  } catch (e) {
    status.textContent = "Could not load quiz. Tap to retry.";
    toast("Network error — is the server awake?");
  }
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
    car.style.top = (p.y / 280) * 100 + "%";
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
    value = Math.min(max, Math.max(min, Math.round(v)));
    place();
    if (fire && onChange) onChange(value);
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
  document.getElementById("q-index").textContent = qPos + 1;
  document.getElementById("q-text").textContent = q.question_text;
  document.getElementById("q-cat").textContent = (q.category || "").replace(/_/g, " ");
  document.getElementById("q-hint").textContent = KIND_HINT[kind] || "";

  const input = document.getElementById("q-input");
  // Year answers always use the curved slider (the scope is in the question anyway);
  // One-Shots otherwise hides it for a hardcore, type-it-in feel.
  const useSlider = kind === "year" || MODES[currentMode].slider;
  input.step = "1"; input.min = q.slider_min;
  input.max = kind === "percentage" ? 100 : q.slider_max;
  input.value = useSlider ? q.slider_min : "";
  CurveSlider.configure({
    min: +q.slider_min, max: +q.slider_max, value: +q.slider_min, visible: useSlider,
    onChange: (v) => { input.value = v; },
  });
  input.oninput = () => { if (useSlider) CurveSlider.setValue(parseFloat(input.value) || q.slider_min); };

  // Advance the immersive progress bar to reflect questions completed.
  const fill = document.getElementById("game-progress-fill");
  if (fill) fill.style.width = `${(qPos / quiz.questions.length) * 100}%`;
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
  submitBtn.disabled = true; submitBtn.textContent = "Scoring…";
  try {
    const res = await fetch(`${API}/quiz/verify`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tracking_token: q.tracking_token, guess }),
    });
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    sessionScore += result.score;
    sessionCloseness += result.score / result.max_score;
    revealScore(q, result);
  } catch (e) {
    toast("Couldn't score that — try again.");
    submitBtn.disabled = false; submitBtn.textContent = "Lock In Guess";
  }
}

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

  // Drop the guess marker in promptly, then slide the answer bar across slowly
  // with an ease-in-out (speeds up, then eases into the answer — anticipation).
  // The number and score stay hidden until the bar reaches its destination.
  requestAnimationFrame(() => {
    guessNode.style.left = clampPct(result.guess) + "%";
    setTimeout(() => slideToAnswer(actualNode, actualText, clampPct(result.actual), result), 500);
  });
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
    }
  })(start);
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
  qPos++;
  if (qPos < quiz.questions.length) { hide("quiz-reveal"); show("quiz-play"); renderQuestion(); }
  else finishSession();
});

function finishSession() {
  hide("quiz-reveal"); show("quiz-summary");
  const maxPossible = quiz.questions.length * 5000;
  document.getElementById("summary-score").textContent = sessionScore.toLocaleString();
  const acc = Math.round((sessionCloseness / quiz.questions.length) * 100);
  document.getElementById("accuracy-row").textContent = `Accuracy: ${acc}% · ${sessionScore.toLocaleString()} / ${maxPossible.toLocaleString()}`;

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
    else state.daily_streak = 1;
    state.last_played_date = today;
  }
  awardAchievements(acc);
  saveState(state);
}

function awardAchievements(acc) {
  const add = (a) => { if (!state.unlocked_achievements.includes(a)) { state.unlocked_achievements.push(a); toast(`🏆 Achievement: ${a.replace(/_/g, " ")}`); } };
  if (sessionScore >= 20000) add("sharp_shooter");
  if (acc === 100) add("flawless_lap");
  if (state.daily_streak >= 3) add("podium_streak");
}

document.getElementById("summary-back").addEventListener("click", () => navigate("home"));

document.getElementById("share-result").addEventListener("click", async () => {
  const text = `🏁 F1 StatGuesser — I scored ${sessionScore.toLocaleString()} / ` +
    `${quiz.questions.length * 5000} on the ${MODES[currentMode].title}!`;
  if (navigator.share) {
    try { await navigator.share({ title: "F1 StatGuesser", text }); return; } catch { /* cancelled */ }
  }
  try { await navigator.clipboard.writeText(text); document.getElementById("share-status").textContent = "Copied to clipboard!"; }
  catch { document.getElementById("share-status").textContent = text; }
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
  const a = arcade.entity_a, b = arcade.entity_b;
  document.querySelector("#arcade-a .val").textContent = a.value;
  document.querySelector("#arcade-b .val").textContent = b.value;
  const pickedHigher = which === "a" ? a.value >= b.value : b.value >= a.value;
  const card = document.getElementById("arcade-" + which);
  card.classList.add(pickedHigher ? "correct" : "wrong");
  let streak = +localStorage.getItem("arcade_streak") || 0;
  streak = pickedHigher ? streak + 1 : 0;
  localStorage.setItem("arcade_streak", streak);
  let best = +localStorage.getItem("arcade_best") || 0;
  if (streak > best) { best = streak; localStorage.setItem("arcade_best", best); }
  document.getElementById("arcade-streak").textContent = streak;
  document.getElementById("arcade-best").textContent = best;
  document.getElementById("arcade-result").textContent =
    pickedHigher ? "Correct! Loading next…" : "Streak reset. Loading next…";
  setTimeout(loadArcade, 1400);
}
document.getElementById("arcade-a").addEventListener("click", () => pick("a"));
document.getElementById("arcade-b").addEventListener("click", () => pick("b"));

/* ===================== PROFILE ===================== */
function renderProfile() {
  document.getElementById("p-team").textContent = (TEAMS[state.selected_team] || TEAMS.mclaren).name;
  document.getElementById("p-points").textContent = state.lifetime_points.toLocaleString();
  document.getElementById("p-games").textContent = state.games_played;
  document.getElementById("p-accuracy").textContent =
    state._q_count ? `${Math.round(state.average_closeness * 100)}%` : "—";
  document.getElementById("p-streak").textContent = state.daily_streak;
  document.getElementById("p-achievements").textContent =
    state.unlocked_achievements.length ? state.unlocked_achievements.map((a) => a.replace(/_/g, " ")).join(", ") : "none yet";
  document.getElementById("guest-badge").textContent = state.is_guest ? "guest" : "member";
}

document.getElementById("sync-btn").addEventListener("click", () => {
  alert("Account Wall: signing in lets you post to the Global Leaderboard.\n\n" +
        "Per the trust boundary (Architecture §2.2), the server would re-derive your " +
        "leaderboard total from server-verified round events — never from this local blob.");
});
document.getElementById("reset-btn").addEventListener("click", () => {
  localStorage.removeItem(STORAGE_KEY);
  ["arcade_streak", "arcade_best", "played_daily", "played_race_week"].forEach((k) => localStorage.removeItem(k));
  state = defaultState(); applyTeam(state.selected_team); saveState(state);
  toast("Local progress reset.");
});

/* ===================== TEAM PICKER ===================== */
const TeamPicker = (() => {
  function open() {
    const grid = document.getElementById("team-picker-grid");
    grid.innerHTML = Object.entries(TEAMS).map(([key, t]) => {
      const sel = key === state.selected_team;
      return `<button class="team-card${sel ? " selected" : ""}" data-team="${key}"
                      aria-pressed="${sel}" style="${sel ? `border-color:${t.primary}` : ""}">
                <span class="team-card-swatch" style="background:${buildTeamSwatch(t)}"></span>
                <span class="team-card-name">${t.name}<span class="team-card-check">✓</span></span>
              </button>`;
    }).join("");
    grid.querySelectorAll(".team-card").forEach((card) => {
      card.addEventListener("click", () => {
        applyTeam(card.dataset.team);
        saveState(state);
        close();
      });
    });
    show("team-overlay");
  }

  function close() { hide("team-overlay"); }

  function init() {
    document.getElementById("team-select-btn").addEventListener("click", open);
    document.getElementById("team-panel-close").addEventListener("click", close);
    document.getElementById("team-overlay").addEventListener("click", (e) => {
      if (e.target.id === "team-overlay") close();
    });
  }

  return { init };
})();

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
    const view = rows.filter((r) =>
      (!mode || r.game_mode === mode) &&
      (!needle || `${r.question_string} ${r.category}`.toLowerCase().includes(needle)));
    view.sort((a, b) => {
      const va = a[sortKey] ?? "", vb = b[sortKey] ?? "";
      const cmp = (typeof va === "number" && typeof vb === "number")
        ? va - vb : String(va).localeCompare(String(vb));
      return sortAsc ? cmp : -cmp;
    });
    document.getElementById("data-count").textContent = `${view.length} / ${rows.length}`;
    document.getElementById("data-rows").innerHTML = view.map((r) => `
      <tr>
        <td class="dt-q">${esc(r.question_string)}</td>
        <td class="num dt-a">${fmtAnswer(r.verified_answer)}</td>
        <td>${esc(r.answer_kind)}</td>
        <td>${esc((r.category || "").replace(/_/g, " "))}</td>
        <td>${esc(r.game_mode.replace("_", "-"))}</td>
        <td class="num">${r.era_year ?? "—"}</td>
      </tr>`).join("");
    document.querySelectorAll(".data-table th").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === sortKey);
      th.classList.toggle("desc", th.dataset.sort === sortKey && !sortAsc);
    });
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

/* ---- Boot ---- */
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
applyTeam(state.selected_team);
saveState(state);
renderQuizIntro();
CurveSlider.init();
DataCheck.init();
TeamPicker.init();
tickCountdown(); setInterval(tickCountdown, 1000);
renderRaceWeek(); setInterval(renderRaceWeek, 60000); // refresh past/next state each minute
