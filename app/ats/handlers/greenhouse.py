"""
Greenhouse ATS handler (greenhouse.io).

Greenhouse job application forms generally have:
  - First name, Last name, Email, Phone
  - Resume upload or LinkedIn URL
  - Custom questions (dropdowns, text fields, checkboxes)
  - A single "Submit Application" button

The selectors here cover the standard Greenhouse Embedded v2 layout.
Update CUSTOM_QUESTIONS_SELECTORS if specific employers add extra fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord

# Resume file path — expects PDF at this location (configured in candidate_profile)
_RESUME_KEY = "resume_path"


class GreenhouseHandler(ATSHandler):
    ats_type = "greenhouse"

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

        # ── Basic fields ────────────────────────────────────────────────────
        await self._fill_text(page, "input#first_name", profile.get("first_name", ""))
        await self._fill_text(page, "input#last_name", profile.get("last_name", ""))
        await self._fill_text(page, "input#email", profile.get("email", ""))
        await self._fill_text(page, "input#phone", profile.get("phone", ""))

        # LinkedIn / website
        linkedin = profile.get("linkedin_profile_url", "")
        if linkedin:
            await self._fill_text(page, "input#linkedin_profile", linkedin, timeout=2000)

        # ── Resume upload ───────────────────────────────────────────────────
        if self._resume_path and self._resume_path.exists():
            try:
                upload_input = await page.wait_for_selector(
                    "input[type='file'][name='resume']", timeout=5000
                )
                if upload_input:
                    await upload_input.set_input_files(str(self._resume_path))
                    logger.debug("[greenhouse] Resume uploaded: {}", self._resume_path)
            except Exception as exc:
                logger.warning("[greenhouse] Resume upload skipped: {}", exc)

        # ── Cover letter (optional, skip if not provided) ───────────────────
        cover_letter = profile.get("cover_letter", "")
        if cover_letter:
            await self._fill_text(page, "textarea#cover_letter", cover_letter, timeout=2000)

        # ── Screenshot before submit ────────────────────────────────────────
        record.screenshot_path = await self._screenshot(page, record.job_id, "before_submit")

        # ── Submit ──────────────────────────────────────────────────────────
        submitted = await self._click(
            page,
            "input[type='submit']#submit_app, button[type='submit']",
            timeout=8000,
        )
        if not submitted:
            raise RuntimeError("Submit button not found on Greenhouse form")

        await page.wait_for_timeout(3000)
        record.screenshot_path = await self._screenshot(page, record.job_id, "after_submit")

        # Verify success by checking for confirmation text
        body_text = (await page.inner_text("body")).lower()
        if "application submitted" in body_text or "thank you" in body_text or "successfully" in body_text:
            record.status = "applied"
        else:
            record.status = "applied"  # Assume success unless we detect an error
            logger.warning("[greenhouse] job_id={} — could not confirm success via page text", record.job_id)
