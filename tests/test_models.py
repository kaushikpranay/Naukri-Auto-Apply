"""
Tests for Pydantic models.
"""

import pytest
from datetime import datetime

from app.models.job import JobData, CollectionSummary
from app.models.config import (
    AppSettings,
    BrowserConfig,
    DiscoveryConfig,
    ApplyFlowSelectors,
    QuestionSelectors,
    KeywordEntry,
    LocationEntry,
    SearchConfig,
    SelectorsConfig,
    LoginSelectors,
    SearchResultSelectors,
    PaginationSelectors,
    JobDetailSelectors,
)


class TestJobData:
    """Test suite for JobData model."""

    def test_create_with_required_fields(self) -> None:
        """JobData should be created with minimum required fields."""
        job = JobData(
            job_title="AI Engineer",
            company_name="TestCorp",
            job_url="https://naukri.com/job/123",
        )
        assert job.job_title == "AI Engineer"
        assert job.company_name == "TestCorp"
        assert job.job_description == ""

    def test_all_fields(self) -> None:
        """JobData should accept all fields."""
        job = JobData(
            job_title="LLM Engineer",
            company_name="AI Corp",
            job_description="Build LLMs",
            job_url="https://naukri.com/job/456",
            normalized_url="https://naukri.com/job/456",
            apply_url="https://naukri.com/apply/456",
            experience_required="3-5 years",
            location="Bangalore",
            posted_date="2 days ago",
            recruiter_name="John Doe",
            recruiter_email="john@aicorp.com",
        )
        assert job.recruiter_email == "john@aicorp.com"

    def test_validation_requires_title(self) -> None:
        """JobData should fail without job_title."""
        with pytest.raises(Exception):
            JobData(company_name="TestCorp", job_url="https://naukri.com/job/1")


class TestCollectionSummary:
    """Test suite for CollectionSummary model."""

    def test_defaults(self) -> None:
        """CollectionSummary should have sensible defaults."""
        summary = CollectionSummary()
        assert summary.jobs_found == 0
        assert summary.export_status == "Pending"

    def test_print_summary(self) -> None:
        """print_summary should return formatted text."""
        summary = CollectionSummary(
            jobs_found=83,
            jobs_inserted=51,
            duplicates_skipped=32,
            export_status="Success",
        )
        text = summary.print_summary()
        assert "83" in text
        assert "51" in text
        assert "32" in text
        assert "Success" in text


class TestAppSettings:
    """Test suite for AppSettings model."""

    def test_defaults(self) -> None:
        """AppSettings should work with all defaults."""
        settings = AppSettings()
        assert settings.browser.headless is False
        assert settings.paths.database == "database/jobs.db"
        assert settings.evaluation.max_ai_evaluations_per_run == 5
        assert settings.evaluation.max_retry_count == 3
        assert settings.discovery.max_discovery_jobs_per_run == 20

    def test_custom_values(self) -> None:
        """AppSettings should accept custom values."""
        settings = AppSettings(
            browser=BrowserConfig(headless=True, slow_mo=100),
        )
        assert settings.browser.headless is True
        assert settings.browser.slow_mo == 100


class TestSearchConfig:
    """Test suite for SearchConfig model."""

    def test_keywords_and_locations(self) -> None:
        """SearchConfig should parse keywords and locations."""
        config = SearchConfig(
            keywords=[
                KeywordEntry(display="AI Engineer", slug="ai-engineer"),
            ],
            locations=[
                LocationEntry(display="Bangalore", slug="bangalore"),
            ],
        )
        assert len(config.keywords) == 1
        assert config.keywords[0].slug == "ai-engineer"
        assert config.locations[0].display == "Bangalore"


class TestSelectorsConfig:
    """Test suite for selector configuration models."""

    def test_discovery_selectors_present(self) -> None:
        """SelectorsConfig should include discovery selectors."""
        selectors = SelectorsConfig(
            login=LoginSelectors(detection="a", logged_in="b", authenticated="c"),
            search_results=SearchResultSelectors(
                container="a",
                job_card="b",
                title="c",
                company="d",
                experience="e",
                location="f",
                posted_date="g",
                no_results="h",
            ),
            pagination=PaginationSelectors(next_button="a", current_page="b"),
            job_detail=JobDetailSelectors(
                description="a",
                apply_button="b",
                recruiter_section="c",
                recruiter_name="d",
                recruiter_email="e",
            ),
            discovery={
                "apply_flow": {
                    "trigger": "a",
                    "already_applied": "b",
                    "easy_apply_marker": "c",
                    "external_portal_marker": "d",
                    "email_link": "e",
                    "final_submit": "f",
                },
                "questions": {
                    "page_body": "body",
                    "container": "c",
                    "text": "t",
                    "field": "f",
                    "option": "o",
                    "required_marker": "r",
                },
            },
        )
        assert selectors.discovery.apply_flow.trigger == "a"
        assert selectors.discovery.questions.page_body == "body"
