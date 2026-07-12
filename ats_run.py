"""
ats_run.py
ATS auto-apply runner — processes external-portal jobs via platform-specific handlers.

Run after daily_run.py:
    python ats_run.py
    python ats_run.py --max-jobs 10
    python ats_run.py --job-id 42       # force a single job
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from loguru import logger

from app.ats.runner import AtsRunner
from app.browser.session import BrowserSession, ProfileNotFoundError, SessionExpiredError
from app.utils.config_loader import load_settings, load_selectors, resolve_path


def _setup_logging(settings) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> <level>{level:<8}</level> <cyan>{message}</cyan>",
        colorize=True,
    )
    log_dir = resolve_path(settings.paths.logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "ats_run_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="10 MB",
        retention="30 days",
    )


def _load_candidate_profile() -> dict:
    profile_path = Path("config") / "candidate_profile.json"
    if not profile_path.exists():
        logger.error("candidate_profile.json not found at {}", profile_path)
        sys.exit(1)
    with profile_path.open(encoding="utf-8") as f:
        return json.load(f)


async def main_async(args: argparse.Namespace) -> None:
    settings = load_settings()
    selectors = load_selectors()
    _setup_logging(settings)

    db_path = resolve_path(settings.paths.database)
    screenshots_dir = resolve_path(settings.paths.screenshots) / "ats"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    profile = _load_candidate_profile()

    # Resolve resume path from profile if present
    resume_path: Path | None = None
    raw_resume = profile.get("resume_path", "")
    if raw_resume:
        resume_path = Path(raw_resume)
        if not resume_path.is_absolute():
            resume_path = Path.cwd() / resume_path
        if not resume_path.exists():
            logger.warning("Resume not found at {} — uploads will be skipped", resume_path)
            resume_path = None

    runner = AtsRunner(
        db_path=db_path,
        screenshots_dir=screenshots_dir,
        candidate_profile=profile,
        resume_path=resume_path,
        max_jobs=args.max_jobs,
    )

    try:
        async with BrowserSession(settings, selectors) as session:
            try:
                page = await session.validate_session()
            except SessionExpiredError:
                logger.error("Naukri session expired. Run login_setup.py first.")
                sys.exit(1)

            summary = await runner.run(page)

    except ProfileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)
    finally:
        runner.close()

    # ── Summary ─────────────────────────────────────────────────────────────
    sep = "-" * 40
    print(sep)
    print("ATS Run Complete")
    print(sep)
    print(f"Processed : {summary.processed}")
    print(f"Applied   : {summary.applied}")
    print(f"Failed    : {summary.failed}")
    print(f"Skipped   : {summary.skipped}")
    if summary.by_ats:
        print("By ATS    :")
        for ats_type, count in sorted(summary.by_ats.items()):
            print(f"  {ats_type:<20} {count}")
    print(sep)


def main() -> None:
    parser = argparse.ArgumentParser(description="ATS Auto-Apply Runner")
    parser.add_argument("--max-jobs", type=int, default=20, help="Max jobs to process per run")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
