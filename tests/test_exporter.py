"""
Tests for Excel exporter.
"""

from datetime import datetime
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from app.export.eval_exporter import EvaluatedJobsExporter


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite DB with sample evaluated jobs."""
    db_path = tmp_path / "test_jobs.db"
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
        CREATE TABLE ai_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            interview_probability INTEGER NOT NULL,
            recommended_resume TEXT NOT NULL,
            priority TEXT NOT NULL,
            action TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            reason TEXT NOT NULL,
            missing_skills TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )

    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO jobs (
            job_title, company_name, job_url, normalized_url,
            experience_required, location, posted_date, status, retry_count,
            search_keyword, search_location, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "AI Engineer",
            "TestCorp",
            "https://naukri.com/job/1",
            "https://naukri.com/job/1",
            "3-5 years",
            "Bangalore",
            "2 days ago",
            "evaluated",
            0,
            "ai engineer",
            "bangalore",
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            job_title, company_name, job_url, normalized_url,
            experience_required, location, posted_date, status, retry_count,
            search_keyword, search_location, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "LLM Engineer",
            "AI Corp",
            "https://naukri.com/job/2",
            "https://naukri.com/job/2",
            "5-8 years",
            "Remote",
            "1 day ago",
            "queued",
            1,
            "llm engineer",
            "remote",
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO ai_evaluations (
            job_id, run_id, model_name, prompt_version,
            interview_probability, recommended_resume, priority, action,
            confidence, reason, missing_skills, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "run-123",
            "Groq",
            "v2.0",
            92,
            "GENAI",
            "high",
            "apply",
            95,
            "Good fit.",
            "[]",
            now,
        ),
    )
    conn.commit()
    conn.close()
    return db_path


class TestExcelExporter:
    """Test suite for Excel exporter."""

    def test_export_creates_evaluated_file(
        self, populated_db: Path, tmp_path: Path
    ) -> None:
        """Export should create the evaluated_jobs.xlsx workbook."""
        export_dir = tmp_path / "exports"
        exporter = EvaluatedJobsExporter(populated_db, export_dir)
        result = exporter.export()

        assert result.exists()
        assert result.name == "evaluated_jobs.xlsx"

        workbook = pd.read_excel(result)
        assert list(workbook.columns) == [
            "Company",
            "Role",
            "Interview Probability",
            "Resume",
            "Priority",
            "Action",
            "Confidence",
            "Reason",
            "Provider Used",
        ]
        assert workbook.iloc[0]["Company"] == "TestCorp"
        assert workbook.iloc[0]["Provider Used"] == "Groq"

    def test_export_empty_db_still_creates_file(self, tmp_path: Path) -> None:
        """Export with empty database should still create the workbook."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                job_title TEXT,
                company_name TEXT,
                job_url TEXT,
                normalized_url TEXT UNIQUE,
                apply_url TEXT,
                experience_required TEXT,
                location TEXT,
                posted_date TEXT,
                recruiter_name TEXT,
                recruiter_email TEXT,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                search_keyword TEXT,
                search_location TEXT,
                job_description TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ai_evaluations (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                interview_probability INTEGER NOT NULL,
                recommended_resume TEXT NOT NULL,
                priority TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                reason TEXT NOT NULL,
                missing_skills TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        export_dir = tmp_path / "exports"
        exporter = EvaluatedJobsExporter(db_path, export_dir)
        result = exporter.export()
        assert result.name == "evaluated_jobs.xlsx"
        assert result.exists()
