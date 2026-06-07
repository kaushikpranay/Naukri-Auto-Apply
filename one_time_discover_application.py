"""
one_time_discover_application.py — Temporary utility script to reconcile and re-apply missing jobs.

This script performs a complete reconciliation between:
1. All evaluated jobs (stored in the ai_evaluations table).
2. All applied jobs (stored in the job_applications table).

Missing applications that had an AI action of 'APPLY' are re-queued and reapplied.
"""

import argparse
import asyncio
import sys
import uuid
import re
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
        description="One-Time Job Application Reconciliation Utility"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Perform reconciliation and print report without executing any browser action or changing DB state.",
    )
    parser.add_argument(
        "--max-reapply",
        type=int,
        default=100,
        help="Maximum number of missing applications to re-run in this session to prevent accidental mass applications.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    """Execute reconciliation and reapplications."""
    logger.info("Loading configuration...")
    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()

    setup_logging(settings)
    ensure_directories(settings)

    db_path = resolve_path(settings.paths.database)
    export_dir = resolve_path(settings.paths.exports)
    run_id = str(uuid.uuid4())[:8]

    logger.info("Database Path: {}", db_path)

    # Initialize Repository to execute migration check and establish connection
    temp_repo = JobRepository(db_path)
    temp_repo.close()

    repo = ApplyDiscoveryRepository(db_path)
    cursor = repo._conn.cursor()

    # Step 1: Load All Evaluated Jobs
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
    """)
    evaluated_jobs = [dict(row) for row in cursor.fetchall()]
    total_evaluated = len(evaluated_jobs)
    logger.info("Total evaluated jobs loaded: {}", total_evaluated)

    # Step 2: Load All Applied Jobs
    logger.info("Loading all applied jobs from job_applications...")
    cursor.execute("""
        SELECT 
            ja.id AS application_id,
            ja.job_id,
            ja.apply_type,
            ja.apply_url,
            ja.status AS application_status,
            j.job_url,
            j.normalized_url,
            j.company_name,
            j.job_title
        FROM job_applications ja
        JOIN jobs j ON ja.job_id = j.id
    """)
    applied_jobs = [dict(row) for row in cursor.fetchall()]
    total_applied = len(applied_jobs)
    logger.info("Total applied jobs loaded: {}", total_applied)

    # Build fast lookups
    applied_by_job_id = set()
    applied_by_url = set()
    applied_by_ext_id = set()
    applied_by_company_title = set()

    for app in applied_jobs:
        if app["job_id"]:
            applied_by_job_id.add(app["job_id"])
        
        # Add URLs
        for url in (app["job_url"], app["normalized_url"], app["apply_url"]):
            if url:
                applied_by_url.add(url)
                ext_id = extract_ext_id(url)
                if ext_id:
                    applied_by_ext_id.add(ext_id)
        
        # Add company + title
        comp_title = normalize_company_title(app["company_name"], app["job_title"])
        applied_by_company_title.add(comp_title)

    # Step 3: Compare Records
    already_applied_count = 0
    missing_count = 0
    reapply_queue = []

    for ej in evaluated_jobs:
        ej_id = ej["id"]
        ej_url = ej["job_url"]
        ej_normalized_url = ej["normalized_url"]
        ej_company = ej["company_name"]
        ej_title = ej["job_title"]
        ej_action = ej.get("action", "APPLY")
        
        is_applied = False
        match_reason = ""
        
        # 1. Match by job_id
        if ej_id in applied_by_job_id:
            is_applied = True
            match_reason = f"job_id={ej_id}"
            
        # 2. Match by URLs
        if not is_applied:
            for url in (ej_normalized_url, ej_url):
                if url and url in applied_by_url:
                    is_applied = True
                    match_reason = f"URL"
                    break
                    
        # 3. Match by external job ID
        if not is_applied:
            ext_id = extract_ext_id(ej_url) or extract_ext_id(ej_normalized_url)
            if ext_id and ext_id in applied_by_ext_id:
                is_applied = True
                match_reason = f"external_id={ext_id}"
                
        # 4. Match by company + title
        if not is_applied:
            comp_title = normalize_company_title(ej_company, ej_title)
            if comp_title in applied_by_company_title:
                is_applied = True
                match_reason = f"company_title"

        if is_applied:
            already_applied_count += 1
            logger.debug(
                "MATCH: Job ID {} ('{}' at '{}') already applied (reason: {})",
                ej_id, ej_title, ej_company, match_reason
            )
        else:
            missing_count += 1
            logger.info(
                "MISSING: Job ID {} ('{}' at '{}') has no application. Action: {}",
                ej_id, ej_title, ej_company, ej_action
            )
            # Re-queue only if the AI action is APPLY
            if ej_action.upper() == "APPLY":
                reapply_queue.append(ej)

    logger.info("Reconciliation scan complete.")
    logger.info("Total matched (already applied): {}", already_applied_count)
    logger.info("Total missing applications: {}", missing_count)
    logger.info("Total missing that require application (action=APPLY): {}", len(reapply_queue))

    # Step 4: Re-Run Missing Applications
    successfully_reapplied = 0
    failed_reapplications = 0

    if not reapply_queue:
        logger.info("No missing applications to re-run.")
    elif args.dry_run:
        logger.info("DRY-RUN mode: showing first {} missing applications that would be re-run:", args.max_reapply)
        for index, ej in enumerate(reapply_queue[:args.max_reapply], start=1):
            logger.info(
                "[{}/{}] Job ID: {} | Role: {} | Company: {}",
                index, min(len(reapply_queue), args.max_reapply),
                ej["id"], ej["job_title"], ej["company_name"]
            )
    else:
        # Live run: reapply jobs using standard pipeline
        logger.info("Initializing Browser session for re-applications...")
        try:
            async with BrowserSession(settings, selectors) as session:
                page = await session.validate_session()
                service = ApplyDiscoveryService(repo, settings, selectors)
                
                # Seed the question bank before processing
                logger.info("Seeding question bank from registry...")
                from app.question_bank.seeder import QuestionBankSeeder
                seeder = QuestionBankSeeder(repo)
                seeder.seed()

                limit = min(len(reapply_queue), args.max_reapply)
                logger.info("Executing re-applications (limit: {})...", limit)

                for index, ej in enumerate(reapply_queue[:limit], start=1):
                    logger.info("Re-applying: {}/{} (Job ID: {} | {} - {})",
                                index, limit, ej["id"], ej["company_name"], ej["job_title"])
                    
                    # Recreate the job object in the format expected by the pipeline
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

                    try:
                        batch_summary = await service.run(
                            page, run_id=run_id, force_job_id=job_obj.id
                        )
                        
                        if batch_summary.failed > 0:
                            logger.error("Re-application failed for Job ID: {}", job_obj.id)
                            failed_reapplications += 1
                        else:
                            logger.info("Re-application succeeded for Job ID: {}", job_obj.id)
                            successfully_reapplied += 1

                        if batch_summary.quota_stopped:
                            logger.warning("Naukri quota limit reached. Stopping further executions.")
                            break

                    except QuotaExhaustedStop as exc:
                        logger.warning("Quota exhaustion stopped re-application: {}", exc)
                        failed_reapplications += 1
                        break
                    except Exception as exc:
                        logger.exception("Error processing Job ID {}: {}", job_obj.id, exc)
                        failed_reapplications += 1

        except (ProfileNotFoundError, SessionExpiredError) as exc:
            logger.error("Session validation failed: {}", exc)
            sys.exit(1)
        finally:
            # Export reports after a live run to capture the updated state
            logger.info("Exporting updated apply discovery reports...")
            try:
                exporter = ApplyDiscoveryExporter(db_path, export_dir)
                exporter.export()
                exporter.export_debug()
            except Exception as exc:
                logger.error("Failed to export updated apply discovery reports: {}", exc)

    repo.close()

    # Step 6: Final Report
    print("\n" + "=" * 50)
    print("ONE-TIME APPLICATION RECONCILIATION REPORT")
    print("=" * 50)
    print(f"Evaluated Jobs:          {total_evaluated}")
    print(f"Applied Jobs:            {total_applied}")
    print(f"Already Applied:         {already_applied_count}")
    print(f"Missing Applications:    {missing_count}")
    print(f"Successfully Reapplied:  {successfully_reapplied}")
    print(f"Failed Reapplications:   {failed_reapplications}")
    print("=" * 50 + "\n")
    if args.dry_run:
        print("NOTE: Executed in DRY-RUN mode. No state changes were applied.\n")


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(1)
    except PipelineSuspendedException:
        print("\n\nPipeline suspended: WAITING_FOR_USER action required in browser. Exiting...")
        sys.exit(0)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n[ERROR] Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
