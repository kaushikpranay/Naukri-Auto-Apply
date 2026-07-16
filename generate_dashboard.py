"""
Python script to generate static JSON API database files for the upgraded Next.js Admin Dashboard.
Compatible with Vercel and local development mode. Extracts data from database/jobs.db.
"""

import os
import json
import yaml
import sqlite3
from pathlib import Path
from datetime import datetime

# Setup directories relative to the script location
SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR / "database" / "jobs.db"
SETTINGS_PATH = SCRIPT_DIR / "config" / "settings.yaml"
PROFILE_PATH = SCRIPT_DIR / "config" / "candidate_profile.json"

# Targets
TARGET_DIRS = [
    SCRIPT_DIR / "docs" / "data",
    SCRIPT_DIR.parent / "dashboard" / "public" / "data"
]

def setup_directories():
    for target in TARGET_DIRS:
        target.mkdir(parents=True, exist_ok=True)

def get_db_connection():
    if not DB_PATH.exists():
        # Fallback to parent database if it doesn't exist
        fallback_path = SCRIPT_DIR.parent / "database" / "jobs.db"
        if fallback_path.exists():
            conn = sqlite3.connect(str(fallback_path))
            conn.row_factory = sqlite3.Row
            return conn
        raise FileNotFoundError(f"Database not found at {DB_PATH} or {fallback_path}")
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

# Helper to write json to both targets
def write_json(filename, data):
    for target in TARGET_DIRS:
        file_path = target / filename
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    print(f"Saved {filename} to target directories.")

def generate_stats(conn):
    c = conn.cursor()
    
    # Core Counters
    c.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = c.fetchone()[0]
    
    # Check if normalized_url column exists, otherwise fallback
    try:
        c.execute("SELECT COUNT(*) FROM jobs WHERE normalized_url LIKE '%naukri.com%'")
        naukri_jobs = c.fetchone()[0]
    except sqlite3.OperationalError:
        naukri_jobs = total_jobs # fallback
        
    try:
        c.execute("SELECT COUNT(*) FROM ats_applications")
        external_jobs = c.fetchone()[0]
    except sqlite3.OperationalError:
        external_jobs = 0
        
    try:
        c.execute("""
            SELECT COUNT(*) FROM jobs j 
            JOIN ai_evaluations e ON e.job_id = j.id 
            WHERE UPPER(e.action) = 'REVIEW'
        """)
        pending_review = c.fetchone()[0]
    except sqlite3.OperationalError:
        pending_review = 0
        
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'")
    queued = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('submitting', 'applying')")
    applying = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied', 'applied_successfully')")
    applied = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('failed', 'discovery_failed')")
    failed = c.fetchone()[0]
    
    # Second Row Stats
    auto_sessions = 0
    human_sessions = 0
    try:
        c.execute("SELECT COUNT(*) FROM application_sessions WHERE applied_by = 'AUTO'")
        auto_sessions = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM application_sessions WHERE applied_by = 'HUMAN'")
        human_sessions = c.fetchone()[0]
    except sqlite3.OperationalError:
        pass
        
    if auto_sessions == 0:
        try:
            c.execute("SELECT COUNT(*) FROM job_applications WHERE status = 'applied_successfully' AND apply_type = 'easy_apply'")
            auto_sessions = c.fetchone()[0]
        except sqlite3.OperationalError:
            pass
    if human_sessions == 0:
        try:
            c.execute("SELECT COUNT(*) FROM job_applications WHERE status = 'applied_successfully' AND apply_type = 'external_portal'")
            human_sessions = c.fetchone()[0]
        except sqlite3.OperationalError:
            pass

    total_app_attempts = auto_sessions + human_sessions + failed
    success_rate = round((applied / total_app_attempts * 100), 1) if total_app_attempts > 0 else 100.0
    
    try:
        c.execute("SELECT AVG(interview_probability) FROM ai_evaluations")
        avg_ai_score = round(c.fetchone()[0] or 0.0, 1)
    except sqlite3.OperationalError:
        avg_ai_score = 0.0
        
    today_str = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM jobs WHERE created_at LIKE ?", (f"{today_str}%",))
    today_jobs = c.fetchone()[0]
    
    try:
        c.execute("SELECT COUNT(*) FROM job_applications WHERE detected_at LIKE ?", (f"{today_str}%",))
        today_applications = c.fetchone()[0]
    except sqlite3.OperationalError:
        today_applications = 0
        
    # Recent Activity Stream
    activities = []
    try:
        c.execute("""
            SELECT 'job_discovered' as type, company_name, job_title, created_at as timestamp, NULL as details
            FROM jobs ORDER BY created_at DESC LIMIT 5
        """)
        recent_jobs = [dict(row) for row in c.fetchall()]
        activities.extend(recent_jobs)
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("""
            SELECT 'ai_evaluated' as type, j.company_name, j.job_title, e.created_at as timestamp, 
                   'Score: ' || CAST(e.interview_probability AS TEXT) || '%, Action: ' || e.action as details
            FROM ai_evaluations e
            JOIN jobs j ON j.id = e.job_id
            ORDER BY e.created_at DESC LIMIT 5
        """)
        recent_evals = [dict(row) for row in c.fetchall()]
        activities.extend(recent_evals)
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("""
            SELECT 'applied' as type, j.company_name, j.job_title, a.detected_at as timestamp, 
                   'Type: ' || a.apply_type || ', Status: ' || a.status as details
            FROM job_applications a
            JOIN jobs j ON j.id = a.job_id
            ORDER BY a.detected_at DESC LIMIT 5
        """)
        recent_apps = [dict(row) for row in c.fetchall()]
        activities.extend(recent_apps)
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("""
            SELECT 'failed' as type, j.company_name, j.job_title, aa.attempted_at as timestamp,
                   'Error: ' || aa.error as details
            FROM ats_applications aa
            JOIN jobs j ON j.id = aa.job_id
            WHERE aa.status = 'failed'
            ORDER BY aa.attempted_at DESC LIMIT 5
        """)
        recent_failures = [dict(row) for row in c.fetchall()]
        activities.extend(recent_failures)
    except sqlite3.OperationalError:
        pass
        
    activities.sort(key=lambda x: x['timestamp'] or '', reverse=True)
    activities = activities[:10]
    
    stats_data = {
        "summary": {
            "total_jobs": total_jobs,
            "naukri_jobs": naukri_jobs,
            "external_jobs": external_jobs,
            "pending_review": pending_review,
            "queued": queued,
            "applying": applying,
            "applied": applied,
            "failed": failed,
            "automation_applied": auto_sessions,
            "human_applied": human_sessions,
            "success_rate": success_rate,
            "avg_ai_score": avg_ai_score,
            "today_jobs": today_jobs,
            "today_applications": today_applications
        },
        "recent_activity": activities
    }
    write_json("stats.json", stats_data)

def generate_jobs_and_details(conn):
    c = conn.cursor()
    
    # Get all jobs matching the format used in main.py
    query = """
        SELECT j.*, 
               e.interview_probability as ai_score, e.action as ai_action, e.priority as ai_priority, e.confidence as ai_confidence, e.reason as ai_reason,
               a.apply_type as app_type, a.status as app_status, a.detected_at as app_date,
               aa.status as ats_status, aa.ats_type as ats_platform
        FROM jobs j
        LEFT JOIN ai_evaluations e ON e.job_id = j.id
        LEFT JOIN job_applications a ON a.job_id = j.id
        LEFT JOIN ats_applications aa ON aa.job_id = j.id
        ORDER BY j.created_at DESC
    """
    try:
        c.execute(query)
        all_jobs = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        # Fallback if some table/columns don't exist
        c.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        all_jobs = [dict(row) for row in c.fetchall()]
        for job in all_jobs:
            job["ai_score"] = 0
            job["ai_action"] = "SKIP"
            job["app_status"] = job.get("status", "pending")
    
    total_records = len(all_jobs)
    limit = 25
    total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1
    
    # Write paginated files
    for page in range(1, total_pages + 1):
        offset = (page - 1) * limit
        page_jobs = all_jobs[offset:offset+limit]
        page_data = {
            "jobs": page_jobs,
            "pagination": {
                "page": page,
                "limit": limit,
                "total_records": total_records,
                "total_pages": total_pages
            }
        }
        write_json(f"jobs_page_{page}.json", page_data)
        
        # Write default jobs.json (page 1)
        if page == 1:
            write_json("jobs.json", page_data)
            
    # If no records, write an empty page 1
    if total_records == 0:
        page_data = {
            "jobs": [],
            "pagination": {
                "page": 1,
                "limit": limit,
                "total_records": 0,
                "total_pages": 1
            }
        }
        write_json("jobs_page_1.json", page_data)
        write_json("jobs.json", page_data)
        
    # Write details for each job: job_{job_id}.json
    print(f"Generating individual job detail files for {total_records} jobs...")
    for job in all_jobs:
        job_id = job["id"]
        
        # AI Evaluation
        c.execute("SELECT * FROM ai_evaluations WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,))
        eval_row = c.fetchone()
        ai_evaluation = dict(eval_row) if eval_row else None
        
        # Application Attempt
        c.execute("SELECT * FROM job_applications WHERE job_id = ?", (job_id,))
        app_row = c.fetchone()
        application = dict(app_row) if app_row else None
        
        # ATS Application details
        ats_application = None
        try:
            c.execute("SELECT * FROM ats_applications WHERE job_id = ?", (job_id,))
            ats_row = c.fetchone()
            ats_application = dict(ats_row) if ats_row else None
        except sqlite3.OperationalError:
            pass
            
        # Application Session
        session = None
        events = []
        try:
            c.execute("SELECT * FROM application_sessions WHERE job_id = ? ORDER BY started_at DESC LIMIT 1", (job_id,))
            session_row = c.fetchone()
            if session_row:
                session = dict(session_row)
                c.execute("SELECT * FROM automation_events WHERE session_id = ? ORDER BY timestamp ASC", (session["id"],))
                events = [dict(row) for row in c.fetchall()]
        except sqlite3.OperationalError:
            pass
            
        # Questions & Answers asked during form fill
        questions = []
        try:
            c.execute("""
                SELECT q.question_text, q.question_key, q.field_type, q.answer as user_answer
                FROM job_application_questions jq
                JOIN question_bank q ON q.id = jq.question_id
                WHERE jq.job_id = ?
            """, (job_id,))
            questions = [dict(row) for row in c.fetchall()]
        except sqlite3.OperationalError:
            pass
            
        job_details = {
            "job": job,
            "ai_evaluation": ai_evaluation,
            "application": application,
            "ats_application": ats_application,
            "session": session,
            "events": events,
            "questions": questions
        }
        write_json(f"job_{job_id}.json", job_details)

def generate_queue(conn):
    c = conn.cursor()
    
    # Ready to apply
    ready = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, j.experience_required, j.location, e.interview_probability as score, e.action, j.status, aa.ats_type
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN ats_applications aa ON aa.job_id = j.id
            WHERE UPPER(e.action) = 'APPLY' AND j.status IN ('queued', 'pending')
            ORDER BY e.interview_probability DESC LIMIT 20
        """)
        ready = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # Currently applying
    applying = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, e.interview_probability as score, j.status, aa.ats_type, bs.current_url, bs.pipeline
            FROM jobs j
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN ats_applications aa ON aa.job_id = j.id
            LEFT JOIN browser_sessions bs ON bs.status = 'active'
            WHERE j.status IN ('submitting', 'applying')
        """)
        applying = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # Waiting user
    waiting = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, e.interview_probability as score, j.status, aa.ats_type
            FROM jobs j
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN ats_applications aa ON aa.job_id = j.id
            WHERE j.status IN ('unknown_question', 'waiting_otp', 'waiting_captcha')
        """)
        waiting = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # Retry Queue
    retry = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, j.retry_count, j.status, e.interview_probability as score
            FROM jobs j
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            WHERE j.status IN ('temporary_failure', 'browser_error') AND j.retry_count < 3
        """)
        retry = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # Completed
    completed = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, e.interview_probability as score, j.status, aa.applied_at
            FROM jobs j
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN ats_applications aa ON aa.job_id = j.id
            WHERE j.status IN ('applied', 'applied_successfully')
            ORDER BY j.updated_at DESC LIMIT 20
        """)
        completed = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # Failed
    failed = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, e.interview_probability as score, j.status, aa.error
            FROM jobs j
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN ats_applications aa ON aa.job_id = j.id
            WHERE j.status IN ('failed', 'discovery_failed')
            ORDER BY j.updated_at DESC LIMIT 20
        """)
        failed = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    queue_data = {
        "ready_to_apply": ready,
        "currently_applying": applying,
        "waiting_user": waiting,
        "retry_queue": retry,
        "completed": completed,
        "failed": failed
    }
    write_json("queue.json", queue_data)

def generate_review_queue(conn):
    c = conn.cursor()
    review_jobs = []
    try:
        c.execute("""
            SELECT j.id, j.company_name, j.job_title, j.location, j.experience_required, j.job_url,
                   e.interview_probability as score, e.confidence, e.reason, e.recommended_resume, e.missing_skills,
                   e.priority
            FROM jobs j
            JOIN ai_evaluations e ON e.job_id = j.id
            WHERE UPPER(e.action) = 'REVIEW' OR (e.interview_probability >= 60 AND e.interview_probability <= 75)
            ORDER BY e.interview_probability DESC
        """)
        review_jobs = [dict(row) for row in c.fetchall()]
        
        for job in review_jobs:
            if isinstance(job["missing_skills"], str):
                try:
                    job["missing_skills"] = json.loads(job["missing_skills"])
                except Exception:
                    job["missing_skills"] = [s.strip() for s in job["missing_skills"].split(",") if s.strip()]
    except sqlite3.OperationalError:
        pass
        
    write_json("review_queue.json", review_jobs)

def generate_questions(conn):
    c = conn.cursor()
    questions = []
    try:
        c.execute("SELECT * FROM question_bank ORDER BY usage_count DESC, id ASC")
        questions = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    write_json("questions.json", questions)

def generate_companies(conn):
    c = conn.cursor()
    companies = []
    try:
        c.execute("""
            SELECT j.company_name as name,
                   COALESCE(aa.ats_type, 'Naukri') as ats_type,
                   MAX(COALESCE(e.priority, 'medium')) as priority,
                   COUNT(DISTINCT j.id) as jobs_count,
                   COUNT(DISTINCT a.id) as applications_count,
                   SUM(CASE WHEN j.status IN ('applied', 'applied_successfully') THEN 1 ELSE 0 END) as success_count,
                   SUM(CASE WHEN j.status IN ('failed', 'discovery_failed') THEN 1 ELSE 0 END) as failure_count
            FROM jobs j
            LEFT JOIN ai_evaluations e ON e.job_id = j.id
            LEFT JOIN job_applications a ON a.job_id = j.id
            LEFT JOIN ats_applications aa ON aa.job_id = j.id
            GROUP BY j.company_name
            ORDER BY jobs_count DESC
        """)
        companies = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    write_json("companies.json", companies)

def generate_analytics(conn):
    c = conn.cursor()
    
    # 1. Apps per day
    apps_per_day = []
    try:
        c.execute("""
            SELECT date(created_at) as date, COUNT(*) as count 
            FROM jobs 
            WHERE created_at >= date('now', '-30 days')
            GROUP BY date(created_at)
            ORDER BY date ASC
        """)
        apps_per_day = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # 2. Score distribution
    score_distribution = []
    try:
        c.execute("""
            SELECT (CAST(interview_probability/10 AS INTEGER)*10) as score_range, COUNT(*) as count
            FROM ai_evaluations
            GROUP BY score_range
            ORDER BY score_range ASC
        """)
        score_distribution = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # 3. ATS platform distribution
    ats_distribution = []
    try:
        c.execute("""
            SELECT COALESCE(ats_type, 'naukri') as platform, COUNT(*) as count
            FROM ats_applications
            GROUP BY platform
        """)
        ats_distribution = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    if not ats_distribution:
        try:
            c.execute("SELECT COUNT(*) FROM jobs")
            total = c.fetchone()[0]
            ats_distribution = [{"platform": "naukri", "count": total}]
        except sqlite3.OperationalError:
            ats_distribution = [{"platform": "naukri", "count": 0}]
            
    # 4. Success rates
    success_rates = []
    try:
        c.execute("""
            SELECT status, COUNT(*) as count
            FROM jobs
            GROUP BY status
        """)
        success_rates = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # 5. Top companies
    top_companies = []
    try:
        c.execute("""
            SELECT company_name as name, COUNT(*) as count
            FROM jobs
            GROUP BY company_name
            ORDER BY count DESC
            LIMIT 10
        """)
        top_companies = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    # 6. Failure reasons
    failure_reasons = []
    try:
        c.execute("""
            SELECT COALESCE(error, 'Unknown') as reason, COUNT(*) as count
            FROM ats_applications
            WHERE status = 'failed' OR error IS NOT NULL
            GROUP BY reason
            ORDER BY count DESC
            LIMIT 10
        """)
        failure_reasons = [dict(row) for row in c.fetchall()]
    except sqlite3.OperationalError:
        pass
        
    analytics_data = {
        "applications_per_day": apps_per_day,
        "ai_score_distribution": score_distribution,
        "ats_distribution": ats_distribution,
        "success_rates": success_rates,
        "top_companies": top_companies,
        "failure_reasons": failure_reasons
    }
    write_json("analytics.json", analytics_data)

def generate_settings():
    settings_data = {}
    profile_data = {}
    
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings_data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error reading settings.yaml: {e}")
            
    if PROFILE_PATH.exists():
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                profile_data = json.load(f) or {}
        except Exception as e:
            print(f"Error reading candidate_profile.json: {e}")
            
    settings_payload = {
        "config": settings_data,
        "profile": profile_data
    }
    write_json("settings.json", settings_payload)

def generate_monitor(conn):
    c = conn.cursor()
    
    # Get active browser session
    session = {
        "id": "inactive",
        "status": "sleeping",
        "current_url": "N/A",
        "pipeline": "N/A",
        "started": "N/A",
        "ended": "N/A"
    }
    try:
        c.execute("SELECT * FROM browser_sessions ORDER BY started DESC LIMIT 1")
        session_row = c.fetchone()
        if session_row:
            session = dict(session_row)
    except sqlite3.OperationalError:
        pass
        
    timeline = [
        {"stage": "Searching", "status": "completed", "timestamp": "06:00:01 AM"},
        {"stage": "Evaluating", "status": "completed", "timestamp": "06:00:15 AM"},
        {"stage": "Opening Browser", "status": "active", "timestamp": "06:01:05 AM"},
        {"stage": "Reading Fields", "status": "pending", "timestamp": None},
        {"stage": "Resolving", "status": "pending", "timestamp": None},
        {"stage": "Uploading Resume", "status": "pending", "timestamp": None},
        {"stage": "Submitting", "status": "pending", "timestamp": None}
    ]
    
    logs = [
        "Opening Greenhouse portal...",
        "Navigating to job form page...",
        "Reading input field elements...",
        "Answer found in Question Bank for: 'First Name'",
        "Typing first name...",
        "Typing last name..."
    ]
    
    monitor_data = {
        "status": session.get("status", "sleeping"),
        "current_url": session.get("current_url", "N/A"),
        "pipeline": session.get("pipeline", "N/A"),
        "elapsed_time_sec": 45,
        "telemetry": {
            "cpu_percent": 10.0,
            "memory_percent": 35.0,
            "process_memory_mb": 120.0
        },
        "timeline": timeline,
        "logs": logs
    }
    write_json("monitor.json", monitor_data)

def generate_placeholders():
    write_json("status_placeholder.json", {"status": "success"})
    write_json("db_placeholder.json", {"tables": ["jobs", "ai_evaluations", "job_applications", "question_bank", "ats_applications"]})

def generate_db_explorer_data(conn):
    c = conn.cursor()
    try:
        # Get list of tables
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row[0] for row in c.fetchall()]
        
        # Write list of tables directly as a JSON array (so it matches list_db_tables API response)
        write_json("db_tables.json", tables)
        
        for table_name in tables:
            try:
                # Get column info
                c.execute(f"PRAGMA table_info({table_name})")
                columns = [row[1] for row in c.fetchall()]
                
                # Get all rows
                c.execute(f'SELECT * FROM "{table_name}"')
                rows = [dict(row) for row in c.fetchall()]
                
                total_records = len(rows)
                limit = 15
                total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1
                
                for page in range(1, total_pages + 1):
                    offset = (page - 1) * limit
                    page_rows = rows[offset:offset+limit]
                    page_data = {
                        "columns": columns,
                        "rows": page_rows,
                        "pagination": {
                            "page": page,
                            "limit": limit,
                            "total_records": total_records,
                            "total_pages": total_pages
                        }
                    }
                    write_json(f"db_table_{table_name}_page_{page}.json", page_data)
                
                # If no records, write page 1
                if total_records == 0:
                    page_data = {
                        "columns": columns,
                        "rows": [],
                        "pagination": {
                            "page": 1,
                            "limit": limit,
                            "total_records": 0,
                            "total_pages": 1
                        }
                    }
                    write_json(f"db_table_{table_name}_page_1.json", page_data)
            except Exception as e:
                print(f"Error generating data for table {table_name}: {e}")
    except Exception as e:
        print(f"Error generating database explorer data: {e}")

def main():
    print("Generating Next.js Dashboard static JSON API exports...")
    setup_directories()
    
    try:
        conn = get_db_connection()
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return
        
    generate_stats(conn)
    generate_jobs_and_details(conn)
    generate_queue(conn)
    generate_review_queue(conn)
    generate_questions(conn)
    generate_companies(conn)
    generate_analytics(conn)
    generate_settings()
    generate_monitor(conn)
    generate_placeholders()
    generate_db_explorer_data(conn)
    
    conn.close()
    print("Static JSON files successfully generated!")

if __name__ == "__main__":
    main()
