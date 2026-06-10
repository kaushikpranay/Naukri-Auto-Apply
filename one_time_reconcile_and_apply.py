"""
one_time_reconcile_and_apply.py — Standalone utility script to reconcile and apply to eligible jobs.

Features:
1. Reconciles evaluated jobs against existing application records.
2. Processes jobs deterministically sorted by job ID.
3. Automatically identifies already-applied jobs.
4. Executes the application pipeline for eligible jobs (action = 'APPLY').
5. Captures detailed stack traces and error details, classifying failures into:
   - login_error
   - page_load_error
   - job_expired
   - external_portal
   - question_answering_error
   - form_fill_error
   - application_submit_error
   - rate_limit_error
   - unknown_error
6. Supports script resume, saving progress to the database after every job.
7. Exports JSON, CSV, and Markdown reports at completion.
"""

import argparse
import asyncio
import csv
import json
import re
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from loguru import logger

# Import existing project modules
from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
from app.database.repository import JobRepository
from app.discovery.repository import ApplyDiscoveryRepository
from app.discovery.service import ApplyDiscoveryService
from app.export.apply_discovery_exporter import ApplyDiscoveryExporter
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoverySummary, QuotaExhaustedStop, PipelineSuspendedException
from app.models.job import JobData
from app.utils.config_loader import (
    ensure_directories,
    load_selectors,
    load_settings,
    resolve_path,
)


def setup_logging(settings: AppSettings) -> None:
    """Configure detailed logging for the reconciliation run."""
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{message}</cyan>",
        colorize=True,
    )
    log_dir = resolve_path(settings.paths.logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_dir / "one_time_reconciliation_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        encoding="utf-8",
    )


def extract_ext_id(url: str) -> str | None:
    """Extract standard Naukri job ID (digits sequence) from URL."""
    if not url:
        return None
    match = re.search(r'\b\d{8,15}\b', url)
    if match:
        return match.group(0)
    return None


def normalize_company_title(company: str, title: str) -> tuple[str, str]:
    """Normalize company name and job title for reliable comparison."""
    c = (company or "").strip().lower()
    t = (title or "").strip().lower()
    return (c, t)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="One-Time Job Application Reconciliation & Application Utility"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Perform reconciliation and generate reports without executing any browser action or changing DB state.",
    )
    parser.add_argument(
        "--max-reapply",
        type=int,
        default=100,
        help="Maximum number of missing applications to attempt in this session.",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        default=False,
        help="Retry processing jobs that are already in the reconciliation table with status 'failed'.",
    )
    return parser.parse_args()


def export_reports(db_path: Path, export_dir: Path, duration_run: float) -> None:
    """Query reconciliation database results and export JSON, CSV, and MD files."""
    import sqlite3
    
    logger.info("Exporting reports to {}", export_dir)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Check if table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_reconciliations'")
    if not cursor.fetchone():
        logger.warning("No job_reconciliations table found. Skipping report generation.")
        conn.close()
        return
        
    cursor.execute("SELECT * FROM job_reconciliations ORDER BY job_id ASC")
    rows = [dict(row) for row in cursor.fetchall()]
    
    # Get total evaluated count from evaluations table
    cursor.execute("SELECT COUNT(*) FROM ai_evaluations")
    total_evaluated_jobs = cursor.fetchone()[0]
    
    # Get total eligible count (APPLY action)
    cursor.execute("SELECT COUNT(*) FROM ai_evaluations WHERE UPPER(action) = 'APPLY'")
    total_eligible = cursor.fetchone()[0]
    
    conn.close()
    
    total_already_applied = sum(1 for r in rows if r['status'] == 'already_applied')
    total_successful_applications = sum(1 for r in rows if r['status'] == 'applied')
    total_failed_applications = sum(1 for r in rows if r['status'] == 'failed')
    total_jobs_skipped = sum(1 for r in rows if r['status'] == 'skipped_non_eligible')
    
    total_external_portals = sum(1 for r in rows if r['status'] == 'failed' and r['error_category'] == 'external_portal')
    total_errors = total_failed_applications
    
    total_missing_applications = max(0, total_eligible - total_already_applied)
    total_application_attempts = total_successful_applications + total_failed_applications
    
    success_rate = 0.0
    if total_application_attempts > 0:
        success_rate = (total_successful_applications / total_application_attempts) * 100
        
    minutes = int(duration_run // 60)
    seconds = int(duration_run % 60)
    exec_time_str = f"{minutes}m {seconds}s"
    
    summary = {
        "TOTAL_EVALUATED_JOBS": total_evaluated_jobs,
        "TOTAL_ALREADY_APPLIED": total_already_applied,
        "TOTAL_MISSING_APPLICATIONS": total_missing_applications,
        "TOTAL_APPLICATION_ATTEMPTS": total_application_attempts,
        "TOTAL_SUCCESSFUL_APPLICATIONS": total_successful_applications,
        "TOTAL_FAILED_APPLICATIONS": total_failed_applications,
        "TOTAL_EXTERNAL_PORTALS": total_external_portals,
        "TOTAL_JOBS_SKIPPED": total_jobs_skipped,
        "TOTAL_ERRORS": total_errors,
        "SUCCESS_RATE_PCT": round(success_rate, 2),
        "DURATION": exec_time_str,
        "RUN_DATE": datetime.now().isoformat()
    }
    
    # 1. Export JSON
    json_path = export_dir / "reconciliation_report.json"
    report_data = {
        "summary": summary,
        "jobs": rows
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=4)
    logger.info("Exported JSON report to {}", json_path)
    
    # 2. Export CSV
    csv_path = export_dir / "reconciliation_report.csv"
    if rows:
        keys = rows[0].keys()
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
        logger.info("Exported CSV report to {}", csv_path)
        
    # 3. Export Markdown (MD)
    md_path = export_dir / "reconciliation_report.md"
    md_content = f"""# One-Time Application Reconciliation Report

Generated at: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

> [!NOTE]
> This reconciliation run was completed in {exec_time_str}.

## Execution Summary

| Metric | Count | Description |
|---|---|---|
| **Total Evaluated Jobs** | {total_evaluated_jobs} | Total jobs evaluated by AI |
| **Already Applied** | {total_already_applied} | Jobs found with existing successful application record |
| **Missing Applications** | {total_missing_applications} | Jobs with APPLY action lacking successful application record |
| **Application Attempts** | {total_application_attempts} | Jobs where application attempt was executed |
| **Successfully Applied** | {total_successful_applications} | Jobs applied successfully in this reconciliation process |
| **Failed Applications** | {total_failed_applications} | Jobs that failed during application execution |
| **External Portals** | {total_external_portals} | Jobs redirecting to external portals |
| **Jobs Skipped (Non-eligible)** | {total_jobs_skipped} | Jobs evaluated but skipped due to non-APPLY AI action |
| **Total Errors** | {total_errors} | Total application attempts with failures |
| **Success Rate** | {success_rate:.2f}% | (Successful Applications / Application Attempts) |

## Failures by Category

"""
    from collections import Counter
    categories = Counter(r['error_category'] for r in rows if r['status'] == 'failed' and r['error_category'])
    
    if categories:
        md_content += "| Category | Count |\n|---|---|\n"
        for cat, cnt in categories.items():
            md_content += f"| {cat} | {cnt} |\n"
    else:
        md_content += "*No failures recorded.*\n"
        
    md_content += "\n## Detailed Job Logs\n\n"
    md_content += "| Job ID | Role | Company | Status | Error Category | Duration |\n|---|---|---|---|---|---|\n"
    for r in rows:
        status_emoji = "✅" if r['status'] in ('applied', 'already_applied') else ("❌" if r['status'] == 'failed' else "⏭️")
        duration_display = f"{r['duration_seconds']:.1f}s" if r['duration_seconds'] else "N/A"
        md_content += f"| {r['job_id']} | {r['job_title']} | {r['company_name']} | {status_emoji} {r['status']} | {r['error_category'] or 'N/A'} | {duration_display} |\n"
        
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info("Exported Markdown report to {}", md_path)


async def run(args: argparse.Namespace) -> None:
    """Execute reconciliation and application runs."""
    start_time_run = datetime.now()
    
    logger.info("Loading configuration...")
    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()

    setup_logging(settings)
    ensure_directories(settings)

    db_path = resolve_path(settings.paths.database)
    export_dir = resolve_path(settings.paths.exports)
    run_id = str(uuid.uuid4())[:8]

    logger.info("Database Path: {}", db_path)

    # Initialize Repo
    temp_repo = JobRepository(db_path)
    temp_repo.close()

    repo = ApplyDiscoveryRepository(db_path)
    cursor = repo._conn.cursor()

    # Create reconciliation tracking table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_reconciliations (
            job_id INTEGER PRIMARY KEY,
            job_title TEXT,
            company_name TEXT,
            job_url TEXT,
            status TEXT NOT NULL,
            error_category TEXT,
            error_message TEXT,
            stack_trace TEXT,
            failing_stage TEXT,
            processed_at TEXT NOT NULL,
            duration_seconds REAL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
    """)
    repo._conn.commit()

    # Step 1: Load All Evaluated Jobs in deterministic order
    logger.info("Loading all evaluated jobs from ai_evaluations...")
    cursor.execute("""
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
            j.search_location,
            e.action,
            e.interview_probability,
            e.priority
        FROM jobs j
        JOIN ai_evaluations e ON e.job_id = j.id
        ORDER BY j.id ASC
    """)
    evaluated_jobs = [dict(row) for row in cursor.fetchall()]
    total_evaluated = len(evaluated_jobs)
    logger.info("Total evaluated jobs loaded: {}", total_evaluated)

    # Step 2: Load All Existing Successful Applications
    logger.info("Loading existing successful applications...")
    cursor.execute("""
        SELECT 
            ja.id AS application_id,
            ja.job_id,
            ja.apply_type,
            ja.apply_url,
            ja.status AS application_status,
            j.status AS job_status,
            j.job_url,
            j.normalized_url,
            j.company_name,
            j.job_title
        FROM job_applications ja
        JOIN jobs j ON ja.job_id = j.id
        WHERE ja.apply_type IN ('applied_successfully', 'easy_apply', 'already_applied', 'external_portal')
    """)
    applied_jobs = [dict(row) for row in cursor.fetchall()]
    
    # Build lookups for comparison
    applied_by_job_id = set()
    applied_by_url = set()
    applied_by_ext_id = set()
    applied_by_company_title = set()

    for app in applied_jobs:
        if app["job_id"]:
            applied_by_job_id.add(app["job_id"])
        
        for url in (app["job_url"], app["normalized_url"], app["apply_url"]):
            if url:
                applied_by_url.add(url)
                ext_id = extract_ext_id(url)
                if ext_id:
                    applied_by_ext_id.add(ext_id)
        
        comp_title = normalize_company_title(app["company_name"], app["job_title"])
        applied_by_company_title.add(comp_title)

    # Load already reconciled records from reconciliation table for resumption
    reconciled_jobs = {}
    cursor.execute("SELECT job_id, status FROM job_reconciliations")
    for row in cursor.fetchall():
        reconciled_jobs[row['job_id']] = row['status']

    # Step 3: Reconciliation Phase
    reapply_queue = []
    
    for ej in evaluated_jobs:
        ej_id = ej["id"]
        ej_url = ej["job_url"]
        ej_normalized_url = ej["normalized_url"]
        ej_company = ej["company_name"]
        ej_title = ej["job_title"]
        ej_action = (ej.get("action") or "").upper()
        
        # Check recovery state
        if ej_id in reconciled_jobs:
            current_status = reconciled_jobs[ej_id]
            if current_status in ('applied', 'already_applied', 'skipped_non_eligible'):
                continue
            elif current_status == 'failed' and not args.retry_failed:
                continue

        # Check eligibility (must be action = 'APPLY')
        if ej_action != "APPLY":
            # Store skipped non-eligible result
            cursor.execute("""
                INSERT OR REPLACE INTO job_reconciliations 
                (job_id, job_title, company_name, job_url, status, processed_at, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (ej_id, ej_title, ej_company, ej_url, 'skipped_non_eligible', datetime.now().isoformat(), 0.0))
            repo._conn.commit()
            continue

        # Check if already applied
        is_applied = False
        match_reason = ""
        
        if ej_id in applied_by_job_id:
            is_applied = True
            match_reason = f"job_id={ej_id}"
            
        if not is_applied:
            for url in (ej_normalized_url, ej_url):
                if url and url in applied_by_url:
                    is_applied = True
                    match_reason = "URL"
                    break
                    
        if not is_applied:
            ext_id = extract_ext_id(ej_url) or extract_ext_id(ej_normalized_url)
            if ext_id and ext_id in applied_by_ext_id:
                is_applied = True
                match_reason = f"external_id={ext_id}"
                
        if not is_applied:
            comp_title = normalize_company_title(ej_company, ej_title)
            if comp_title in applied_by_company_title:
                is_applied = True
                match_reason = "company_title"

        if is_applied:
            # Save reconciliation status 'already_applied'
            cursor.execute("""
                INSERT OR REPLACE INTO job_reconciliations 
                (job_id, job_title, company_name, job_url, status, processed_at, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (ej_id, ej_title, ej_company, ej_url, 'already_applied', datetime.now().isoformat(), 0.0))
            repo._conn.commit()
            logger.info("RECONCILED: Job ID {} ('{}' at '{}') marked as already_applied", ej_id, ej_title, ej_company)
        else:
            # Eligible and missing application, queue for browser action
            reapply_queue.append(ej)

    logger.info("Reconciliation analysis complete. Missing eligible applications count: {}", len(reapply_queue))

    # Step 4: Application Processing Phase
    if not reapply_queue:
        logger.info("All eligible jobs are already applied to. No action needed.")
    elif args.dry_run:
        logger.info("DRY-RUN mode: showing first {} missing applications that would be applied to:", args.max_reapply)
        for index, ej in enumerate(reapply_queue[:args.max_reapply], start=1):
            logger.info(
                "[{}/{}] Job ID: {} | Role: {} | Company: {}",
                index, min(len(reapply_queue), args.max_reapply),
                ej["id"], ej["job_title"], ej["company_name"]
            )
    else:
        logger.info("Launching browser session for applications...")
        try:
            async with BrowserSession(settings, selectors) as session:
                page = await session.validate_session()
                service = ApplyDiscoveryService(repo, settings, selectors)
                
                # Seed the question bank
                logger.info("Seeding question bank from registry...")
                from app.question_bank.seeder import QuestionBankSeeder
                seeder = QuestionBankSeeder(repo)
                seeder.seed()

                limit = min(len(reapply_queue), args.max_reapply)
                logger.info("Processing {} applications...", limit)

                for index, ej in enumerate(reapply_queue[:limit], start=1):
                    ej_id = ej["id"]
                    ej_title = ej["job_title"]
                    ej_company = ej["company_name"]
                    ej_url = ej["job_url"]
                    
                    logger.info("Processing job {}/{}: ID {} ({} - {})", index, limit, ej_id, ej_company, ej_title)
                    
                    # Recreate job data object
                    job_obj = JobData(
                        id=ej["id"],
                        job_title=ej["job_title"],
                        company_name=ej["company_name"],
                        job_description=ej["job_description"] or "",
                        job_url=ej["job_url"],
                        normalized_url=ej["normalized_url"] or "",
                        apply_url=ej["apply_url"] or "",
                        experience_required=ej["experience_required"] or "",
                        location=ej["location"] or "",
                        posted_date=ej["posted_date"] or "",
                        recruiter_name=ej["recruiter_name"] or "",
                        recruiter_email=ej["recruiter_email"] or "",
                        status=ej["status"] or "pending",
                        retry_count=ej["retry_count"] or 0,
                        search_keyword=ej.get("search_keyword"),
                        search_location=ej.get("search_location")
                    )

                    start_time_job = datetime.now()
                    status = 'failed'
                    error_category = 'unknown_error'
                    error_msg = None
                    stack_trace = None
                    failing_stage = 'discovery'

                    try:
                        # 1. Clear application in repo to allow clean retry
                        repo.clear_application(job_obj.id)
                        
                        # 2. Run discovery and form filling
                        outcome = await service._discover_job(page, job_obj)
                        
                        # 3. Save application details
                        repo.save_application(outcome.record)
                        for question in outcome.questions:
                            repo.save_question(job_obj.id, question)
                            
                        # 4. Classify outcome
                        apply_type = outcome.record.apply_type or "unknown"
                        
                        if apply_type == "quota_exhausted":
                            status = 'failed'
                            error_category = 'rate_limit_error'
                            error_msg = outcome.record.quota_message or "Quota exhausted"
                            repo.update_job_status(job_obj.id, "quota_exhausted")
                        elif apply_type == "external_portal":
                            status = 'failed'
                            error_category = 'external_portal'
                            error_msg = f"Redirected to external portal: {outcome.record.apply_url}"
                            repo.update_job_status(job_obj.id, "external_portal")
                        elif apply_type in ("login_required", "register"):
                            status = 'failed'
                            error_category = 'login_error'
                            error_msg = f"Requires {apply_type}"
                            repo.update_job_status(job_obj.id, apply_type)
                        elif apply_type == "unknown":
                            page_text = await page.locator("body").inner_text()
                            expired_keywords = ["expired", "no longer active", "deactivated", "removed by the recruiter"]
                            if any(kw in page_text.lower() for kw in expired_keywords):
                                status = 'failed'
                                error_category = 'job_expired'
                                error_msg = "Job has expired or is no longer active"
                            else:
                                status = 'failed'
                                error_category = 'unknown_error'
                                error_msg = "Unknown apply flow or no apply button found"
                            repo.update_job_status(job_obj.id, "unknown")
                        elif apply_type == "easy_apply":
                            # Check form fill results
                            has_unknown = False
                            has_error = False
                            fill_error_details = []
                            unknown_questions = []
                            
                            if outcome.form_fill_report is not None:
                                for f in outcome.form_fill_report.filled:
                                    if f.status == "error":
                                        has_error = True
                                        fill_error_details.append(f"{f.question_key}: {f.error}")
                                for u in outcome.form_fill_report.unknown:
                                    has_unknown = True
                                    unknown_questions.append(u.question_key)
                            else:
                                if any(q.field_type == "unknown" for q in outcome.questions):
                                    has_unknown = True
                                    unknown_questions = [q.question_key for q in outcome.questions if q.field_type == "unknown"]
                                    
                            if has_error:
                                status = 'failed'
                                error_category = 'form_fill_error'
                                error_msg = "Form fill errors: " + ", ".join(fill_error_details)
                                failing_stage = 'form_fill'
                                repo.increment_retry_count(job_obj.id)
                                repo.update_job_status(job_obj.id, "temporary_failure")
                            elif has_unknown:
                                status = 'failed'
                                error_category = 'question_answering_error'
                                error_msg = "Unanswered/unknown questions: " + ", ".join(unknown_questions)
                                failing_stage = 'form_fill'
                                repo.update_job_status(job_obj.id, "unknown_question")
                            else:
                                status = 'applied'
                                repo.update_job_status(job_obj.id, "easy_apply")
                        elif apply_type == "applied_successfully":
                            status = 'applied'
                            repo.update_job_status(job_obj.id, "applied_successfully")
                        elif apply_type == "already_applied":
                            status = 'already_applied'
                            repo.update_job_status(job_obj.id, "already_applied")
                        else:
                            status = 'applied'
                            repo.update_job_status(job_obj.id, apply_type)

                    except PipelineSuspendedException as exc:
                        logger.warning("Pipeline suspended for Job ID {}: {}", ej_id, exc)
                        status = 'failed'
                        error_category = 'question_answering_error'
                        error_msg = str(exc)
                        stack_trace = traceback.format_exc()
                        failing_stage = 'form_fill'
                        repo.update_job_status(ej_id, "waiting_for_user")
                        
                    except QuotaExhaustedStop as exc:
                        logger.warning("Quota exhausted for Job ID {}: {}", ej_id, exc)
                        status = 'failed'
                        error_category = 'rate_limit_error'
                        error_msg = str(exc)
                        stack_trace = traceback.format_exc()
                        failing_stage = 'discovery'
                        repo.update_job_status(ej_id, "quota_exhausted")
                        
                    except Exception as exc:
                        logger.exception("Unexpected error processing Job ID {}: {}", ej_id, exc)
                        status = 'failed'
                        import playwright.async_api
                        if isinstance(exc, (playwright.async_api.Error, asyncio.TimeoutError)):
                            error_category = 'page_load_error'
                            repo.update_job_status(ej_id, "browser_error")
                        elif "login" in str(exc).lower() or "register" in str(exc).lower():
                            error_category = 'login_error'
                            repo.update_job_status(ej_id, "temporary_failure")
                        elif "expired" in str(exc).lower():
                            error_category = 'job_expired'
                            repo.update_job_status(ej_id, "temporary_failure")
                        else:
                            error_category = 'unknown_error'
                            repo.update_job_status(ej_id, "temporary_failure")
                        error_msg = str(exc)
                        stack_trace = traceback.format_exc()
                        repo.increment_retry_count(ej_id)

                    # Update reconciliation status in DB
                    duration_seconds = (datetime.now() - start_time_job).total_seconds()
                    cursor.execute("""
                        INSERT OR REPLACE INTO job_reconciliations 
                        (job_id, job_title, company_name, job_url, status, error_category, error_message, stack_trace, failing_stage, processed_at, duration_seconds)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ej_id,
                        ej_title,
                        ej_company,
                        ej_url,
                        status,
                        error_category if status == 'failed' else None,
                        error_msg if status == 'failed' else None,
                        stack_trace if status == 'failed' else None,
                        failing_stage if status == 'failed' else None,
                        datetime.now().isoformat(),
                        duration_seconds
                    ))
                    repo._conn.commit()

                    # Create detailed logs
                    logger.info(
                        "\n--- RECONCILIATION JOB SUMMARY ---\n"
                        "Job ID:         {}\n"
                        "Company:        {}\n"
                        "Job Title:      {}\n"
                        "URL:            {}\n"
                        "Start Time:     {}\n"
                        "End Time:       {}\n"
                        "Duration:       {:.2f}s\n"
                        "Final Status:   {}\n"
                        "Error Category: {}\n"
                        "Error Details:  {}\n"
                        "----------------------------------",
                        ej_id, ej_company, ej_title, ej_url,
                        start_time_job.isoformat(), datetime.now().isoformat(),
                        duration_seconds,
                        status,
                        error_category if status == 'failed' else 'N/A',
                        error_msg if status == 'failed' else 'N/A'
                    )

        except (ProfileNotFoundError, SessionExpiredError) as exc:
            logger.error("Session validation failed: {}", exc)
            sys.exit(1)
        finally:
            logger.info("Exporting updated apply discovery reports...")
            try:
                exporter = ApplyDiscoveryExporter(db_path, export_dir)
                exporter.export()
                exporter.export_debug()
            except Exception as exc:
                logger.error("Failed to export updated apply discovery reports: {}", exc)

    # Calculate final duration
    duration_run = (datetime.now() - start_time_run).total_seconds()
    
    # Export reports
    export_reports(db_path, export_dir, duration_run)
    
    # Query final stats from DB for printed report
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_reconciliations'")
    if cursor.fetchone():
        cursor.execute("SELECT status, error_category FROM job_reconciliations")
        rows = [dict(row) for row in cursor.fetchall()]
        
        # Calculate printed numbers
        cursor.execute("SELECT COUNT(*) FROM ai_evaluations")
        total_eval = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ai_evaluations WHERE UPPER(action) = 'APPLY'")
        total_elig = cursor.fetchone()[0]
        
        already_applied = sum(1 for r in rows if r['status'] == 'already_applied')
        successful_applied = sum(1 for r in rows if r['status'] == 'applied')
        failed_applied = sum(1 for r in rows if r['status'] == 'failed')
        
        missing_apps = max(0, total_elig - already_applied)
        attempted_apps = successful_applied + failed_applied
        external_portals = sum(1 for r in rows if r['status'] == 'failed' and r['error_category'] == 'external_portal')
        
        success_rate = (successful_applied / attempted_apps * 100) if attempted_apps > 0 else 0.0
        
        minutes = int(duration_run // 60)
        seconds = int(duration_run % 60)
        exec_time_str = f"{minutes}m {seconds}s"
        
        print()
        print("==================================================")
        print("ONE-TIME APPLICATION RECONCILIATION REPORT")
        print("==================================================")
        print(f"Evaluated Jobs:         {total_eval}")
        print(f"Already Applied:        {already_applied}")
        print(f"Missing Applications:   {missing_apps}")
        print(f"Attempted Applications: {attempted_apps}")
        print(f"Successfully Applied:   {successful_applied}")
        print(f"Failed Applications:    {failed_applied}")
        print(f"External Portals:       {external_portals}")
        print(f"Success Rate:           {success_rate:.2f}%")
        print(f"Duration:               {exec_time_str}")
        print("==================================================")
        print()

    repo.close()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(1)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
