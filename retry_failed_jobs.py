"""
CLI tool to retry processing jobs that failed due to temporary errors or missing information.
Usage:
    python retry_failed_jobs.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from loguru import logger

from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
from app.discovery.repository import ApplyDiscoveryRepository
from app.discovery.service import ApplyDiscoveryService
from app.export.apply_discovery_exporter import ApplyDiscoveryExporter
from app.export.application_review_exporter import ApplicationReviewExporter
from app.export.form_fill_report_exporter import FormFillReportExporter
from app.export.question_bank_report_exporter import QuestionBankReportExporter
from app.models.application_review import build_review_record
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoverySummary, QuotaExhaustedStop, PipelineSuspendedException
from app.question_bank.lookup_service import QuestionBankLookupService
from app.question_bank.seeder import QuestionBankSeeder
from app.utils.config_loader import (
    ensure_directories,
    load_selectors,
    load_settings,
    resolve_path,
)


def setup_logging(settings: AppSettings) -> None:
    """Configure logging for the retry run."""
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
        str(log_dir / "retry_failed_jobs_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        encoding="utf-8",
    )


async def run() -> None:
    """Execute the retry pipeline."""
    logger.info("Loading configuration...")
    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()

    setup_logging(settings)
    ensure_directories(settings)

    run_id = str(uuid.uuid4())[:8]
    db_path = resolve_path(settings.paths.database)
    export_dir = resolve_path(settings.paths.exports)
    summary = DiscoverySummary()

    logger.info("Database Path: {}", db_path)
    logger.info(
        "Discovery limit: {} job(s) per run",
        settings.discovery.max_discovery_jobs_per_run,
    )

    from app.database.repository import JobRepository
    # Instantiate JobRepository to ensure all DB migrations are applied
    temp_job_repo = JobRepository(db_path)
    temp_job_repo.close()

    repo = ApplyDiscoveryRepository(db_path)

    # Seed question bank
    logger.info("Seeding question bank from candidate answer registry...")
    seeder = QuestionBankSeeder(repo)
    seeded_count = seeder.seed()
    logger.info("Question bank seed complete: {} entries.", seeded_count)

    try:
        async with BrowserSession(settings, selectors) as session:
            try:
                page = await session.validate_session()
            except SessionExpiredError as exc:
                logger.error(str(exc))
                print(f"\nError: {exc}")
                sys.exit(1)
            except ProfileNotFoundError as exc:
                logger.error(str(exc))
                print(f"\nError: {exc}")
                sys.exit(1)

            service = ApplyDiscoveryService(repo, settings, selectors)
            
            cursor = repo._conn.cursor()
            cursor.execute("""
                SELECT j.id
                FROM jobs j
                JOIN ai_evaluations e ON e.job_id = j.id
                WHERE UPPER(e.action) = 'APPLY'
                  AND j.status IN ('unknown_question', 'waiting_for_user', 'quota_exhausted', 'temporary_failure', 'browser_error')
                ORDER BY e.interview_probability DESC, j.id ASC
            """)
            retry_job_ids = [row[0] for row in cursor.fetchall()]

            if not retry_job_ids:
                logger.info("No retryable jobs in the queue.")
                print("\nNo retryable jobs in the queue.")
            else:
                logger.info("Found {} retryable job(s) in queue", len(retry_job_ids))
                print(f"\nFound {len(retry_job_ids)} retryable job(s) in queue")

                for index, job_id in enumerate(retry_job_ids, start=1):
                    logger.info("Processing retry job {}/{} (ID: {})", index, len(retry_job_ids), job_id)
                    print(f"\nProcessing retry job {index}/{len(retry_job_ids)} (ID: {job_id})")

                    try:
                        batch_summary = await service.run(
                            page, run_id=run_id, force_job_id=job_id
                        )
                    except QuotaExhaustedStop as exc:
                        logger.warning("QuotaExhaustedStop raised: {}", exc)
                        summary.quota_stopped = True
                        if exc.summary:
                            summary.processed += exc.summary.processed
                            summary.discovered += exc.summary.discovered
                            summary.already_applied += exc.summary.already_applied
                            summary.requires_review += exc.summary.requires_review
                            summary.failed += exc.summary.failed
                            summary.easy_apply += exc.summary.easy_apply
                            summary.external_portal += exc.summary.external_portal
                            summary.email += exc.summary.email
                            summary.needs_register += exc.summary.needs_register
                            summary.login_required += exc.summary.login_required
                            summary.unknown_flow += exc.summary.unknown_flow
                            summary.quota_exhausted += exc.summary.quota_exhausted
                            summary.form_fill_reports.extend(exc.summary.form_fill_reports)
                        break

                    summary.processed += batch_summary.processed
                    summary.discovered += batch_summary.discovered
                    summary.already_applied += batch_summary.already_applied
                    summary.requires_review += batch_summary.requires_review
                    summary.failed += batch_summary.failed
                    summary.easy_apply += batch_summary.easy_apply
                    summary.external_portal += batch_summary.external_portal
                    summary.email += batch_summary.email
                    summary.needs_register += batch_summary.needs_register
                    summary.login_required += batch_summary.login_required
                    summary.unknown_flow += batch_summary.unknown_flow
                    summary.quota_exhausted += batch_summary.quota_exhausted
                    summary.form_fill_reports.extend(batch_summary.form_fill_reports)

                    if batch_summary.quota_stopped:
                        break

    finally:
        repo.close()

    logger.info("Exporting retry discovery workbooks...")
    try:
        exporter = ApplyDiscoveryExporter(db_path, export_dir)
        export_path = exporter.export()
        logger.info("Export successful: {}", export_path)
        debug_path = exporter.export_debug()
        logger.info("Debug export successful: {}", debug_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to export apply discovery workbook: {}", exc)

    # Question bank lookup + report
    logger.info("Running question bank lookup...")
    qb_report = None
    try:
        lookup_service = QuestionBankLookupService(db_path)
        qb_report = lookup_service.run()
        lookup_service.close()
    except Exception as exc:  # noqa: BLE001
        logger.error("Question bank lookup failed: {}", exc)

    if qb_report is not None:
        logger.info("Exporting question bank report...")
        try:
            report_exporter = QuestionBankReportExporter(export_dir)
            report_path = report_exporter.export(qb_report)
            logger.info("Question bank report exported: {}", report_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to export question bank report: {}", exc)

    # Export form fill report
    from app.question_bank.form_filler import DRY_RUN as FORM_FILL_DRY_RUN
    ff_reports = summary.form_fill_reports
    if ff_reports:
        logger.info("Exporting form fill report ({} job(s))...", len(ff_reports))
        try:
            ff_exporter = FormFillReportExporter(export_dir)
            ff_path = ff_exporter.export(ff_reports)
            logger.info("Form fill report exported: {}", ff_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to export form fill report: {}", exc)

    # Application review
    review_records = [build_review_record(r) for r in ff_reports]
    if review_records:
        logger.info("Exporting application review ({} job(s))...", len(review_records))
        try:
            review_exporter = ApplicationReviewExporter(export_dir)
            review_path = review_exporter.export(review_records)
            logger.info("Application review exported: {}", review_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to export application review: {}", exc)

    print("\n" + "=" * 40)
    print("RETRY FAILED JOBS SUMMARY")
    print("=" * 40)
    print(f"Processed:        {summary.processed}")
    print(f"Discovered:       {summary.discovered}")
    print(f"Already Applied:  {summary.already_applied}")
    print(f"Requires Review:  {summary.requires_review}")
    print(f"Easy Apply:       {summary.easy_apply}")
    print(f"External Portal:  {summary.external_portal}")
    print(f"Email:            {summary.email}")
    print(f"Register:         {summary.needs_register}")
    print(f"Login Required:   {summary.login_required}")
    print(f"Unknown:          {summary.unknown_flow}")
    print(f"Quota Exhausted:  {summary.quota_exhausted}")
    print(f"Failed:           {summary.failed}")
    if summary.quota_stopped:
        print()
        print("!" * 40)
        print("QUOTA_EXHAUSTED_DETECTED")
        print("Discovery stopped because Naukri quota appears exhausted.")
        print("Reason:")
        print("3 consecutive quota exhaustion events detected.")
        print("!" * 40)
    print("=" * 40)


def main() -> None:
    """CLI entrypoint for retry failed jobs."""
    print()
    print("==================================================")
    print("       NAUKRI RETRY FAILED JOBS - PIPELINE        ")
    print("==================================================")
    print()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        logger.info("Interrupted by user")
    except PipelineSuspendedException:
        print("\n\nPipeline suspended: WAITING_FOR_USER action required in browser. Exiting...")
        logger.info("Pipeline suspended on interactive question")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal error: {}", exc)
        print(f"\n[ERROR] Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
