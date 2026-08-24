"""
list_needs_human_review.py
CLI report of all jobs in 'needs_human_review' status, listing blocking questions and failure reasons.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("database/jobs.db")


def main() -> None:
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    query = """
        SELECT
            j.id,
            j.job_title,
            j.company_name,
            j.job_url,
            j.failure_reason,
            j.failure_type,
            j.failed_at,
            GROUP_CONCAT(COALESCE(q.question_text, q.question_key), ' | ') AS blocking_questions
        FROM jobs j
        LEFT JOIN job_application_questions q ON q.job_id = j.id
        WHERE j.status = 'needs_human_review'
        GROUP BY j.id
        ORDER BY j.id ASC
    """
    rows = c.execute(query).fetchall()

    print(f"\n{'='*70}")
    print(f"  JOBS REQUIRING HUMAN REVIEW (Count: {len(rows)})")
    print(f"{'='*70}\n")

    if not rows:
        print("No jobs currently requiring human review.\n")
        return

    for idx, r in enumerate(rows, 1):
        print(f"[{idx}] Job ID: {r['id']} | Company: {r['company_name']}")
        print(f"    Title: {r['job_title']}")
        print(f"    URL: {r['job_url']}")
        print(f"    Failure Reason: {r['failure_reason'] or 'N/A'}")
        print(f"    Failure Type: {r['failure_type'] or 'N/A'}")
        if r['blocking_questions']:
            print(f"    Blocking Question(s): {r['blocking_questions']}")
        print("-" * 70)


if __name__ == "__main__":
    main()
