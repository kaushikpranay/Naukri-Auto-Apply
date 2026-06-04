"""
Data model and builder for POC-3C — Application Review Mode.

An ApplicationReviewRecord is derived from a FormFillReport.
It adds the review-specific interpretation layer:
    - required_fields_missing
    - ready_to_submit
    - values_used   (deduplicated key → answer map)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.models.form_fill import FieldFillResult, FormFillReport


@dataclass
class ApplicationReviewRecord:
    """
    Full review state for one job application.

    Produced by ``build_review_record(form_fill_report)``.
    """

    job_id: int
    job_title: str
    company: str

    # Filled / would-be-filled fields (DRY_RUN or LIVE)
    filled_fields: list[FieldFillResult] = field(default_factory=list)
    # Fields with no stored answer
    unknown_fields: list[FieldFillResult] = field(default_factory=list)
    # Required fields that are in unknown_fields — blocking submission
    required_fields_missing: list[FieldFillResult] = field(default_factory=list)
    # Deduplicated key → answer map for all filled fields
    values_used: dict[str, str] = field(default_factory=dict)

    # Core verdict
    ready_to_submit: bool = False
    dry_run: bool = True

    # Artifact paths
    screenshot_final_state: str | None = None
    reviewed_at: datetime = field(default_factory=datetime.now)

    # ── Derived properties ──────────────────────────────────────────────────

    @property
    def filled_count(self) -> int:
        return len(self.filled_fields)

    @property
    def unknown_count(self) -> int:
        return len(self.unknown_fields)

    @property
    def missing_required_count(self) -> int:
        return len(self.required_fields_missing)

    @property
    def total_fields(self) -> int:
        return self.filled_count + self.unknown_count

    @property
    def fill_rate_pct(self) -> float:
        if self.total_fields == 0:
            return 0.0
        return round(self.filled_count / self.total_fields * 100, 1)

    @property
    def ready_to_submit_label(self) -> str:
        return "YES" if self.ready_to_submit else "NO"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_review_record(report: FormFillReport) -> ApplicationReviewRecord:
    """
    Derive an ApplicationReviewRecord from a FormFillReport.

    Logic:
        ready_to_submit = True  iff
            (a) at least one field was detected, AND
            (b) no required field is in the unknown list or has an error status
    """
    required_missing = [f for f in report.unknown if f.required]
    required_missing.extend([f for f in report.filled if f.required and f.status == "error"])
    values_used = {
        f.question_key: f.answer_used
        for f in report.filled
        if f.answer_used and f.status in ("filled", "skipped_dry_run")
    }
    ready = report.total_fields > 0 and len(required_missing) == 0

    return ApplicationReviewRecord(
        job_id=report.job_id,
        job_title=report.role,
        company=report.company,
        filled_fields=[f for f in report.filled if f.status in ("filled", "skipped_dry_run")],
        unknown_fields=report.unknown + [f for f in report.filled if f.status == "error"],
        required_fields_missing=required_missing,
        values_used=values_used,
        ready_to_submit=ready,
        dry_run=report.dry_run,
        screenshot_final_state=report.screenshot_after,
        reviewed_at=report.filled_at,
    )
