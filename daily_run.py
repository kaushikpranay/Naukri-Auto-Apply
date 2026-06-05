"""
daily_run.py — Batched pipeline orchestrator for Naukri automation.

Pipeline batching workflow:
  Loop over keyword × location search combinations:
    1. Collect jobs for the current search combo.
       Stop collection when 150 new jobs have been inserted OR the current
       search combo is fully paginated.
    2. Evaluate all newly-collected (pending) jobs.
    3. Run apply discovery on APPLY-evaluated jobs.
    4. Process APPLY jobs (form fill / classification).
    5. Resume collection from the next search combo.
    6. Repeat until all search combinations are exhausted.

Final stage: export reports and print summary.
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
from app.models.discovery import DiscoverySummary, QuotaExhaustedStop

# Load configs
from app.models.config import AppSettings, SelectorsConfig, SearchConfig
from app.utils.config_loader import (
    ensure_directories,
    load_search_config,
    load_selectors,
    load_settings,
    resolve_path,
)

# ── Constants ─────────────────────────────────────────────────────────────────
BATCH_NEW_JOB_THRESHOLD = 150


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


def _accumulate_discovery(total: DiscoverySummary, batch: DiscoverySummary) -> None:
    """Merge a batch discovery summary into the running total."""
    total.processed += batch.processed
    total.discovered += batch.discovered
    total.already_applied += batch.already_applied
    total.requires_review += batch.requires_review
    total.failed += batch.failed
    total.easy_apply += batch.easy_apply
    total.external_portal += batch.external_portal
    total.email += batch.email
    total.needs_register += batch.needs_register
    total.login_required += batch.login_required
    total.unknown_flow += batch.unknown_flow
    total.quota_exhausted += batch.quota_exhausted
    if batch.quota_stopped:
        total.quota_stopped = True
    total.form_fill_reports.extend(batch.form_fill_reports)


def _accumulate_eval_stats(total: EvaluationBatchStats, batch: EvaluationBatchStats) -> None:
    """Merge a batch evaluation stats into the running total."""
    total.evaluated += batch.evaluated
    total.apply += batch.apply
    total.review += batch.review
    total.skip += batch.skip
    total.errors += batch.errors


# ── Pipeline Stages ───────────────────────────────────────────────────────────

def _run_evaluation_stage(
    repo_eval: EvaluationsRepository,
    eval_service: EvaluationService,
    run_id: str,
    total_eval_stats: EvaluationBatchStats,
    pipeline_batch: int,
) -> None:
    """Evaluate all pending (unevaluated) jobs. Runs in sub-batches."""
    pending = repo_eval.get_pending_jobs_count()
    if pending == 0:
        logger.info("Pipeline batch {}: No pending jobs to evaluate.", pipeline_batch)
        return

    logger.info("Pipeline batch {}: Evaluating {} pending jobs...", pipeline_batch, pending)
    sub_batch = 1
    while pending > 0:
        logger.info("  Eval sub-batch {} ({} pending)", sub_batch, pending)
        try:
            stats = eval_service.run(run_id)
            if not stats or stats.evaluated == 0:
                break
            _accumulate_eval_stats(total_eval_stats, stats)
            pending = repo_eval.get_pending_jobs_count()
            sub_batch += 1
        except Exception as e:
            logger.error("Error during evaluation sub-batch: {}", e)
            break

    logger.info(
        "EVALUATION_BATCH_COMPLETE | pipeline_batch={} evaluated={} apply={} review={} skip={}",
        pipeline_batch,
        total_eval_stats.evaluated,
        total_eval_stats.apply,
        total_eval_stats.review,
        total_eval_stats.skip,
    )


async def _run_discovery_stage(
    page,
    repo_discovery: ApplyDiscoveryRepository,
    discovery_service: ApplyDiscoveryService,
    run_id: str,
    total_discovery: DiscoverySummary,
    pipeline_batch: int,
) -> bool:
    """Process all pending APPLY jobs through discovery + form-fill.

    Returns:
        True  — loop should continue.
        False — quota exhaustion detected, stop the pipeline.
    """
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

        logger.info(
            "Pipeline batch {}: Discovery sub-batch {} — {} APPLY jobs pending",
            pipeline_batch, batch_number, pending_count,
        )
        try:
            batch_summary = await discovery_service.run(
                page, run_id=run_id, force_job_id=None
            )
        except QuotaExhaustedStop as exc:
            logger.warning(
                "QUOTA_EXHAUSTED_STOP: pipeline_batch={} reason='{}'",
                pipeline_batch, exc,
            )
            total_discovery.quota_stopped = True
            if exc.summary:
                _accumulate_discovery(total_discovery, exc.summary)
            break

        _accumulate_discovery(total_discovery, batch_summary)

        if total_discovery.quota_stopped or batch_summary.quota_stopped:
            break

        if batch_summary.processed == 0:
            break

        batch_number += 1

    logger.info(
        "DISCOVERY_BATCH_COMPLETE | pipeline_batch={} processed={} easy_apply={} "
        "external={} quota_exhausted={} failed={}",
        pipeline_batch,
        total_discovery.processed,
        total_discovery.easy_apply,
        total_discovery.external_portal,
        total_discovery.quota_exhausted,
        total_discovery.failed,
    )
    return not total_discovery.quota_stopped


# ── Main ──────────────────────────────────────────────────────────────────────

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

        # ── Shared service instances (reused across pipeline batches) ─────
        repo_coll = JobRepository(db_path)
        repo_eval = EvaluationsRepository(db_path)
        providers = _build_providers(prompt_path, profile_path)
        eval_service = EvaluationService(
            repo=repo_eval,
            providers=providers,
            max_jobs_per_run=settings.evaluation.max_ai_evaluations_per_run,
            profile_path=profile_path,
        )
        repo_discovery = ApplyDiscoveryRepository(db_path)
        seeder = QuestionBankSeeder(repo_discovery)
        seeder.seed()
        discovery_service = ApplyDiscoveryService(repo_discovery, settings, selectors)

        collector = JobCollector(
            page=page,
            settings=settings,
            selectors=selectors,
            search_config=search_config,
        )

        # ── Build the search combination queue ────────────────────────────
        search_combos = [
            (keyword, location)
            for keyword in search_config.keywords
            for location in search_config.locations
        ]
        total_combos = len(search_combos)

        # ── Pipeline batching loop ────────────────────────────────────────
        pipeline_batch = 0
        combo_index = 0          # next search combo to process
        batch_new_jobs = 0       # new jobs in the current pipeline batch

        logger.info(
            "Starting batched pipeline: {} search combos, batch threshold = {} new jobs",
            total_combos, BATCH_NEW_JOB_THRESHOLD,
        )

        while combo_index < total_combos:
            pipeline_batch += 1
            batch_new_jobs = 0
            batch_combos_processed = 0

            logger.info(
                "═══ Pipeline Batch {} ═══ (resuming from combo {}/{})",
                pipeline_batch, combo_index + 1, total_combos,
            )

            # ── Step 1: Collect jobs until threshold or combos exhausted ──
            while combo_index < total_combos:
                keyword, location = search_combos[combo_index]
                combo_index += 1
                batch_combos_processed += 1

                logger.info(
                    "[{}/{}] Searching: '{}' in '{}'",
                    combo_index, total_combos,
                    keyword.display, location.display,
                )
                try:
                    jobs = await collector.collect_for_search(keyword, location)
                    if jobs:
                        jobs = await collector.enrich_with_details(jobs)
                        inserted, _ = repo_coll.insert_many(jobs)
                        jobs_collected += inserted
                        batch_new_jobs += inserted
                except Exception as e:
                    logger.error(
                        "Error collecting '{}' in '{}': {}",
                        keyword.display, location.display, e,
                    )

                # Check if we've hit the batch threshold
                if batch_new_jobs >= BATCH_NEW_JOB_THRESHOLD:
                    logger.info(
                        "Batch threshold reached: {} new jobs collected (threshold={}). "
                        "Pausing collection for evaluation/discovery.",
                        batch_new_jobs, BATCH_NEW_JOB_THRESHOLD,
                    )
                    break

            logger.info(
                "COLLECTION_BATCH_COMPLETE | pipeline_batch={} combos_processed={} "
                "new_jobs={} total_collected={}",
                pipeline_batch, batch_combos_processed,
                batch_new_jobs, jobs_collected,
            )

            # ── Step 2: Evaluate newly collected jobs ─────────────────────
            logger.info("Pipeline batch {}: Starting evaluation stage...", pipeline_batch)
            _run_evaluation_stage(
                repo_eval, eval_service, run_id,
                total_eval_stats, pipeline_batch,
            )

            # ── Step 3 & 4: Discovery + Application processing ───────────
            logger.info("Pipeline batch {}: Starting discovery stage...", pipeline_batch)
            should_continue = await _run_discovery_stage(
                page, repo_discovery, discovery_service,
                run_id, discovery_summary, pipeline_batch,
            )

            if not should_continue:
                logger.warning(
                    "Pipeline stopping early due to quota exhaustion "
                    "(pipeline_batch={}).", pipeline_batch
                )
                # Break outer combo loop so we reach exports + summary
                combo_index = total_combos
                break

            logger.info(
                "Pipeline batch {} complete: collected={} evaluated={} "
                "applications_processed={}",
                pipeline_batch, batch_new_jobs,
                total_eval_stats.evaluated, discovery_summary.processed,
            )

        # ── Close database connections ────────────────────────────────────
        repo_coll.close()
        repo_eval.close()
        repo_discovery.close()

    # ── Stage: Export all reports ──────────────────────────────────────────
    logger.info("Starting report export stage...")

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

    # ── Final Summary ─────────────────────────────────────────────────────
    duration = time.perf_counter() - start_time
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    exec_time_str = f"{minutes}m {seconds}s"

    known_count = len(qb_report.known) if qb_report else 0
    unknown_count = len(qb_report.unknown) if qb_report else 0
    coverage_pct = qb_report.coverage_pct if qb_report else 0

    print()
    print("==================================================")
    print("           DAILY RUN SUMMARY (Batched)            ")
    print("==================================================")
    print(f"Pipeline Batches:  {pipeline_batch}")
    print(f"Search Combos:     {total_combos}")
    print()
    print(f"Jobs Collected:    {jobs_collected}")
    print(f"Jobs Evaluated:    {total_eval_stats.evaluated}")
    print(f"  Apply:           {total_eval_stats.apply}")
    print(f"  Review:          {total_eval_stats.review}")
    print(f"  Skip:            {total_eval_stats.skip}")
    print()
    print("Applications Processed:")
    print(f"  Total Processed: {discovery_summary.processed}")
    print(f"  Easy Apply:      {discovery_summary.easy_apply}")
    print(f"  External Portal: {discovery_summary.external_portal}")
    print(f"  Already Applied: {discovery_summary.already_applied}")
    print(f"  Requires Review: {discovery_summary.requires_review}")
    print(f"  Quota Exhausted: {discovery_summary.quota_exhausted}")
    print(f"  Failed:          {discovery_summary.failed}")
    if discovery_summary.quota_stopped:
        print()
        print("  !" * 20)
        print("  QUOTA_EXHAUSTED_DETECTED")
        print("  Discovery stopped because Naukri quota appears exhausted.")
        print("  Reason: 3 consecutive quota exhaustion events detected.")
        print("  !" * 20)
    print()
    print("Question Bank:")
    print(f"  Known:           {known_count}")
    print(f"  Unknown:         {unknown_count}")
    print(f"  Coverage %:      {coverage_pct}%")
    print()
    print(f"Execution Time:    {exec_time_str}")
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
