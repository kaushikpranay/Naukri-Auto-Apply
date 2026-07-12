"""
app/ats/models.py
Data models for ATS auto-apply results.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AtsApplicationRecord(BaseModel):
    """Result of an ATS apply attempt for a single job."""

    id: int | None = None
    job_id: int
    ats_type: str
    apply_url: str
    status: str = "pending"          # pending | applied | failed | skipped
    error: str | None = None
    screenshot_path: str | None = None
    attempted_at: str | None = None
    applied_at: str | None = None


class AtsSummary(BaseModel):
    """Aggregate counters returned by the ATS runner."""

    processed: int = 0
    applied: int = 0
    failed: int = 0
    skipped: int = 0
    by_ats: dict[str, int] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
