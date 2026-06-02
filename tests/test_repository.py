"""
Tests for SQLite job repository.
"""

import sqlite3
from pathlib import Path

import pytest

from app.database.evaluations_repo import EvaluationsRepository
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
        assert all(j["status"] == "pending" for j in jobs)


class TestEvaluationsRepository:
    """Test suite for the evaluation queue repository."""

    @pytest.fixture
    def eval_repo(self, tmp_db: Path) -> EvaluationsRepository:
        """Provide a repository backed by the same temporary DB."""
        r = JobRepository(tmp_db)
        for index in range(5):
            r.insert_job(
                _make_job(
                    title=f"Job {index + 1}",
                    normalized=f"https://naukri.com/{index + 1}",
                )
            )
        r.close()

        eval_r = EvaluationsRepository(tmp_db)
        yield eval_r
        eval_r.close()

    def test_get_jobs_for_evaluation_marks_remaining_queued(
        self, eval_repo: EvaluationsRepository
    ) -> None:
        """Only the first batch should be selected; the rest should be queued."""
        batch = eval_repo.get_jobs_for_evaluation(limit=2, max_retry_count=3)
        assert len(batch) == 2
        assert [job.id for job in batch] == [1, 2]

        queued_jobs = eval_repo.get_queued_jobs()
        assert len(queued_jobs) == 3
        assert [job.id for job in queued_jobs] == [3, 4, 5]

    def test_get_pending_jobs_for_evaluation_deduplicates_by_company_and_title(
        self, tmp_db: Path
    ) -> None:
        """Duplicate company/title pairs should only return the first occurrence."""
        r = JobRepository(tmp_db)
        r.insert_job(
            _make_job(
                title="AI Engineer",
                company="TestCorp",
                normalized="https://naukri.com/job/1",
            )
        )
        r.insert_job(
            _make_job(
                title="AI Engineer",
                company=" testcorp ",
                normalized="https://naukri.com/job/2",
            )
        )
        r.insert_job(
            _make_job(
                title="ML Engineer",
                company="TestCorp",
                normalized="https://naukri.com/job/3",
            )
        )
        r.close()

        eval_repo = EvaluationsRepository(tmp_db)
        try:
            batch = eval_repo.get_pending_jobs_for_evaluation(limit=20)
            assert [job.id for job in batch] == [1, 3]
        finally:
            eval_repo.close()

    def test_mark_job_retry_exhausts_after_limit(
        self, eval_repo: EvaluationsRepository
    ) -> None:
        """Retry counter should flip a job to failed after the max count."""
        retry_count = eval_repo.mark_job_retry(1, max_retry_count=3)
        assert retry_count == 1
        retry_count = eval_repo.mark_job_retry(1, max_retry_count=3)
        assert retry_count == 2
        retry_count = eval_repo.mark_job_retry(1, max_retry_count=3)
        assert retry_count == 3

        conn = sqlite3.connect(str(eval_repo._db_path))
        row = conn.execute(
            "SELECT status, retry_count FROM jobs WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row[0] == "failed"
        assert row[1] == 3


class TestMigrationCompatibility:
    """Test suite for migrating an old POC-1 jobs table in place."""

    def test_old_schema_is_upgraded_idempotently(self, tmp_path: Path) -> None:
        """Existing jobs.db data should be preserved while new columns are added."""
        db_path = tmp_path / "legacy_jobs.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_title TEXT NOT NULL,
                company_name TEXT NOT NULL,
                job_description TEXT DEFAULT '',
                job_url TEXT NOT NULL,
                normalized_url TEXT NOT NULL UNIQUE,
                apply_url TEXT DEFAULT '',
                experience_required TEXT DEFAULT '',
                location TEXT DEFAULT '',
                posted_date TEXT DEFAULT '',
                recruiter_name TEXT DEFAULT '',
                recruiter_email TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (
                job_title, company_name, job_url, normalized_url, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Legacy Role",
                "Legacy Co",
                "https://naukri.com/job/legacy",
                "https://naukri.com/job/legacy",
                "2026-06-02T00:00:00",
            ),
        )
        conn.commit()
        conn.close()

        repo = JobRepository(db_path)
        try:
            conn = sqlite3.connect(str(db_path))
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            conn.close()

            assert {"status", "retry_count", "search_keyword", "search_location"}.issubset(columns)
            row = repo.get_all_jobs()[0]
            assert row["status"] == "pending"
            assert row["retry_count"] == 0

            conn = sqlite3.connect(str(db_path))
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            conn.close()
            assert migration_count == 1
        finally:
            repo.close()

        repo_again = JobRepository(db_path)
        try:
            conn = sqlite3.connect(str(db_path))
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            conn.close()
            assert migration_count == 1
        finally:
            repo_again.close()


class TestLegacyNullCompatibility:
    """Test suite for loading legacy NULL search fields without validation errors."""

    def test_legacy_null_search_fields_load_successfully(
        self, tmp_path: Path
    ) -> None:
        """Legacy jobs with NULL search fields should still be loadable."""
        db_path = tmp_path / "legacy_nulls.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_title TEXT NOT NULL,
                company_name TEXT NOT NULL,
                job_description TEXT DEFAULT '',
                job_url TEXT NOT NULL,
                normalized_url TEXT NOT NULL UNIQUE,
                apply_url TEXT DEFAULT '',
                experience_required TEXT DEFAULT '',
                location TEXT DEFAULT '',
                posted_date TEXT DEFAULT '',
                recruiter_name TEXT DEFAULT '',
                recruiter_email TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                search_keyword TEXT,
                search_location TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO jobs (
                job_title, company_name, job_url, normalized_url,
                status, retry_count, search_keyword, search_location, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Legacy Role",
                "Legacy Co",
                "https://naukri.com/job/legacy-null",
                "https://naukri.com/job/legacy-null",
                "pending",
                0,
                None,
                None,
                "2026-06-02T00:00:00",
            ),
        )
        conn.commit()
        conn.close()

        repo = JobRepository(db_path)
        eval_repo = EvaluationsRepository(db_path)
        try:
            jobs = eval_repo.get_unevaluated_jobs()
            assert len(jobs) == 1
            job = jobs[0]
            assert job.search_keyword is None
            assert job.search_location is None
            assert job.job_title == "Legacy Role"
        finally:
            eval_repo.close()
            repo.close()
