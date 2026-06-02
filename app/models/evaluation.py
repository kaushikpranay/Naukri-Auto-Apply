"""
Pydantic models for AI-based job analysis.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    """
    Canonical structured result expected from AI providers.
    """

    interview_probability: int = Field(
        ...,
        ge=0,
        le=100,
        description="Interview probability as a percentage from 0 to 100.",
    )
    recommended_resume: Literal["GENAI", "APPLIED_AI", "PYTHON", "ML", "STARTUP"]
    priority: Literal["high", "medium", "low"]
    action: Literal["apply", "review", "skip"]
    confidence: int = Field(
        ...,
        ge=0,
        le=100,
        description="Confidence score as a percentage from 0 to 100.",
    )
    reason: str = Field(..., min_length=1)
    missing_skills: list[str] = Field(default_factory=list)


class JobEvaluation(BaseModel):
    """
    Internal model representing a stored evaluation record.
    """

    id: int | None = None
    job_id: int
    run_id: str
    model_name: str
    prompt_version: str

    interview_probability: int
    recommended_resume: str
    priority: str
    action: str
    confidence: int
    reason: str
    missing_skills: list[str]

    created_at: str | None = None
