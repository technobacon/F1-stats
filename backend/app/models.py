"""Pydantic API models.

These enforce the request/response contracts at the API boundary. Note what is
deliberately ABSENT from the client-facing models: the verified answer. It never
leaves the server (PRD §2, Architecture §1.1).
"""

from __future__ import annotations

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


class VerifyRequest(BaseModel):
    tracking_token: str
    guess: float


class VerifyResponse(BaseModel):
    score: int = Field(..., ge=0, le=5000)
    actual: float = Field(..., description="True value, revealed only AFTER the guess")
    guess: float
    max_score: int = 5000


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
