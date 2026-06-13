"""FastAPI application (Architecture §1).

Endpoints:
    GET  /api/v1/quiz/daily          -> 6 questions, NO answers, tracking tokens
    POST /api/v1/quiz/daily/verify   -> server-side score for one guess
    GET  /api/v1/practice/question   -> one random Free Practice question (unlimited,
                                        non-competitive; score is never recorded)
    GET  /api/v1/arcade/pair         -> over/under matchup (non-competitive v1)
    GET  /api/v1/dev/questions       -> full question bank WITH answers (proofreading
                                        tool; disable with F1_DEV_TOOLS=0)
    GET  /api/v1/health              -> liveness + question count
    GET  /                           -> static prototype frontend

The verified answer is computed/stored server-side and only returned AFTER a
guess is submitted — never in the question payload.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import auth, db, seed, service
from .models import (
    ArcadePairResponse,
    AuthResponse,
    ClaimRequest,
    DailyQuizResponse,
    LeaderboardResponse,
    LoginRequest,
    MeResponse,
    PracticeQuestionResponse,
    RegisterRequest,
    VerifyRequest,
    VerifyResponse,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Self-seed on boot so a fresh deploy (ephemeral filesystem) is playable with
    # no manual step. The default serves the committed, validated real-data bank
    # (backend/app/data/questions.json). With F1_DATA_SOURCE=jolpica this instead
    # pulls the real, cached, weekly ETL (a cheap no-op when still fresh); only an
    # explicit F1_DATA_SOURCE=synthetic falls back to the placeholder seed. The
    # data source never changes at runtime.
    conn = db.connect()
    db.init_db(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM production_trivia_questions"
    ).fetchone()["n"]
    conn.close()
    source = os.environ.get("F1_DATA_SOURCE", "dataset").lower()
    if count == 0 or source in seed._REAL_SOURCES or source in seed._DATASET_SOURCES:
        # refresh() is weekly-gated for the real source, so re-running on every
        # boot is safe and only hits the network when the data is stale; the
        # dataset source just reloads the committed bank (cheap, offline).
        seed.refresh(source=source)
    yield


app = FastAPI(title="F1 StatGuesser API", version="0.1.0-prototype", lifespan=lifespan)


def get_conn():
    # One connection per request keeps the prototype simple; production swaps in a
    # pooled Postgres session (Architecture §0).
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
    """Serve one random Free Practice question. The mode is unlimited and its
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
    # rebuilt trustworthily (Architecture §2.2). Logged in -> attach to the user;
    # logged out -> attach to the guest device id for later claim. Recording is
    # best-effort: a storage hiccup must not break scoring the guess.
    # Free Practice is deliberately excluded: it is a non-competitive training mode
    # whose scores must never touch a user's totals or the leaderboard.
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
        except Exception:  # noqa: BLE001 — scoring already succeeded; don't fail the response
            pass
        finally:
            conn.close()
    return result


# Back-compat aliases for the original daily-only endpoints.
@app.post("/api/v1/quiz/daily/verify", response_model=VerifyResponse)
def daily_verify(req: VerifyRequest, authorization: str | None = Header(default=None)):
    return quiz_verify(req, authorization)


@app.post("/api/v1/auth/register", response_model=AuthResponse)
def auth_register(req: RegisterRequest):
    conn = get_conn()
    try:
        try:
            user = auth.create_user(conn, req.username, req.password)
        except auth.AuthError as exc:
            raise HTTPException(400, str(exc))
        claimed = auth.claim_anon_events(conn, req.anon_id, user["id"])
        token = auth.create_session(conn, user["id"])
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {
        "token": token, "username": user["username"],
        "selected_team": user["selected_team"], "stats": stats,
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
        "selected_team": user["selected_team"], "stats": stats,
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
    return {"username": user["username"], "selected_team": user["selected_team"], "stats": stats}


@app.post("/api/v1/sync/claim", response_model=MeResponse)
def sync_claim(req: ClaimRequest, user: dict = Depends(require_user)):
    """Merge a guest device's verified events into the signed-in account, then
    return the refreshed server-derived profile (Architecture §2.2)."""
    conn = get_conn()
    try:
        auth.claim_anon_events(conn, req.anon_id, user["id"])
        stats = auth.user_stats(conn, user["id"])
    finally:
        conn.close()
    return {"username": user["username"], "selected_team": user["selected_team"], "stats": stats}


@app.get("/api/v1/leaderboard", response_model=LeaderboardResponse)
def leaderboard(limit: int = 20):
    conn = get_conn()
    try:
        entries = auth.leaderboard(conn, limit=max(1, min(limit, 100)))
    finally:
        conn.close()
    return {"entries": entries}


@app.get("/api/v1/dev/questions")
def dev_questions():
    """Development proofreading tool: the full active question bank INCLUDING the
    verified answers, so the stats can be eyeballed against the record books.
    This intentionally crosses the no-answers-to-the-client trust boundary —
    set F1_DEV_TOOLS=0 in production to switch it off."""
    if os.environ.get("F1_DEV_TOOLS", "1").lower() in ("0", "false", "off"):
        raise HTTPException(404, "Dev tools are disabled (F1_DEV_TOOLS=0).")
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT question_string, verified_answer, answer_kind, category, "
            "       game_mode, era_year, difficulty_weight "
            "FROM production_trivia_questions WHERE is_active = 1 "
            "ORDER BY category, question_string"
        ).fetchall()
    finally:
        conn.close()
    return {"count": len(rows), "questions": [dict(r) for r in rows]}


@app.get("/api/v1/arcade/pair", response_model=ArcadePairResponse)
def arcade_pair():
    conn = get_conn()
    try:
        return service.build_arcade_pair(conn)
    finally:
        conn.close()


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Serve the rest of the static prototype frontend (css/js).
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
