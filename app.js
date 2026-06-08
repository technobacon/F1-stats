/**
 * Apex Quiz — F1 stats trivia prototype.
 *
 * A tiny hash-free SPA: a small router swaps "views" into #app. State for the
 * active quiz lives in `session`; best scores persist to localStorage so the
 * leaderboard survives reloads. No build step, no dependencies.
 */
(function () {
  "use strict";

  var DATA = window.QUIZ_DATA;
  var app = document.getElementById("app");
  var STORE_KEY = "apexquiz.best.v1";

  // ---- Persistence ---------------------------------------------------------
  function loadBest() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY)) || {};
    } catch (e) {
      return {};
    }
  }
  function saveBest(best) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(best));
    } catch (e) {
      /* storage unavailable — scores just won't persist */
    }
  }

  // ---- Helpers -------------------------------------------------------------
  function el(html) {
    var t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstChild;
  }
  function categoryById(id) {
    return DATA.categories.filter(function (c) { return c.id === id; })[0];
  }
  function shuffle(arr) {
    var a = arr.slice();
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = a[i]; a[i] = a[j]; a[j] = tmp;
    }
    return a;
  }
  function render(node) {
    app.innerHTML = "";
    node.classList.add("fade-in");
    app.appendChild(node);
    window.scrollTo(0, 0);
  }

  // ---- Active quiz state ---------------------------------------------------
  var session = null; // { category, questions, index, score, answered }

  // ---- Views ---------------------------------------------------------------
  function viewHome() {
    var best = loadBest();
    var view = el('<div></div>');

    view.appendChild(el(
      '<section class="hero">' +
        '<h1>Think you know <span class="hl">Formula 1?</span></h1>' +
        '<p>Pick a category and test your knowledge of F1 stats — champions, ' +
        'circuits, records and more. Beat your personal best on each.</p>' +
      '</section>'
    ));

    var grid = el('<section class="cat-grid"></section>');
    DATA.categories.forEach(function (cat) {
      var b = best[cat.id];
      var bestLabel = b
        ? '<span class="cat-best">Best ' + b.score + '/' + b.total + '</span>'
        : '<span>Not played yet</span>';
      var card = el(
        '<button class="cat-card">' +
          '<div class="cat-icon">' + cat.icon + '</div>' +
          '<h3>' + cat.name + '</h3>' +
          '<p>' + cat.blurb + '</p>' +
          '<div class="cat-meta">' +
            '<span>' + cat.questions.length + ' questions</span>' +
            bestLabel +
          '</div>' +
        '</button>'
      );
      card.addEventListener("click", function () { startQuiz(cat.id); });
      grid.appendChild(card);
    });
    view.appendChild(grid);
    render(view);
  }

  function startQuiz(catId) {
    var cat = categoryById(catId);
    session = {
      category: cat,
      questions: shuffle(cat.questions),
      index: 0,
      score: 0,
      answered: false
    };
    viewQuestion();
  }

  function viewQuestion() {
    var s = session;
    var q = s.questions[s.index];
    var total = s.questions.length;
    var pct = Math.round((s.index / total) * 100);

    var view = el('<div></div>');
    view.appendChild(buildBackLink("Quit to menu", viewHome));

    view.appendChild(el(
      '<div class="quiz-head">' +
        '<h2>' + s.category.icon + " " + s.category.name + '</h2>' +
        '<span class="quiz-progress-label">Question ' + (s.index + 1) + ' of ' + total + '</span>' +
      '</div>'
    ));
    view.appendChild(el(
      '<div class="progress-track"><div class="progress-fill" style="width:' + pct + '%"></div></div>'
    ));

    var card = el('<div class="question-card"></div>');
    card.appendChild(el('<div class="q-text">' + q.q + '</div>'));

    var keys = ["A", "B", "C", "D", "E"];
    var optsWrap = el('<div class="options"></div>');
    q.options.forEach(function (opt, i) {
      var btn = el(
        '<button class="option">' +
          '<span class="key">' + keys[i] + '</span>' +
          '<span class="label">' + opt + '</span>' +
        '</button>'
      );
      btn.addEventListener("click", function () { answer(i, card); });
      optsWrap.appendChild(btn);
    });
    card.appendChild(optsWrap);

    // Feedback area (revealed after answering)
    card.appendChild(el(
      '<div class="feedback">' +
        '<div class="verdict"></div>' +
        '<div class="fact"></div>' +
        '<button class="btn btn-primary next-btn"></button>' +
      '</div>'
    ));

    view.appendChild(card);
    render(view);
  }

  function answer(choice, card) {
    var s = session;
    if (s.answered) return;
    s.answered = true;

    var q = s.questions[s.index];
    var correct = q.answer;
    var isRight = choice === correct;
    if (isRight) s.score++;

    var optionBtns = card.querySelectorAll(".option");
    optionBtns.forEach(function (btn, i) {
      btn.disabled = true;
      if (i === correct) btn.classList.add("correct");
      else if (i === choice) btn.classList.add("wrong");
    });

    var fb = card.querySelector(".feedback");
    var verdict = fb.querySelector(".verdict");
    verdict.textContent = isRight ? "Correct!" : "Not quite.";
    verdict.classList.add(isRight ? "right" : "nope");
    fb.querySelector(".fact").textContent = q.fact || "";

    var isLast = s.index === s.questions.length - 1;
    var nextBtn = fb.querySelector(".next-btn");
    nextBtn.textContent = isLast ? "See results" : "Next question";
    nextBtn.addEventListener("click", function () {
      if (isLast) {
        finishQuiz();
      } else {
        s.index++;
        s.answered = false;
        viewQuestion();
      }
    });

    fb.classList.add("show");
  }

  function finishQuiz() {
    var s = session;
    var total = s.questions.length;
    var score = s.score;
    var pct = Math.round((score / total) * 100);

    // Persist best score for this category.
    var best = loadBest();
    var prev = best[s.category.id];
    var isPB = !prev || score > prev.score;
    if (isPB) {
      best[s.category.id] = { score: score, total: total, at: Date.now() };
      saveBest(best);
    }

    var view = el('<div class="results"></div>');
    view.appendChild(el(
      '<div class="score-ring" style="--pct:' + pct + '">' +
        '<div>' +
          '<div class="score-num">' + score + '/' + total + '</div>' +
          '<div class="score-sub">' + pct + '%</div>' +
        '</div>' +
      '</div>'
    ));
    view.appendChild(el('<h2>' + verdictTitle(pct) + '</h2>'));
    view.appendChild(el('<p class="blurb">' + s.category.icon + " " + s.category.name + '</p>'));
    if (isPB) view.appendChild(el('<p class="pb">🎉 New personal best!</p>'));

    var row = el('<div class="btn-row"></div>');
    var retry = el('<button class="btn btn-primary">Play again</button>');
    retry.addEventListener("click", function () { startQuiz(s.category.id); });
    var menu = el('<button class="btn btn-ghost">Choose another category</button>');
    menu.addEventListener("click", viewHome);
    row.appendChild(retry);
    row.appendChild(menu);
    view.appendChild(row);

    render(view);
  }

  function verdictTitle(pct) {
    if (pct === 100) return "Lights out and perfect!";
    if (pct >= 80) return "Podium finish!";
    if (pct >= 60) return "Points scored!";
    if (pct >= 40) return "Midfield battle.";
    return "Back of the grid.";
  }

  function viewStats() {
    var best = loadBest();
    var view = el('<div></div>');
    view.appendChild(el('<h2 class="section-title">🏁 Your Leaderboard</h2>'));

    var panel = el('<div class="panel"></div>');
    DATA.categories.forEach(function (cat) {
      var b = best[cat.id];
      var scoreHtml = b
        ? '<span class="lb-score">' + b.score + '/' + b.total + '</span>'
        : '<span class="lb-score empty">—</span>';
      panel.appendChild(el(
        '<div class="lb-row">' +
          '<span class="lb-cat"><span class="ic">' + cat.icon + '</span>' + cat.name + '</span>' +
          scoreHtml +
        '</div>'
      ));
    });
    view.appendChild(panel);

    var anyPlayed = DATA.categories.some(function (c) { return best[c.id]; });
    if (!anyPlayed) {
      view.appendChild(el('<p class="empty-note">No scores yet — play a quiz to set your first record!</p>'));
    }

    var row = el('<div class="btn-row"></div>');
    var play = el('<button class="btn btn-primary">Play a quiz</button>');
    play.addEventListener("click", viewHome);
    row.appendChild(play);
    view.appendChild(row);

    render(view);
  }

  function buildBackLink(label, handler) {
    var b = el('<button class="back-link">← ' + label + '</button>');
    b.addEventListener("click", handler);
    return b;
  }

  // ---- Nav wiring ----------------------------------------------------------
  function setActiveNav(name) {
    document.querySelectorAll(".topnav a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-nav") === name);
    });
  }

  document.querySelectorAll("[data-nav]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      var name = a.getAttribute("data-nav");
      setActiveNav(name);
      if (name === "stats") viewStats();
      else viewHome();
    });
  });

  // ---- Boot ----------------------------------------------------------------
  viewHome();
})();
