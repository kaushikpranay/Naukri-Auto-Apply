"""
Models for apply discovery.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class ApplicationDiscoveryRecord(BaseModel):
    """Persisted application-discovery result."""

    id: int | None = None
    job_id: int
    apply_type: str | None = None
    apply_url: str | None = None
    email: str | None = None
    hr_name: str | None = None
    button_text: str | None = None
    button_selector: str | None = None
    url_before: str | None = None
    url_after: str | None = None
    redirect_count: int = 0
    redirect_chain: str | None = None
    status: str = "discovered"
    screenshot_before: str | None = None
    screenshot_after: str | None = None
    screenshot_modal: str | None = None
    html_before_path: str | None = None
    html_path: str | None = None
    elements_path: str | None = None
    detected_at: str | None = None


class DiscoveredQuestion(BaseModel):
    """Question found during apply discovery."""

    question_key: str
    question_text: str
    field_type: str
    required: bool = False
    answer: str | None = None


class DiscoverySummary(BaseModel):
    """Summary counters for discovery runs."""

    processed: int = 0
    discovered: int = 0
    already_applied: int = 0
    requires_review: int = 0
    failed: int = 0
    easy_apply: int = 0
    external_portal: int = 0
    email: int = 0
    needs_register: int = 0
    login_required: int = 0
    unknown_flow: int = 0
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
