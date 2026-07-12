"""
app/ats/runner.py
Orchestrates ATS form automation for external-portal jobs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsSummary
from app.ats.repository import AtsRepository
from app.utils.ats_detector import detect_ats

from app.ats.handlers.greenhouse import GreenhouseHandler
from app.ats.handlers.lever import LeverHandler
from app.ats.handlers.workday import WorkdayHandler
from app.ats.handlers.ashby import AshbyHandler
from app.ats.handlers.smartrecruiters import SmartRecruitersHandler
from app.ats.handlers.google_form import GoogleFormHandler
from app.ats.handlers.unknown import UnknownHandler


def _build_handler(ats_type: str, screenshots_dir: Path, resume_path: Path | None) -> ATSHandler:
    kwargs = {"screenshots_dir": screenshots_dir, "resume_path": resume_path}
    mapping: dict[str, type[ATSHandler]] = {
        "greenhouse": GreenhouseHandler,
        "lever": LeverHandler,
        "workday": WorkdayHandler,
        "ashby": AshbyHandler,
        "smartrecruiters": SmartRecruitersHandler,
        "google_form": GoogleFormHandler,
    }
    cls = mapping.get(ats_type)
    if cls is None:
        return UnknownHandler(screenshots_dir=screenshots_dir)
    # UnknownHandler doesn't accept resume_path
    try:
        return cls(**kwargs)
    except TypeError:
        return cls(screenshots_dir=screenshots_dir)


class AtsRunner:
    """
    Picks up all unprocessed external-portal jobs and runs the
    appropriate ATS handler for each one.
    """

    def __init__(
        self,
        db_path: Path,
        screenshots_dir: Path,
        candidate_profile: dict[str, Any],
        resume_path: Path | None = None,
        max_jobs: int = 20,
    ) -> None:
        self._repo = AtsRepository(db_path)
        self._screenshots_dir = screenshots_dir
        self._profile = candidate_profile
        self._resume_path = resume_path
        self._max_jobs = max_jobs

    async def run(self, page: Page) -> AtsSummary:
        summary = AtsSummary()
        pending = self._repo.get_pending_jobs()

        if not pending:
            logger.info("[ats_runner] No pending external-portal jobs")
            return summary

        logger.info("[ats_runner] {} external-portal jobs to process (max={})", len(pending), self._max_jobs)

        for job in pending[: self._max_jobs]:
            job_id: int = job["job_id"]
            apply_url: str = job.get("apply_url") or ""
            if not apply_url:
                logger.warning("[ats_runner] job_id={} has no apply_url — skipping", job_id)
                summary.skipped += 1
                continue

            ats_type = detect_ats(apply_url)
            handler = _build_handler(ats_type, self._screenshots_dir, self._resume_path)

            record = await handler.apply(page, job_id, apply_url, self._profile)
            self._repo.upsert(record)

            summary.processed += 1
            summary.by_ats[ats_type] = summary.by_ats.get(ats_type, 0) + 1
            if record.status == "applied":
                summary.applied += 1
            elif record.status == "skipped":
                summary.skipped += 1
            else:
                summary.failed += 1

        summary.completed_at = datetime.now()
        logger.info(
            "[ats_runner] Done — applied={} failed={} skipped={}",
            summary.applied, summary.failed, summary.skipped,
        )
        return summary

    def close(self) -> None:
        self._repo.close()
