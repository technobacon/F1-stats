"""Pydantic API models.

These enforce the request/response contracts at the API boundary. Note what is
deliberately ABSENT from the client-facing models: the verified answer. It never
leaves the server.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DailyQuestion(BaseModel):
    """A single client-facing question. NO answer field — by design."""
    tracking_token: str = Field(..., description="Opaque server-side handle for scoring")
    question_text: str
    difficulty_weight: float
    answer_kind: str = "count"   # 'count' | 'level' | 'xp' | 'coins' | 'year' | 'percentage'
    category: str = ""           # UI grouping hint: 'item' | 'monster' | 'quest' | 'skill'
    # Optional UI hint for the slider bounds. coins/xp kinds are rendered on a
    # log scale client-side. The true answer is NOT derivable from these bounds.
    slider_min: float
    slider_max: float


class DailyQuizResponse(BaseModel):
    game_mode: str
    questions: list[DailyQuestion]


class PracticeQuestionResponse(BaseModel):
    """A single Training Grounds question. Same trust boundary as the daily set —
    no answer field — but served one at a time and never recorded server-side."""
    game_mode: str
    question: DailyQuestion
    # False when a requested focus (?category= / ?era=) matched nothing and the
    # question was drawn from the full bank instead, so the client can say so.
    focus_matched: bool = True


class VerifyRequest(BaseModel):
    tracking_token: str
    guess: float
    # Optional client-generated guest device id, so a guess made while logged out
    # is recorded server-side and can be claimed on sign-in.
    anon_id: str | None = None


class QuestionInsight(BaseModel):
    """Social proof for the question just answered (auth.question_insight): how
    many players have answered it, the average score, and the percentage of them
    this guess beat. Built from server-scored events, so it can't be gamed."""
    players_answered: int
    average_score: int
    beat_percent: int = Field(..., ge=0, le=100)


class VerifyResponse(BaseModel):
    score: int = Field(..., ge=0, le=5000)
    actual: float = Field(..., description="True value, revealed only AFTER the guess")
    guess: float
    max_score: int = 5000
    # True when the Wise Old Man was consulted for this question — the returned
    # score already has his fee taken off (service.HINT_COST).
    hint_used: bool = False
    # Aggregate comparison vs. other players. Present only for recorded competitive
    # modes once enough players have answered; None for Training Grounds and new
    # questions (it is never derivable into the answer).
    insight: QuestionInsight | None = None


class HintRequest(BaseModel):
    tracking_token: str


class HintResponse(BaseModel):
    """The Wise Old Man's reply: a band guaranteed to contain the answer, wide
    enough that its midpoint is no better than an informed guess. Requesting it
    marks the token, and verify() takes cost_percent off the eventual score."""
    hint_min: float
    hint_max: float
    cost_percent: int
    max_score_after: int


class DailyFieldResponse(BaseModel):
    """Where the caller finished among everyone (members AND guests) who played
    today's Slayer Task. rank is 0 when the caller hasn't scored today."""
    players: int
    rank: int
    points: int
    beat_percent: int = Field(..., ge=0, le=100)


class RegisterRequest(BaseModel):
    username: str
    password: str
    # Guest device id to merge into the new account (verified events only).
    anon_id: str | None = None
    # The god faction the player pledges to in the God Wars championship.
    # Optional; an unknown value is normalized to the default rather than rejected.
    selected_god: str | None = None
    # OPTIONAL email for future opt-in streak/daily reminders. Blank/None is fine;
    # a malformed non-empty value is rejected with a 400.
    email: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str
    anon_id: str | None = None


class UserStats(BaseModel):
    lifetime_points: int
    questions_answered: int
    average_accuracy: float   # mean proximity in [0, 1]
    best_answer: int
    daily_streak: int = 0     # consecutive days completing a Daily Slayer Task


class AuthResponse(BaseModel):
    """Returned by register/login: the session token plus the server-derived
    profile. The token is an opaque bearer credential — store it and send it as
    'Authorization: Bearer <token>'."""
    token: str
    username: str
    selected_god: str
    stats: UserStats
    claimed_events: int = 0


class MeResponse(BaseModel):
    username: str
    selected_god: str
    stats: UserStats


class ClaimRequest(BaseModel):
    anon_id: str


class SetGodRequest(BaseModel):
    selected_god: str


class LeaderboardEntry(BaseModel):
    rank: int
    username: str
    selected_god: str
    lifetime_points: int
    questions_answered: int


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry]
    period: str = "all"


class GodLeaderboardEntry(BaseModel):
    rank: int
    god: str
    points: int
    members: int
    questions_answered: int
    avg_per_member: int


class GodLeaderboardResponse(BaseModel):
    entries: list[GodLeaderboardEntry]
    period: str = "all"


class GodOverviewEntry(BaseModel):
    rank: int
    god: str
    members: int   # registered players who pledged to this god
    points: int    # server-verified God Wars points (all-time)


class GodOverviewResponse(BaseModel):
    """Per-god headcount + all-time standings for the first-run god picker.
    Lists EVERY god (including empty ones), unlike the championship board."""
    gods: list[GodOverviewEntry]
    total_players: int


class MyRankResponse(BaseModel):
    """The signed-in player's own position on the global HiScores for a window.
    rank/points are 0 when they haven't scored in the window yet."""
    rank: int
    total_ranked: int
    points: int
    percentile: int
    period: str = "all"


class GodMemberEntry(BaseModel):
    rank: int
    username: str
    points: int


class GodDetailResponse(BaseModel):
    """The caller's personal stake in the God Wars championship: their god's
    standing plus a within-faction leaderboard."""
    god: str
    god_rank: int
    god_points: int
    members: int
    your_points: int
    your_rank_in_god: int
    leaders: list[GodMemberEntry]
    period: str = "all"


class PlayHistoryDay(BaseModel):
    date: str          # ISO 'YYYY-MM-DD'
    questions: int
    points: int


class PlayHistoryResponse(BaseModel):
    """Per-day Daily Slayer Task play totals for the streak heatmap."""
    days: list[PlayHistoryDay]


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
    entity_id: str
    full_name: str
    value: float


class ArcadePairResponse(BaseModel):
    metric: str
    metric_label: str
    entity_a: ArcadeEntity
    entity_b: ArcadeEntity
    # v1 is non-competitive / client-evaluated, so values are returned directly.
    # The shape is forward-compatible with a future server-validated pick endpoint.


class DevFlagRequest(BaseModel):
    """Flag (or unflag) a question from the dev proofreading tool. Identified by
    its text, which is the stable key across the boot-time bank reseed."""
    question_string: str
    flagged: bool = True
    note: str | None = None   # optional reason, e.g. "too obscure"


class DevFlagResponse(BaseModel):
    question_string: str
    flagged: bool
    flagged_count: int   # total flags now in the review queue
