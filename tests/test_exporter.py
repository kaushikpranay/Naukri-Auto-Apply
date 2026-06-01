"""
Tests for Excel exporter.
"""

from pathlib import Path

import pytest
import sqlite3
from datetime import datetime

from app.export.excel_exporter import ExcelExporter


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite DB with sample job data."""
    db_path = tmp_path / "test_jobs.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
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
    """)
    conn.execute("""
        INSERT INTO jobs (
            job_title, company_name, job_url, normalized_url,
            experience_required, location, posted_date, created_at
        ) VALUES (
            'AI Engineer', 'TestCorp', 'https://naukri.com/job/1',
            'https://naukri.com/job/1', '3-5 years', 'Bangalore',
            '2 days ago', ?
        )
    """, (datetime.now().isoformat(),))
    conn.execute("""
        INSERT INTO jobs (
            job_title, company_name, job_url, normalized_url,
            experience_required, location, posted_date, created_at
        ) VALUES (
            'LLM Engineer', 'AI Corp', 'https://naukri.com/job/2',
            'https://naukri.com/job/2', '5-8 years', 'Remote',
            '1 day ago', ?
        )
    """, (datetime.now().isoformat(),))
    conn.commit()
    conn.close()
    return db_path


class TestExcelExporter:
    """Test suite for ExcelExporter."""

    def test_export_creates_file(
        self, populated_db: Path, tmp_path: Path
    ) -> None:
        """Export should create an .xlsx file."""
        export_dir = tmp_path / "exports"
        exporter = ExcelExporter(populated_db, export_dir)
        result = exporter.export()
        assert result.exists()
        assert result.suffix == ".xlsx"

    def test_export_filename_format(
        self, populated_db: Path, tmp_path: Path
    ) -> None:
        """Export filename should follow jobs_YYYY_MM_DD.xlsx pattern."""
        export_dir = tmp_path / "exports"
        exporter = ExcelExporter(populated_db, export_dir)
        result = exporter.export()
        date_str = datetime.now().strftime("%Y_%m_%d")
        assert f"jobs_{date_str}" in result.name

    def test_export_empty_db_raises(self, tmp_path: Path) -> None:
        """Export with empty database should raise ValueError."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
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
                job_description TEXT,
                created_at TEXT
            )
        """)
        conn.commit()
        conn.close()

        export_dir = tmp_path / "exports"
        exporter = ExcelExporter(db_path, export_dir)
        with pytest.raises(ValueError, match="No jobs found"):
            exporter.export()
