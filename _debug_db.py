import sqlite3
conn = sqlite3.connect("database/jobs.db")
conn.row_factory = sqlite3.Row

print("=== APPLY job status distribution ===")
cur = conn.execute("""
    SELECT j.status, COUNT(*) as cnt
    FROM jobs j
    JOIN ai_evaluations e ON e.job_id = j.id
    WHERE UPPER(e.action) = 'APPLY'
    GROUP BY j.status
    ORDER BY cnt DESC
""")
for row in cur:
    print(f"  {row[0]}: {row[1]}")

print()
print("=== Inline query count (discover_applications.py while-loop check) ===")
cur = conn.execute("""
    SELECT COUNT(*)
    FROM jobs j
    JOIN ai_evaluations e ON e.job_id = j.id
    LEFT JOIN job_applications a ON a.job_id = j.id
    WHERE UPPER(e.action) = 'APPLY'
      AND (
           j.status IN ('unknown_question', 'waiting_for_user', 'quota_exhausted', 'temporary_failure', 'browser_error')
           OR (a.job_id IS NULL AND COALESCE(j.status, '') NOT IN ('unknown_question', 'waiting_for_user', 'quota_exhausted', 'temporary_failure', 'browser_error'))
      )
""")
print(f"  Count: {cur.fetchone()[0]}")

print()
print("=== APPLY jobs with NO application record ===")
cur = conn.execute("""
    SELECT j.id, j.status, j.retry_count
    FROM jobs j
    JOIN ai_evaluations e ON e.job_id = j.id
    LEFT JOIN job_applications a ON a.job_id = j.id
    WHERE UPPER(e.action) = 'APPLY' AND a.job_id IS NULL
    LIMIT 10
""")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  id={r[0]} status={r[1]} retry={r[2]}")
else:
    print("  None")

conn.close()
