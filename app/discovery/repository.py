"""
SQLite repository for apply discovery.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.models.discovery import ApplicationDiscoveryRecord, DiscoveredQuestion
from app.models.job import JobData


_CREATE_JOB_APPLICATIONS_SQL = """
CREATE TABLE IF NOT EXISTS job_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    apply_type TEXT,
    apply_url TEXT,
    email TEXT,
    hr_name TEXT,
    button_text TEXT,
    button_selector TEXT,
    url_before TEXT,
    url_after TEXT,
    redirect_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    screenshot_before TEXT,
    screenshot_after TEXT,
    screenshot_modal TEXT,
    redirect_chain TEXT,
    html_before_path TEXT,
    html_path TEXT,
    elements_path TEXT,
    detected_at TEXT NOT NULL,
    page_title TEXT,
    modal_detected INTEGER DEFAULT 0,
    forms_count INTEGER DEFAULT 0,
    inputs_count INTEGER DEFAULT 0,
    radio_count INTEGER DEFAULT 0,
    dropdown_count INTEGER DEFAULT 0,
    buttons_count INTEGER DEFAULT 0,
    quota_message TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""

_CREATE_QUESTION_BANK_SQL = """
CREATE TABLE IF NOT EXISTS question_bank (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key TEXT NOT NULL UNIQUE,
    question_text TEXT NOT NULL,
    answer TEXT,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used TEXT,
    field_type TEXT,
    created_at TEXT,
    last_used_at TEXT
);
"""

_CREATE_JOB_APPLICATION_QUESTIONS_SQL = """
CREATE TABLE IF NOT EXISTS job_application_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    field_type TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0,
    answer TEXT,
    detected_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id),
    UNIQUE(job_id, question_key, question_text)
);
"""

_CREATE_ANSWER_MAPPINGS_SQL = """
CREATE TABLE IF NOT EXISTS answer_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_key TEXT NOT NULL,
    raw_answer TEXT NOT NULL,
    selected_option TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(question_key, raw_answer)
);
"""


class ApplyDiscoveryRepository:
    """Persistence layer for apply discovery artifacts."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    _MIGRATIONS: list[str] = [
        "ALTER TABLE job_applications ADD COLUMN button_text TEXT",
        "ALTER TABLE job_applications ADD COLUMN screenshot_before TEXT",
        "ALTER TABLE job_applications ADD COLUMN screenshot_after TEXT",
        "ALTER TABLE job_applications ADD COLUMN screenshot_modal TEXT",
        "ALTER TABLE job_applications ADD COLUMN html_path TEXT",
        "ALTER TABLE job_applications ADD COLUMN button_selector TEXT",
        "ALTER TABLE job_applications ADD COLUMN url_before TEXT",
        "ALTER TABLE job_applications ADD COLUMN url_after TEXT",
        "ALTER TABLE job_applications ADD COLUMN redirect_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE job_applications ADD COLUMN html_before_path TEXT",
        "ALTER TABLE job_applications ADD COLUMN elements_path TEXT",
        "ALTER TABLE job_applications ADD COLUMN redirect_chain TEXT",
        "ALTER TABLE job_applications ADD COLUMN page_title TEXT",
        "ALTER TABLE job_applications ADD COLUMN modal_detected INTEGER DEFAULT 0",
        "ALTER TABLE job_applications ADD COLUMN forms_count INTEGER DEFAULT 0",
        "ALTER TABLE job_applications ADD COLUMN inputs_count INTEGER DEFAULT 0",
        "ALTER TABLE job_applications ADD COLUMN radio_count INTEGER DEFAULT 0",
        "ALTER TABLE job_applications ADD COLUMN dropdown_count INTEGER DEFAULT 0",
        "ALTER TABLE job_applications ADD COLUMN buttons_count INTEGER DEFAULT 0",
        "ALTER TABLE question_bank ADD COLUMN field_type TEXT",
        "ALTER TABLE question_bank ADD COLUMN created_at TEXT",
        "ALTER TABLE question_bank ADD COLUMN last_used_at TEXT",
        # Quota exhaustion detection
        "ALTER TABLE job_applications ADD COLUMN quota_message TEXT",
    ]

    def _init_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(_CREATE_JOB_APPLICATIONS_SQL)
        cursor.execute(_CREATE_QUESTION_BANK_SQL)
        cursor.execute(_CREATE_JOB_APPLICATION_QUESTIONS_SQL)
        cursor.execute(_CREATE_ANSWER_MAPPINGS_SQL)
        for migration in self._MIGRATIONS:
            try:
                cursor.execute(migration)
            except sqlite3.OperationalError:
                pass
        self._conn.commit()
        logger.debug("Apply discovery schema verified")

    def get_jobs_for_discovery(self, limit: int) -> list[JobData]:
        """Return shortlisted jobs that still need discovery."""
        query = """
            SELECT
                j.id,
                j.job_title,
                j.company_name,
                j.job_description,
                j.job_url,
                j.normalized_url,
                j.apply_url,
                j.experience_required,
                j.location,
                j.posted_date,
                j.recruiter_name,
                j.recruiter_email,
                j.status,
                j.retry_count,
                j.search_keyword,
                j.search_location
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN job_applications a ON a.job_id = j.id
            WHERE UPPER(e.action) = 'APPLY'
              AND a.job_id IS NULL
            ORDER BY e.interview_probability DESC, j.id ASC
            LIMIT ?
        """
        cursor = self._conn.cursor()
        cursor.execute(query, (limit,))
        rows = cursor.fetchall()
        return [JobData(**dict(row)) for row in rows]

    def get_job_by_id(self, job_id: int) -> JobData | None:
        """Fetch a single job by its ID regardless of discovery status."""
        query = """
            SELECT
                j.id, j.job_title, j.company_name, j.job_description,
                j.job_url, j.normalized_url, j.apply_url,
                j.experience_required, j.location, j.posted_date,
                j.recruiter_name, j.recruiter_email, j.status,
                j.retry_count, j.search_keyword, j.search_location
            FROM jobs j
            WHERE j.id = ?
        """
        cursor = self._conn.cursor()
        cursor.execute(query, (job_id,))
        row = cursor.fetchone()
        return JobData(**dict(row)) if row else None

    def clear_application(self, job_id: int) -> None:
        """Remove existing discovery record for a job (force reprocess)."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM job_applications WHERE job_id = ?", (job_id,))
        cursor.execute(
            "DELETE FROM job_application_questions WHERE job_id = ?", (job_id,)
        )
        self._conn.commit()
        logger.info("Cleared existing discovery record for job_id={}", job_id)

    def save_application(
        self,
        record: ApplicationDiscoveryRecord,
    ) -> None:
        """Insert or update one job application discovery record."""
        cursor = self._conn.cursor()
        detected_at = record.detected_at or datetime.now().isoformat()
        cursor.execute(
            """
            INSERT INTO job_applications (
                job_id, apply_type, apply_url, email, hr_name, button_text,
                button_selector, url_before, url_after, redirect_count,
                redirect_chain, status, screenshot_before, screenshot_after,
                screenshot_modal, html_before_path, html_path, elements_path,
                detected_at, page_title, modal_detected, forms_count,
                inputs_count, radio_count, dropdown_count, buttons_count,
                quota_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                apply_type = excluded.apply_type,
                apply_url = excluded.apply_url,
                email = excluded.email,
                hr_name = excluded.hr_name,
                button_text = excluded.button_text,
                button_selector = excluded.button_selector,
                url_before = excluded.url_before,
                url_after = excluded.url_after,
                redirect_count = excluded.redirect_count,
                redirect_chain = excluded.redirect_chain,
                status = excluded.status,
                screenshot_before = excluded.screenshot_before,
                screenshot_after = excluded.screenshot_after,
                screenshot_modal = excluded.screenshot_modal,
                html_before_path = excluded.html_before_path,
                html_path = excluded.html_path,
                elements_path = excluded.elements_path,
                detected_at = excluded.detected_at,
                page_title = excluded.page_title,
                modal_detected = excluded.modal_detected,
                forms_count = excluded.forms_count,
                inputs_count = excluded.inputs_count,
                radio_count = excluded.radio_count,
                dropdown_count = excluded.dropdown_count,
                buttons_count = excluded.buttons_count,
                quota_message = excluded.quota_message
            """,
            (
                record.job_id,
                record.apply_type,
                record.apply_url,
                record.email,
                record.hr_name,
                record.button_text,
                record.button_selector,
                record.url_before,
                record.url_after,
                record.redirect_count,
                record.redirect_chain,
                record.status,
                record.screenshot_before,
                record.screenshot_after,
                record.screenshot_modal,
                record.html_before_path,
                record.html_path,
                record.elements_path,
                detected_at,
                record.page_title,
                1 if record.modal_detected else 0,
                record.forms_count,
                record.inputs_count,
                record.radio_count,
                record.dropdown_count,
                record.buttons_count,
                record.quota_message,
            ),
        )
        self._conn.commit()

    def save_question(
        self,
        job_id: int,
        question: DiscoveredQuestion,
    ) -> None:
        """Persist a discovered question and keep the question bank updated."""
        if question.answer:
            ans_lower = str(question.answer).strip().lower()
            if ans_lower in ("save", "skip", "submit", "continue"):
                question.answer = None

        now = datetime.now().isoformat()
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO question_bank (
                question_key, question_text, answer, usage_count, last_used,
                field_type, created_at, last_used_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            ON CONFLICT(question_key) DO UPDATE SET
                question_text = excluded.question_text,
                answer = COALESCE(NULLIF(excluded.answer, ''), question_bank.answer),
                usage_count = question_bank.usage_count + 1,
                last_used = excluded.last_used,
                field_type = COALESCE(NULLIF(excluded.field_type, ''), question_bank.field_type),
                last_used_at = excluded.last_used_at
            """,
            (
                question.question_key,
                question.question_text,
                question.answer,
                now,
                question.field_type,
                now,
                now,
            ),
        )
        cursor.execute(
            """
            INSERT INTO job_application_questions (
                job_id, question_key, question_text, field_type,
                required, answer, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id, question_key, question_text) DO UPDATE SET
                field_type = excluded.field_type,
                required = excluded.required,
                answer = excluded.answer,
                detected_at = excluded.detected_at
            """,
            (
                job_id,
                question.question_key,
                question.question_text,
                question.field_type,
                1 if question.required else 0,
                question.answer,
                now,
            ),
        )
        self._conn.commit()

    def get_question_answer(self, question_key: str) -> str | None:
        """Return a stored answer for a question key, if available."""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT answer FROM question_bank WHERE question_key = ?",
            (question_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        answer = row["answer"]
        return str(answer).strip() if answer is not None and str(answer).strip() else None

    def get_question_count_for_job(self, job_id: int) -> int:
        """Count the questions captured for a given job."""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM job_application_questions WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def get_table_counts(self) -> dict[str, int]:
        """Return row counts for discovery tables."""
        cursor = self._conn.cursor()
        counts: dict[str, int] = {}
        for table in ("job_applications", "question_bank", "job_application_questions", "answer_mappings"):
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = int(cursor.fetchone()[0])
            except Exception:
                pass
        return counts

    def save_answer_mapping(self, question_key: str, raw_answer: str, selected_option: str) -> None:
        """Insert or replace a question answer to option mapping."""
        if selected_option:
            opt_lower = str(selected_option).strip().lower()
            if opt_lower in ("save", "skip", "submit", "continue"):
                return

        cursor = self._conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(
            """
            INSERT OR REPLACE INTO answer_mappings (question_key, raw_answer, selected_option, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (question_key, raw_answer, selected_option, now)
        )
        self._conn.commit()

    def get_answer_mapping(self, question_key: str, raw_answer: str) -> str | None:
        """Get the mapped selected option for a given question key and raw answer."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT selected_option FROM answer_mappings
            WHERE question_key = ? AND raw_answer = ?
            """,
            (question_key, raw_answer)
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def close(self) -> None:
        if self._conn:
            self._conn.close()
