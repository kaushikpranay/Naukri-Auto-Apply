"""
Data models for POC-3B Phase 2 — form auto-fill results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FieldFillResult:
    """Result for a single field fill attempt."""

    question_key: str
    question_text: str
    field_type: str
    required: bool
    # status values:
    #   "filled"           — field was successfully filled
    #   "skipped_dry_run"  — would have been filled but DRY_RUN=True
    #   "unknown"          — no answer in question bank
    #   "error"            — fill attempted but failed
    status: str
    answer_used: str | None = None
    error: str | None = None
    answer_source: str | None = None  # "AUTO" or "USER_LEARNED"


@dataclass
class FormFillReport:
    """Aggregated fill report for one job's application form."""

    job_id: int
    company: str
    role: str
    dry_run: bool
    filled: list[FieldFillResult] = field(default_factory=list)
    unknown: list[FieldFillResult] = field(default_factory=list)
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    filled_at: datetime = field(default_factory=datetime.now)

    @property
    def total_fields(self) -> int:
        return len(self.filled) + len(self.unknown)

    @property
    def fill_rate_pct(self) -> float:
        if self.total_fields == 0:
            return 0.0
        successful = sum(1 for f in self.filled if f.status in ("filled", "skipped_dry_run"))
        return round(successful / self.total_fields * 100, 1)
