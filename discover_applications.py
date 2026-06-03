"""
POC-3A entrypoint for apply discovery.

Usage:
    python discover_applications.py
    python discover_applications.py --job-id 1 --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from loguru import logger

from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
from app.discovery.repository import ApplyDiscoveryRepository
from app.discovery.service import ApplyDiscoveryService
from app.export.apply_discovery_exporter import ApplyDiscoveryExporter
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoverySummary
from app.utils.config_loader import (
    ensure_directories,
    load_selectors,
    load_settings,
    resolve_path,
)


def setup_logging(settings: AppSettings) -> None:
    """Configure logging for the discovery run."""
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
        str(log_dir / "apply_discovery_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Naukri Apply Discovery — POC-3A"
    )
    parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="Force discovery on a specific job ID",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Reprocess an existing job (clears prior discovery record). "
             "Must be used with --job-id.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    """Execute the discovery-only pipeline."""
    logger.info("Loading configuration...")
    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()

    setup_logging(settings)
    ensure_directories(settings)

    run_id = str(uuid.uuid4())[:8]
    db_path = resolve_path(settings.paths.database)
    export_dir = resolve_path(settings.paths.exports)
    summary = DiscoverySummary()

    if args.force and args.job_id is None:
        print("Error: --force requires --job-id")
        sys.exit(1)

    force_job_id: int | None = args.job_id if args.force else None

    if force_job_id is not None:
        logger.info("FORCE mode: reprocessing job_id={}", force_job_id)
    else:
        logger.info("Database Path: {}", db_path)
        logger.info(
            "Discovery limit: {} job(s) per run",
            settings.discovery.max_discovery_jobs_per_run,
        )

    repo = ApplyDiscoveryRepository(db_path)

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
            summary = await service.run(
                page, run_id=run_id, force_job_id=force_job_id
            )
    finally:
        repo.close()

    logger.info("Exporting apply discovery workbooks...")
    try:
        exporter = ApplyDiscoveryExporter(db_path, export_dir)
        export_path = exporter.export()
        logger.info("Export successful: {}", export_path)
        debug_path = exporter.export_debug()
        logger.info("Debug export successful: {}", debug_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to export apply discovery workbook: {}", exc)

    print("\n" + "=" * 40)
    print("APPLY DISCOVERY SUMMARY")
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
    print(f"Failed:           {summary.failed}")
    print("=" * 40)


def main() -> None:
    """CLI entrypoint for apply discovery."""
    print()
    print("==================================================")
    print("       NAUKRI APPLY DISCOVERY - POC-3A           ")
    print("==================================================")
    print()

    args = parse_args()

    if args.force:
        print(f"  FORCE MODE — reprocessing job_id={args.job_id}")
        print()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        logger.info("Interrupted by user")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal error: {}", exc)
        print(f"\n✗ Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
