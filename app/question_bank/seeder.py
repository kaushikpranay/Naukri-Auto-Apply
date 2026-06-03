"""
Question bank seeder.

Inserts every entry from ``answers.CANDIDATE_ANSWERS`` into the
``question_bank`` SQLite table.  Existing rows are updated
(answer refreshed) but their ``usage_count`` is preserved.

Usage::

    from app.question_bank.seeder import QuestionBankSeeder
    seeder = QuestionBankSeeder(repo)
    seeder.seed()
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from app.discovery.repository import ApplyDiscoveryRepository
from app.question_bank.answers import CANDIDATE_ANSWERS


class QuestionBankSeeder:
    """Populate the ``question_bank`` table from the canonical answer registry."""

    def __init__(self, repo: ApplyDiscoveryRepository) -> None:
        self._repo = repo

    def seed(self) -> int:
        """
        Upsert all known answers into the question bank.

        Returns:
            Number of rows upserted.
        """
        conn = self._repo._conn
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        upserted = 0

        for question_key, answer in CANDIDATE_ANSWERS.items():
            # Use the key itself as the canonical question text placeholder.
            # Real discovered question_text values are stored when questions
            # are first seen during discovery; this seeds only key + answer.
            cursor.execute(
                """
                INSERT INTO question_bank (question_key, question_text, answer, usage_count, last_used)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(question_key) DO UPDATE SET
                    answer   = excluded.answer,
                    last_used = excluded.last_used
                """,
                (question_key, question_key, answer, now),
            )
            upserted += 1

        conn.commit()
        logger.info("Question bank seeded: {} entries upserted.", upserted)
        return upserted
