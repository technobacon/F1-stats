# F1-stats

**Apex Quiz** — a website for Formula 1 stat quizzes.

Pick a category and test your knowledge of F1 stats — World Champions, wins &
podiums, circuits, and records. The app tracks your personal best for each
category and shows them on a leaderboard.

## Status

This is an early **prototype**: a zero-dependency static web app (plain HTML,
CSS and JavaScript). No build step or server is required.

## Run it

Just open `index.html` in a browser, or serve the folder locally:

```bash
# Python 3
python3 -m http.server 8000
# then visit http://localhost:8000
```

## Project structure

```
index.html        # Page shell + layout
styles.css        # F1-themed styling
app.js            # SPA logic: router, quiz flow, scoring, leaderboard
data/questions.js # Quiz content (categories + multiple-choice questions)
```

## How it works

- **Categories & questions** live in `data/questions.js`. Each question is
  multiple-choice with a correct answer index and a short explanatory fact.
- **Scoring** is tracked per quiz; best scores persist in `localStorage` and
  appear on the Leaderboard.
- Questions are shuffled each play.

## Roadmap ideas

- Pull live data from a real F1 API (e.g. Ergast / Jolpica) instead of static
  questions.
- Timed / streak modes and a global leaderboard backend.
- More categories (constructors, current season, historic eras) and difficulty
  levels.
