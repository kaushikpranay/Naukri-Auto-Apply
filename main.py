"""
Naukri Job Collector — Main Entry Point

Orchestrates the full pipeline:
  1. Load configuration
  2. Launch browser with persistent session
  3. Validate session (login check)
  4. Collect jobs from search results
  5. Enrich with detail-page data
  6. Store in SQLite with deduplication
  7. Export to Excel
  8. Print summary
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
from app.collector.job_collector import JobCollector
from app.database.repository import JobRepository
from app.export.excel_exporter import ExcelExporter
from app.models.config import AppSettings, SearchConfig, SelectorsConfig
from app.models.job import CollectionSummary, JobData
from app.utils.config_loader import (
    ensure_directories,
    load_search_config,
    load_selectors,
    load_settings,
    resolve_path,
)


def setup_logging(settings: AppSettings) -> None:
    """Configure Loguru with file and console sinks."""
    # Remove default handler
    logger.remove()

    # Console sink — colorized, INFO level
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{message}</cyan>",
        colorize=True,
    )

    # File sink — detailed, rotating
    log_dir: Path = resolve_path(settings.paths.logs)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_dir / "naukri_collector_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format=settings.logging.format,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        encoding="utf-8",
    )


async def run() -> None:
    """Execute the full collection pipeline."""

    # ── 1. Load Configuration ──────────────────────────────────────────
    logger.info("Loading configuration...")
    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()
    search_config: SearchConfig = load_search_config()

    setup_logging(settings)
    ensure_directories(settings)

    logger.info(
        "Config loaded: {} keywords × {} locations = {} search combinations",
        len(search_config.keywords),
        len(search_config.locations),
        len(search_config.keywords) * len(search_config.locations),
    )

    # ── Initialize Summary ─────────────────────────────────────────────
    summary: CollectionSummary = CollectionSummary()

    # ── 2. Launch Browser & Validate Session ───────────────────────────
    async with BrowserSession(settings, selectors) as session:
        try:
            page = await session.validate_session()
        except SessionExpiredError as e:
            logger.error(str(e))
            print(f"\nError: {e}")
            sys.exit(1)
        except ProfileNotFoundError as e:
            logger.error(str(e))
            print(f"\nError: {e}")
            sys.exit(1)

        # ── 3. Initialize SQLite Repository ────────────────────────────
        logger.info("Initializing database...")
        db_path: Path = resolve_path(settings.paths.database)
        repo: JobRepository = JobRepository(db_path)

        # ── 4. Collect, Enrich, and Store Progressively ───────────────
        logger.info("Starting progressive job collection...")
        collector: JobCollector = JobCollector(
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
                        # Collect cards for this search
                        jobs: list[JobData] = await collector.collect_for_search(keyword, location)
                        
                        if jobs:
                            summary.jobs_found += len(jobs)
                            logger.info(f"  → Found {len(jobs)} jobs. Enriching details...")
                            
                            # Enrich with detail page data
                            jobs = await collector.enrich_with_details(jobs)
                            
                            # Store immediately
                            inserted, duplicates = repo.insert_many(jobs)
                            summary.jobs_inserted += inserted
                            summary.duplicates_skipped += duplicates
                        else:
                            logger.info("  → No jobs found.")
                            
                    except Exception as e:
                        logger.error(f"  ✗ Error collecting '{keyword.display}' in '{location.display}': {str(e)}")
        finally:
            repo.close()

        if summary.jobs_found == 0:
            logger.warning("No jobs found across all searches")
            print("\nNo jobs found. Check your search configuration.")
            return

    # ── 6. Export to Excel ─────────────────────────────────────────────
    logger.info("Exporting to Excel...")
    export_dir: Path = resolve_path(settings.paths.exports)

    try:
        exporter: ExcelExporter = ExcelExporter(db_path, export_dir)
        export_path: Path = exporter.export()
        summary.export_status = "Success"
        summary.export_path = str(export_path)
    except Exception as e:
        logger.error("Excel export failed: {}", str(e))
        summary.export_status = f"Failed: {str(e)}"

    # ── 7. Print Summary ──────────────────────────────────────────────
    summary.completed_at = datetime.now()
    summary_text: str = summary.print_summary()
    print(summary_text)
    logger.info(summary_text)


def main() -> None:
    """Entry point — runs the async pipeline."""
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║       NAUKRI JOB COLLECTOR — POC-1              ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        logger.info("Interrupted by user")
    except Exception as e:
        logger.exception("Fatal error: {}", str(e))
        print(f"\n✗ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
