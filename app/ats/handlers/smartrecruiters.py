"""
SmartRecruiters ATS handler (smartrecruiters.com).

SmartRecruiters application flow:
  1. Job posting page with "Apply" button
  2. Multi-step form: Personal Info → Experience → Questions → Review
  3. Each step has a "Next" button; final step has "Send Application"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord


class SmartRecruitersHandler(ATSHandler):
    ats_type = "smartrecruiters"

    def __init__(self, screenshots_dir: Path, resume_path: Path | None = None) -> None:
        super().__init__(screenshots_dir)
        self._resume_path = resume_path

    async def _do_apply(
        self,
        page: Page,
        apply_url: str,
        profile: dict[str, Any],
        record: AtsApplicationRecord,
    ) -> None:
        await page.goto(apply_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        # Click Apply button on posting page if present
        await self._click(page, "button.apply-button, a.apply-button, button:has-text('Apply')", timeout=5000)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(2000)

        # ── Step 1: Personal Info ───────────────────────────────────────────
        await self._fill_text(page, "input#firstName, input[name='firstName']", profile.get("first_name", ""))
        await self._fill_text(page, "input#lastName, input[name='lastName']", profile.get("last_name", ""))
        await self._fill_text(page, "input#email, input[name='email']", profile.get("email", ""))
        await self._fill_text(page, "input#phoneNumber, input[name='phoneNumber']", profile.get("phone", ""), timeout=2000)

        # ── Resume upload ───────────────────────────────────────────────────
        if self._resume_path and self._resume_path.exists():
            try:
                upload_input = await page.wait_for_selector("input[type='file']", timeout=5000)
                if upload_input:
                    await upload_input.set_input_files(str(self._resume_path))
                    await page.wait_for_timeout(2000)
                    logger.debug("[smartrecruiters] Resume uploaded")
            except Exception as exc:
                logger.warning("[smartrecruiters] Resume upload skipped: {}", exc)

        record.screenshot_path = await self._screenshot(page, record.job_id, "before_submit")

        # ── Navigate through steps ──────────────────────────────────────────
        for _ in range(8):
            await page.wait_for_timeout(2000)
            body_text = (await page.inner_text("body")).lower()
            if "application sent" in body_text or "thank you" in body_text:
                break
            next_clicked = await self._click(
                page,
                "button:has-text('Next'), button:has-text('Continue'), button:has-text('Send Application'), button[type='submit']",
                timeout=5000,
            )
            if not next_clicked:
                break
            await page.wait_for_load_state("networkidle")

        record.screenshot_path = await self._screenshot(page, record.job_id, "after_submit")
        record.status = "applied"
