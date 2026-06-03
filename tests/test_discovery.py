"""
Tests for apply discovery repository and question normalization.
"""

from datetime import datetime
from pathlib import Path
import sqlite3

import pandas as pd

from app.discovery.question_normalizer import normalize_question_key
from app.discovery.repository import ApplyDiscoveryRepository
from app.export.apply_discovery_exporter import ApplyDiscoveryExporter
from app.models.discovery import ApplicationDiscoveryRecord, DiscoveredQuestion


def _create_discovery_db(db_path: Path) -> None:
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
            created_at TEXT NOT NULL
        )
        """
    )
    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO jobs (
            job_title, company_name, job_url, normalized_url,
            recruiter_name, recruiter_email, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Python Engineer",
            "TestCorp",
            "https://naukri.com/job/1",
            "https://naukri.com/job/1",
            "HR Person",
            "hr@testcorp.com",
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO jobs (
            job_title, company_name, job_url, normalized_url,
            recruiter_name, recruiter_email, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ML Engineer",
            "OtherCorp",
            "https://naukri.com/job/2",
            "https://naukri.com/job/2",
            "HR Person 2",
            "hr2@othercorp.com",
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
            "run-1",
            "Groq",
            "v2.0",
            92,
            "GENAI",
            "high",
            "apply",
            88,
            "Strong fit.",
            "[]",
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
            2,
            "run-1",
            "Groq",
            "v2.0",
            70,
            "ML",
            "medium",
            "review",
            76,
            "Review needed.",
            "[]",
            now,
        ),
    )
    conn.commit()
    conn.close()


def test_question_normalization_maps_similar_questions() -> None:
    assert normalize_question_key("Years of Python Experience?") == "python_experience"
    assert normalize_question_key("Notice Period") == "notice_period"
    assert normalize_question_key("Custom Random Question!") == "custom_random_question"


def test_apply_discovery_repository_selects_only_apply_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "discovery.db"
    _create_discovery_db(db_path)

    repo = ApplyDiscoveryRepository(db_path)
    try:
        jobs = repo.get_jobs_for_discovery(limit=20)
        assert len(jobs) == 1
        assert jobs[0].job_title == "Python Engineer"

        record = ApplicationDiscoveryRecord(
            job_id=1,
            apply_type="easy_apply",
            apply_url="https://example.com/apply",
            email="hr@testcorp.com",
            hr_name="HR Person",
            status="discovered",
        )
        repo.save_application(record)
        repo.save_application(record)

        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM job_applications").fetchone()[0]
        conn.close()
        assert count == 1

        question = DiscoveredQuestion(
            question_key="python_experience",
            question_text="Years of Python Experience?",
            field_type="input",
            required=True,
            answer="2",
        )
        repo.save_question(1, question)
        assert repo.get_question_answer("python_experience") == "2"
        assert repo.get_question_count_for_job(1) == 1
    finally:
        repo.close()


def test_apply_discovery_exporter_creates_expected_workbook(tmp_path: Path) -> None:
    db_path = tmp_path / "discovery.db"
    _create_discovery_db(db_path)

    repo = ApplyDiscoveryRepository(db_path)
    try:
        repo.save_application(
            ApplicationDiscoveryRecord(
                job_id=1,
                apply_type="easy_apply",
                apply_url="https://example.com/apply",
                email="hr@testcorp.com",
                hr_name="HR Person",
                status="discovered",
            )
        )
        repo.save_question(
            1,
            DiscoveredQuestion(
                question_key="python_experience",
                question_text="Years of Python Experience?",
                field_type="input",
                required=True,
                answer="2",
            ),
        )
    finally:
        repo.close()

    exporter = ApplyDiscoveryExporter(db_path, tmp_path / "exports")
    workbook = exporter.export()
    assert workbook.name == "apply_discovery.xlsx"
    assert workbook.exists()

    df = pd.read_excel(workbook)
    assert list(df.columns) == [
        "Company",
        "Role",
        "Apply Type",
        "Apply URL",
        "Email",
        "HR Name",
        "Status",
        "Questions Found",
        "Discovery Date",
    ]
    assert df.iloc[0]["Company"] == "TestCorp"
    assert df.iloc[0]["Questions Found"] == 1


def test_apply_discovery_debug_exporter_creates_expected_workbook(tmp_path: Path) -> None:
    """Debug export should include all new evidence columns."""
    db_path = tmp_path / "discovery.db"
    _create_discovery_db(db_path)

    repo = ApplyDiscoveryRepository(db_path)
    try:
        repo.save_application(
            ApplicationDiscoveryRecord(
                job_id=1,
                apply_type="easy_apply",
                apply_url="https://example.com/apply",
                email="hr@testcorp.com",
                hr_name="HR Person",
                button_text="Apply Now",
                status="discovered",
                screenshot_before="screenshots/job_1_before.png",
                screenshot_after="screenshots/job_1_after.png",
                screenshot_modal="screenshots/job_1_modal.png",
            )
        )
    finally:
        repo.close()

    exporter = ApplyDiscoveryExporter(db_path, tmp_path / "exports")
    workbook = exporter.export_debug()
    assert workbook.name == "apply_discovery_debug.xlsx"
    assert workbook.exists()

    df = pd.read_excel(workbook)
    expected_columns = [
        "Company",
        "Role",
        "Button Text",
        "Button Selector",
        "URL Before",
        "URL After",
        "Redirects",
        "Redirect Chain",
        "Apply Type",
        "Apply URL",
        "Status",
        "Screenshot Before",
        "Screenshot After",
        "Screenshot Modal",
        "HTML Before",
        "HTML After",
        "Elements JSON",
        "Detected At",
    ]
    assert list(df.columns) == expected_columns
    assert df.iloc[0]["Company"] == "TestCorp"
    assert df.iloc[0]["Button Text"] == "Apply Now"
    assert pd.isna(df.iloc[0]["HTML Before"]) or str(df.iloc[0]["HTML Before"]) == ""
    assert pd.isna(df.iloc[0]["HTML After"]) or str(df.iloc[0]["HTML After"]) == ""
