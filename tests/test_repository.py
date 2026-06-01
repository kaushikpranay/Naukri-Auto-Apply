"""
Tests for SQLite job repository.
"""

import sqlite3
from pathlib import Path

import pytest

from app.database.repository import JobRepository
from app.models.job import JobData


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Provide a temporary database path."""
    return tmp_path / "test_jobs.db"


@pytest.fixture
def repo(tmp_db: Path) -> JobRepository:
    """Provide a fresh JobRepository."""
    r = JobRepository(tmp_db)
    yield r
    r.close()


def _make_job(
    title: str = "AI Engineer",
    company: str = "TestCorp",
    url: str = "https://naukri.com/job/123",
    normalized: str = "https://naukri.com/job/123",
) -> JobData:
    """Helper to create a JobData instance."""
    return JobData(
        job_title=title,
        company_name=company,
        job_url=url,
        normalized_url=normalized,
    )


class TestJobRepository:
    """Test suite for JobRepository."""

    def test_schema_creation(self, repo: JobRepository) -> None:
        """Database should have the jobs table after init."""
        conn = sqlite3.connect(str(repo._db_path))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
        )
        tables = cursor.fetchall()
        conn.close()
        assert len(tables) == 1

    def test_insert_job(self, repo: JobRepository) -> None:
        """A new job should be inserted successfully."""
        job = _make_job()
        result = repo.insert_job(job)
        assert result is True
        assert repo.get_job_count() == 1

    def test_duplicate_skipped(self, repo: JobRepository) -> None:
        """A duplicate job (same normalized_url) should be skipped."""
        job = _make_job()
        repo.insert_job(job)
        result = repo.insert_job(job)
        assert result is False
        assert repo.get_job_count() == 1

    def test_different_urls_both_inserted(self, repo: JobRepository) -> None:
        """Jobs with different normalized_urls should both be inserted."""
        job1 = _make_job(normalized="https://naukri.com/job/1")
        job2 = _make_job(normalized="https://naukri.com/job/2")
        repo.insert_job(job1)
        repo.insert_job(job2)
        assert repo.get_job_count() == 2

    def test_insert_many(self, repo: JobRepository) -> None:
        """insert_many should return correct counts."""
        jobs = [
            _make_job(normalized="https://naukri.com/job/a"),
            _make_job(normalized="https://naukri.com/job/b"),
            _make_job(normalized="https://naukri.com/job/a"),  # duplicate
        ]
        inserted, duplicates = repo.insert_many(jobs)
        assert inserted == 2
        assert duplicates == 1

    def test_get_all_jobs(self, repo: JobRepository) -> None:
        """get_all_jobs should return all inserted jobs."""
        repo.insert_job(_make_job(title="Job A", normalized="https://naukri.com/a"))
        repo.insert_job(_make_job(title="Job B", normalized="https://naukri.com/b"))
        jobs = repo.get_all_jobs()
        assert len(jobs) == 2
        titles = {j["job_title"] for j in jobs}
        assert "Job A" in titles
        assert "Job B" in titles
