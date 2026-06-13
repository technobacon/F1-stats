"""Pydantic API models.

These enforce the request/response contracts at the API boundary. Note what is
deliberately ABSENT from the client-facing models: the verified answer. It never
leaves the server (PRD §2, Architecture §1.1).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DailyQuestion(BaseModel):
    """A single client-facing question. NO answer field — by design."""
    tracking_token: str = Field(..., description="Opaque server-side handle for scoring")
    question_text: str
    difficulty_weight: float
    answer_kind: str = "count"   # 'count' | 'points' | 'year' | 'percentage'
    category: str = ""           # UI grouping hint, e.g. 'reliability'
    # Optional UI hint for the odometer slider bounds (Architecture §3.2). The
    # true answer is NOT derivable from these bounds.
    slider_min: float
    slider_max: float


class DailyQuizResponse(BaseModel):
    game_mode: str
    questions: list[DailyQuestion]


class PracticeQuestionResponse(BaseModel):
    """A single Free Practice question. Same trust boundary as the daily set — no
    answer field — but served one at a time and never recorded server-side."""
    game_mode: str
    question: DailyQuestion


class VerifyRequest(BaseModel):
    tracking_token: str
    guess: float
    # Optional client-generated guest device id, so a guess made while logged out
    # is recorded server-side and can be claimed on sign-in (Architecture §2.2).
    anon_id: str | None = None


class VerifyResponse(BaseModel):
    score: int = Field(..., ge=0, le=5000)
    actual: float = Field(..., description="True value, revealed only AFTER the guess")
    guess: float
    max_score: int = 5000


class RegisterRequest(BaseModel):
    username: str
    password: str
    # Guest device id to merge into the new account (verified events only).
    anon_id: str | None = None
    # The constructor faction the player pledges to (PRD §5.3). Optional; an
    # unknown value is normalized to the default rather than rejected.
    selected_team: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str
    anon_id: str | None = None


class UserStats(BaseModel):
    lifetime_points: int
    questions_answered: int
    average_accuracy: float   # mean proximity in [0, 1]
    best_answer: int
    daily_streak: int = 0     # consecutive days completing a daily challenge


class AuthResponse(BaseModel):
    """Returned by register/login: the session token plus the server-derived
    profile. The token is an opaque bearer credential — store it and send it as
    'Authorization: Bearer <token>'."""
    token: str
    username: str
    selected_team: str
    stats: UserStats
    claimed_events: int = 0


class MeResponse(BaseModel):
    username: str
    selected_team: str
    stats: UserStats


class ClaimRequest(BaseModel):
    anon_id: str


class SetTeamRequest(BaseModel):
    selected_team: str


class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    selected_team: str
    lifetime_points: int
    questions_answered: int


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    period: str = "all"


class TeamLeaderboardEntry(BaseModel):
    rank: int
    team: str
    points: int
    members: int
    questions_answered: int
    avg_per_member: int


class TeamLeaderboardResponse(BaseModel):
    entries: list[TeamLeaderboardEntry]
    period: str = "all"


class AnalyticsEvent(BaseModel):
    event: str
    # Accept anything for props so one malformed field can't 422 a whole batch;
    # analytics._clean_props sanitizes it (non-dicts / oversized blobs are dropped).
    props: Any = None
    t: int | None = None   # client timestamp (ms); informational, server time is authoritative


class AnalyticsBatch(BaseModel):
    """A best-effort batch of pseudonymous client events (see analytics.py). Keyed
    by the guest anon_id and a per-tab session id — no PII, no third party."""
    anon_id: str | None = None
    session_id: str | None = None
    events: list[AnalyticsEvent] = []


class AnalyticsCollectResponse(BaseModel):
    stored: int


class ArcadeEntity(BaseModel):
    driver_id: str
    full_name: str
    value: float


class ArcadePairResponse(BaseModel):
    metric: str
    metric_label: str
    entity_a: ArcadeEntity
    entity_b: ArcadeEntity
    # v1 is non-competitive / client-evaluated (Architecture §1.2), so values are
    # returned directly. The shape is forward-compatible with a future
    # server-validated pick endpoint.
