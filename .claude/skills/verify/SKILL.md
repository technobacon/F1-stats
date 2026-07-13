---
name: verify
description: Build, launch and drive GridMaster (and scapemaster) to verify changes end-to-end in a real browser.
---

# Verifying GridMaster changes

## Launch the backend (serves the frontend too)

```bash
cd backend
pip install -q -r requirements.txt            # once per container
F1_DB_PATH=/tmp/verify.db python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8123
```

- Boot self-seeds the committed 2,000-question bank (offline, ~5 s). Health:
  `curl http://127.0.0.1:8123/api/v1/health`.
- Use a throwaway `F1_DB_PATH` — accounts/play_events persist in that file
  across restarts, which skews "today's field" style counts between runs.
- Start it detached/managed; plain `&` inside a one-shot shell orphans the
  process and a later `kill %1` won't find it (stale servers then serve OLD
  code on the port — `pkill -9 -f "uvicorn app.main"` before restarting).
- `python3 -m uvicorn app.main:app` must run with `backend/` as cwd.

## Drive it (headless Chromium + Python Playwright)

`pip install playwright` (client only), then launch with the pre-installed
browser: `p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")`
(check the versioned dir name under `/opt/pw-browsers`). Do NOT run
`playwright install`.

Flow gotchas:
- First visit forces the team-picker onboarding: click a `.team-card`, then
  wait for `#team-overlay.hidden` with `state="attached"` (it's hidden, never
  "visible").
- First "Start Session" shows the scoring explainer: click `#scoring-go`.
- The reveal's answer slide + odometer take ~3.1 s + 0.9 s; reading `#odometer`
  earlier samples a mid-animation value.
- In-game (immersive) mode hides the header, so `#settings-btn` etc. are not
  clickable mid-run; the reveal's `#data-check-reveal` opens a dialog that IS
  reachable there.
- Console shows one `ERR_CONNECTION_RESET` per load in the sandbox: the Google
  Fonts stylesheet is blocked by the egress proxy. Pre-existing, ignore.

## Test suites (CI ground truth, not verification)

```bash
cd backend && python3 -m pytest -q               # F1 app
cd scapemaster/backend && python3 -m pytest -q   # the fork — run it whenever
                                                 # scoring/auth/service/db/main change
```
