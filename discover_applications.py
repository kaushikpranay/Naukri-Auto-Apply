"""
POC-3A / POC-3B entrypoint for apply discovery and question bank lookup.

Usage:
    python discover_applications.py
    python discover_applications.py --job-id 1 --force

POC-3B Phase 1 additions:
    - Seeds the question_bank table from the canonical answer registry.
    - After discovery, resolves every detected question against the bank.
    - Generates exports/question_bank_report.xlsx with three sheets:
        Known Questions / Unknown Questions / Suggested Answers.
    - Does NOT fill forms, submit applications, or click any submit button.
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
from app.export.application_review_exporter import ApplicationReviewExporter
from app.export.form_fill_report_exporter import FormFillReportExporter
from app.export.question_bank_report_exporter import QuestionBankReportExporter
from app.models.application_review import build_review_record
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoverySummary
from app.question_bank.lookup_service import QuestionBankLookupService
from app.question_bank.seeder import QuestionBankSeeder
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

    # ── POC-3B: seed question bank ───────────────────────────────────────────
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

    # ── POC-3B: question bank lookup + report ────────────────────────────────
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

    # ── POC-3B Phase 2: export form fill report ──────────────────────────────
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

    # ── POC-3C: application review ─────────────────────────────────────
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

    if qb_report is not None:
        print("\n" + "=" * 40)
        print("QUESTION BANK REPORT - POC-3B Phase 1")
        print("=" * 40)
        print(f"Total Questions:  {qb_report.total_questions}")
        print(f"Known:            {len(qb_report.known)}")
        print(f"Unknown:          {len(qb_report.unknown)}")
        print(f"Coverage:         {qb_report.coverage_pct}%")
        if qb_report.known:
            print("\n-- Known Questions --")
            for q in qb_report.known:
                marker = "[REQUIRED]" if q.required else ""
                print(f"  [OK] [{q.question_key}] {q.question_text[:60]} {marker}")
                print(f"    Answer: {q.stored_answer[:80]}")
        if qb_report.unknown:
            print("\n-- Unknown Questions --")
            for q in qb_report.unknown:
                marker = "[REQUIRED]" if q.required else ""
                print(f"  [?] [{q.question_key}] {q.question_text[:60]} {marker}")
                if q.suggested_answer:
                    print(f"    Suggested: {q.suggested_answer[:80]}")
                else:
                    print("    Suggested: - none -")
        print("=" * 40)

    if ff_reports:
        total_filled  = sum(len(r.filled)  for r in ff_reports)
        total_unknown = sum(len(r.unknown) for r in ff_reports)
        total_fields  = total_filled + total_unknown
        fill_pct = round(total_filled / total_fields * 100, 1) if total_fields else 0.0
        mode_label = "DRY_RUN (no DOM changes)" if FORM_FILL_DRY_RUN else "LIVE"
        print("\n" + "=" * 40)
        print(f"FORM FILL REPORT - POC-3B Phase 2 [{mode_label}]")
        print("=" * 40)
        print(f"Jobs Processed:  {len(ff_reports)}")
        print(f"Total Fields:    {total_fields}")
        print(f"Filled:          {total_filled}")
        print(f"Unknown:         {total_unknown}")
        print(f"Fill Rate:       {fill_pct}%")
        for rep in ff_reports:
            print(f"\n  Job {rep.job_id} | {rep.company} | {rep.role}")
            if rep.filled:
                print("    Filled Fields:")
                for f in rep.filled:
                    marker = "[REQUIRED]" if f.required else ""
                    status = "WOULD_FILL" if f.status == "skipped_dry_run" else f.status.upper()
                    print(f"      [{status}] {f.question_key}: {str(f.answer_used or '')[:50]} {marker}")
            if rep.unknown:
                print("    Unknown Fields:")
                for u in rep.unknown:
                    marker = "[REQUIRED]" if u.required else ""
                    print(f"      [UNKNOWN] {u.question_key}: {u.question_text[:50]} {marker}")
        print("=" * 40)

    if review_records:
        ready_jobs = [r for r in review_records if r.ready_to_submit]
        not_ready  = [r for r in review_records if not r.ready_to_submit]
        mode_label = "DRY_RUN" if FORM_FILL_DRY_RUN else "LIVE"
        print("\n" + "=" * 40)
        print(f"APPLICATION REVIEW - POC-3C [{mode_label}]")
        print("=" * 40)
        print(f"Jobs Reviewed:       {len(review_records)}")
        print(f"Ready To Submit:     {len(ready_jobs)}")
        print(f"Not Ready:           {len(not_ready)}")
        print()
        for r in review_records:
            verdict = "YES" if r.ready_to_submit else "NO"
            print(f"  [{verdict}] Job {r.job_id} | {r.company} | {r.job_title}")
            print(f"       Total Fields:     {r.total_fields}")
            print(f"       Filled:           {r.filled_count}  ({r.fill_rate_pct}%)")
            print(f"       Unknown:          {r.unknown_count}")
            print(f"       Required Missing: {r.missing_required_count}")
            if r.values_used:
                print("       Values Used:")
                for key, val in r.values_used.items():
                    print(f"         {key}: {str(val)[:50]}")
            if r.required_fields_missing:
                print("       BLOCKING (required - no answer in bank):")
                for m in r.required_fields_missing:
                    print(f"         ! {m.question_key}: {m.question_text[:50]}")
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
        print(f"  FORCE MODE - reprocessing job_id={args.job_id}")
        print()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        logger.info("Interrupted by user")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal error: {}", exc)
        print(f"\n[ERROR] Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
