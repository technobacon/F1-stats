"""FastAPI application (Architecture §1).

Endpoints:
    GET  /api/v1/quiz/daily          -> 6 questions, NO answers, tracking tokens
    POST /api/v1/quiz/daily/verify   -> server-side score for one guess
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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, seed, service
from .models import ArcadePairResponse, DailyQuizResponse, VerifyRequest, VerifyResponse

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
def quiz(mode: str, circuit: str | None = Query(default=None)):
    if mode not in service.MODE_QUESTION_COUNT:
        raise HTTPException(404, f"Unknown game mode '{mode}'.")
    conn = get_conn()
    try:
        payload = service.build_quiz(conn, game_mode=mode, circuit_id=circuit)
    finally:
        conn.close()
    if not payload["questions"]:
        raise HTTPException(503, "No questions provisioned. Run the seed pipeline.")
    return payload


@app.post("/api/v1/quiz/verify", response_model=VerifyResponse)
def quiz_verify(req: VerifyRequest):
    result = service.verify_guess(req.tracking_token, req.guess)
    if result is None:
        raise HTTPException(404, "Unknown or expired tracking token.")
    return result


# Back-compat aliases for the original daily-only endpoints.
@app.post("/api/v1/quiz/daily/verify", response_model=VerifyResponse)
def daily_verify(req: VerifyRequest):
    return quiz_verify(req)


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
