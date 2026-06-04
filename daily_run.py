"""
daily_run.py — Complete Naukri automation workflow orchestrator.

Orchestrates:
  1. Job Collection (POC-1)
  2. AI Job Evaluation (POC-2)
  3. Apply Discovery & Form Filling (POC-3)
  4. Export all reports
  5. Print final summary
"""

import sys
import uuid
import asyncio
import time
from pathlib import Path
from datetime import datetime

from loguru import logger

from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
# Collection imports
from app.collector.job_collector import JobCollector
from app.database.repository import JobRepository
from app.export.excel_exporter import ExcelExporter
from app.models.job import JobData as CollectorJobData
# Evaluation imports
from app.database.evaluations_repo import EvaluationsRepository
from app.evaluator.evaluation_service import EvaluationService, EvaluationBatchStats
from app.export.eval_exporter import EvaluatedJobsExporter
# Discovery imports
from app.discovery.repository import ApplyDiscoveryRepository
from app.discovery.service import ApplyDiscoveryService
from app.export.apply_discovery_exporter import ApplyDiscoveryExporter
from app.export.application_review_exporter import ApplicationReviewExporter
from app.export.form_fill_report_exporter import FormFillReportExporter
from app.export.question_bank_report_exporter import QuestionBankReportExporter
from app.question_bank.lookup_service import QuestionBankLookupService
from app.question_bank.seeder import QuestionBankSeeder
from app.models.application_review import build_review_record
from app.models.discovery import DiscoverySummary

# Load configs
from app.models.config import AppSettings, SelectorsConfig, SearchConfig
from app.utils.config_loader import (
    ensure_directories,
    load_search_config,
    load_selectors,
    load_settings,
    resolve_path,
)


def setup_logging(settings: AppSettings) -> None:
    """Configure logging for the daily run."""
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
        str(log_dir / "daily_run_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        encoding="utf-8",
    )


def _build_providers(prompt_path: Path, profile_path: Path) -> list:
    providers = []
    try:
        from app.evaluator.providers.groq_provider import GroqEvaluator
        providers.append(GroqEvaluator(prompt_path, profile_path))
        logger.info("Groq provider initialized.")
    except Exception as exc:
        logger.warning("Groq provider unavailable: {}", exc)

    try:
        from app.evaluator.providers.gemini_provider import GeminiEvaluator
        providers.append(GeminiEvaluator(prompt_path, profile_path))
        logger.info("Gemini provider initialized.")
    except Exception as exc:
        logger.warning("Gemini provider unavailable: {}", exc)

    return providers


async def main_async() -> None:
    start_time = time.perf_counter()

    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()
    search_config: SearchConfig = load_search_config()

    setup_logging(settings)
    ensure_directories(settings)

    run_id = str(uuid.uuid4())[:8]
    db_path = resolve_path(settings.paths.database)
    export_dir = resolve_path(settings.paths.exports)
    prompt_path = resolve_path("prompts/job_evaluation_prompt.txt")
    profile_path = resolve_path("config/candidate_profile.json")

    jobs_collected = 0
    total_eval_stats = EvaluationBatchStats()
    discovery_summary = DiscoverySummary()
    qb_report = None

    logger.info("Launching browser session...")
    async with BrowserSession(settings, selectors) as session:
        try:
            page = await session.validate_session()
        except (ProfileNotFoundError, SessionExpiredError) as exc:
            logger.error("Session validation failed: {}", exc)
            print(f"\nError: {exc}")
            sys.exit(1)

        # ── Stage 1: Collect jobs ─────────────────────────────────────────────
        logger.info("Starting Stage 1: Job Collection...")
        repo_coll = JobRepository(db_path)
        collector = JobCollector(
            page=page,
            settings=settings,
            selectors=selectors,
            search_config=search_config,
        )
        total_combos = len(search_config.keywords) * len(search_config.locations)
        current = 0
        try:
            for keyword in search_config.keywords:
                for location in search_config.locations:
                    current += 1
                    logger.info(f"[{current}/{total_combos}] Searching: '{keyword.display}' in '{location.display}'")
                    try:
                        jobs = await collector.collect_for_search(keyword, location)
                        if jobs:
                            jobs = await collector.enrich_with_details(jobs)
                            inserted, _ = repo_coll.insert_many(jobs)
                            jobs_collected += inserted
                    except Exception as e:
                        logger.error("Error collecting keyword '{}' in '{}': {}", keyword.display, location.display, e)
        finally:
            repo_coll.close()

        # ── Stage 2: Evaluate all pending jobs ───────────────────────────────
        logger.info("Starting Stage 2: Job Evaluation...")
        repo_eval = EvaluationsRepository(db_path)
        providers = _build_providers(prompt_path, profile_path)
        eval_service = EvaluationService(
            repo=repo_eval,
            providers=providers,
            max_jobs_per_run=settings.evaluation.max_ai_evaluations_per_run,
            profile_path=profile_path,
        )
        
        pending_eval_count = repo_eval.get_pending_jobs_count()
        batch_idx = 1
        while pending_eval_count > 0:
            logger.info("Evaluating Batch {} ({} pending)", batch_idx, pending_eval_count)
            try:
                # EvaluationService.run is synchronous
                stats = eval_service.run(run_id)
                if not stats or stats.evaluated == 0:
                    break
                total_eval_stats.evaluated += stats.evaluated
                total_eval_stats.apply += stats.apply
                total_eval_stats.review += stats.review
                total_eval_stats.skip += stats.skip
                total_eval_stats.errors += stats.errors
                
                pending_eval_count = repo_eval.get_pending_jobs_count()
                batch_idx += 1
            except Exception as e:
                logger.error("Error during evaluation batch: {}", e)
                break
        repo_eval.close()

        # ── Stage 3: Process all pending APPLY jobs until none remain ──────────
        logger.info("Starting Stage 3: Apply Discovery...")
        repo_discovery = ApplyDiscoveryRepository(db_path)
        seeder = QuestionBankSeeder(repo_discovery)
        seeder.seed()
        
        discovery_service = ApplyDiscoveryService(repo_discovery, settings, selectors)
        batch_number = 1
        while True:
            cursor = repo_discovery._conn.cursor()
            cursor.execute("""
                SELECT COUNT(*)
                FROM jobs j
                JOIN ai_evaluations e ON e.job_id = j.id
                LEFT JOIN job_applications a ON a.job_id = j.id
                WHERE UPPER(e.action) = 'APPLY'
                  AND a.job_id IS NULL
            """)
            pending_count = cursor.fetchone()[0]

            if pending_count == 0:
                break

            logger.info("Batch {}: Found {} APPLY jobs for discovery", batch_number, pending_count)
            batch_summary = await discovery_service.run(
                page, run_id=run_id, force_job_id=None
            )
            
            discovery_summary.processed += batch_summary.processed
            discovery_summary.discovered += batch_summary.discovered
            discovery_summary.already_applied += batch_summary.already_applied
            discovery_summary.requires_review += batch_summary.requires_review
            discovery_summary.failed += batch_summary.failed
            discovery_summary.easy_apply += batch_summary.easy_apply
            discovery_summary.external_portal += batch_summary.external_portal
            discovery_summary.email += batch_summary.email
            discovery_summary.needs_register += batch_summary.needs_register
            discovery_summary.login_required += batch_summary.login_required
            discovery_summary.unknown_flow += batch_summary.unknown_flow
            discovery_summary.form_fill_reports.extend(batch_summary.form_fill_reports)

            if batch_summary.processed == 0:
                break
            
            batch_number += 1
            
        repo_discovery.close()

    # ── Stage 4: Export all reports ───────────────────────────────────────
    logger.info("Starting Stage 4: Report Export...")
    
    # 1. Collector Excel Export
    try:
        exporter = ExcelExporter(db_path, export_dir)
        exporter.export()
    except Exception as exc:
        logger.error("Excel export failed: {}", exc)

    # 2. Evaluation Excel Export
    try:
        eval_exporter = EvaluatedJobsExporter(db_path, export_dir)
        eval_exporter.export()
    except Exception as exc:
        logger.error("Evaluated jobs export failed: {}", exc)

    # 3. Discovery Excel Export
    try:
        disc_exporter = ApplyDiscoveryExporter(db_path, export_dir)
        disc_exporter.export()
        disc_exporter.export_debug()
    except Exception as exc:
        logger.error("Apply discovery export failed: {}", exc)

    # 4. Question Bank Report
    try:
        lookup_service = QuestionBankLookupService(db_path)
        qb_report = lookup_service.run()
        lookup_service.close()
        if qb_report is not None:
            report_exporter = QuestionBankReportExporter(export_dir)
            report_exporter.export(qb_report)
    except Exception as exc:
        logger.error("Question bank lookup or export failed: {}", exc)

    # 5. Form Fill Report Export
    if discovery_summary.form_fill_reports:
        try:
            ff_exporter = FormFillReportExporter(export_dir)
            ff_exporter.export(discovery_summary.form_fill_reports)
        except Exception as exc:
            logger.error("Form fill report export failed: {}", exc)

    # 6. Application Review Export
    review_records = [build_review_record(r) for r in discovery_summary.form_fill_reports]
    if review_records:
        try:
            review_exporter = ApplicationReviewExporter(export_dir)
            review_exporter.export(review_records)
        except Exception as exc:
            logger.error("Application review export failed: {}", exc)

    # ── Stage 5: Print final summary ─────────────────────────────────────
    duration = time.perf_counter() - start_time
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    exec_time_str = f"{minutes}m {seconds}s"

    known_count = len(qb_report.known) if qb_report else 0
    unknown_count = len(qb_report.unknown) if qb_report else 0
    coverage_pct = qb_report.coverage_pct if qb_report else 0

    print()
    print("==================================================")
    print("              DAILY RUN SUMMARY                   ")
    print("==================================================")
    print(f"Jobs Collected:   {jobs_collected}")
    print(f"Jobs Evaluated:   {total_eval_stats.evaluated}")
    print(f"Apply:            {total_eval_stats.apply}")
    print(f"Review:           {total_eval_stats.review}")
    print(f"Skip:             {total_eval_stats.skip}")
    print()
    print("Applications:")
    print(f"  Easy Apply:     {discovery_summary.easy_apply}")
    print(f"  External Portal:{discovery_summary.external_portal}")
    print(f"  Already Applied:{discovery_summary.already_applied}")
    print(f"  Requires Review:{discovery_summary.requires_review}")
    print(f"  Failed:         {discovery_summary.failed}")
    print()
    print("Question Bank:")
    print(f"  Known:          {known_count}")
    print(f"  Unknown:        {unknown_count}")
    print(f"  Coverage %:     {coverage_pct}%")
    print()
    print(f"Execution Time:   {exec_time_str}")
    print("==================================================")
    print()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
