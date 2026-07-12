"""
app/ats/repository.py
Database operations for ATS auto-apply tracking.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.ats.models import AtsApplicationRecord


_CREATE_ATS_APPLICATIONS_SQL = """
CREATE TABLE IF NOT EXISTS ats_applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL UNIQUE,
    ats_type        TEXT    NOT NULL,
    apply_url       TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    error           TEXT,
    screenshot_path TEXT,
    attempted_at    TEXT,
    applied_at      TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
)
"""

_ATS_MIGRATIONS: list[str] = [
    # Reserved for future schema additions.
]


class AtsRepository:
    """SQLite repository for ats_applications table."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._run_migrations()

    def _run_migrations(self) -> None:
        self._conn.execute(_CREATE_ATS_APPLICATIONS_SQL)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS _ats_migrations (
                sql_hash TEXT PRIMARY KEY NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

        for migration in _ATS_MIGRATIONS:
            sql_hash = hashlib.md5(migration.encode("utf-8")).hexdigest()
            row = self._conn.execute(
                "SELECT 1 FROM _ats_migrations WHERE sql_hash = ?", (sql_hash,)
            ).fetchone()
            if row:
                continue
            try:
                self._conn.execute(migration)
                self._conn.execute(
                    "INSERT INTO _ats_migrations VALUES (?, ?)",
                    (sql_hash, datetime.now().isoformat()),
                )
                self._conn.commit()
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    logger.error("ATS migration failed: {} SQL: {}", exc, migration)
                self._conn.commit()

    def get_pending_jobs(self) -> list[dict]:
        """
        Return external-portal jobs that haven't been successfully applied or skipped yet.
        Each row has: job_id, job_title, company_name, apply_url, ats_type (from url_after).
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT
                j.id          AS job_id,
                j.job_title,
                j.company_name,
                COALESCE(ja.url_after, ja.apply_url) AS apply_url
            FROM jobs j
            JOIN job_applications ja ON ja.job_id = j.id
            WHERE ja.apply_type = 'external_portal'
              AND j.id NOT IN (
                  SELECT job_id FROM ats_applications
                  WHERE status IN ('applied', 'skipped')
              )
            ORDER BY j.id ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def upsert(self, record: AtsApplicationRecord) -> None:
        self._conn.execute("""
            INSERT INTO ats_applications
                (job_id, ats_type, apply_url, status, error, screenshot_path, attempted_at, applied_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                ats_type        = excluded.ats_type,
                apply_url       = excluded.apply_url,
                status          = excluded.status,
                error           = excluded.error,
                screenshot_path = excluded.screenshot_path,
                attempted_at    = excluded.attempted_at,
                applied_at      = excluded.applied_at
        """, (
            record.job_id,
            record.ats_type,
            record.apply_url,
            record.status,
            record.error,
            record.screenshot_path,
            record.attempted_at,
            record.applied_at,
        ))
        self._conn.commit()

    def get_stats(self) -> dict:
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT status, COUNT(*) FROM ats_applications GROUP BY status
        """)
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT ats_type, COUNT(*) FROM ats_applications GROUP BY ats_type
        """)
        by_ats = {row[0]: row[1] for row in cursor.fetchall()}

        total = sum(by_status.values())
        return {
            "total": total,
            "applied": by_status.get("applied", 0),
            "failed": by_status.get("failed", 0),
            "skipped": by_status.get("skipped", 0),
            "pending": by_status.get("pending", 0),
            "by_ats": by_ats,
        }

    def close(self) -> None:
        self._conn.close()
