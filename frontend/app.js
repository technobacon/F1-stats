/* F1 StatGuesser — prototype frontend.
 * Guest-first: all progress lives in localStorage (PRD §5.2, Architecture §2.1).
 * Scoring is NEVER computed here — guesses go to the server, which returns the score. */

const API = "/api/v1";
const STORAGE_KEY = "f1statguesser_user_state";

/* ---- Guest-first local state (Architecture §2.1 schema) ---- */
const defaultState = () => ({
  is_guest: true,
  selected_team: "mclaren",
  lifetime_points: 0,
  games_played: 0,
  average_closeness: 0,
  daily_streak: 0,
  last_played_date: null,
  unlocked_achievements: [],
});

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? { ...defaultState(), ...JSON.parse(raw).user_state } : defaultState();
  } catch { return defaultState(); }
}
function saveState(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ user_state: s }));
  renderProfile();
  document.getElementById("hud-points").textContent = `${s.lifetime_points.toLocaleString()} pts`;
}

let state = loadState();

/* ---- Theming (Architecture §3.1) ---- */
function applyTeam(team) {
  document.documentElement.setAttribute("data-team", team);
  document.getElementById("team-select").value = team;
  state.selected_team = team;
  saveState(state);
}

/* ---- Countdown HUD (PRD §5.1) — prototype targets a fixed next race ---- */
function tickCountdown() {
  // Illustrative target; production reads the FIA calendar API.
  const target = new Date(Date.now() + 3 * 864e5 + 5 * 36e5); // ~3d 5h out
  const diff = Math.max(0, target - new Date());
  const d = Math.floor(diff / 864e5), h = Math.floor(diff % 864e5 / 36e5),
        m = Math.floor(diff % 36e5 / 6e4), s = Math.floor(diff % 6e4 / 1e3);
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("countdown-timer").textContent =
    `${pad(d)}:${pad(h)}:${pad(m)}:${pad(s)}`;
}

/* ---- View switching ---- */
document.querySelectorAll(".mode-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".mode-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("view-" + tab.dataset.view).classList.add("active");
    if (tab.dataset.view === "arcade") loadArcade();
    if (tab.dataset.view === "profile") renderProfile();
  });
});

/* ===================== DAILY QUIZ ===================== */
let quiz = null, qPos = 0, sessionScore = 0;

document.getElementById("start-daily").addEventListener("click", startDaily);

async function startDaily() {
  const status = document.getElementById("daily-status");
  status.textContent = "Loading today's questions…";
  try {
    const res = await fetch(`${API}/quiz/daily`);
    if (!res.ok) throw new Error(await res.text());
    quiz = await res.json();
    qPos = 0; sessionScore = 0;
    document.getElementById("q-total").textContent = quiz.questions.length;
    show("daily-play"); hide("daily-intro"); hide("daily-summary"); hide("daily-reveal");
    renderQuestion();
  } catch (e) {
    status.textContent = "Could not load quiz. Has the seed pipeline run?";
  }
}

function renderQuestion() {
  const q = quiz.questions[qPos];
  document.getElementById("q-index").textContent = qPos + 1;
  document.getElementById("q-text").textContent = q.question_text;
  const slider = document.getElementById("q-slider"), input = document.getElementById("q-input");
  slider.min = q.slider_min; slider.max = q.slider_max;
  slider.value = q.slider_min; input.value = q.slider_min;
  slider.oninput = () => (input.value = slider.value);
  input.oninput = () => (slider.value = input.value);
}

document.getElementById("submit-guess").addEventListener("click", submitGuess);

async function submitGuess() {
  const q = quiz.questions[qPos];
  const guess = parseFloat(document.getElementById("q-input").value) || 0;
  const res = await fetch(`${API}/quiz/daily/verify`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tracking_token: q.tracking_token, guess }),
  });
  const result = await res.json(); // { score, actual, guess, max_score }
  sessionScore += result.score;
  revealScore(q, result);
}

/* Odometer Score Reveal (Architecture §3.2) */
function revealScore(q, result) {
  hide("daily-play"); show("daily-reveal");
  const lo = +q.slider_min, hi = +q.slider_max, span = (hi - lo) || 1;
  const pct = (v) => `${Math.min(100, Math.max(0, ((v - lo) / span) * 100))}%`;
  const guessNode = document.getElementById("node-guess");
  const actualNode = document.getElementById("node-actual");
  guessNode.style.left = "0%"; actualNode.style.left = "0%";
  document.getElementById("reveal-guess").textContent = result.guess;
  document.getElementById("reveal-actual").textContent = result.actual;

  // 1-2: slide the guess node; 3: drop the actual marker; 4: tick the counter.
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
  if (qPos < quiz.questions.length) { hide("daily-reveal"); show("daily-play"); renderQuestion(); }
  else finishSession();
});

function finishSession() {
  hide("daily-reveal"); show("daily-summary");
  document.getElementById("summary-score").textContent = sessionScore.toLocaleString();

  // Update guest-first local stats (no server trust; leaderboard sync is separate).
  const today = new Date().toISOString().slice(0, 10);
  const playedYesterday = state.last_played_date &&
    (new Date(today) - new Date(state.last_played_date)) <= 864e5;
  state.lifetime_points += sessionScore;
  state.games_played += 1;
  state.daily_streak = playedYesterday ? state.daily_streak + 1 : 1;
  state.last_played_date = today;
  if (sessionScore >= 20000 && !state.unlocked_achievements.includes("sharp_shooter"))
    state.unlocked_achievements.push("sharp_shooter");
  saveState(state);
}

document.getElementById("share-result").addEventListener("click", () => {
  const text = `🏁 F1 StatGuesser — I scored ${sessionScore.toLocaleString()} / ` +
    `${quiz.questions.length * 5000} on today's Daily Quiz!`;
  navigator.clipboard?.writeText(text).then(
    () => (document.getElementById("share-status").textContent = "Copied to clipboard!"),
    () => (document.getElementById("share-status").textContent = text)
  );
});

/* ===================== ARCADE OVER/UNDER ===================== */
let arcade = null, locked = false;

async function loadArcade() {
  locked = false;
  document.getElementById("arcade-result").textContent = "";
  const a = document.getElementById("arcade-a"), b = document.getElementById("arcade-b");
  [a, b].forEach((c) => c.classList.remove("correct", "wrong"));
  const res = await fetch(`${API}/arcade/pair`);
  arcade = await res.json();
  document.getElementById("arcade-metric").textContent = `Who has more ${arcade.metric_label}?`;
  a.querySelector(".name").textContent = arcade.entity_a.full_name;
  b.querySelector(".name").textContent = arcade.entity_b.full_name;
  a.querySelector(".val").textContent = "?";
  b.querySelector(".val").textContent = "?";
}

function pick(which) {
  if (locked || !arcade) return;
  locked = true;
  const a = arcade.entity_a, b = arcade.entity_b;
  document.querySelector("#arcade-a .val").textContent = a.value;
  document.querySelector("#arcade-b .val").textContent = b.value;
  // v1: non-competitive, client-side evaluation (Architecture §1.2).
  const pickedHigher = which === "a" ? a.value >= b.value : b.value >= a.value;
  const cards = { a: document.getElementById("arcade-a"), b: document.getElementById("arcade-b") };
  cards[which].classList.add(pickedHigher ? "correct" : "wrong");
  let streak = +localStorage.getItem("arcade_streak") || 0;
  streak = pickedHigher ? streak + 1 : 0;
  localStorage.setItem("arcade_streak", streak);
  document.getElementById("arcade-streak").textContent = streak;
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
  document.getElementById("p-streak").textContent = state.daily_streak;
  document.getElementById("p-achievements").textContent =
    state.unlocked_achievements.length ? state.unlocked_achievements.join(", ") : "none yet";
  document.getElementById("guest-badge").textContent = state.is_guest ? "guest" : "member";
}

document.getElementById("sync-btn").addEventListener("click", () => {
  // Account Wall trigger (PRD §5.2). Real flow opens NextAuth; prototype explains it.
  alert("Account Wall: signing in lets you post to the Global Leaderboard.\n\n" +
        "Per the trust boundary (Architecture §2.2), the server would re-derive your " +
        "leaderboard total from server-verified round events — never from this local blob.");
});
document.getElementById("reset-btn").addEventListener("click", () => {
  localStorage.removeItem(STORAGE_KEY); localStorage.removeItem("arcade_streak");
  state = defaultState(); applyTeam(state.selected_team); saveState(state);
});

document.getElementById("team-select").addEventListener("change", (e) => applyTeam(e.target.value));

/* ---- Boot ---- */
function show(id) { document.getElementById(id).classList.remove("hidden"); }
function hide(id) { document.getElementById(id).classList.add("hidden"); }
applyTeam(state.selected_team);
saveState(state);
tickCountdown(); setInterval(tickCountdown, 1000);
