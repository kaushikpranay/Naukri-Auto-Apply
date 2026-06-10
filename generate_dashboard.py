"""
Python script to generate static HTML, CSS, and JS files for the Naukri Dashboard.
Compatible with GitHub Pages. Extracts data from database/jobs.db.
"""

import os
import json
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime

# Setup directories
DOCS_DIR = Path("docs")
DATA_DIR = DOCS_DIR / "data"
CSS_DIR = DOCS_DIR / "css"
JS_DIR = DOCS_DIR / "js"

def setup_directories():
    DOCS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    CSS_DIR.mkdir(exist_ok=True)
    JS_DIR.mkdir(exist_ok=True)

def fetch_data():
    db_path = Path("database/jobs.db")
    if not db_path.exists():
        print(f"Error: Database not found at {db_path}")
        return None

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get overview stats
    c.execute("SELECT COUNT(*) FROM jobs")
    total_jobs = c.fetchone()[0]

    c.execute("SELECT UPPER(action) AS act, COUNT(*) FROM ai_evaluations GROUP BY UPPER(action)")
    eval_counts = {row[0]: row[1] for row in c.fetchall()}

    c.execute("SELECT apply_type, COUNT(*) FROM job_applications GROUP BY apply_type")
    app_counts = {row[0] or "unknown": row[1] for row in c.fetchall()}

    c.execute("SELECT COUNT(*) FROM jobs WHERE id NOT IN (SELECT job_id FROM ai_evaluations)")
    pending_eval = c.fetchone()[0]

    c.execute("""
        SELECT COUNT(*)
        FROM jobs j
        JOIN ai_evaluations e ON e.job_id = j.id
        LEFT JOIN job_applications a ON a.job_id = j.id
        WHERE UPPER(e.action) = 'APPLY'
          AND (
              j.status IN ('unknown_question', 'quota_exhausted', 'temporary_failure', 'browser_error')
              OR (a.job_id IS NULL AND COALESCE(j.status, '') NOT IN ('unknown_question', 'quota_exhausted', 'temporary_failure', 'browser_error'))
          )
    """)
    pending_apply = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM question_bank")
    total_questions = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM question_bank WHERE answer IS NOT NULL AND TRIM(answer) != ''")
    answered_questions = c.fetchone()[0]
    coverage_pct = round((answered_questions / total_questions * 100), 1) if total_questions > 0 else 0

    c.execute("SELECT MAX(created_at) FROM ai_evaluations")
    row = c.fetchone()
    last_run = row[0] if row and row[0] else "Never"

    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM jobs WHERE created_at LIKE ?", (f"{today}%",))
    jobs_today = c.fetchone()[0]

    # Retry queue breakdown
    c.execute("""
        SELECT status, COUNT(*)
        FROM jobs
        WHERE status IN ('unknown_question', 'quota_exhausted', 'temporary_failure', 'browser_error')
        GROUP BY status
    """)
    retry_counts = {row[0]: row[1] for row in c.fetchall()}
    retry_total = sum(retry_counts.values())

    overview_stats = {
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
        "retry_queue": {
            "count": retry_total,
            "reasons": {
                "unknown_question": retry_counts.get("unknown_question", 0),
                "quota_exhausted": retry_counts.get("quota_exhausted", 0),
                "temporary_failure": retry_counts.get("temporary_failure", 0),
                "browser_error": retry_counts.get("browser_error", 0),
            }
        }
    }

    # Quota status
    c.execute("SELECT COUNT(*) FROM job_applications WHERE apply_type = 'quota_exhausted'")
    total_quota = c.fetchone()[0]
    c.execute("SELECT MAX(detected_at) FROM job_applications WHERE apply_type = 'quota_exhausted'")
    row = c.fetchone()
    last_detected = row[0] if row and row[0] else None

    c.execute("SELECT apply_type FROM job_applications ORDER BY detected_at DESC LIMIT 20")
    recent_types = [r[0] for r in c.fetchall()]
    consecutive = 0
    for t in recent_types:
        if t == "quota_exhausted":
            consecutive += 1
        else:
            break
    exhausted = total_quota > 0 and consecutive >= 3
    status_label = "Exhausted" if exhausted else "Available"

    quota_status = {
        "total_quota_exhausted": total_quota,
        "last_detected": last_detected,
        "consecutive_count": consecutive,
        "is_exhausted": exhausted,
        "status_label": status_label,
    }

    # Top Jobs
    c.execute("""
        SELECT
            j.id, j.job_title, j.company_name, j.location,
            j.experience_required, j.job_url,
            e.interview_probability, e.priority, e.reason,
            e.confidence,
            COALESCE(a.apply_type, 'pending') AS apply_status
        FROM jobs j
        JOIN ai_evaluations e ON e.job_id = j.id
        LEFT JOIN job_applications a ON a.job_id = j.id
        WHERE UPPER(e.action) = 'APPLY'
        ORDER BY e.interview_probability DESC, j.id ASC
    """)
    top_jobs = [dict(row) for row in c.fetchall()]

    # External Jobs
    c.execute("""
        SELECT
            j.id, j.job_title, j.company_name, j.location,
            e.interview_probability,
            a.apply_url, a.apply_type, j.job_url
        FROM jobs j
        JOIN ai_evaluations e ON e.job_id = j.id
        JOIN job_applications a ON a.job_id = j.id
        WHERE a.apply_type = 'external_portal'
        ORDER BY e.interview_probability DESC, j.id ASC
    """)
    external_jobs = [dict(row) for row in c.fetchall()]

    # Review Jobs
    c.execute("""
        SELECT
            j.id, j.job_title, j.company_name, j.location,
            j.experience_required, j.job_url,
            e.interview_probability, e.reason, e.confidence,
            e.priority, e.missing_skills
        FROM jobs j
        JOIN ai_evaluations e ON e.job_id = j.id
        WHERE UPPER(e.action) = 'REVIEW'
        ORDER BY e.interview_probability DESC, j.id ASC
    """)
    review_jobs = [dict(row) for row in c.fetchall()]

    # Failed Jobs
    c.execute("""
        SELECT
            j.id, j.job_title, j.company_name, j.location,
            j.job_url,
            COALESCE(e.reason, 'Unknown') AS reason,
            e.interview_probability,
            a.detected_at
        FROM jobs j
        JOIN job_applications a ON a.job_id = j.id
        LEFT JOIN ai_evaluations e ON e.job_id = j.id
        WHERE a.status = 'discovery_failed' OR a.apply_type = 'discovery_failed'
        ORDER BY a.detected_at DESC
    """)
    failed_jobs = [dict(row) for row in c.fetchall()]

    # Question Bank
    c.execute("""
        SELECT
            id, question_key, question_text, answer,
            usage_count, field_type, last_used_at
        FROM question_bank
        ORDER BY usage_count DESC, id ASC
    """)
    question_bank = [dict(row) for row in c.fetchall()]

    # System Status
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'pending'")
    pending_jobs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'evaluated'")
    evaluated_jobs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'")
    queued_jobs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'")
    failed_jobs_count = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT run_id) FROM ai_evaluations")
    total_runs = c.fetchone()[0]

    c.execute("SELECT DISTINCT model_name FROM ai_evaluations")
    models_used = [r[0] for r in c.fetchall()]

    c.execute("SELECT COUNT(*) FROM job_applications")
    total_apps = c.fetchone()[0]
    c.execute("SELECT MAX(detected_at) FROM job_applications")
    row = c.fetchone()
    last_discovery = row[0] if row and row[0] else "Never"

    c.execute("SELECT DISTINCT search_keyword FROM jobs WHERE search_keyword IS NOT NULL AND search_keyword != ''")
    keywords = [r[0] for r in c.fetchall()]
    c.execute("SELECT DISTINCT search_location FROM jobs WHERE search_location IS NOT NULL AND search_location != ''")
    locations = [r[0] for r in c.fetchall()]

    c.execute("""
        SELECT run_id, COUNT(*) as cnt, MIN(created_at) as started, MAX(created_at) as ended
        FROM ai_evaluations
        GROUP BY run_id
        ORDER BY MAX(created_at) DESC
        LIMIT 5
    """)
    recent_runs = [dict(row) for row in c.fetchall()]

    system_status = {
        "collector": {
            "total_jobs": total_jobs,
            "pending": pending_jobs,
            "evaluated": evaluated_jobs,
            "queued": queued_jobs,
            "failed": failed_jobs_count,
            "keywords": keywords,
            "locations": locations,
        },
        "evaluator": {
            "total_evaluations": len(top_jobs) + len(review_jobs),
            "total_runs": total_runs,
            "last_evaluation": last_run,
            "models_used": models_used,
            "pending_eval": pending_eval,
        },
        "discovery": {
            "total_processed": total_apps,
            "pending_apply": pending_apply,
            "last_discovery": last_discovery,
            "quota": quota_status,
            "retry_queue": {
                "count": retry_total,
                "reasons": {
                    "unknown_question": retry_counts.get("unknown_question", 0),
                    "quota_exhausted": retry_counts.get("quota_exhausted", 0),
                    "temporary_failure": retry_counts.get("temporary_failure", 0),
                    "browser_error": retry_counts.get("browser_error", 0),
                }
            }
        },
        "question_bank": {
            "total": total_questions,
            "answered": answered_questions,
            "coverage_pct": coverage_pct,
        },
        "recent_runs": recent_runs,
    }

    conn.close()

    return {
        "overview_stats": overview_stats,
        "quota_status": quota_status,
        "top_jobs": top_jobs,
        "external_jobs": external_jobs,
        "review_jobs": review_jobs,
        "failed_jobs": failed_jobs,
        "question_bank": question_bank,
        "system_status": system_status,
        "generated_at": datetime.now().isoformat(),
    }

def write_html_templates():
    # Overview Template
    overview_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - Overview</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-grid-fill text-accent"></i> Overview</h1>
            <p class="text-muted mb-0">System counters and job processing status</p>
        </div>

        <div class="row g-3 mb-4">
            <div class="col-6 col-md-4 col-lg-2">
                <div class="stat-card stat-card-primary h-100">
                    <div class="stat-icon text-accent"><i class="bi bi-briefcase-fill"></i></div>
                    <div class="stat-value" id="stat-total-jobs">0</div>
                    <div class="stat-label">Total Jobs</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="stat-card stat-card-success h-100">
                    <div class="stat-icon text-success"><i class="bi bi-calendar-check-fill"></i></div>
                    <div class="stat-value" id="stat-jobs-today">0</div>
                    <div class="stat-label">Jobs Today</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="stat-card stat-card-warning h-100">
                    <div class="stat-icon text-warning"><i class="bi bi-clock-history"></i></div>
                    <div class="stat-value" id="stat-pending-eval">0</div>
                    <div class="stat-label">Pending Eval</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="stat-card stat-card-success h-100">
                    <div class="stat-icon text-success"><i class="bi bi-send-check-fill"></i></div>
                    <div class="stat-value" id="stat-pending-apply">0</div>
                    <div class="stat-label">Pending Apply</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="stat-card stat-card-primary h-100">
                    <div class="stat-icon text-accent"><i class="bi bi-patch-question-fill"></i></div>
                    <div class="stat-value" id="stat-coverage-pct">0%</div>
                    <div class="stat-label">Q-Bank Coverage</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="stat-card stat-card-muted h-100">
                    <div class="stat-icon text-muted"><i class="bi bi-play-circle-fill"></i></div>
                    <div class="stat-value" style="font-size: 1.1rem; padding-top: 0.5rem;" id="stat-last-run">Never</div>
                    <div class="stat-label">Last Evaluation</div>
                </div>
            </div>
        </div>

        <h2 class="section-title"><i class="bi bi-send-fill text-accent"></i> Applications Breakdown</h2>
        <div class="row g-3 mb-4">
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-success" id="count-easy-apply">0</div>
                    <div class="mini-stat-label">Easy Apply</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-info" id="count-external-portal">0</div>
                    <div class="mini-stat-label">External Portal</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-muted" id="count-already-applied">0</div>
                    <div class="mini-stat-label">Already Applied</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-warning" id="count-unknown-flow">0</div>
                    <div class="mini-stat-label">Unknown Flow</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-danger" id="count-failed">0</div>
                    <div class="mini-stat-label">Failed</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-danger" id="count-quota-exhausted">0</div>
                    <div class="mini-stat-label">Quota Exhausted</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value" style="color: var(--accent)" id="count-applied-ok">0</div>
                    <div class="mini-stat-label">Applied OK</div>
                </div>
            </div>
            <div class="col-6 col-md-4 col-lg-2">
                <div class="mini-stat-card">
                    <div class="mini-stat-value text-secondary" id="count-hidden-jobs">0</div>
                    <div class="mini-stat-label">Hidden Jobs</div>
                </div>
            </div>
        </div>

        <div class="row g-3">
            <div class="col-12 col-md-4">
                <div class="glass-card mb-4 p-3">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h6 class="fw-bold text-white mb-0"><i class="bi bi-patch-question-fill text-accent"></i> Question Bank</h6>
                        <span class="text-muted small" id="qbank-ratio">0/0 answered</span>
                    </div>
                    <div class="progress mb-2" style="height: 8px;">
                        <div class="progress-bar" id="qbank-progress" style="width: 0%"></div>
                    </div>
                    <a href="question_bank.html" class="btn btn-sm btn-outline-accent mt-2">Manage Question Bank</a>
                </div>
            </div>
            <div class="col-12 col-md-4">
                <div class="glass-card mb-4 p-3">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h6 class="fw-bold text-white mb-0"><i class="bi bi-shield-lock-fill text-accent"></i> Quota Status</h6>
                        <span class="badge bg-success" id="quota-status-badge">Available</span>
                    </div>
                    <div class="d-flex justify-content-between small text-muted mb-1">
                        <span>Consecutive Hits:</span>
                        <span id="quota-consecutive">0/3</span>
                    </div>
                    <a href="system_status.html" class="btn btn-sm btn-outline-accent mt-2">View Quota Details</a>
                </div>
            </div>
            <div class="col-12 col-md-4">
                <div class="glass-card mb-4 p-3">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <h6 class="fw-bold text-white mb-0"><i class="bi bi-arrow-repeat text-accent"></i> Retry Queue</h6>
                        <span class="badge bg-warning text-dark" id="retry-queue-badge">0</span>
                    </div>
                    <div class="d-flex justify-content-between small text-muted mb-1" style="font-size: 0.75rem;">
                        <span>Q: <strong id="retry-reason-q">0</strong></span>
                        <span>Quota: <strong id="retry-reason-quota">0</strong></span>
                        <span>Temp: <strong id="retry-reason-temp">0</strong></span>
                        <span>Browser: <strong id="retry-reason-browser">0</strong></span>
                    </div>
                    <a href="system_status.html" class="btn btn-sm btn-outline-accent mt-2">View System Status</a>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(overview_html)

    # Top Jobs Template
    top_jobs_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - Top Jobs</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-star-fill text-accent"></i> Top Jobs</h1>
            <p class="text-muted mb-0">List of shortlisted jobs identified for auto-applying</p>
        </div>

        <div class="glass-card p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12 col-md-5">
                    <div class="input-group">
                        <span class="input-group-text glass-input-addon"><i class="bi bi-search"></i></span>
                        <input type="text" id="search-input" class="form-control glass-input" placeholder="Search role, company or location...">
                    </div>
                </div>
                <div class="col-6 col-md-3">
                    <select id="status-filter" class="form-select glass-input">
                        <option value="all">All Statuses</option>
                        <option value="pending">Pending</option>
                        <option value="easy_apply">Easy Apply</option>
                        <option value="external_portal">External Portal</option>
                        <option value="already_applied">Already Applied</option>
                        <option value="applied_successfully">Applied Successfully</option>
                        <option value="quota_exhausted">Quota Exhausted</option>
                        <option value="discovery_failed">Failed</option>
                    </select>
                </div>
                <div class="col-6 col-md-4">
                    <select id="sort-select" class="form-select glass-input">
                        <option value="probability_desc">Probability: High to Low</option>
                        <option value="probability_asc">Probability: Low to High</option>
                        <option value="company_asc">Company Name (A-Z)</option>
                        <option value="title_asc">Role Title (A-Z)</option>
                    </select>
                </div>
            </div>
        </div>

        <!-- Desktop View -->
        <div class="glass-card d-none d-md-block overflow-hidden">
            <div class="table-responsive">
                <table class="table table-hover align-middle glass-table mb-0">
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Role</th>
                            <th>Experience</th>
                            <th>Probability</th>
                            <th>Status</th>
                            <th>Analysis</th>
                        </tr>
                    </thead>
                    <tbody id="jobs-tbody">
                        <tr><td colspan="6" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Mobile Cards View -->
        <div class="d-md-none" id="mobile-cards">
            <div class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</div>
        </div>

        <!-- Pagination -->
        <div class="pagination-nav"></div>
    </div>

    <!-- Reason Modal -->
    <div class="modal fade" id="analysisModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content bg-dark border-secondary text-white">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title fw-bold text-accent" id="modal-job-title">Job Details</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row g-2 mb-3">
                        <div class="col-6">
                            <div class="p-2 rounded bg-secondary-subtle">
                                <small class="text-muted d-block">Probability</small>
                                <strong id="modal-probability" class="text-success">0%</strong>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="p-2 rounded bg-secondary-subtle">
                                <small class="text-muted d-block">Confidence</small>
                                <strong id="modal-confidence" class="text-info">0%</strong>
                            </div>
                        </div>
                    </div>
                    <div class="p-3 rounded reason-text">
                        <h6 class="fw-bold text-white mb-2">AI Assessment Reason:</h6>
                        <p id="modal-reason" class="mb-0 small text-muted"></p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "top_jobs.html", "w", encoding="utf-8") as f:
        f.write(top_jobs_html)

    # External Jobs Template
    external_jobs_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - External Jobs</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-link-45deg text-accent"></i> External Portal Jobs</h1>
            <p class="text-muted mb-0">Jobs requiring manual application submissions on third-party domains</p>
        </div>

        <div class="glass-card p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12">
                    <div class="input-group">
                        <span class="input-group-text glass-input-addon"><i class="bi bi-search"></i></span>
                        <input type="text" id="search-input" class="form-control glass-input" placeholder="Search role or company...">
                    </div>
                </div>
            </div>
        </div>

        <!-- Desktop View -->
        <div class="glass-card d-none d-md-block overflow-hidden">
            <div class="table-responsive">
                <table class="table table-hover align-middle glass-table mb-0">
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Role</th>
                            <th>Location</th>
                            <th>Probability</th>
                            <th>Apply</th>
                        </tr>
                    </thead>
                    <tbody id="jobs-tbody">
                        <tr><td colspan="5" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Mobile Cards View -->
        <div class="d-md-none" id="mobile-cards">
            <div class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</div>
        </div>

        <!-- Pagination -->
        <div class="pagination-nav"></div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "external_jobs.html", "w", encoding="utf-8") as f:
        f.write(external_jobs_html)

    # Review Jobs Template
    review_jobs_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - Review Jobs</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-eye-fill text-accent"></i> Review Jobs</h1>
            <p class="text-muted mb-0">Jobs flagged by AI as medium matches requiring review</p>
        </div>

        <div class="glass-card p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12">
                    <div class="input-group">
                        <span class="input-group-text glass-input-addon"><i class="bi bi-search"></i></span>
                        <input type="text" id="search-input" class="form-control glass-input" placeholder="Search role, company or missing skills...">
                    </div>
                </div>
            </div>
        </div>

        <!-- Desktop View -->
        <div class="glass-card d-none d-md-block overflow-hidden">
            <div class="table-responsive">
                <table class="table table-hover align-middle glass-table mb-0">
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Role</th>
                            <th>Probability</th>
                            <th>Experience</th>
                            <th>Missing Skills</th>
                            <th>Analysis</th>
                        </tr>
                    </thead>
                    <tbody id="jobs-tbody">
                        <tr><td colspan="6" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Mobile Cards View -->
        <div class="d-md-none" id="mobile-cards">
            <div class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</div>
        </div>

        <!-- Pagination -->
        <div class="pagination-nav"></div>
    </div>

    <!-- Reason Modal -->
    <div class="modal fade" id="analysisModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content bg-dark border-secondary text-white">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title fw-bold text-accent" id="modal-job-title">Job Details</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row g-2 mb-3">
                        <div class="col-6">
                            <div class="p-2 rounded bg-secondary-subtle">
                                <small class="text-muted d-block">Probability</small>
                                <strong id="modal-probability" class="text-warning">0%</strong>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="p-2 rounded bg-secondary-subtle">
                                <small class="text-muted d-block">Confidence</small>
                                <strong id="modal-confidence" class="text-info">0%</strong>
                            </div>
                        </div>
                    </div>
                    <div class="p-3 rounded reason-text reason-text-danger">
                        <h6 class="fw-bold text-white mb-2">AI Assessment Reason:</h6>
                        <p id="modal-reason" class="mb-0 small text-muted"></p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "review_jobs.html", "w", encoding="utf-8") as f:
        f.write(review_jobs_html)

    # Failed Jobs Template
    failed_jobs_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - Failed Jobs</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-exclamation-triangle-fill text-accent"></i> Failed Jobs</h1>
            <p class="text-muted mb-0">Jobs that resulted in exceptions during the Playwright application flow</p>
        </div>

        <div class="glass-card p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12">
                    <div class="input-group">
                        <span class="input-group-text glass-input-addon"><i class="bi bi-search"></i></span>
                        <input type="text" id="search-input" class="form-control glass-input" placeholder="Search role, company or error reason...">
                    </div>
                </div>
            </div>
        </div>

        <!-- Desktop View -->
        <div class="glass-card d-none d-md-block overflow-hidden">
            <div class="table-responsive">
                <table class="table table-hover align-middle glass-table mb-0">
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Role</th>
                            <th>Location</th>
                            <th>Error Details</th>
                            <th>Failed Date</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="jobs-tbody">
                        <tr><td colspan="6" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Mobile Cards View -->
        <div class="d-md-none" id="mobile-cards">
            <div class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</div>
        </div>

        <!-- Pagination -->
        <div class="pagination-nav"></div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "failed_jobs.html", "w", encoding="utf-8") as f:
        f.write(failed_jobs_html)

    # Hidden Jobs Template
    hidden_jobs_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - Hidden Jobs</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-eye-slash-fill text-accent"></i> Hidden Jobs</h1>
            <p class="text-muted mb-0">Jobs that have been hidden (toggled green) from the main lists</p>
        </div>

        <div class="glass-card p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12">
                    <div class="input-group">
                        <span class="input-group-text glass-input-addon"><i class="bi bi-search"></i></span>
                        <input type="text" id="search-input" class="form-control glass-input" placeholder="Search role, company, location or category...">
                    </div>
                </div>
            </div>
        </div>

        <!-- Desktop View -->
        <div class="glass-card d-none d-md-block overflow-hidden">
            <div class="table-responsive">
                <table class="table table-hover align-middle glass-table mb-0">
                    <thead>
                        <tr>
                            <th>Category</th>
                            <th>Company</th>
                            <th>Role</th>
                            <th>Location / Info</th>
                            <th>Actions</th>
                        </tr>
                    </thead>
                    <tbody id="jobs-tbody">
                        <tr><td colspan="5" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Mobile Cards View -->
        <div class="d-md-none" id="mobile-cards">
            <div class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</div>
        </div>

        <!-- Pagination -->
        <div class="pagination-nav"></div>
    </div>

    <!-- Reason Modal -->
    <div class="modal fade" id="analysisModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content bg-dark border-secondary text-white">
                <div class="modal-header border-secondary">
                    <h5 class="modal-title fw-bold text-accent" id="modal-job-title">Job Details</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row g-2 mb-3">
                        <div class="col-6">
                            <div class="p-2 rounded bg-secondary-subtle">
                                <small class="text-muted d-block">Probability</small>
                                <strong id="modal-probability" class="text-success">0%</strong>
                            </div>
                        </div>
                        <div class="col-6">
                            <div class="p-2 rounded bg-secondary-subtle">
                                <small class="text-muted d-block">Confidence</small>
                                <strong id="modal-confidence" class="text-info">0%</strong>
                            </div>
                        </div>
                    </div>
                    <div class="p-3 rounded reason-text">
                        <h6 class="fw-bold text-white mb-2">AI Assessment Reason:</h6>
                        <p id="modal-reason" class="mb-0 small text-muted"></p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "hidden_jobs.html", "w", encoding="utf-8") as f:
        f.write(hidden_jobs_html)

    # Question Bank Template
    question_bank_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - Question Bank</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-patch-question-fill text-accent"></i> Question Bank</h1>
            <p class="text-muted mb-0">Knowledge registry normalized questions and local answers</p>
        </div>

        <div class="glass-card p-3 mb-4">
            <div class="row g-2 align-items-center">
                <div class="col-12 col-md-8">
                    <div class="input-group">
                        <span class="input-group-text glass-input-addon"><i class="bi bi-search"></i></span>
                        <input type="text" id="search-input" class="form-control glass-input" placeholder="Search question texts, key words, answers...">
                    </div>
                </div>
                <div class="col-12 col-md-4">
                    <select id="filter-status" class="form-select glass-input">
                        <option value="all">All Questions</option>
                        <option value="answered">Answered Only</option>
                        <option value="unanswered">Needs Answer</option>
                    </select>
                </div>
            </div>
        </div>

        <div id="questions-container">
            <div class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm text-accent" role="status"></div> Loading...</div>
        </div>

        <!-- Pagination -->
        <div class="pagination-nav"></div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "question_bank.html", "w", encoding="utf-8") as f:
        f.write(question_bank_html)

    # System Status Template
    system_status_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Naukri Automation - System Status</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <link rel="stylesheet" href="css/style.css">
</head>
<body>
    <div id="navbar-container"></div>

    <div class="container main-content">
        <div class="page-header">
            <h1 class="page-title"><i class="bi bi-cpu-fill text-accent"></i> System Status</h1>
            <p class="text-muted mb-0">Pipeline execution parameters and limits monitor</p>
        </div>

        <div class="row g-3 mb-4">
            <!-- Collector -->
            <div class="col-12 col-lg-4">
                <div class="glass-card p-3 h-100">
                    <div class="d-flex align-items-center gap-2 mb-3">
                        <div class="status-icon status-icon-success"><i class="bi bi-collection-fill"></i></div>
                        <div>
                            <h6 class="mb-0 fw-bold">Collector</h6>
                            <small class="text-muted">Job scraper pipeline</small>
                        </div>
                    </div>
                    <div class="status-grid">
                        <div class="status-item"><span class="status-label">Total Jobs</span><span class="status-value" id="status-col-total">0</span></div>
                        <div class="status-item"><span class="status-label">Pending</span><span class="status-value text-warning" id="status-col-pending">0</span></div>
                        <div class="status-item"><span class="status-label">Evaluated</span><span class="status-value text-success" id="status-col-eval">0</span></div>
                        <div class="status-item"><span class="status-label">Queued</span><span class="status-value text-info" id="status-col-queued">0</span></div>
                        <div class="status-item"><span class="status-label">Failed</span><span class="status-value text-danger" id="status-col-failed">0</span></div>
                    </div>
                    <div class="mt-3">
                        <small class="text-muted d-block mb-1">Active Keywords:</small>
                        <div id="status-col-keywords"></div>
                    </div>
                    <div class="mt-2">
                        <small class="text-muted d-block mb-1">Active Locations:</small>
                        <div id="status-col-locations"></div>
                    </div>
                </div>
            </div>

            <!-- Evaluator -->
            <div class="col-12 col-lg-4">
                <div class="glass-card p-3 h-100">
                    <div class="d-flex align-items-center gap-2 mb-3">
                        <div class="status-icon status-icon-info"><i class="bi bi-robot"></i></div>
                        <div>
                            <h6 class="mb-0 fw-bold">Evaluator</h6>
                            <small class="text-muted">AI evaluation pipeline</small>
                        </div>
                    </div>
                    <div class="status-grid">
                        <div class="status-item"><span class="status-label">Total Evals</span><span class="status-value" id="status-ev-total">0</span></div>
                        <div class="status-item"><span class="status-label">Pending</span><span class="status-value text-warning" id="status-ev-pending">0</span></div>
                        <div class="status-item"><span class="status-label">Runs Count</span><span class="status-value" id="status-ev-runs">0</span></div>
                        <div class="status-item"><span class="status-label">Last Run</span><span class="status-value small" id="status-ev-last">Never</span></div>
                    </div>
                    <div class="mt-3">
                        <small class="text-muted d-block mb-1">AI Engines Used:</small>
                        <div id="status-ev-models"></div>
                    </div>
                </div>
            </div>

            <!-- Discovery -->
            <div class="col-12 col-lg-4">
                <div class="glass-card p-3 h-100">
                    <div class="d-flex align-items-center gap-2 mb-3">
                        <div class="status-icon status-icon-accent"><i class="bi bi-search-heart"></i></div>
                        <div>
                            <h6 class="mb-0 fw-bold">Discovery</h6>
                            <small class="text-muted">Application processing</small>
                        </div>
                    </div>
                    <div class="status-grid">
                        <div class="status-item"><span class="status-label">Processed</span><span class="status-value" id="status-disc-proc">0</span></div>
                        <div class="status-item"><span class="status-label">Pending APPLY</span><span class="status-value text-warning" id="status-disc-pending">0</span></div>
                        <div class="status-item"><span class="status-label">Last Run</span><span class="status-value small" id="status-disc-last">Never</span></div>
                    </div>

                    <!-- Quota Status -->
                    <div class="mt-3 p-2 rounded" style="background: rgba(255,255,255,0.05);">
                        <div class="d-flex justify-content-between align-items-center mb-1 small">
                            <span class="text-muted">Quota Status</span>
                            <span class="badge bg-success" id="status-quota-badge">Available</span>
                        </div>
                        <div class="d-flex justify-content-between small text-muted">
                            <span>Consecutive Hits</span>
                            <span id="status-quota-consecutive">0/3</span>
                        </div>
                        <div class="d-flex justify-content-between small text-muted mt-1">
                            <span>Last Exhausted</span>
                            <span class="small" id="status-quota-last">Never</span>
                        </div>
                    </div>

                    <!-- Retry Queue Status -->
                    <div class="mt-3 p-2 rounded" style="background: rgba(255,255,255,0.05);">
                        <div class="d-flex justify-content-between align-items-center mb-1 small">
                            <span class="text-muted">Retry Queue</span>
                            <span class="badge bg-warning text-dark" id="status-retry-badge">0</span>
                        </div>
                        <div class="d-flex justify-content-between small text-muted">
                            <span>Unknown Questions</span>
                            <span id="status-retry-q">0</span>
                        </div>
                        <div class="d-flex justify-content-between small text-muted mt-1">
                            <span>Quota Exhausted</span>
                            <span id="status-retry-quota">0</span>
                        </div>
                        <div class="d-flex justify-content-between small text-muted mt-1">
                            <span>Temporary Failures</span>
                            <span id="status-retry-temp">0</span>
                        </div>
                        <div class="d-flex justify-content-between small text-muted mt-1">
                            <span>Browser Errors</span>
                            <span id="status-retry-browser">0</span>
                        </div>
                    </div>

                    <!-- Question Bank -->
                    <div class="mt-3">
                        <div class="d-flex justify-content-between mb-1 small">
                            <span>Q-Bank Coverage</span>
                            <span id="status-qb-cov">0%</span>
                        </div>
                        <div class="progress" style="height: 8px;">
                            <div class="progress-bar" id="status-qb-pb" style="width: 0%"></div>
                        </div>
                        <div class="d-flex justify-content-between small text-muted mt-1">
                            <span id="status-qb-ratio">0/0 answered</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <h2 class="section-title"><i class="bi bi-clock-history text-accent"></i> Recent Evaluation Runs</h2>
        <div class="glass-card p-3">
            <div class="table-responsive">
                <table class="table table-hover align-middle glass-table mb-0">
                    <thead>
                        <tr>
                            <th>Run ID</th>
                            <th>Jobs Count</th>
                            <th>Started Time</th>
                            <th>Ended Time</th>
                        </tr>
                    </thead>
                    <tbody id="recent-runs-tbody">
                        <tr><td colspan="4" class="text-center text-muted">No runs found</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="js/app.js"></script>
</body>
</html>"""
    with open(DOCS_DIR / "system_status.html", "w", encoding="utf-8") as f:
        f.write(system_status_html)

def write_css():
    css_content = """/* ── Naukri Automation Dashboard — Premium Dark Theme ─────────────────────── */
:root {
    --bg-primary: #0a0e1a;
    --bg-card: rgba(255, 255, 255, 0.04);
    --bg-card-hover: rgba(255, 255, 255, 0.07);
    --bg-nav: rgba(10, 14, 26, 0.85);
    --accent: #6c5ce7;
    --accent-light: #a29bfe;
    --accent-glow: rgba(108, 92, 231, 0.3);
    --border-subtle: rgba(255, 255, 255, 0.08);
    --text-primary: #e8e8f0;
    --text-muted: #8b8da0;
    --success: #00cec9;
    --warning: #fdcb6e;
    --danger: #ff7675;
    --info: #74b9ff;
    --radius: 14px;
    --radius-sm: 10px;
    --font: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    --bs-secondary-color: white;
    --bs-secondary-color-rgb: 255, 255, 255;
}

* { box-sizing: border-box; }

body {
    font-family: var(--font);
    background: var(--bg-primary);
    background-image:
        radial-gradient(ellipse at 20% 50%, rgba(108, 92, 231, 0.08) 0%, transparent 50%),
        radial-gradient(ellipse at 80% 20%, rgba(0, 206, 201, 0.06) 0%, transparent 50%);
    color: var(--text-primary);
    min-height: 100vh;
}

/* ── Navigation ──────────────────────────────────────────────────────────── */
.glass-nav {
    background: var(--bg-nav);
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    border-bottom: 1px solid var(--border-subtle);
    padding: 0.5rem 0;
}

.brand-icon {
    width: 34px; height: 34px;
    background: linear-gradient(135deg, var(--accent), var(--accent-light));
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1rem; color: #fff;
}

.navbar-brand { font-size: 1.1rem; }
.text-accent { color: var(--accent-light) !important; }

.nav-link {
    color: var(--text-muted) !important;
    font-size: 0.85rem;
    font-weight: 500;
    padding: 0.4rem 0.75rem !important;
    border-radius: var(--radius-sm);
    transition: all 0.2s;
}
.nav-link:hover { color: var(--text-primary) !important; background: rgba(255,255,255,0.05); }
.nav-link.active {
    color: #fff !important;
    background: linear-gradient(135deg, var(--accent), rgba(108, 92, 231, 0.6));
}

.live-dot {
    width: 8px; height: 8px;
    background: var(--success);
    border-radius: 50%;
    display: inline-block;
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0, 206, 201, 0.4); }
    50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(0, 206, 201, 0); }
}

/* Mobile Nav */
.glass-offcanvas {
    background: var(--bg-primary) !important;
    border-left: 1px solid var(--border-subtle);
}
.mobile-nav-link {
    font-size: 1rem !important;
    padding: 0.75rem 1rem !important;
    border-radius: var(--radius-sm);
    margin-bottom: 2px;
}

/* ── Main Content ────────────────────────────────────────────────────────── */
.main-content { padding-top: 70px; padding-bottom: 1rem; }

.page-header { margin-bottom: 1.5rem; padding-top: 0.5rem; }
.page-title {
    font-size: 1.4rem; font-weight: 700;
    display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 0.25rem;
}
.section-title {
    font-size: 1rem; font-weight: 600;
    margin-bottom: 0.75rem; margin-top: 0.5rem;
    display: flex; align-items: center; gap: 0.5rem;
}

/* ── Stat Cards ──────────────────────────────────────────────────────────── */
.stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 1.25rem 1rem;
    position: relative;
    overflow: hidden;
    transition: transform 0.2s, box-shadow 0.2s;
}
.stat-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 30px rgba(0,0,0,0.3);
}
.stat-card::before {
    content: '';
    position: absolute; top: 0; left: 0;
    width: 100%; height: 3px;
}
.stat-card-primary::before { background: linear-gradient(90deg, var(--accent), var(--accent-light)); }
.stat-card-success::before { background: linear-gradient(90deg, var(--success), #55efc4); }
.stat-card-warning::before { background: linear-gradient(90deg, var(--warning), #ffeaa7); }
.stat-card-muted::before { background: linear-gradient(90deg, #636e72, #b2bec3); }

.stat-icon {
    font-size: 1.3rem; opacity: 0.6; margin-bottom: 0.5rem;
}
.stat-value { font-size: 1.8rem; font-weight: 700; line-height: 1.1; }
.stat-label { font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }
.stat-sub {
    font-size: 0.7rem; color: var(--text-muted);
    margin-top: 0.5rem; opacity: 0.7;
}

.mini-stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 0.75rem;
    text-align: center;
    transition: transform 0.2s;
}
.mini-stat-card:hover { transform: translateY(-1px); }
.mini-stat-value { font-size: 1.5rem; font-weight: 700; }
.mini-stat-label { font-size: 0.7rem; color: var(--text-muted); }

/* ── Glass Cards ─────────────────────────────────────────────────────────── */
.glass-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    backdrop-filter: blur(10px);
}

/* ── Tables ──────────────────────────────────────────────────────────────── */
.glass-table {
    --bs-table-bg: transparent;
    --bs-table-hover-bg: rgba(255,255,255,0.03);
}
.glass-table thead th {
    background: rgba(255,255,255,0.03);
    border-bottom: 1px solid var(--border-subtle);
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    padding: 0.75rem;
    white-space: nowrap;
}
.glass-table td {
    border-bottom: 1px solid rgba(255,255,255,0.03);
    padding: 0.6rem 0.75rem;
    font-size: 0.85rem;
}

/* ── Job Cards (Mobile) ──────────────────────────────────────────────────── */
.job-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 1rem;
    transition: transform 0.15s;
}
.job-card:active { transform: scale(0.99); }
.job-card-failed { border-left: 3px solid var(--danger); }

/* ── Search ──────────────────────────────────────────────────────────────── */
.glass-input {
    background: var(--bg-card) !important;
    border: 1px solid var(--border-subtle) !important;
    color: var(--text-primary) !important;
    border-radius: var(--radius-sm) !important;
}
.glass-input:focus {
    box-shadow: 0 0 0 2px var(--accent-glow) !important;
    border-color: var(--accent) !important;
}
.glass-input-addon {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid var(--border-subtle) !important;
    color: var(--text-muted) !important;
    border-radius: var(--radius-sm) 0 0 var(--radius-sm) !important;
}
.btn-accent {
    background: linear-gradient(135deg, var(--accent), rgba(108, 92, 231, 0.8));
    border: none; color: #fff; font-weight: 500;
    border-radius: var(--radius-sm) !important;
}
.btn-accent:hover { background: var(--accent); color: #fff; transform: translateY(-1px); }
.btn-outline-accent {
    border: 1px solid var(--accent); color: var(--accent-light);
    border-radius: var(--radius-sm) !important;
}
.btn-outline-accent:hover {
    background: var(--accent); color: #fff;
}

/* ── Pagination ──────────────────────────────────────────────────────────── */
.page-link {
    background: var(--bg-card) !important;
    border-color: var(--border-subtle) !important;
    color: var(--text-muted) !important;
    font-size: 0.8rem;
}
.page-link:hover { background: rgba(255,255,255,0.08) !important; color: #fff !important; }
.page-item.active .page-link {
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: #fff !important;
}

/* ── Progress ────────────────────────────────────────────────────────────── */
.progress { background: rgba(255,255,255,0.06); }
.dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 4px;
}
.dot-success { background: var(--success); }
.dot-warning { background: var(--warning); }
.dot-muted { background: #636e72; }

/* ── Quick Links ─────────────────────────────────────────────────────────── */
.quick-link-card {
    display: flex; align-items: center; gap: 0.75rem;
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius);
    padding: 1rem;
    color: var(--text-primary);
    text-decoration: none;
    font-weight: 500; font-size: 0.85rem;
    transition: all 0.2s;
}
.quick-link-card i:first-child { font-size: 1.2rem; color: var(--accent-light); }
.quick-link-card:hover {
    background: var(--bg-card-hover);
    border-color: var(--accent);
    color: #fff;
    transform: translateY(-1px);
}

/* ── Reason & Answer boxes ───────────────────────────────────────────────── */
.reason-text {
    background: rgba(255,255,255,0.03);
    border-radius: var(--radius-sm);
    border-left: 3px solid var(--info);
    color: var(--text-muted);
}
.reason-text-danger { border-left-color: var(--danger); }
.answer-box {
    background: rgba(0, 206, 201, 0.06);
    border-radius: var(--radius-sm);
    border-left: 3px solid var(--success);
}
.answer-box-empty {
    background: rgba(255, 118, 117, 0.06);
    border-left-color: var(--danger);
}

/* ── System Status ───────────────────────────────────────────────────────── */
.status-icon {
    width: 42px; height: 42px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 1.2rem;
}
.status-icon-success { background: rgba(0,206,201,0.15); color: var(--success); }
.status-icon-info { background: rgba(116,185,255,0.15); color: var(--info); }
.status-icon-accent { background: rgba(108,92,231,0.15); color: var(--accent-light); }

.status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
.status-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.4rem 0.6rem;
    background: rgba(255,255,255,0.02);
    border-radius: 8px; font-size: 0.82rem;
}
.status-label { color: var(--text-muted); }
.status-value { font-weight: 600; }

/* ── Empty State ─────────────────────────────────────────────────────────── */
.empty-state {
    text-align: center; padding: 3rem 1rem;
    color: var(--text-muted);
}
.empty-state i { font-size: 3rem; display: block; margin-bottom: 1rem; opacity: 0.4; }

/* ── Sort Link ───────────────────────────────────────────────────────────── */
.sort-link {
    color: var(--text-muted); text-decoration: none;
    transition: color 0.15s;
}
.sort-link:hover { color: var(--accent-light); }

/* ── Badge overrides ─────────────────────────────────────────────────────── */
.badge { font-weight: 500; font-size: 0.72rem; letter-spacing: 0.02em; }

/* ── Responsive ──────────────────────────────────────────────────────────── */
@media (max-width: 576px) {
    .stat-value { font-size: 1.4rem; }
    .page-title { font-size: 1.2rem; }
    .main-content { padding-top: 60px; }
}

/* ── Scrollbar ───────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
"""
    with open(CSS_DIR / "style.css", "w", encoding="utf-8") as f:
        f.write(css_content)

def write_js_logic():
    js_content = """let dashboardData = null;

// Page rendering configs
let currentPage = 1;
let pageSize = 25;
let currentSort = "probability_desc";
let searchTerm = "";
let filterStatus = "all";

document.addEventListener("DOMContentLoaded", async () => {
    injectNavbar();
    highlightNav();
    await loadData();
    initPage();
});

function injectNavbar() {
    const nav = document.getElementById("navbar-container");
    if (!nav) return;
    nav.innerHTML = `
    <nav class="navbar navbar-expand-lg glass-nav fixed-top">
        <div class="container">
            <a class="navbar-brand d-flex align-items-center gap-2 fw-bold text-white" href="index.html">
                <div class="brand-icon"><i class="bi bi-robot"></i></div>
                <span>Naukri Auto</span>
            </a>
            <button class="navbar-toggler border-0 text-white" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <i class="bi bi-list fs-3"></i>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav ms-auto gap-1 mt-2 mt-lg-0">
                    <li class="nav-item"><a class="nav-link" id="nav-overview" href="index.html"><i class="bi bi-grid-fill me-1"></i> Overview</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-top-jobs" href="top_jobs.html"><i class="bi bi-star-fill me-1"></i> Top Jobs</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-external-jobs" href="external_jobs.html"><i class="bi bi-link-45deg me-1"></i> External</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-review-jobs" href="review_jobs.html"><i class="bi bi-eye-fill me-1"></i> Review</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-failed-jobs" href="failed_jobs.html"><i class="bi bi-exclamation-triangle-fill me-1"></i> Failed</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-hidden-jobs" href="hidden_jobs.html"><i class="bi bi-eye-slash-fill me-1"></i> Hidden <span class="badge bg-secondary ms-1" id="nav-hidden-count">0</span></a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-question-bank" href="question_bank.html"><i class="bi bi-patch-question-fill me-1"></i> Q-Bank</a></li>
                    <li class="nav-item"><a class="nav-link" id="nav-system-status" href="system_status.html"><i class="bi bi-cpu-fill me-1"></i> Status</a></li>
                </ul>
            </div>
        </div>
    </nav>`;
}

function highlightNav() {
    const path = window.location.pathname;
    let activeId = "nav-overview";
    if (path.includes("top_jobs.html")) activeId = "nav-top-jobs";
    else if (path.includes("external_jobs.html")) activeId = "nav-external-jobs";
    else if (path.includes("review_jobs.html")) activeId = "nav-review-jobs";
    else if (path.includes("failed_jobs.html")) activeId = "nav-failed-jobs";
    else if (path.includes("hidden_jobs.html")) activeId = "nav-hidden-jobs";
    else if (path.includes("question_bank.html")) activeId = "nav-question-bank";
    else if (path.includes("system_status.html")) activeId = "nav-system-status";
    
    const activeLink = document.getElementById(activeId);
    if (activeLink) activeLink.classList.add("active");
}

async function loadData() {
    try {
        const res = await fetch("data/dashboard_data.json");
        dashboardData = await res.json();
    } catch (e) {
        console.error("Failed to load dashboard data:", e);
    }
}

function initPage() {
    if (!dashboardData) return;
    
    // Update hidden count badge in navbar
    try {
        const hiddenCount = getHiddenJobs().length;
        const navHiddenBadge = document.getElementById("nav-hidden-count");
        if (navHiddenBadge) {
            navHiddenBadge.innerText = hiddenCount;
        }
    } catch (e) {
        console.error("Failed to update hidden count:", e);
    }

    const path = window.location.pathname;
    if (path.includes("index.html") || path.endsWith("/") || path.endsWith("docs") || path.endsWith("docs/")) {
        renderOverview();
    } else if (path.includes("top_jobs.html")) {
        renderTopJobs();
    } else if (path.includes("external_jobs.html")) {
        renderExternalJobs();
    } else if (path.includes("review_jobs.html")) {
        renderReviewJobs();
    } else if (path.includes("failed_jobs.html")) {
        renderFailedJobs();
    } else if (path.includes("hidden_jobs.html")) {
        renderHiddenJobs();
    } else if (path.includes("question_bank.html")) {
        renderQuestionBank();
    } else if (path.includes("system_status.html")) {
        renderSystemStatus();
    } else {
        renderOverview();
    }
}

// ── Overview Page ────────────────────────────────────────────────────────
function renderOverview() {
    const stats = dashboardData.overview_stats;
    const quota = dashboardData.quota_status;
    
    document.getElementById("stat-total-jobs").innerText = stats.total_jobs;
    document.getElementById("stat-jobs-today").innerText = stats.jobs_today;
    document.getElementById("stat-pending-eval").innerText = stats.pending_eval;
    document.getElementById("stat-pending-apply").innerText = stats.pending_apply;
    document.getElementById("stat-coverage-pct").innerText = stats.coverage_pct + "%";
    document.getElementById("stat-last-run").innerText = stats.last_run.substring(0, 16);
    
    // Application types
    document.getElementById("count-easy-apply").innerText = stats.easy_apply;
    document.getElementById("count-external-portal").innerText = stats.external_portal;
    document.getElementById("count-already-applied").innerText = stats.already_applied;
    document.getElementById("count-unknown-flow").innerText = stats.unknown_flow;
    document.getElementById("count-failed").innerText = stats.failed;
    document.getElementById("count-quota-exhausted").innerText = stats.quota_exhausted;
    document.getElementById("count-applied-ok").innerText = stats.applied_successfully;
    
    const countHiddenJobs = document.getElementById("count-hidden-jobs");
    if (countHiddenJobs) {
        countHiddenJobs.innerText = getHiddenJobs().length;
    }

    // Progress bar for Q-bank
    const pb = document.getElementById("qbank-progress");
    if (pb) {
        pb.style.width = stats.coverage_pct + "%";
        pb.className = `progress-bar ${stats.coverage_pct >= 80 ? 'bg-success' : stats.coverage_pct >= 50 ? 'bg-warning' : 'bg-danger'}`;
    }
    document.getElementById("qbank-ratio").innerText = `${stats.answered_questions}/${stats.total_questions} answered`;

    // Quota Badge
    const qBadge = document.getElementById("quota-status-badge");
    if (qBadge) {
        qBadge.innerText = quota.status_label;
        qBadge.className = `badge ${quota.is_exhausted ? 'bg-danger' : 'bg-success'}`;
    }
    document.getElementById("quota-consecutive").innerText = `${quota.consecutive_count}/3`;

    // Retry Queue
    const retry = stats.retry_queue || { count: 0, reasons: {} };
    const rBadge = document.getElementById("retry-queue-badge");
    if (rBadge) {
        rBadge.innerText = retry.count;
    }
    const reqQ = document.getElementById("retry-reason-q");
    if (reqQ) reqQ.innerText = retry.reasons.unknown_question || 0;
    const reqQuota = document.getElementById("retry-reason-quota");
    if (reqQuota) reqQuota.innerText = retry.reasons.quota_exhausted || 0;
    const reqTemp = document.getElementById("retry-reason-temp");
    if (reqTemp) reqTemp.innerText = retry.reasons.temporary_failure || 0;
    const reqBrowser = document.getElementById("retry-reason-browser");
    if (reqBrowser) reqBrowser.innerText = retry.reasons.browser_error || 0;
}

// ── Top Jobs Page ────────────────────────────────────────────────────────
function renderTopJobs() {
    let jobs = dashboardData.top_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");
    const sortSelect = document.getElementById("sort-select");
    const statusSelect = document.getElementById("status-filter");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        currentSort = sortSelect.value;
        filterStatus = statusSelect.value;
        
        let filtered = jobs.filter(j => {
            const matchesSearch = (j.company_name || "").toLowerCase().includes(searchTerm) ||
                                  (j.job_title || "").toLowerCase().includes(searchTerm) ||
                                  (j.location || "").toLowerCase().includes(searchTerm);
            const matchesStatus = filterStatus === "all" || j.apply_status === filterStatus;
            return matchesSearch && matchesStatus;
        });

        // Sorting
        filtered.sort((a, b) => {
            if (currentSort === "probability_desc") return b.interview_probability - a.interview_probability;
            if (currentSort === "probability_asc") return a.interview_probability - b.interview_probability;
            if (currentSort === "company_asc") return (a.company_name || "").localeCompare(b.company_name || "");
            if (currentSort === "title_asc") return (a.job_title || "").localeCompare(b.job_title || "");
            return 0;
        });

        renderTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    sortSelect.addEventListener("change", () => { currentPage = 1; update(); });
    statusSelect.addEventListener("change", () => { currentPage = 1; update(); });

    update();
}

function renderTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        const badgeClass = getApplyStatusBadgeClass(j.apply_status);
        return `
        <tr>
            <td>
                <div class="fw-bold">${escapeHtml(j.company_name)}</div>
                <small class="text-muted">${escapeHtml(j.location)}</small>
            </td>
            <td>
                <a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-bold hover-accent">${escapeHtml(j.job_title)}</a>
            </td>
            <td><span class="badge bg-dark">${escapeHtml(j.experience_required || "N/A")}</span></td>
            <td>
                <div class="d-flex align-items-center gap-2">
                    <div class="progress flex-grow-1" style="height: 6px; width: 60px;">
                        <div class="progress-bar bg-success" style="width: ${j.interview_probability}%"></div>
                    </div>
                    <span>${j.interview_probability}%</span>
                </div>
            </td>
            <td><span class="badge ${badgeClass}">${escapeHtml(j.apply_status)}</span></td>
            <td>
                <div class="d-flex gap-1">
                    <button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Reason</button>
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        const badgeClass = getApplyStatusBadgeClass(j.apply_status);
        return `
        <div class="job-card mb-3">
            <div class="d-flex justify-content-between align-items-start mb-2">
                <div>
                    <h6 class="mb-0 fw-bold text-white">${escapeHtml(j.company_name)}</h6>
                    <small class="text-muted">${escapeHtml(j.location)}</small>
                </div>
                <span class="badge ${badgeClass}">${escapeHtml(j.apply_status)}</span>
            </div>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <div class="d-flex justify-content-between align-items-center small text-muted">
                <span>Exp: ${escapeHtml(j.experience_required || "N/A")}</span>
                <span>Prob: <strong class="text-success">${j.interview_probability}%</strong></span>
            </div>
            <div class="mt-2 d-flex gap-2">
                <button class="btn btn-sm btn-outline-accent flex-grow-1" onclick="showReasonModal(${j.id})">View Analysis</button>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

function showReasonModal(jobId) {
    const job = dashboardData.top_jobs.find(j => j.id === jobId) || 
                dashboardData.review_jobs.find(j => j.id === jobId) ||
                dashboardData.failed_jobs.find(j => j.id === jobId);
    if (!job) return;
    
    document.getElementById("modal-job-title").innerText = `${job.company_name} - ${job.job_title}`;
    document.getElementById("modal-probability").innerText = `${job.interview_probability}%`;
    document.getElementById("modal-confidence").innerText = `${job.confidence || 0}%`;
    document.getElementById("modal-reason").innerText = job.reason || "No details provided.";
    
    const myModal = new bootstrap.Modal(document.getElementById('analysisModal'));
    myModal.show();
}

function getApplyStatusBadgeClass(status) {
    if (status === "easy_apply" || status === "applied_successfully") return "bg-success text-white";
    if (status === "external_portal") return "bg-info text-dark";
    if (status === "pending") return "bg-warning text-dark";
    if (status === "discovery_failed" || status === "quota_exhausted") return "bg-danger text-white";
    return "bg-secondary text-white";
}

// ── External Jobs Page ───────────────────────────────────────────────────
function renderExternalJobs() {
    let jobs = dashboardData.external_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        
        let filtered = jobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm);
        });

        renderExternalTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    update();
}

function renderExternalTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted">No external jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No external jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        return `
        <tr>
            <td><strong>${escapeHtml(j.company_name)}</strong></td>
            <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
            <td>${escapeHtml(j.location)}</td>
            <td><strong class="text-success">${j.interview_probability}%</strong></td>
            <td>
                <div class="d-flex gap-1">
                    <a href="${escapeHtml(j.apply_url)}" target="_blank" class="btn btn-sm btn-accent"><i class="bi bi-box-arrow-up-right me-1"></i> Apply</a>
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        return `
        <div class="job-card mb-3">
            <h6 class="fw-bold text-white mb-1">${escapeHtml(j.company_name)}</h6>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <div class="d-flex justify-content-between align-items-center mb-3 text-muted small">
                <span>Loc: ${escapeHtml(j.location)}</span>
                <span>Prob: <strong class="text-success">${j.interview_probability}%</strong></span>
            </div>
            <div class="d-flex gap-2">
                <a href="${escapeHtml(j.apply_url)}" target="_blank" class="btn btn-sm btn-accent flex-grow-1"><i class="bi bi-box-arrow-up-right me-1"></i> Apply on Portal</a>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── Review Jobs Page ─────────────────────────────────────────────────────
function renderReviewJobs() {
    let jobs = dashboardData.review_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        let filtered = jobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm) ||
                   (j.reason || "").toLowerCase().includes(searchTerm);
        });

        renderReviewTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    update();
}

function renderReviewTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No review jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No review jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        let skills = [];
        try {
            skills = JSON.parse(j.missing_skills || "[]");
        } catch(e) {}
        const skillsBadges = skills.map(s => `<span class="badge bg-danger me-1">${escapeHtml(s)}</span>`).join("");
        
        return `
        <tr>
            <td><strong>${escapeHtml(j.company_name)}</strong></td>
            <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
            <td><strong class="text-warning">${j.interview_probability}%</strong></td>
            <td><span class="badge bg-dark">${escapeHtml(j.experience_required || "N/A")}</span></td>
            <td>${skillsBadges || '<span class="text-muted small">None</span>'}</td>
            <td>
                <div class="d-flex gap-1">
                    <button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Details</button>
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        let skills = [];
        try {
            skills = JSON.parse(j.missing_skills || "[]");
        } catch(e) {}
        const skillsBadges = skills.map(s => `<span class="badge bg-danger me-1 mb-1">${escapeHtml(s)}</span>`).join("");

        return `
        <div class="job-card mb-3">
            <div class="d-flex justify-content-between align-items-center mb-1">
                <h6 class="fw-bold text-white mb-0">${escapeHtml(j.company_name)}</h6>
                <strong class="text-warning">${j.interview_probability}%</strong>
            </div>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <div class="mb-2">
                <span class="text-muted small d-block">Missing Skills:</span>
                <div>${skillsBadges || '<span class="text-muted small">None</span>'}</div>
            </div>
            <div class="mt-2 d-flex gap-2">
                <button class="btn btn-sm btn-outline-accent flex-grow-1" onclick="showReasonModal(${j.id})">View Reason</button>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── Failed Jobs Page ─────────────────────────────────────────────────────
function renderFailedJobs() {
    let jobs = dashboardData.failed_jobs.filter(j => !getHiddenJobs().includes(j.id));
    
    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        let filtered = jobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm);
        });

        renderFailedTable(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    update();
}

function renderFailedTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted">No failed jobs found</td></tr>`;
        mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No failed jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    tbody.innerHTML = pageList.map(j => {
        return `
        <tr>
            <td><strong>${escapeHtml(j.company_name)}</strong></td>
            <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
            <td>${escapeHtml(j.location)}</td>
            <td class="text-danger small">${escapeHtml(j.reason)}</td>
            <td class="text-muted small">${escapeHtml(j.detected_at.substring(0,16))}</td>
            <td>
                <div class="d-flex gap-1">
                    ${renderToggleButton(j.id)}
                </div>
            </td>
        </tr>`;
    }).join("");

    mCards.innerHTML = pageList.map(j => {
        return `
        <div class="job-card job-card-failed mb-3">
            <h6 class="fw-bold text-white mb-1">${escapeHtml(j.company_name)}</h6>
            <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
            <p class="text-danger small mb-2">Error: ${escapeHtml(j.reason)}</p>
            <div class="d-flex justify-content-between align-items-center mt-2">
                <span class="text-muted small">Failed: ${escapeHtml(j.detected_at.substring(0, 16))}</span>
                ${renderToggleButton(j.id)}
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── Question Bank Page ───────────────────────────────────────────────────
function renderQuestionBank() {
    let questions = [...dashboardData.question_bank];
    const searchInput = document.getElementById("search-input");
    const filterSelect = document.getElementById("filter-status");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        const option = filterSelect.value;
        
        let filtered = questions.filter(q => {
            const matchesSearch = (q.question_text || "").toLowerCase().includes(searchTerm) || 
                                  (q.question_key || "").toLowerCase().includes(searchTerm) ||
                                  (q.answer || "").toLowerCase().includes(searchTerm);
            const hasAns = q.answer && q.answer.trim() !== "";
            const matchesFilter = option === "all" || (option === "answered" && hasAns) || (option === "unanswered" && !hasAns);
            return matchesSearch && matchesFilter;
        });

        renderQuestionList(filtered);
    }

    searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    filterSelect.addEventListener("change", () => { currentPage = 1; update(); });
    update();
}

function renderQuestionList(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const container = document.getElementById("questions-container");
    if (pageList.length === 0) {
        container.innerHTML = `<div class="empty-state"><i class="bi bi-patch-question"></i>No questions found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    container.innerHTML = pageList.map(q => {
        const hasAns = q.answer && q.answer.trim() !== "";
        return `
        <div class="glass-card p-3 mb-3">
            <div class="d-flex justify-content-between align-items-center mb-2">
                <span class="badge bg-secondary">${escapeHtml(q.field_type || "text")}</span>
                <span class="text-muted small">Used ${q.usage_count} times</span>
            </div>
            <h6 class="fw-bold text-white mb-1">${escapeHtml(q.question_text)}</h6>
            <code class="d-block text-accent mb-3" style="font-size: 0.8rem;">${escapeHtml(q.question_key)}</code>
            <div class="p-2 rounded ${hasAns ? 'answer-box' : 'answer-box answer-box-empty'}">
                <small class="text-muted d-block">${hasAns ? 'Normalised Answer:' : 'Needs Answer:'}</small>
                <strong class="text-white">${hasAns ? escapeHtml(q.answer) : 'N/A (Will be skipped or prompt user)'}</strong>
            </div>
        </div>`;
    }).join("");

    renderPaginationControls(totalPages);
}

// ── System Status Page ───────────────────────────────────────────────────
function renderSystemStatus() {
    const sys = dashboardData.system_status;

    // Collector card
    document.getElementById("status-col-total").innerText = sys.collector.total_jobs;
    document.getElementById("status-col-pending").innerText = sys.collector.pending;
    document.getElementById("status-col-eval").innerText = sys.collector.evaluated;
    document.getElementById("status-col-queued").innerText = sys.collector.queued;
    document.getElementById("status-col-failed").innerText = sys.collector.failed;

    // Keywords & Locations
    const kwContainer = document.getElementById("status-col-keywords");
    kwContainer.innerHTML = sys.collector.keywords.slice(0, 6).map(kw => `<span class="badge bg-dark me-1 mb-1">${escapeHtml(kw)}</span>`).join("");
    if (sys.collector.keywords.length > 6) {
        kwContainer.innerHTML += `<span class="text-muted small">+${sys.collector.keywords.length - 6} more</span>`;
    }

    const locContainer = document.getElementById("status-col-locations");
    locContainer.innerHTML = sys.collector.locations.slice(0, 6).map(loc => `<span class="badge bg-dark me-1 mb-1">${escapeHtml(loc)}</span>`).join("");

    // Evaluator card
    document.getElementById("status-ev-total").innerText = sys.evaluator.total_evaluations;
    document.getElementById("status-ev-pending").innerText = sys.evaluator.pending_eval;
    document.getElementById("status-ev-runs").innerText = sys.evaluator.total_runs;
    document.getElementById("status-ev-last").innerText = sys.evaluator.last_evaluation.substring(0, 16);
    
    document.getElementById("status-ev-models").innerHTML = sys.evaluator.models_used.map(m => `<span class="badge bg-dark me-1 mb-1">${escapeHtml(m)}</span>`).join("");

    // Discovery card
    document.getElementById("status-disc-proc").innerText = sys.discovery.total_processed;
    document.getElementById("status-disc-pending").innerText = sys.discovery.pending_apply;
    document.getElementById("status-disc-last").innerText = sys.discovery.last_discovery.substring(0, 16);

    // Quota details
    const quota = sys.discovery.quota;
    const qBadge = document.getElementById("status-quota-badge");
    qBadge.innerText = quota.status_label;
    qBadge.className = `badge ${quota.is_exhausted ? 'bg-danger' : 'bg-success'}`;
    document.getElementById("status-quota-consecutive").innerText = `${quota.consecutive_count}/3`;
    document.getElementById("status-quota-last").innerText = quota.last_detected ? quota.last_detected.substring(0, 16) : "Never";

    // Retry details
    const retry = sys.discovery.retry_queue || { count: 0, reasons: {} };
    const rBadge = document.getElementById("status-retry-badge");
    if (rBadge) {
        rBadge.innerText = retry.count;
    }
    const rq = document.getElementById("status-retry-q");
    if (rq) rq.innerText = retry.reasons.unknown_question || 0;
    const rquota = document.getElementById("status-retry-quota");
    if (rquota) rquota.innerText = retry.reasons.quota_exhausted || 0;
    const rtemp = document.getElementById("status-retry-temp");
    if (rtemp) rtemp.innerText = retry.reasons.temporary_failure || 0;
    const rbrowser = document.getElementById("status-retry-browser");
    if (rbrowser) rbrowser.innerText = retry.reasons.browser_error || 0;

    // Q-bank
    document.getElementById("status-qb-ratio").innerText = `${sys.question_bank.answered}/${sys.question_bank.total} answered`;
    document.getElementById("status-qb-cov").innerText = `${sys.question_bank.coverage_pct}%`;
    const pb = document.getElementById("status-qb-pb");
    pb.style.width = sys.question_bank.coverage_pct + "%";
    pb.className = `progress-bar ${sys.question_bank.coverage_pct >= 80 ? 'bg-success' : sys.question_bank.coverage_pct >= 50 ? 'bg-warning' : 'bg-danger'}`;

    // Recent runs table
    const tbody = document.getElementById("recent-runs-tbody");
    tbody.innerHTML = sys.recent_runs.map(r => `
    <tr>
        <td><code>${escapeHtml(r.run_id)}</code></td>
        <td><span class="badge bg-secondary">${r.cnt}</span></td>
        <td class="small text-muted">${r.started.substring(0, 19)}</td>
        <td class="small text-muted">${r.ended.substring(0, 19)}</td>
    </tr>`).join("");
}

// ── Pagination Helper ────────────────────────────────────────────────────
function renderPaginationControls(totalPages) {
    const navs = document.querySelectorAll(".pagination-nav");
    navs.forEach(nav => {
        if (!nav) return;
        
        let html = `
        <ul class="pagination pagination-sm justify-content-center mb-0 mt-3">
            <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
                <button class="page-link" onclick="changePage(${currentPage - 1})"><i class="bi bi-chevron-left"></i></button>
            </li>`;
        
        for (let i = 1; i <= totalPages; i++) {
            if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
                html += `
                <li class="page-item ${currentPage === i ? 'active' : ''}">
                    <button class="page-link" onclick="changePage(${i})">${i}</button>
                </li>`;
            } else if (i === currentPage - 3 || i === currentPage + 3) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }

        html += `
            <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
                <button class="page-link" onclick="changePage(${currentPage + 1})"><i class="bi bi-chevron-right"></i></button>
            </li>
        </ul>`;
        
        nav.innerHTML = html;
    });
}

window.changePage = function(page) {
    currentPage = page;
    initPage();
};

// ── Utils ────────────────────────────────────────────────────────────────
function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ── Hidden Jobs Logic ────────────────────────────────────────────────────
function getHiddenJobs() {
    try {
        return JSON.parse(localStorage.getItem("hidden_job_ids") || "[]");
    } catch (e) {
        return [];
    }
}

window.toggleJobHidden = function(jobId, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    let hidden = getHiddenJobs();
    if (hidden.includes(jobId)) {
        hidden = hidden.filter(id => id !== jobId);
    } else {
        hidden.push(jobId);
    }
    localStorage.setItem("hidden_job_ids", JSON.stringify(hidden));
    initPage();
};

function renderToggleButton(jobId) {
    const isHidden = getHiddenJobs().includes(jobId);
    const btnClass = isHidden ? "btn-success" : "btn-danger";
    const titleText = isHidden ? "Show Card" : "Hide Card";
    const iconClass = isHidden ? "bi-eye-fill" : "bi-eye-slash-fill";
    return `<button class="btn btn-sm ${btnClass}" onclick="toggleJobHidden(${jobId}, event)" title="${titleText}"><i class="bi ${iconClass}"></i></button>`;
}

function renderHiddenJobs() {
    const hiddenIds = getHiddenJobs();
    
    // Consolidate all jobs across categories
    let allJobs = [];
    if (dashboardData.top_jobs) {
        dashboardData.top_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "Top Job"});
        });
    }
    if (dashboardData.external_jobs) {
        dashboardData.external_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "External"});
        });
    }
    if (dashboardData.review_jobs) {
        dashboardData.review_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "Review"});
        });
    }
    if (dashboardData.failed_jobs) {
        dashboardData.failed_jobs.forEach(j => {
            if (hiddenIds.includes(j.id)) allJobs.push({...j, category: "Failed"});
        });
    }

    const searchInput = document.getElementById("search-input");

    function update() {
        searchTerm = searchInput.value.toLowerCase();
        let filtered = allJobs.filter(j => {
            return (j.company_name || "").toLowerCase().includes(searchTerm) ||
                   (j.job_title || "").toLowerCase().includes(searchTerm) ||
                   (j.location || "").toLowerCase().includes(searchTerm) ||
                   (j.category || "").toLowerCase().includes(searchTerm);
        });

        renderHiddenTable(filtered);
    }

    if (searchInput) {
        searchInput.addEventListener("input", () => { currentPage = 1; update(); });
    }
    update();
}

function renderHiddenTable(list) {
    const totalCount = list.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const pageList = list.slice(start, end);

    const tbody = document.getElementById("jobs-tbody");
    const mCards = document.getElementById("mobile-cards");
    
    if (pageList.length === 0) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted">No hidden jobs found</td></tr>`;
        if (mCards) mCards.innerHTML = `<div class="empty-state"><i class="bi bi-inbox"></i>No hidden jobs found</div>`;
        renderPaginationControls(totalPages);
        return;
    }

    if (tbody) {
        tbody.innerHTML = pageList.map(j => {
            const catBadge = j.category === "Top Job" ? "bg-success" : j.category === "External" ? "bg-info text-dark" : j.category === "Review" ? "bg-warning text-dark" : "bg-danger";
            return `
            <tr>
                <td><span class="badge ${catBadge}">${escapeHtml(j.category)}</span></td>
                <td><strong>${escapeHtml(j.company_name)}</strong></td>
                <td><a href="${escapeHtml(j.job_url)}" target="_blank" class="text-white text-decoration-none fw-semibold">${escapeHtml(j.job_title)}</a></td>
                <td>
                    <div>${escapeHtml(j.location || "N/A")}</div>
                    ${j.reason ? `<div class="text-muted small text-truncate" style="max-width: 300px;">${escapeHtml(j.reason)}</div>` : ''}
                </td>
                <td>
                    <div class="d-flex gap-1">
                        ${j.reason ? `<button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Details</button>` : ''}
                        ${renderToggleButton(j.id)}
                    </div>
                </td>
            </tr>`;
        }).join("");
    }

    if (mCards) {
        mCards.innerHTML = pageList.map(j => {
            const catBadge = j.category === "Top Job" ? "bg-success" : j.category === "External" ? "bg-info text-dark" : j.category === "Review" ? "bg-warning text-dark" : "bg-danger";
            return `
            <div class="job-card mb-3">
                <div class="d-flex justify-content-between align-items-center mb-1">
                    <h6 class="fw-bold text-white mb-0">${escapeHtml(j.company_name)}</h6>
                    <span class="badge ${catBadge}">${escapeHtml(j.category)}</span>
                </div>
                <a href="${escapeHtml(j.job_url)}" target="_blank" class="d-block text-white text-decoration-none fw-semibold mb-2">${escapeHtml(j.job_title)}</a>
                ${j.reason ? `<p class="text-muted small mb-2">${escapeHtml(j.reason)}</p>` : ''}
                <div class="d-flex justify-content-between align-items-center mt-2">
                    ${j.reason ? `<button class="btn btn-sm btn-outline-accent" onclick="showReasonModal(${j.id})">Reason</button>` : '<span></span>'}
                    ${renderToggleButton(j.id)}
                </div>
            </div>`;
        }).join("");
    }

    renderPaginationControls(totalPages);
}
"""
    with open(JS_DIR / "app.js", "w", encoding="utf-8") as f:
        f.write(js_content)

def main():
    print("Generating static Naukri Dashboard compatible with GitHub Pages...")
    setup_directories()
    
    # Fetch data
    data = fetch_data()
    if not data:
        print("Error: Could not retrieve database data.")
        return

    # Write data file
    with open(DATA_DIR / "dashboard_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print("Saved docs/data/dashboard_data.json successfully.")

    # Write HTML, CSS, and JS
    write_html_templates()
    write_css()
    write_js_logic()
    print("Static HTML pages, CSS, and JS successfully generated inside docs/ folder!")

if __name__ == "__main__":
    main()
