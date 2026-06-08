/* F1 StatGuesser — prototype frontend.
 * Guest-first: all progress lives in localStorage (PRD §5.2, Architecture §2.1).
 * Scoring is NEVER computed here — guesses go to the server, which returns the score. */

const API = "/api/v1";
const STORAGE_KEY = "f1statguesser_user_state";

/* ---- Mode metadata (PRD §4.1) ---- */
const MODES = {
  daily: {
    title: "Daily General Quiz",
    desc: "Five questions. Guess the stat — the closer you are, the more of the 5,000 points you keep.",
    capKey: () => utcDate(), capLabel: "today's Daily Quiz", slider: true,
  },
  race_week: {
    title: "Race-Week Quiz",
    desc: "Five questions tied to the active race weekend. One run per race week.",
    capKey: () => isoWeek(), capLabel: "this week's Race-Week Quiz", slider: true,
  },
  one_shot: {
    title: "The One-Shots",
    desc: "Three hardcore questions. No slider — type your answer and commit.",
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

/* ---- Theming (Architecture §3.1) ---- */
function applyTeam(team) {
  document.documentElement.setAttribute("data-team", team);
  document.getElementById("team-select").value = team;
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content", getComputedStyle(document.documentElement).getPropertyValue("--color-primary").trim());
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
/* Illustrative 2026 schedule (race day, UTC). Production reads the FIA calendar API. */
const CAL_2026 = [
  ["Canadian GP", "2026-06-14"], ["Austrian GP", "2026-06-28"], ["British GP", "2026-07-05"],
  ["Hungarian GP", "2026-07-26"], ["Belgian GP", "2026-08-30"], ["Italian GP", "2026-09-06"],
  ["Singapore GP", "2026-09-20"], ["United States GP", "2026-10-25"], ["Mexico City GP", "2026-11-01"],
  ["São Paulo GP", "2026-11-08"], ["Las Vegas GP", "2026-11-21"], ["Qatar GP", "2026-11-29"],
  ["Abu Dhabi GP", "2026-12-06"],
];
const SEASON_2027_OPENER = ["Australian GP", "2027-03-08"]; // off-season pivot target

function buildSessions() {
  // Each race day yields FP1 (−2d 11:00), Qualifying (−1d 14:00), Race (14:00 UTC).
  const out = [];
  for (const [name, raceDay] of CAL_2026) {
    const race = new Date(`${raceDay}T14:00:00Z`);
    const quali = new Date(race - 864e5); quali.setUTCHours(14);
    const fp1 = new Date(race - 2 * 864e5); fp1.setUTCHours(11);
    out.push({ when: fp1, text: `${name} · FP1` },
             { when: quali, text: `${name} · Qualifying` },
             { when: race, text: `${name} · Race` });
  }
  return out.sort((a, b) => a.when - b.when);
}
const SESSIONS = buildSessions();

function tickCountdown() {
  const now = new Date();
  let target = SESSIONS.find((s) => s.when > now);
  let label = target ? `NEXT · ${target.text}` : `SEASON 2027 · ${SEASON_2027_OPENER[0]}`;
  let when = target ? target.when : new Date(`${SEASON_2027_OPENER[1]}T14:00:00Z`);

  const diff = Math.max(0, when - now);
  const d = Math.floor(diff / 864e5), h = Math.floor(diff % 864e5 / 36e5),
        m = Math.floor(diff % 36e5 / 6e4), s = Math.floor(diff % 6e4 / 1e3);
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("countdown-label").textContent = label;
  document.getElementById("countdown-timer").textContent = `${pad(d)}:${pad(h)}:${pad(m)}:${pad(s)}`;
}

/* ===================== VIEW SWITCHING ===================== */
let currentMode = "daily";
document.querySelectorAll(".mode-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".mode-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("view-" + tab.dataset.view).classList.add("active");
    if (tab.dataset.view === "quiz") { currentMode = tab.dataset.mode; renderQuizIntro(); }
    if (tab.dataset.view === "arcade") loadArcade();
    if (tab.dataset.view === "profile") renderProfile();
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
    renderQuestion();
  } catch (e) {
    status.textContent = "Could not load quiz. Tap to retry.";
    toast("Network error — is the server awake?");
  }
}

function renderQuestion() {
  const q = quiz.questions[qPos];
  document.getElementById("q-index").textContent = qPos + 1;
  document.getElementById("q-text").textContent = q.question_text;
  const slider = document.getElementById("q-slider"), input = document.getElementById("q-input");
  // One-Shots is hardcore: no slider hand-holding.
  slider.style.display = MODES[currentMode].slider ? "" : "none";
  slider.min = q.slider_min; slider.max = q.slider_max;
  slider.value = q.slider_min; input.value = MODES[currentMode].slider ? q.slider_min : "";
  slider.oninput = () => (input.value = slider.value);
  input.oninput = () => { if (MODES[currentMode].slider) slider.value = input.value; };
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
  const lo = +q.slider_min, hi = +q.slider_max, span = (hi - lo) || 1;
  const pct = (v) => `${Math.min(100, Math.max(0, ((v - lo) / span) * 100))}%`;
  const guessNode = document.getElementById("node-guess");
  const actualNode = document.getElementById("node-actual");
  guessNode.style.left = "0%"; actualNode.style.left = "0%";
  document.getElementById("reveal-guess").textContent = result.guess;
  document.getElementById("reveal-actual").textContent = result.actual;
  document.getElementById("odometer").textContent = "0";

  requestAnimationFrame(() => {
    guessNode.style.left = pct(result.guess);
    setTimeout(() => { actualNode.style.left = pct(result.actual); }, 600);
    setTimeout(() => tickOdometer(result.score), 1100);
  });
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

document.getElementById("summary-back").addEventListener("click", renderQuizIntro);

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
  document.getElementById("p-team").textContent = state.selected_team;
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

document.getElementById("team-select").addEventListener("change", (e) => { applyTeam(e.target.value); saveState(state); });

/* ---- Boot ---- */
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
applyTeam(state.selected_team);
saveState(state);
renderQuizIntro();
tickCountdown(); setInterval(tickCountdown, 1000);
