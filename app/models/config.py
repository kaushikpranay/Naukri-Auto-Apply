"""
Pydantic models for application configuration.
"""

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# settings.yaml models
# ---------------------------------------------------------------------------

class BrowserConfig(BaseModel):
    """Browser launch configuration."""

    headless: bool = Field(default=False)
    profile_dir: str = Field(default="browser_profile")
    slow_mo: int = Field(default=500)
    default_timeout: int = Field(default=30000)
    viewport_width: int = Field(default=1920)
    viewport_height: int = Field(default=1080)


class PathsConfig(BaseModel):
    """Project path configuration."""

    database: str = Field(default="database/jobs.db")
    exports: str = Field(default="exports")
    logs: str = Field(default="logs")
    screenshots: str = Field(default="screenshots")


class NaukriConfig(BaseModel):
    """Naukri-specific settings."""

    base_url: str = Field(default="https://www.naukri.com")
    search_url_template: str = Field(
        default="https://www.naukri.com/{keyword}-jobs-in-{location}?k={keyword_raw}"
    )
    results_per_page: int = Field(default=20)
    max_pages_per_search: int = Field(default=5)
    page_load_wait: int = Field(default=3000)
    detail_load_wait: int = Field(default=2000)


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="INFO")
    rotation: str = Field(default="10 MB")
    retention: str = Field(default="30 days")
    format: str = Field(
        default="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {module}:{function}:{line} | {message}"
    )


class EvaluationConfig(BaseModel):
    """Evaluation queue limits."""

    max_ai_evaluations_per_run: int = Field(default=5)
    max_retry_count: int = Field(default=3)


class AppSettings(BaseModel):
    """Root settings model loaded from settings.yaml."""

    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    naukri: NaukriConfig = Field(default_factory=NaukriConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)


# ---------------------------------------------------------------------------
# selectors.yaml models
# ---------------------------------------------------------------------------

class LoginSelectors(BaseModel):
    """Selectors for login page detection."""

    detection: str = Field(...)
    logged_in: str = Field(...)


class SearchResultSelectors(BaseModel):
    """Selectors for search results page."""

    container: str = Field(...)
    job_card: str = Field(...)
    title: str = Field(...)
    company: str = Field(...)
    experience: str = Field(...)
    location: str = Field(...)
    posted_date: str = Field(...)
    no_results: str = Field(...)


class PaginationSelectors(BaseModel):
    """Selectors for pagination controls."""

    next_button: str = Field(...)
    current_page: str = Field(...)


class JobDetailSelectors(BaseModel):
    """Selectors for job detail page."""

    description: str = Field(...)
    apply_button: str = Field(...)
    recruiter_section: str = Field(...)
    recruiter_name: str = Field(...)
    recruiter_email: str = Field(...)


class SelectorsConfig(BaseModel):
    """Root selectors model loaded from selectors.yaml."""

    login: LoginSelectors
    search_results: SearchResultSelectors
    pagination: PaginationSelectors
    job_detail: JobDetailSelectors


# ---------------------------------------------------------------------------
# locations.yaml models
# ---------------------------------------------------------------------------

class KeywordEntry(BaseModel):
    """A search keyword with its URL slug."""

    display: str = Field(..., description="Human-readable keyword")
    slug: str = Field(..., description="URL-safe slug for Naukri search")


class LocationEntry(BaseModel):
    """A search location with its URL slug."""

    display: str = Field(..., description="Human-readable location name")
    slug: str = Field(..., description="URL-safe slug for Naukri search")


class SearchConfig(BaseModel):
    """Root search config loaded from locations.yaml."""

    keywords: list[KeywordEntry] = Field(default_factory=list)
    locations: list[LocationEntry] = Field(default_factory=list)
