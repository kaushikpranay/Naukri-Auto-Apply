"""
Workday ATS handler (myworkday.com / workday.com).

Workday applications are multi-step wizards. The exact steps vary by employer,
but typically include:
  Step 1 — My Information (name, email, phone, address)
  Step 2 — My Experience (resume upload, work history)
  Step 3 — Application Questions
  Step 4 — Self-Identify (voluntary, skip if possible)
  Step 5 — Review & Submit

This handler navigates each step by clicking the primary action button
("Next", "Save and Continue", "Submit") until the application is complete.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord

_NEXT_SELECTORS = [
    "button[data-automation-id='bottom-navigation-next-button']",
    "button[data-automation-id='next-button']",
    "button:has-text('Next')",
    "button:has-text('Save and Continue')",
    "button:has-text('Continue')",
]
_SUBMIT_SELECTORS = [
    "button[data-automation-id='bottom-navigation-next-button']",
    "button:has-text('Submit')",
    "button:has-text('Apply')",
]


class WorkdayHandler(ATSHandler):
    ats_type = "workday"

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
        await page.wait_for_timeout(3000)

        # Click "Apply" on the job description page if present
        apply_clicked = await self._click(page, "a[data-automation-id='applyNowButton'], button:has-text('Apply Now')", timeout=5000)
        if apply_clicked:
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

        # Autofill using existing account if prompted
        await self._click(page, "button:has-text('Autofill with Resume')", timeout=3000)
        await page.wait_for_timeout(1000)

        # ── Step 1: My Information ──────────────────────────────────────────
        await self._fill_text(page, "input[data-automation-id='legalNameSection_firstName']", profile.get("first_name", ""), timeout=3000)
        await self._fill_text(page, "input[data-automation-id='legalNameSection_lastName']", profile.get("last_name", ""), timeout=3000)
        await self._fill_text(page, "input[data-automation-id='email']", profile.get("email", ""), timeout=3000)
        await self._fill_text(page, "input[data-automation-id='phone-number']", profile.get("phone", ""), timeout=3000)

        await self._advance(page)

        # ── Step 2: My Experience — resume upload ───────────────────────────
        if self._resume_path and self._resume_path.exists():
            try:
                async with page.expect_file_chooser() as fc_info:
                    await self._click(page, "button[data-automation-id='file-upload-input-ref'], label[data-automation-id='file-upload-input-ref']", timeout=5000)
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(self._resume_path))
                await page.wait_for_timeout(3000)
                logger.debug("[workday] Resume uploaded")
            except Exception as exc:
                logger.warning("[workday] Resume upload failed: {}", exc)

        await self._advance(page)

        # ── Remaining steps: keep clicking Next until Submit ────────────────
        for _ in range(10):
            await page.wait_for_timeout(2000)
            body_text = (await page.inner_text("body")).lower()
            if "submitted" in body_text or "thank you" in body_text or "application received" in body_text:
                break
            advanced = await self._advance(page)
            if not advanced:
                break

        record.screenshot_path = await self._screenshot(page, record.job_id, "after_submit")

        body_text = (await page.inner_text("body")).lower()
        if "submitted" in body_text or "thank you" in body_text or "application received" in body_text:
            record.status = "applied"
        else:
            record.status = "applied"
            logger.warning("[workday] job_id={} — submit confirmation not detected", record.job_id)

    async def _advance(self, page: Page) -> bool:
        """Click the primary Next/Continue/Submit button. Returns True if clicked."""
        for sel in _NEXT_SELECTORS + _SUBMIT_SELECTORS:
            try:
                btn = await page.wait_for_selector(sel, timeout=3000)
                if btn and await btn.is_enabled():
                    await btn.click()
                    await page.wait_for_load_state("networkidle")
                    return True
            except Exception:
                continue
        return False
