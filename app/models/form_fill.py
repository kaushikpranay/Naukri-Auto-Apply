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


class FailureType:
    TIMEOUT = "TIMEOUT"
    UNRECOGNIZED_QUESTION = "UNRECOGNIZED_QUESTION"
    DOM_ELEMENT_NOT_FOUND = "DOM_ELEMENT_NOT_FOUND"
    SESSION_DROP = "SESSION_DROP"
    INFINITE_DRAWER_LOOP = "INFINITE_DRAWER_LOOP"
    OTHER = "OTHER"


class FailureCategory:
    TRANSIENT = "TRANSIENT"
    DETERMINISTIC = "DETERMINISTIC"


TRANSIENT_FAILURE_TYPES = {
    FailureType.TIMEOUT,
    FailureType.SESSION_DROP,
}


def classify_failure_category(failure_type: str | None) -> str:
    """Classify failure type as TRANSIENT or DETERMINISTIC."""
    if not failure_type:
        return FailureCategory.DETERMINISTIC
    norm = str(failure_type).upper().strip()
    return FailureCategory.TRANSIENT if norm in TRANSIENT_FAILURE_TYPES else FailureCategory.DETERMINISTIC


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
    error_message: str | None = None
    failure_type: str | None = None
    failure_category: str | None = None
    status: str = "ok"  # "ok" or "error"
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
