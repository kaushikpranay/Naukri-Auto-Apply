"""
app/ats/base_handler.py
Abstract base class for ATS form handlers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.models import AtsApplicationRecord


class ATSHandler(ABC):
    """
    Base class for all ATS-specific apply handlers.

    Subclasses implement `_do_apply` which navigates to the ATS form,
    fills all fields using candidate_profile data, and submits.
    """

    ats_type: str = "unknown"

    def __init__(self, screenshots_dir: Path) -> None:
        self._screenshots_dir = screenshots_dir
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    async def apply(
        self,
        page: Page,
        job_id: int,
        apply_url: str,
        candidate_profile: dict[str, Any],
    ) -> AtsApplicationRecord:
        """Navigate, fill, submit. Returns a completed record."""
        attempted_at = datetime.now().isoformat()
        record = AtsApplicationRecord(
            job_id=job_id,
            ats_type=self.ats_type,
            apply_url=apply_url,
            status="pending",
            attempted_at=attempted_at,
        )
        try:
            logger.info("[{}] Applying job_id={} url={}", self.ats_type, job_id, apply_url)
            await self._do_apply(page, apply_url, candidate_profile, record)
            if record.status == "pending":
                record.status = "applied"
                record.applied_at = datetime.now().isoformat()
            logger.info("[{}] job_id={} → {}", self.ats_type, job_id, record.status)
        except Exception as exc:
            logger.error("[{}] job_id={} error: {}", self.ats_type, job_id, exc)
            record.status = "failed"
            record.error = str(exc)[:500]
            try:
                screenshot_path = self._screenshots_dir / f"ats_{self.ats_type}_{job_id}_error.png"
                await page.screenshot(path=str(screenshot_path))
                record.screenshot_path = str(screenshot_path)
            except Exception:
                pass
        return record

    @abstractmethod
    async def _do_apply(
        self,
        page: Page,
        apply_url: str,
        profile: dict[str, Any],
        record: AtsApplicationRecord,
    ) -> None:
        """
        Implement ATS-specific apply logic.
        Set record.status = "applied" / "failed" / "skipped" as appropriate.
        Raise on unrecoverable errors — base class will catch and set status=failed.
        """

    async def _screenshot(self, page: Page, job_id: int, label: str) -> str:
        path = self._screenshots_dir / f"ats_{self.ats_type}_{job_id}_{label}.png"
        await page.screenshot(path=str(path))
        return str(path)

    async def _fill_text(self, page: Page, selector: str, value: str, timeout: int = 5000) -> bool:
        """Fill a text/textarea input. Returns True on success."""
        try:
            el = await page.wait_for_selector(selector, timeout=timeout)
            if el:
                await el.triple_click()
                await el.fill(value)
                return True
        except Exception:
            pass
        return False

    async def _select_option(self, page: Page, selector: str, value: str, timeout: int = 5000) -> bool:
        """Select a <select> option by label or value. Returns True on success."""
        try:
            el = await page.wait_for_selector(selector, timeout=timeout)
            if el:
                try:
                    await el.select_option(label=value)
                    return True
                except Exception:
                    await el.select_option(value=value)
                    return True
        except Exception:
            pass
        return False

    async def _click(self, page: Page, selector: str, timeout: int = 8000) -> bool:
        """Click an element. Returns True on success."""
        try:
            el = await page.wait_for_selector(selector, timeout=timeout)
            if el:
                await el.click()
                return True
        except Exception:
            pass
        return False
