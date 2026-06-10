"""
app/models/job.py
Pydantic models for job data and collection summary.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class JobData(BaseModel):
    """Represents a single job listing collected from Naukri."""

    id: int | None = Field(default=None, description="Database row identifier")
    job_title: str = Field(..., description="Job title / role name")
    company_name: str = Field(..., description="Hiring company name")
    job_description: str = Field(default="", description="Full job description text")
    job_url: str = Field(..., description="Original job listing URL")
    normalized_url: str = Field(default="", description="Cleaned URL without tracking params")
    apply_url: str = Field(default="", description="Direct apply URL")
    experience_required: str = Field(default="", description="Experience requirement string")
    location: str = Field(default="", description="Job location")
    posted_date: str = Field(default="", description="When the job was posted")
    recruiter_name: str = Field(default="", description="Recruiter / contact name")
    recruiter_email: str = Field(default="", description="Recruiter email address")
    status: str = Field(default="pending", description="Queue state for evaluation")
    retry_count: int = Field(default=0, description="Number of failed evaluation attempts")
    search_keyword: str | None = Field(
        default=None,
        description="Search keyword used to collect the job",
    )
    search_location: str | None = Field(
        default=None,
        description="Search location used to collect the job",
    )


class CollectionSummary(BaseModel):
    """Summary statistics for a collection run."""

    jobs_found: int = Field(default=0, description="Total jobs found across all searches")
    jobs_inserted: int = Field(default=0, description="New jobs inserted into database")
    duplicates_skipped: int = Field(default=0, description="Jobs skipped due to dedup")
    export_status: str = Field(default="Pending", description="Excel export result")
    export_path: str = Field(default="", description="Path to exported file")
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = Field(default=None)

    def print_summary(self) -> str:
        """Format summary for CLI output."""
        lines: list[str] = [
            "",
            "=" * 50,
            "  COLLECTION SUMMARY",
            "=" * 50,
            f"  Jobs Found:    {self.jobs_found}",
            f"  Inserted:      {self.jobs_inserted}",
            f"  Duplicates:    {self.duplicates_skipped}",
            f"  Export:        {self.export_status}",
        ]
        if self.export_path:
            lines.append(f"  Export File:   {self.export_path}")
        lines.append("=" * 50)
        return "\n".join(lines)
