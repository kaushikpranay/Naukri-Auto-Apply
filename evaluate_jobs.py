import asyncio
import sys
import uuid
from pathlib import Path

from loguru import logger

from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
from app.database.evaluations_repo import EvaluationsRepository
from app.evaluator.evaluation_service import EvaluationService, EvaluationBatchStats
from app.export.eval_exporter import EvaluatedJobsExporter
from app.models.config import AppSettings, SelectorsConfig
from app.utils.config_loader import load_selectors, load_settings, resolve_path


def _validate_session_sync(settings: AppSettings, selectors: SelectorsConfig) -> None:
    """Validate Naukri session before running evaluations. Hard stop on failure."""
    async def _check() -> None:
        async with BrowserSession(settings, selectors) as session:
            await session.validate_session()

    try:
        asyncio.run(_check())
    except (ProfileNotFoundError, SessionExpiredError) as exc:
        print(f"\nError: {exc}")
        sys.exit(1)


def setup_logging(settings: AppSettings) -> None:
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
        str(log_dir / "evaluate_jobs_{time:YYYY-MM-DD}.log"),
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
    except ImportError as exc:
        logger.warning("Groq provider unavailable: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Groq provider unavailable: {}", exc)

    try:
        from app.evaluator.providers.gemini_provider import GeminiEvaluator

        providers.append(GeminiEvaluator(prompt_path, profile_path))
        logger.info("Gemini provider initialized.")
    except ImportError as exc:
        logger.warning("Gemini provider unavailable: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini provider unavailable: {}", exc)

    return providers


def main():
    print()
    print("==================================================")
    print("       NAUKRI JOB EVALUATOR â€” POC-2               ")
    print("==================================================")
    print()

    settings = load_settings()
    selectors = load_selectors()
    setup_logging(settings)

    _validate_session_sync(settings, selectors)

    run_id = str(uuid.uuid4())[:8]
    db_path = resolve_path(settings.paths.database)
    export_dir = resolve_path(settings.paths.exports)
    prompt_path = resolve_path("prompts/job_evaluation_prompt.txt")
    profile_path = resolve_path("config/candidate_profile.json")
    max_ai_evaluations = settings.evaluation.max_ai_evaluations_per_run

    if not prompt_path.exists():
        logger.error("Prompt file not found at {}", prompt_path)
        sys.exit(1)

    if not profile_path.exists():
        logger.error("Candidate profile not found at {}", profile_path)
        sys.exit(1)

    logger.info("Initializing components...")

    try:
        repo = EvaluationsRepository(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to initialize evaluation repository: {}", exc)
        sys.exit(1)

    logger.info("Database Path: {}", db_path)
    table_counts = repo.get_table_audit()
    table_names = sorted(table_counts.keys())
    logger.info("Tables: {}", ", ".join(table_names) if table_names else "None")
    logger.info("Table Counts:")
    for table_name, count in table_counts.items():
        logger.info("  {}: {}", table_name, count)
    for expected_table in ("jobs", "ai_evaluations", "applications"):
        logger.info(
            "  {}: {}",
            expected_table,
            table_counts.get(expected_table, 0),
        )
    other_tables = {
        name: count
        for name, count in table_counts.items()
        if name not in {"jobs", "ai_evaluations", "applications"}
    }
    if other_tables:
        logger.info("  other tables: {}", other_tables)
    else:
        logger.info("  other tables: None")

    startup_report = repo.get_migration_report()
    applied_migrations = (
        ", ".join(startup_report.applied_migrations)
        if startup_report.applied_migrations
        else "None"
    )
    logger.info("Current schema version: {}", startup_report.current_schema_version)
    logger.info("Applied migrations: {}", applied_migrations)
    logger.info("Pending jobs count: {}", startup_report.pending_jobs)

    print(f"Total jobs:            {startup_report.total_jobs}")
    print(f"Pending jobs:          {startup_report.pending_jobs}")
    print(f"Queued jobs:           {startup_report.queued_jobs}")
    print(f"Already evaluated jobs: {startup_report.evaluated_jobs}")
    print(f"Database Path:         {db_path}")
    print("Table Counts:")
    for table_name, count in table_counts.items():
        print(f"{table_name}: {count}")
    for expected_table in ("jobs", "ai_evaluations", "applications"):
        if expected_table not in table_counts:
            print(f"{expected_table}: 0")
    if other_tables:
        print(f"other tables: {other_tables}")
    else:
        print("other tables: None")

    providers = _build_providers(prompt_path, profile_path)
    service = EvaluationService(
        repo=repo,
        providers=providers,
        max_jobs_per_run=max_ai_evaluations,
        profile_path=profile_path,
    )

    total_stats = EvaluationBatchStats()
    try:
        batch_idx = 1
        pending_before_total = repo.get_pending_jobs_count()
        while pending_before_total > 0:
            print(f"\nBatch {batch_idx}")
            print(f"Pending before: {pending_before_total}")
            
            try:
                stats = service.run(run_id)
                if not stats or stats.evaluated == 0:
                    print("No jobs evaluated in this batch. Exiting loop.")
                    break
                
                total_stats.evaluated += stats.evaluated
                total_stats.apply += stats.apply
                total_stats.review += stats.review
                total_stats.skip += stats.skip
                total_stats.errors += stats.errors
                
                pending_after = repo.get_pending_jobs_count()
                print(f"Evaluated: {stats.evaluated}")
                print(f"Pending after: {pending_after}")
                
                pending_before_total = pending_after
                batch_idx += 1
            except KeyboardInterrupt:
                logger.info("Evaluation interrupted by user.")
                print("\nInterrupted! Stopping gracefully...")
                break
            except Exception as exc:
                logger.error("Error occurred during batch evaluation: {}", exc)
                print(f"Error occurred: {exc}")
                pending_remaining = repo.get_pending_jobs_count()
                print(f"Stopping gracefully. Remaining pending jobs: {pending_remaining}")
                break
    finally:
        repo.close()

    logger.info("Exporting evaluated jobs to Excel...")
    try:
        exporter = EvaluatedJobsExporter(db_path, export_dir)
        export_path = exporter.export()
        logger.info("Export successful: {}", export_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to export evaluated jobs: {}", exc)

    print("\n" + "=" * 40)
    print("EVALUATION SUMMARY")
    print("=" * 40)
    print(f"Jobs Evaluated: {total_stats.evaluated}")
    print(f"Apply:          {total_stats.apply}")
    print(f"Review:         {total_stats.review}")
    print(f"Skip:           {total_stats.skip}")
    print(f"Errors:         {total_stats.errors}")
    print("=" * 40)


if __name__ == "__main__":
    main()
