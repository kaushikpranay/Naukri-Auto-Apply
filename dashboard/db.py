"""
Database query layer for the Naukri Automation Dashboard.

All queries are read-only. Connects directly to the existing jobs.db.
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime


DB_PATH = Path(__file__).resolve().parent.parent / "database" / "jobs.db"


@contextmanager
def get_db():
    """Yield a read-only SQLite connection with Row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(row) for row in rows]


# ── Overview Stats ────────────────────────────────────────────────────────────

def get_overview_stats() -> dict:
    """Return all dashboard overview counters."""
    with get_db() as conn:
        c = conn.cursor()

        # Total jobs
        c.execute("SELECT COUNT(*) FROM jobs")
        total_jobs = c.fetchone()[0]

        # Evaluation breakdown
        c.execute("SELECT UPPER(action) AS act, COUNT(*) FROM ai_evaluations GROUP BY UPPER(action)")
        eval_counts = {row[0]: row[1] for row in c.fetchall()}

        # Application type breakdown
        c.execute("SELECT apply_type, COUNT(*) FROM job_applications GROUP BY apply_type")
        app_counts = {row[0] or "unknown": row[1] for row in c.fetchall()}

        # Pending evaluations
        c.execute("""
            SELECT COUNT(*) FROM jobs
            WHERE id NOT IN (SELECT job_id FROM ai_evaluations)
        """)
        pending_eval = c.fetchone()[0]

        # Pending applications (APPLY but not yet in job_applications)
        c.execute("""
            SELECT COUNT(*)
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN job_applications a ON a.job_id = j.id
            WHERE UPPER(e.action) = 'APPLY' AND a.job_id IS NULL
        """)
        pending_apply = c.fetchone()[0]

        # Question bank coverage
        c.execute("SELECT COUNT(*) FROM question_bank")
        total_questions = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM question_bank WHERE answer IS NOT NULL AND TRIM(answer) != ''")
        answered_questions = c.fetchone()[0]
        coverage_pct = round((answered_questions / total_questions * 100), 1) if total_questions > 0 else 0

        # Last run time (most recent ai_evaluation created_at)
        c.execute("SELECT MAX(created_at) FROM ai_evaluations")
        row = c.fetchone()
        last_run = row[0] if row and row[0] else "Never"

        # Jobs collected today
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT COUNT(*) FROM jobs WHERE created_at LIKE ?", (f"{today}%",))
        jobs_today = c.fetchone()[0]

        return {
            "total_jobs": total_jobs,
            "jobs_today": jobs_today,
            "apply": eval_counts.get("APPLY", 0),
            "review": eval_counts.get("REVIEW", 0),
            "skip": eval_counts.get("SKIP", 0),
            "pending_eval": pending_eval,
            "pending_apply": pending_apply,
            "easy_apply": app_counts.get("easy_apply", 0),
            "external_portal": app_counts.get("external_portal", 0),
            "already_applied": app_counts.get("already_applied", 0),
            "applied_successfully": app_counts.get("applied_successfully", 0),
            "failed": app_counts.get("discovery_failed", 0),
            "unknown_flow": app_counts.get("unknown", 0),
            "quota_exhausted": app_counts.get("quota_exhausted", 0),
            "register": app_counts.get("register", 0),
            "login_required": app_counts.get("login_required", 0),
            "email": app_counts.get("email", 0),
            "total_questions": total_questions,
            "answered_questions": answered_questions,
            "coverage_pct": coverage_pct,
            "last_run": last_run,
        }


# ── Quota Status ──────────────────────────────────────────────────────────────

def get_quota_status() -> dict:
    """Return quota exhaustion status derived from the job_applications table."""
    with get_db() as conn:
        c = conn.cursor()

        # Total quota_exhausted records
        c.execute(
            "SELECT COUNT(*) FROM job_applications WHERE apply_type = 'quota_exhausted'"
        )
        total_quota = c.fetchone()[0]

        # Most recent detected_at for a quota_exhausted record
        c.execute(
            "SELECT MAX(detected_at) FROM job_applications WHERE apply_type = 'quota_exhausted'"
        )
        row = c.fetchone()
        last_detected = row[0] if row and row[0] else None

        # Consecutive count: walk backwards through detected_at order and count
        # how many of the most-recent records are quota_exhausted
        c.execute(
            """
            SELECT apply_type
            FROM job_applications
            ORDER BY detected_at DESC
            LIMIT 20
            """
        )
        recent_types = [r[0] for r in c.fetchall()]
        consecutive = 0
        for t in recent_types:
            if t == "quota_exhausted":
                consecutive += 1
            else:
                break

        exhausted = total_quota > 0 and consecutive >= 3
        status_label = "Exhausted" if exhausted else "Available"

        return {
            "total_quota_exhausted": total_quota,
            "last_detected": last_detected,
            "consecutive_count": consecutive,
            "is_exhausted": exhausted,
            "status_label": status_label,
        }


# ── Top Jobs (APPLY, sorted by probability) ──────────────────────────────────

def get_top_jobs(page: int = 1, per_page: int = 25,
                 sort: str = "probability", order: str = "desc",
                 search: str = "") -> tuple[list[dict], int]:
    """Return APPLY jobs sorted by interview probability."""
    sort_map = {
        "probability": "e.interview_probability",
        "company": "j.company_name",
        "title": "j.job_title",
        "location": "j.location",
    }
    sort_col = sort_map.get(sort, "e.interview_probability")
    order_dir = "ASC" if order == "asc" else "DESC"

    with get_db() as conn:
        c = conn.cursor()
        base_where = "WHERE UPPER(e.action) = 'APPLY'"
        params: list = []

        if search:
            base_where += " AND (j.job_title LIKE ? OR j.company_name LIKE ? OR j.location LIKE ?)"
            params.extend([f"%{search}%"] * 3)

        # Count
        c.execute(f"""
            SELECT COUNT(*)
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN job_applications a ON a.job_id = j.id
            {base_where}
        """, params)
        total = c.fetchone()[0]

        # Data
        offset = (page - 1) * per_page
        c.execute(f"""
            SELECT
                j.id, j.job_title, j.company_name, j.location,
                j.experience_required, j.job_url,
                e.interview_probability, e.priority, e.reason,
                e.confidence,
                COALESCE(a.apply_type, 'pending') AS apply_status
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN job_applications a ON a.job_id = j.id
            {base_where}
            ORDER BY {sort_col} {order_dir}, j.id ASC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        return _rows_to_dicts(c.fetchall()), total


# ── External Portal Jobs ─────────────────────────────────────────────────────

def get_external_portal_jobs(page: int = 1, per_page: int = 25,
                             search: str = "") -> tuple[list[dict], int]:
    """Return jobs with external_portal apply type (with apply URLs)."""
    with get_db() as conn:
        c = conn.cursor()
        base_where = "WHERE a.apply_type = 'external_portal'"
        params: list = []

        if search:
            base_where += " AND (j.job_title LIKE ? OR j.company_name LIKE ?)"
            params.extend([f"%{search}%"] * 2)

        c.execute(f"""
            SELECT COUNT(*)
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            JOIN job_applications a ON a.job_id = j.id
            {base_where}
        """, params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        c.execute(f"""
            SELECT
                j.id, j.job_title, j.company_name, j.location,
                e.interview_probability,
                a.apply_url, a.apply_type, j.job_url
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            JOIN job_applications a ON a.job_id = j.id
            {base_where}
            ORDER BY e.interview_probability DESC, j.id ASC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        return _rows_to_dicts(c.fetchall()), total


# ── Review Jobs ──────────────────────────────────────────────────────────────

def get_review_jobs(page: int = 1, per_page: int = 25,
                    search: str = "") -> tuple[list[dict], int]:
    """Return all REVIEW-action jobs."""
    with get_db() as conn:
        c = conn.cursor()
        base_where = "WHERE UPPER(e.action) = 'REVIEW'"
        params: list = []

        if search:
            base_where += " AND (j.job_title LIKE ? OR j.company_name LIKE ? OR e.reason LIKE ?)"
            params.extend([f"%{search}%"] * 3)

        c.execute(f"""
            SELECT COUNT(*)
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            {base_where}
        """, params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        c.execute(f"""
            SELECT
                j.id, j.job_title, j.company_name, j.location,
                j.experience_required, j.job_url,
                e.interview_probability, e.reason, e.confidence,
                e.priority, e.missing_skills
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            {base_where}
            ORDER BY e.interview_probability DESC, j.id ASC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        return _rows_to_dicts(c.fetchall()), total


# ── Failed Jobs ──────────────────────────────────────────────────────────────

def get_failed_jobs(page: int = 1, per_page: int = 25,
                    search: str = "") -> tuple[list[dict], int]:
    """Return jobs where discovery failed."""
    with get_db() as conn:
        c = conn.cursor()
        base_where = "WHERE a.status = 'discovery_failed' OR a.apply_type = 'discovery_failed'"
        params: list = []

        if search:
            base_where += " AND (j.job_title LIKE ? OR j.company_name LIKE ?)"
            params.extend([f"%{search}%"] * 2)

        c.execute(f"""
            SELECT COUNT(*)
            FROM jobs j
            JOIN job_applications a ON a.job_id = j.id
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            {base_where}
        """, params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        c.execute(f"""
            SELECT
                j.id, j.job_title, j.company_name, j.location,
                j.job_url,
                COALESCE(e.reason, 'Unknown') AS reason,
                e.interview_probability,
                a.detected_at
            FROM jobs j
            JOIN job_applications a ON a.job_id = j.id
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            {base_where}
            ORDER BY a.detected_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        return _rows_to_dicts(c.fetchall()), total


# ── Question Bank ────────────────────────────────────────────────────────────

def get_question_bank(page: int = 1, per_page: int = 25,
                      search: str = "") -> tuple[list[dict], int]:
    """Return question bank entries ordered by frequency."""
    with get_db() as conn:
        c = conn.cursor()
        base_where = "WHERE 1=1"
        params: list = []

        if search:
            base_where += " AND (question_text LIKE ? OR answer LIKE ?)"
            params.extend([f"%{search}%"] * 2)

        c.execute(f"SELECT COUNT(*) FROM question_bank {base_where}", params)
        total = c.fetchone()[0]

        offset = (page - 1) * per_page
        c.execute(f"""
            SELECT
                id, question_key, question_text, answer,
                usage_count, field_type, last_used_at
            FROM question_bank
            {base_where}
            ORDER BY usage_count DESC, id ASC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset])

        return _rows_to_dicts(c.fetchall()), total


# ── System Status ────────────────────────────────────────────────────────────

def get_system_status() -> dict:
    """Return pipeline system status from database state."""
    with get_db() as conn:
        c = conn.cursor()

        # Jobs table stats
        c.execute("SELECT COUNT(*) FROM jobs")
        total_jobs = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending'")
        pending_jobs = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'evaluated'")
        evaluated_jobs = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'")
        queued_jobs = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'")
        failed_jobs = c.fetchone()[0]

        # Evaluations
        c.execute("SELECT COUNT(*) FROM ai_evaluations")
        total_evals = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT run_id) FROM ai_evaluations")
        total_runs = c.fetchone()[0]

        c.execute("SELECT MAX(created_at) FROM ai_evaluations")
        row = c.fetchone()
        last_eval = row[0] if row and row[0] else "Never"

        # Distinct models used
        c.execute("SELECT DISTINCT model_name FROM ai_evaluations")
        models_used = [r[0] for r in c.fetchall()]

        # Discovery stats
        c.execute("SELECT COUNT(*) FROM job_applications")
        total_apps = c.fetchone()[0]

        c.execute("SELECT MAX(detected_at) FROM job_applications")
        row = c.fetchone()
        last_discovery = row[0] if row and row[0] else "Never"

        # Pending APPLY
        c.execute("""
            SELECT COUNT(*)
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN job_applications a ON a.job_id = j.id
            WHERE UPPER(e.action) = 'APPLY' AND a.job_id IS NULL
        """)
        pending_apply = c.fetchone()[0]

        # Question bank
        c.execute("SELECT COUNT(*) FROM question_bank")
        qb_total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM question_bank WHERE answer IS NOT NULL AND TRIM(answer) != ''")
        qb_answered = c.fetchone()[0]

        # Distinct search keywords and locations
        c.execute("SELECT DISTINCT search_keyword FROM jobs WHERE search_keyword IS NOT NULL AND search_keyword != ''")
        keywords = [r[0] for r in c.fetchall()]
        c.execute("SELECT DISTINCT search_location FROM jobs WHERE search_location IS NOT NULL AND search_location != ''")
        locations = [r[0] for r in c.fetchall()]

        # Recent evaluations per run
        c.execute("""
            SELECT run_id, COUNT(*) as cnt, MIN(created_at) as started, MAX(created_at) as ended
            FROM ai_evaluations
            GROUP BY run_id
            ORDER BY MAX(created_at) DESC
            LIMIT 5
        """)
        recent_runs = _rows_to_dicts(c.fetchall())

        return {
            "collector": {
                "total_jobs": total_jobs,
                "pending": pending_jobs,
                "evaluated": evaluated_jobs,
                "queued": queued_jobs,
                "failed": failed_jobs,
                "keywords": keywords,
                "locations": locations,
            },
            "evaluator": {
                "total_evaluations": total_evals,
                "total_runs": total_runs,
                "last_evaluation": last_eval,
                "models_used": models_used,
                "pending_eval": total_jobs - total_evals,
            },
            "discovery": {
                "total_processed": total_apps,
                "pending_apply": pending_apply,
                "last_discovery": last_discovery,
                "quota": get_quota_status(),
            },
            "question_bank": {
                "total": qb_total,
                "answered": qb_answered,
                "coverage_pct": round(qb_answered / qb_total * 100, 1) if qb_total > 0 else 0,
            },
            "recent_runs": recent_runs,
        }
