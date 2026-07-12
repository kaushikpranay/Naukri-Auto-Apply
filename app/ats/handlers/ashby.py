"""
Ashby ATS handler (ashbyhq.com).

Ashby forms are React-based single-page apps. They typically show:
  - Basic info (name, email, phone, LinkedIn/website)
  - Resume upload (drag-and-drop or file input)
  - Custom questions (text, dropdown, yes/no)
  - A "Submit Application" button
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord


class AshbyHandler(ATSHandler):
    ats_type = "ashby"

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
        await self._fill_text(page, "input[name='_systemfield_name']", f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip())
        await self._fill_text(page, "input[name='_systemfield_email']", profile.get("email", ""))
        await self._fill_text(page, "input[name='_systemfield_phone']", profile.get("phone", ""), timeout=2000)

        linkedin = profile.get("linkedin_profile_url", "")
        if linkedin:
            await self._fill_text(page, "input[name='_systemfield_linkedin']", linkedin, timeout=2000)

        # ── Resume upload ───────────────────────────────────────────────────
        if self._resume_path and self._resume_path.exists():
            try:
                upload_input = await page.wait_for_selector("input[type='file']", timeout=5000)
                if upload_input:
                    await upload_input.set_input_files(str(self._resume_path))
                    await page.wait_for_timeout(2000)
                    logger.debug("[ashby] Resume uploaded")
            except Exception as exc:
                logger.warning("[ashby] Resume upload skipped: {}", exc)

        record.screenshot_path = await self._screenshot(page, record.job_id, "before_submit")

        # ── Submit ──────────────────────────────────────────────────────────
        submitted = await self._click(
            page,
            "button[type='submit'], button:has-text('Submit Application'), button:has-text('Submit')",
            timeout=8000,
        )
        if not submitted:
            raise RuntimeError("Submit button not found on Ashby form")

        await page.wait_for_timeout(3000)
        record.screenshot_path = await self._screenshot(page, record.job_id, "after_submit")
        record.status = "applied"
