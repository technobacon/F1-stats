/* GridMaster analytics dashboard (viewer for /api/v1/analytics/summary).
 * Lives in its own file (not inline in analytics.html) so the site's
 * Content-Security-Policy can stay script-src 'self' with no inline allowance. */

const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "f1sg_analytics_token";
const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString());
const pct = (r) => (r == null ? "–" : Math.round(r * 100) + "%");

$("token").value = localStorage.getItem(TOKEN_KEY) || "";
$("load").addEventListener("click", load);
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
if ($("token").value) load();

async function load() {
  const token = $("token").value.trim();
  const days = $("days").value;
  localStorage.setItem(TOKEN_KEY, token);
  showError("");
  try {
    const res = await fetch(`${API}/analytics/summary?days=${days}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.status === 404) return showError("Analytics is disabled on this server (set F1_ANALYTICS_TOKEN).");
    if (res.status === 401) return showError("Invalid token.");
    if (!res.ok) return showError("Could not load report (" + res.status + ").");
    render(await res.json());
  } catch (e) {
    showError("Network error loading the report.");
  }
}

function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.classList.toggle("hidden", !msg);
  if (msg) $("report").classList.add("hidden");
}

function kpi(value, label, sub) {
  return `<div class="kpi"><div class="v">${value}</div><div class="l">${label}</div>${
    sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function render(d) {
  $("report").classList.remove("hidden");
  $("kpis").innerHTML =
    kpi(fmt(d.dau), "DAU", "active today") +
    kpi(fmt(d.wau), "WAU", "last 7 days") +
    kpi(fmt(d.mau), "MAU", "last 30 days") +
    kpi(fmt(d.totals.accounts), "Accounts", fmt(d.totals.visitors_all_time) + " visitors all-time") +
    kpi(fmt(d.totals.scored_answers), "Scored answers", "server-verified");

  // Active-by-day grouped bars (visitors + players), scaled to the max.
  const max = Math.max(1, ...d.active_by_day.flatMap((x) => [x.visitors, x.players]));
  $("active-chart").innerHTML = d.active_by_day.map((x) => `
    <div class="col" title="${x.date}: ${x.visitors} visitors, ${x.players} players">
      <div class="b" style="height:${(x.visitors / max) * 100}%"></div>
      <div class="b players" style="height:${(x.players / max) * 100}%"></div>
      <div class="lbl">${x.date.slice(5)}</div>
    </div>`).join("");

  // Funnel with conversion rates.
  const f = d.funnel;
  $("funnel").innerHTML = `
    <tr><th>Step</th><th class="n">Count</th><th class="n">Conversion</th></tr>
    ${row("App opens", f.app_open, "")}
    ${row("Quiz starts", f.quiz_start, pct(f.start_rate) + " of opens")}
    ${row("Quiz completes", f.quiz_complete, pct(f.completion_rate) + " of starts")}
    ${row("Shares", f.share, pct(f.share_rate) + " of completes")}
    ${row("Sign-ups", f.signup_success, pct(f.signup_rate) + " of opens")}`;

  // Modes, sorted desc with a proportional meter.
  const modes = Object.entries(d.modes).sort((a, b) => b[1] - a[1]);
  const mmax = Math.max(1, ...modes.map((m) => m[1]));
  $("modes").innerHTML = `<tr><th>Mode</th><th class="n">Plays</th><th></th></tr>` +
    modes.map(([k, v]) => `<tr><td>${k.replace(/_/g, " ")}</td><td class="n">${fmt(v)}</td>
      <td style="width:40%"><div class="meter"><span style="width:${(v / mmax) * 100}%"></span></div></td></tr>`).join("");

  const r = d.retention;
  $("retention").innerHTML =
    kpi(pct(r.d1), "Day 1", `cohort ${fmt(r.d1_cohort)}`) +
    kpi(pct(r.d7), "Day 7", `cohort ${fmt(r.d7_cohort)}`);

  const smax = Math.max(1, ...d.signups_by_day.map((x) => x.count));
  $("signups-chart").innerHTML = d.signups_by_day.map((x) => `
    <div class="col" title="${x.date}: ${x.count}">
      <div class="b" style="height:${(x.count / smax) * 100}%"></div>
      <div class="lbl">${x.date.slice(5)}</div>
    </div>`).join("");
}

function row(label, count, conv) {
  return `<tr><td>${label}</td><td class="n">${fmt(count)}</td><td class="n muted">${conv}</td></tr>`;
}
