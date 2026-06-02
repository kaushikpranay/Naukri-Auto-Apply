"""
SQLite job repository with deduplication.

Manages the jobs.db database — creates the schema, inserts jobs
with UNIQUE constraint on normalized_url, and provides query methods.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.database.migrations import DatabaseMigrationManager
from app.models.job import JobData


# Schema definition
_CREATE_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_title       TEXT    NOT NULL,
    company_name    TEXT    NOT NULL,
    job_description TEXT    DEFAULT '',
    job_url         TEXT    NOT NULL,
    normalized_url  TEXT    NOT NULL UNIQUE,
    apply_url       TEXT    DEFAULT '',
    experience_required TEXT DEFAULT '',
    location        TEXT    DEFAULT '',
    posted_date     TEXT    DEFAULT '',
    recruiter_name  TEXT    DEFAULT '',
    recruiter_email TEXT    DEFAULT '',
    status          TEXT    DEFAULT 'pending',
    retry_count     INTEGER DEFAULT 0,
    search_keyword  TEXT,
    search_location TEXT,
    created_at      TEXT    NOT NULL
);
"""

_INSERT_JOB_SQL: str = """
INSERT OR IGNORE INTO jobs (
    job_title, company_name, job_description, job_url,
    normalized_url, apply_url, experience_required, location,
    posted_date, recruiter_name, recruiter_email, status, retry_count,
    search_keyword, search_location, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class JobRepository:
    """
    SQLite repository for job data.

    Handles schema initialization, insert with dedup, and queries.
    """

    def __init__(self, db_path: Path) -> None:
        """
        Initialize the repository and create the schema.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path: Path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: sqlite3.Connection = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_legacy_database()
        self._migration_manager = DatabaseMigrationManager(self._conn)
        self._migration_manager.apply_pending_migrations()

        logger.info("Database initialized: {}", self._db_path)

    def _init_schema(self) -> None:
        """Create the jobs table if it doesn't exist."""
        cursor: sqlite3.Cursor = self._conn.cursor()
        cursor.execute(_CREATE_TABLE_SQL)
        self._conn.commit()
        logger.debug("Database schema verified")

    def _migrate_legacy_database(self) -> None:
        """
        Import rows from the legacy database path if it exists.

        Older runs used the project-root ``jobs.db``. The new canonical
        location is ``database/jobs.db``. This keeps existing data available
        while preventing future confusion about which file is authoritative.
        """
        legacy_db_path: Path = self._db_path.parent.parent / self._db_path.name

        if not legacy_db_path.exists():
            return

        if legacy_db_path.resolve() == self._db_path.resolve():
            return

        cursor: sqlite3.Cursor = self._conn.cursor()
        try:
            cursor.execute("ATTACH DATABASE ? AS legacy_db", (str(legacy_db_path),))

            cursor.execute(
                "SELECT name FROM legacy_db.sqlite_master WHERE type='table' AND name='jobs'"
            )
            if cursor.fetchone() is None:
                cursor.execute("DETACH DATABASE legacy_db")
                return

            cursor.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    job_title, company_name, job_description, job_url,
                    normalized_url, apply_url, experience_required, location,
                    posted_date, recruiter_name, recruiter_email, created_at
                )
                SELECT
                    job_title, company_name, job_description, job_url,
                    normalized_url, apply_url, experience_required, location,
                    posted_date, recruiter_name, recruiter_email, created_at
                FROM legacy_db.jobs
                """
            )
            inserted_rows: int = cursor.rowcount if cursor.rowcount != -1 else 0
            self._conn.commit()
            cursor.execute("DETACH DATABASE legacy_db")

            logger.info(
                "Migrated {} row(s) from legacy database {} into {}",
                inserted_rows,
                legacy_db_path,
                self._db_path,
            )
        except Exception as e:
            try:
                cursor.execute("DETACH DATABASE legacy_db")
            except Exception:
                pass
            logger.warning("Legacy database migration skipped: {}", e)

    def insert_job(self, job: JobData) -> bool:
        """
        Insert a single job. Skips if normalized_url already exists.

        Args:
            job: The job data to insert.

        Returns:
            True if the job was inserted, False if it was a duplicate.
        """
        cursor: sqlite3.Cursor = self._conn.cursor()
        now: str = datetime.now().isoformat()

        cursor.execute(_INSERT_JOB_SQL, (
            job.job_title,
            job.company_name,
            job.job_description,
            job.job_url,
            job.normalized_url,
            job.apply_url,
            job.experience_required,
            job.location,
            job.posted_date,
            job.recruiter_name,
            job.recruiter_email,
            getattr(job, "status", "pending"),
            getattr(job, "retry_count", 0),
            getattr(job, "search_keyword", ""),
            getattr(job, "search_location", ""),
            now,
        ))
        self._conn.commit()

        inserted: bool = cursor.rowcount > 0

        if inserted:
            logger.debug("Inserted: {} at {}", job.job_title[:50], job.company_name)
        else:
            logger.debug("Duplicate skipped: {}", job.normalized_url[:80])

        return inserted

    def insert_many(self, jobs: list[JobData]) -> tuple[int, int]:
        """
        Insert multiple jobs with deduplication tracking.

        Args:
            jobs: List of job data to insert.

        Returns:
            Tuple of (inserted_count, duplicate_count).
        """
        inserted: int = 0
        duplicates: int = 0

        for job in jobs:
            if self.insert_job(job):
                inserted += 1
            else:
                duplicates += 1

        logger.info(
            "Batch insert complete: {} inserted, {} duplicates",
            inserted,
            duplicates,
        )
        return inserted, duplicates

    def get_all_jobs(self) -> list[dict]:
        """
        Retrieve all jobs from the database.

        Returns:
            List of job rows as dictionaries.
        """
        cursor: sqlite3.Cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_jobs_by_status(self, status: str) -> list[dict]:
        """Return all jobs with a given queue status."""
        cursor: sqlite3.Cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC",
            (status,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def get_job_count(self) -> int:
        """Return the total number of jobs in the database."""
        cursor: sqlite3.Cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM jobs")
        result = cursor.fetchone()
        return result[0] if result else 0

    def get_migration_report(self):
        """Return the current database migration and queue state."""
        return self._migration_manager.get_state_report()

    def update_job_status(self, job_id: int, status: str) -> None:
        """Update the queue status for a job."""
        cursor: sqlite3.Cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            (status, job_id),
        )
        self._conn.commit()

    def increment_retry_count(self, job_id: int) -> int:
        """Increment retry_count and return the new value."""
        cursor: sqlite3.Cursor = self._conn.cursor()
        cursor.execute(
            "UPDATE jobs SET retry_count = retry_count + 1 WHERE id = ?",
            (job_id,),
        )
        self._conn.commit()

        cursor.execute("SELECT retry_count FROM jobs WHERE id = ?", (job_id,))
        result = cursor.fetchone()
        return int(result[0]) if result else 0

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            logger.debug("Database connection closed")
