# question_bank/lookup_service.py
"""
Question Bank Lookup Service — POC-3B Phase 1.

Reads all questions seen across all discovery runs, queries the
``question_bank`` table for each, and produces a structured report:

- Known Questions   — key found in bank with a stored answer
- Unknown Questions — key not found, or answer is NULL / empty
- Suggested Answers — best-effort suggestions for unknown keys
                      drawn from the canonical answer registry
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from app.discovery.question_normalizer import normalize_question_key
from app.question_bank.answers import CANDIDATE_ANSWERS


@dataclass
class KnownQuestion:
    """A question whose answer is already stored in the bank."""

    job_id: int
    company: str
    role: str
    question_key: str
    question_text: str
    field_type: str
    required: bool
    stored_answer: str


@dataclass
class UnknownQuestion:
    """A question with no stored answer, with an optional suggestion."""

    job_id: int
    company: str
    role: str
    question_key: str
    question_text: str
    field_type: str
    required: bool
    suggested_answer: str | None = None


@dataclass
class QuestionBankReport:
    """Full result of a question bank lookup pass."""

    known: list[KnownQuestion] = field(default_factory=list)
    unknown: list[UnknownQuestion] = field(default_factory=list)

    @property
    def total_questions(self) -> int:
        return len(self.known) + len(self.unknown)

    @property
    def coverage_pct(self) -> float:
        if self.total_questions == 0:
            return 0.0
        return round(len(self.known) / self.total_questions * 100, 1)


class QuestionBankLookupService:
    """
    Resolve every discovered question against the question_bank table.

    Does NOT access the browser. Does NOT modify forms. Read-only.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")

    def run(self) -> QuestionBankReport:
        """
        Load all discovered questions, look each up in the bank, and
        return a fully populated ``QuestionBankReport``.
        """
        rows = self._load_all_discovered_questions()
        if not rows:
            logger.info("No discovered questions found in the database.")
            return QuestionBankReport()

        logger.info("Loaded {} discovered question(s) across all jobs.", len(rows))
        report = QuestionBankReport()

        for row in rows:
            raw_text: str = row["question_text"]
            question_key: str = row["question_key"] or normalize_question_key(raw_text)
            stored_answer: str | None = self._lookup_answer(question_key)
            required: bool = bool(row["required"])
            job_id: int = int(row["job_id"])
            company: str = row["company_name"] or ""
            role: str = row["job_title"] or ""
            field_type: str = row["field_type"] or "unknown"

            if stored_answer:
                report.known.append(
                    KnownQuestion(
                        job_id=job_id,
                        company=company,
                        role=role,
                        question_key=question_key,
                        question_text=raw_text,
                        field_type=field_type,
                        required=required,
                        stored_answer=stored_answer,
                    )
                )
                logger.debug(
                    "KNOWN   [{}] '{}' → '{}'",
                    question_key,
                    raw_text[:60],
                    stored_answer[:40],
                )
            else:
                suggested = CANDIDATE_ANSWERS.get(question_key)
                report.unknown.append(
                    UnknownQuestion(
                        job_id=job_id,
                        company=company,
                        role=role,
                        question_key=question_key,
                        question_text=raw_text,
                        field_type=field_type,
                        required=required,
                        suggested_answer=suggested,
                    )
                )
                log_msg = (
                    f"UNKNOWN [{question_key}] '{raw_text[:60]}'"
                    + (f" → suggested: '{suggested[:40]}'" if suggested else " → no suggestion")
                )
                logger.debug(log_msg)

        logger.info(
            "Question bank lookup complete: {} known / {} unknown / {:.1f}% coverage.",
            len(report.known),
            len(report.unknown),
            report.coverage_pct,
        )
        return report

    # ── Private helpers ──────────────────────────────────────────────────────

    def _load_all_discovered_questions(self) -> list[sqlite3.Row]:
        """Return every row in job_application_questions joined to jobs."""
        query = """
            SELECT
                q.job_id,
                j.company_name,
                j.job_title,
                q.question_key,
                q.question_text,
                q.field_type,
                q.required
            FROM job_application_questions q
            JOIN jobs j ON j.id = q.job_id
            ORDER BY q.job_id ASC, q.id ASC
        """
        cursor = self._conn.cursor()
        cursor.execute(query)
        return cursor.fetchall()

    def _lookup_answer(self, question_key: str) -> str | None:
        """Return a non-empty stored answer for the key, or None."""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT answer FROM question_bank WHERE question_key = ?",
            (question_key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        answer = row["answer"]
        return str(answer).strip() if answer and str(answer).strip() else None

    def close(self) -> None:
        if self._conn:
            self._conn.close()
