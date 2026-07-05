"""FastAPI application.

Endpoints:
    GET  /api/v1/quiz/daily          -> 6 questions, NO answers, tracking tokens
    POST /api/v1/quiz/daily/verify   -> server-side score for one guess
    GET  /api/v1/practice/question   -> one random Training Grounds question
                                        (unlimited, non-competitive; never recorded)
    GET  /api/v1/arcade/pair         -> Duel Arena over/under matchup (non-competitive v1)
    GET  /api/v1/dev/questions       -> full question bank WITH answers (proofreading
                                        tool; disable with OSRS_DEV_TOOLS=0)
    POST /api/v1/dev/flag            -> flag/unflag a question for later review
    GET  /api/v1/dev/flags           -> the dev review queue (flagged questions)
    GET  /api/v1/leaderboard/me      -> caller's own global rank + percentile (auth)
    GET  /api/v1/leaderboard/god     -> caller's God Wars standing + within-faction board (auth)
    GET  /api/v1/user/play-history   -> per-day Daily play totals for the heatmap (auth)
    GET  /api/v1/health              -> liveness + question count
    GET  /sw.js                      -> service worker (root scope, local reminders)
    GET  /                           -> static prototype frontend
    *    (404)                       -> branded HTML page for browser navigations,
                                        plain JSON for API clients

The verified answer is computed/stored server-side and only returned AFTER a
guess is submitted — never in the question payload.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics, auth, db, seed, service
from .models import (
    AnalyticsBatch,
    AnalyticsCollectResponse,
    ArcadePairResponse,
    AuthResponse,
    ClaimRequest,
    DailyQuizResponse,
    DevFlagRequest,
    DevFlagResponse,
    GodDetailResponse,
    GodLeaderboardResponse,
    GodOverviewResponse,
    LeaderboardResponse,
    LoginRequest,
    MeResponse,
    MyRankResponse,
    PlayHistoryResponse,
    PracticeQuestionResponse,
    RegisterRequest,
    SetGodRequest,
    VerifyRequest,
    VerifyResponse,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Self-seed on boot so a fresh deploy (ephemeral filesystem) is playable with
    # no manual step. The default serves the committed, validated question bank
    # (backend/app/data/questions.json). With OSRS_DATA_SOURCE=wiki this first
    # refreshes the GE price snapshot (a cheap no-op when still fresh); only an
    # explicit OSRS_DATA_SOURCE=entities regenerates offline. The data source
    # never changes at runtime.
    conn = db.connect()
    db.init_db(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM production_trivia_questions"
    ).fetchone()["n"]
    conn.close()
    source = os.environ.get("OSRS_DATA_SOURCE", "dataset").lower()
    if count == 0 or source in seed._REAL_SOURCES or source in seed._DATASET_SOURCES:
        # refresh() is weekly-gated for the wiki source, so re-running on every
        # boot is safe and only hits the network when the snapshot is stale; the
        # dataset source just reloads the committed bank (cheap, offline).
        seed.refresh(source=source)
    # Bound the analytics log so it can't grow without limit on a long-lived DB.
    conn = db.connect()
    try:
        db.init_db(conn)
        analytics.prune(conn)
    except Exception:  # noqa: BLE001 — analytics housekeeping must never block boot
        pass
    finally:
        conn.close()
    yield


app = FastAPI(title="ScapeMaster API", version="0.1.0-prototype", lifespan=lifespan)


def get_conn():
    # One connection per request keeps the prototype simple; production swaps in a
    # pooled Postgres session.
    conn = db.connect()
    db.init_db(conn)
    return conn


def _bearer(authorization: str | None) -> str | None:
    """Pull the token out of an 'Authorization: Bearer <token>' header."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token else None


def require_user(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency: resolve the bearer token to a user or 401."""
    conn = get_conn()
    try:
        user = auth.session_user(conn, _bearer(authorization))
    finally:
        conn.close()
    if user is None:
        raise HTTPException(401, "Not signed in (missing or expired session).")
    return user


@app.get("/api/v1/health")
def health():
    conn = get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM production_trivia_questions WHERE is_active = 1"
        ).fetchone()["n"]
    finally:
        conn.close()
    return {"status": "ok", "active_questions": count}


@app.get("/api/v1/data/status")
def data_status():
    """When the Grand Exchange snapshot behind the questions was last refreshed,
    for the home-page footer. Prefers live ETL bookkeeping (OSRS_DATA_SOURCE=wiki);
    otherwise uses the committed bank's build date."""
    from . import etl
    ts = etl.last_refresh()
    if ts is not None:
        return {"refreshed_at": ts.strftime("%Y-%m-%d")}
    return {"refreshed_at": seed.dataset_meta().get("generated_at")}


@app.get("/api/v1/quiz/{mode}", response_model=DailyQuizResponse)
def quiz(mode: str):
    if mode not in service.MODE_QUESTION_COUNT:
        raise HTTPException(404, f"Unknown game mode '{mode}'.")
    conn = get_conn()
    try:
        payload = service.build_quiz(conn, game_mode=mode)
    finally:
        conn.close()
    if not payload["questions"]:
        raise HTTPException(503, "No questions provisioned. Run the seed pipeline.")
    return payload


@app.get("/api/v1/practice/question", response_model=PracticeQuestionResponse)
def practice_question():
    """Serve one random Training Grounds question. The mode is unlimited and its
    scores are never recorded (see quiz_verify), so there is no daily cap and no
    deterministic per-period seeding — every request is a fresh random draw."""
    conn = get_conn()
    try:
        payload = service.build_practice_question(conn)
    finally:
        conn.close()
    if payload is None:
        raise HTTPException(503, "No questions provisioned. Run the seed pipeline.")
    return payload


@app.post("/api/v1/quiz/verify", response_model=VerifyResponse)
def quiz_verify(req: VerifyRequest, authorization: str | None = Header(default=None)):
    result = service.verify_guess(req.tracking_token, req.guess)
    if result is None:
        raise HTTPException(404, "Unknown or expired tracking token.")
    # Persist the server-computed score (never a client number) so totals can be
    # rebuilt trustworthily. Logged in -> attach to the user; logged out -> attach
    # to the guest device id for later claim. Recording is best-effort: a storage
    # hiccup must not break scoring the guess.
    # Training Grounds is deliberately excluded: it is a non-competitive practice
    # mode whose scores must never touch a user's totals or the HiScores.
    meta = service.token_meta(req.tracking_token)
    if meta is not None and meta[1] != service.FREE_PRACTICE_MODE:
        question_id, game_mode = meta
        conn = get_conn()
        try:
            user = auth.session_user(conn, _bearer(authorization))
            auth.record_event(
                conn,
                question_id=question_id,
                score=result["score"],
                user_id=user["id"] if user else None,
                anon_id=None if user else req.anon_id,
                game_mode=game_mode,
                guess=req.guess,
                actual=result["actual"],
            )
            # Social proof: how this guess stacks up against everyone who has
            # answered the same question (computed after the insert so the player's
            # own guess is included). Best-effort — never block the score on it.
            result["insight"] = auth.question_insight(conn, question_id, result["score"])
        except Exception:  # noqa: BLE001 — scoring already succeeded; don't fail the response
            pass
        finally:
            conn.close()
    return result


# Back-compat alias for the daily-only endpoint shape.
@app.post("/api/v1/quiz/daily/verify", response_model=VerifyResponse)
def daily_verify(req: VerifyRequest, authorization: str | None = Header(default=None)):
    return quiz_verify(req, authorization)


@app.post("/api/v1/auth/register", response_model=AuthResponse)
def auth_register(req: RegisterRequest):
    conn = get_conn()
    try:
        try:
            user = auth.create_user(
                conn, req.username, req.password, req.selected_god, req.email
            )
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc))
        claimed = auth.claim_anon_events(conn, req.anon_id, user["id"])
        token = auth.create_session(conn, user["id"])
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {
        "token": token, "username": user["username"],
        "selected_god": user["selected_god"], "stats": stats,
        "claimed_events": claimed,
    }


@app.post("/api/v1/auth/login", response_model=AuthResponse)
def auth_login(req: LoginRequest):
    # Brute-force guard: too many recent failures for this username -> 429.
    try:
        auth.check_login_allowed(req.username)
    except auth.RateLimitError as exc:
        raise HTTPException(
            429, str(exc), headers={"Retry-After": str(exc.retry_after)}
        )
    conn = get_conn()
    try:
        user = auth.authenticate(conn, req.username, req.password)
        if user is None:
            auth.note_failed_login(req.username)
            raise HTTPException(401, "Incorrect username or password.")
        auth.clear_failed_logins(req.username)
        claimed = auth.claim_anon_events(conn, req.anon_id, user["id"])
        token = auth.create_session(conn, user["id"])
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {
        "token": token, "username": user["username"],
        "selected_god": user["selected_god"], "stats": stats,
        "claimed_events": claimed,
    }


@app.post("/api/v1/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)):
    conn = get_conn()
    try:
        auth.delete_session(conn, _bearer(authorization))
    finally:
        conn.close()
    return {"status": "ok"}


@app.get("/api/v1/auth/me", response_model=MeResponse)
def auth_me(user: dict = Depends(require_user)):
    conn = get_conn()
    try:
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {"username": user["username"], "selected_god": user["selected_god"], "stats": stats}


@app.post("/api/v1/sync/claim", response_model=MeResponse)
def sync_claim(req: ClaimRequest, user: dict = Depends(require_user)):
    """Merge a guest device's verified events into the signed-in account, then
    return the refreshed server-derived profile."""
    conn = get_conn()
    try:
        auth.claim_anon_events(conn, req.anon_id, user["id"])
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {"username": user["username"], "selected_god": user["selected_god"], "stats": stats}


@app.get("/api/v1/leaderboard", response_model=LeaderboardResponse)
def leaderboard(limit: int = 20, period: str = "all"):
    period = period if period in ("all", "daily", "weekly") else "all"
    conn = get_conn()
    try:
        entries = auth.leaderboard(conn, limit=max(1, min(limit, 100)), period=period)
    finally:
        conn.close()
    return {"entries": entries, "period": period}


@app.get("/api/v1/leaderboard/gods", response_model=GodLeaderboardResponse)
def god_leaderboard(period: str = "all"):
    """God Wars championship — server-verified points by god faction."""
    period = period if period in ("all", "daily", "weekly") else "all"
    conn = get_conn()
    try:
        entries = auth.god_leaderboard(conn, period=period)
    finally:
        conn.close()
    return {"entries": entries, "period": period}


@app.get("/api/v1/leaderboard/me", response_model=MyRankResponse)
def leaderboard_me(period: str = "all", user: dict = Depends(require_user)):
    """The signed-in player's own global rank + percentile for a window — the
    personal HiScores card. Movement (▲/▼) is diffed client-side."""
    period = period if period in ("all", "daily", "weekly") else "all"
    conn = get_conn()
    try:
        data = auth.my_rank(conn, user["id"], period=period)
    finally:
        conn.close()
    return {**data, "period": period}


@app.get("/api/v1/leaderboard/god", response_model=GodDetailResponse)
def leaderboard_god(period: str = "all", user: dict = Depends(require_user)):
    """The caller's personal stake in the God Wars championship: their god's
    standing plus a within-faction leaderboard with the caller located."""
    period = period if period in ("all", "daily", "weekly") else "all"
    conn = get_conn()
    try:
        data = auth.god_detail(conn, user["id"], user["selected_god"], period=period)
    finally:
        conn.close()
    return {**data, "period": period}


@app.get("/api/v1/user/play-history", response_model=PlayHistoryResponse)
def user_play_history(days: int = 126, user: dict = Depends(require_user)):
    """Per-day Daily Slayer Task play totals for the signed-in player's streak
    heatmap. Guests fall back to a localStorage history on the client."""
    conn = get_conn()
    try:
        return auth.play_history(conn, user["id"], days=days)
    finally:
        conn.close()


@app.get("/api/v1/gods/overview", response_model=GodOverviewResponse)
def gods_overview():
    """First-run god picker snapshot: every god with its registered headcount
    and all-time God Wars points. Public — it shows a newcomer how many players
    back each side and how the war is going before they pledge."""
    conn = get_conn()
    try:
        return auth.god_overview(conn)
    finally:
        conn.close()


@app.post("/api/v1/profile/god", response_model=MeResponse)
def set_god(req: SetGodRequest, user: dict = Depends(require_user)):
    """Persist the signed-in player's god faction. Cosmetic for the player, but
    it also assigns their points to a side in the God Wars championship, so it
    lives server-side rather than only in localStorage."""
    conn = get_conn()
    try:
        god = auth.set_selected_god(conn, user["id"], req.selected_god)
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {"username": user["username"], "selected_god": god, "stats": stats}


def require_dev_tools() -> None:
    """Gate the dev proofreading tools behind OSRS_DEV_TOOLS (default on). Set
    OSRS_DEV_TOOLS=0 in production to 404 every dev endpoint."""
    if os.environ.get("OSRS_DEV_TOOLS", "1").lower() in ("0", "false", "off"):
        raise HTTPException(404, "Dev tools are disabled (OSRS_DEV_TOOLS=0).")


@app.get("/api/v1/dev/questions")
def dev_questions(_: None = Depends(require_dev_tools)):
    """Development proofreading tool: the full active question bank INCLUDING the
    verified answers, so the stats can be eyeballed against the wiki. Each row
    also carries `flagged` — whether a maintainer has marked it for review (see
    POST /api/v1/dev/flag). This intentionally crosses the no-answers-to-the-
    client trust boundary — set OSRS_DEV_TOOLS=0 to switch it off."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT q.question_string, q.verified_answer, q.answer_kind, q.category, "
            "       q.game_mode, q.era_year, q.difficulty_weight, "
            "       (f.question_string IS NOT NULL) AS flagged "
            "FROM production_trivia_questions q "
            "LEFT JOIN dev_flagged_questions f "
            "       ON f.question_string = q.question_string "
            "WHERE q.is_active = 1 "
            "ORDER BY q.category, q.question_string"
        ).fetchall()
    finally:
        conn.close()
    questions = [{**dict(r), "flagged": bool(r["flagged"])} for r in rows]
    return {
        "count": len(questions),
        "flagged_count": sum(q["flagged"] for q in questions),
        "questions": questions,
    }


@app.post("/api/v1/dev/flag", response_model=DevFlagResponse)
def dev_flag(req: DevFlagRequest, _: None = Depends(require_dev_tools)):
    """Flag/unflag a question for later review. Idempotent: flagging an
    already-flagged question (or clearing an unflagged one) is a no-op. Stored by
    question_string so a flag survives the boot-time bank reseed."""
    conn = get_conn()
    try:
        exists = conn.execute(
            "SELECT 1 FROM production_trivia_questions WHERE question_string = ?",
            (req.question_string,),
        ).fetchone()
        if not exists:
            raise HTTPException(404, "No such question in the active bank.")
        if req.flagged:
            conn.execute(
                "INSERT INTO dev_flagged_questions (question_string, note) VALUES (?, ?) "
                "ON CONFLICT(question_string) DO UPDATE SET note = excluded.note",
                (req.question_string, req.note),
            )
        else:
            conn.execute(
                "DELETE FROM dev_flagged_questions WHERE question_string = ?",
                (req.question_string,),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) AS n FROM dev_flagged_questions").fetchone()["n"]
    finally:
        conn.close()
    return {"question_string": req.question_string, "flagged": req.flagged, "flagged_count": total}


@app.get("/api/v1/dev/flags")
def dev_flags(_: None = Depends(require_dev_tools)):
    """The review queue: every flagged question (newest first), for triage or to
    drive a follow-up cull of the committed bank."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT question_string, note, created_at "
            "FROM dev_flagged_questions ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return {"count": len(rows), "flags": [dict(r) for r in rows]}


@app.get("/api/v1/arcade/pair", response_model=ArcadePairResponse)
def arcade_pair():
    conn = get_conn()
    try:
        return service.build_arcade_pair(conn)
    finally:
        conn.close()


# ── Analytics ────────────────────────────────────────────────────────────────
@app.post("/api/v1/analytics/collect", response_model=AnalyticsCollectResponse)
def analytics_collect(batch: AnalyticsBatch, authorization: str | None = Header(default=None)):
    """Ingest a batch of pseudonymous client events (sendBeacon-friendly). Public
    and best-effort: malformed/unknown events are dropped, the batch is bounded,
    and any failure is swallowed so analytics can never break gameplay. Events are
    attributed to the signed-in user when a bearer token is present, else to the
    guest anon_id only — this is AGGREGATE telemetry, never scoring input."""
    conn = get_conn()
    try:
        user = auth.session_user(conn, _bearer(authorization))
        stored = analytics.record_events(
            conn,
            events=[e.model_dump() for e in batch.events],
            anon_id=batch.anon_id,
            session_id=batch.session_id,
            user_id=user["id"] if user else None,
        )
    except Exception:  # noqa: BLE001 — telemetry must never surface an error to the client
        stored = 0
    finally:
        conn.close()
    return {"stored": stored}


def require_analytics(authorization: str | None = Header(default=None)) -> None:
    """Gate the analytics reporting on OSRS_ANALYTICS_TOKEN. Unset -> the dashboard
    is disabled (404, like dev tools); set -> require a matching bearer token."""
    if not analytics.token_configured():
        raise HTTPException(404, "Analytics dashboard is disabled (set OSRS_ANALYTICS_TOKEN).")
    if not analytics.token_matches(_bearer(authorization)):
        raise HTTPException(401, "Invalid analytics token.")


@app.get("/api/v1/analytics/summary")
def analytics_summary(days: int = 14, _: None = Depends(require_analytics)):
    """Engagement report: DAU/WAU/MAU, the play funnel, D1/D7 retention, mode mix
    and account growth. Token-gated; combines client events with server-verified
    play_events. Returns a plain dict (rich nested shape) by design."""
    conn = get_conn()
    try:
        return analytics.summary(conn, days=days)
    finally:
        conn.close()


@app.get("/analytics")
def analytics_dashboard():
    """The dashboard viewer page. Harmless without a token — the data behind it is
    fetched from the token-gated summary endpoint."""
    page = FRONTEND_DIR / "analytics.html"
    if not page.exists():
        raise HTTPException(404, "Dashboard page not found.")
    return FileResponse(page)


@app.get("/sw.js")
def service_worker():
    """Serve the service worker from the root path so its scope covers the whole
    app (a worker served under /static would only control /static). The
    Service-Worker-Allowed header lets it claim the '/' scope explicitly."""
    sw = FRONTEND_DIR / "sw.js"
    if not sw.exists():
        raise HTTPException(404, "Service worker not found.")
    return FileResponse(
        sw,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.exception_handler(404)
async def not_found(request: Request, exc: HTTPException):
    """A branded 404 for stray browser navigations; JSON for everything else.

    Only a real browser page request (Accept: text/html on a non-API path) gets
    the styled page — API clients and the test suite keep the plain JSON body, so
    the trust boundary and error contracts are untouched."""
    wants_html = "text/html" in request.headers.get("accept", "")
    if wants_html and not request.url.path.startswith("/api"):
        page = FRONTEND_DIR / "404.html"
        if page.exists():
            return HTMLResponse(page.read_text(encoding="utf-8"), status_code=404)
    return JSONResponse(
        {"detail": getattr(exc, "detail", "Not Found")},
        status_code=404,
        headers=getattr(exc, "headers", None),
    )


# Serve the rest of the static prototype frontend (css/js).
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
