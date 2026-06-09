"""FastAPI application (Architecture §1).

Endpoints:
    GET  /api/v1/quiz/daily          -> 10 questions, NO answers, tracking tokens
    POST /api/v1/quiz/daily/verify   -> server-side score for one guess
    GET  /api/v1/arcade/pair         -> over/under matchup (non-competitive v1)
    GET  /api/v1/health              -> liveness + question count
    GET  /                           -> static prototype frontend

The verified answer is computed/stored server-side and only returned AFTER a
guess is submitted — never in the question payload.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, seed, service
from .models import ArcadePairResponse, DailyQuizResponse, VerifyRequest, VerifyResponse

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Self-seed on boot so a fresh deploy (ephemeral filesystem) is playable with
    # no manual step. With F1_DATA_SOURCE=jolpica this pulls the real, cached,
    # weekly ETL (and is a cheap no-op when the data is still fresh); otherwise it
    # builds the synthetic fallback. The data source never changes at runtime.
    conn = db.connect()
    db.init_db(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM production_trivia_questions"
    ).fetchone()["n"]
    conn.close()
    source = os.environ.get("F1_DATA_SOURCE", "synthetic").lower()
    if count == 0 or source in seed._REAL_SOURCES:
        # refresh() is weekly-gated for the real source, so re-running on every
        # boot is safe and only hits the network when the data is stale.
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
