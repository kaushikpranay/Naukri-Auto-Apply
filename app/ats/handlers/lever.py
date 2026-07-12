"""
Lever ATS handler (lever.co).

Lever posting pages show a Postings site with an "Apply" button that
opens the application form. The form has:
  - Name, Email, Phone, Current company, LinkedIn/URLs
  - Resume upload
  - Free-text questions
  - A final "Submit application" button
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord


class LeverHandler(ATSHandler):
    ats_type = "lever"

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

        # If we land on the posting page, click the Apply button
        apply_btn = await page.query_selector("a.postings-btn[href*='/apply'], a[data-lever-source='nav']")
        if apply_btn:
            await apply_btn.click()
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(2000)

        # ── Basic fields ────────────────────────────────────────────────────
        full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        await self._fill_text(page, "input[name='name']", full_name)
        await self._fill_text(page, "input[name='email']", profile.get("email", ""))
        await self._fill_text(page, "input[name='phone']", profile.get("phone", ""))
        await self._fill_text(page, "input[name='org']", profile.get("current_company", ""))

        # URLs
        linkedin = profile.get("linkedin_profile_url", "")
        github = profile.get("github_profile_url", "")
        if linkedin:
            await self._fill_text(page, "input[name='urls[LinkedIn]']", linkedin, timeout=2000)
        if github:
            await self._fill_text(page, "input[name='urls[GitHub]']", github, timeout=2000)

        # ── Resume upload ───────────────────────────────────────────────────
        if self._resume_path and self._resume_path.exists():
            try:
                upload_input = await page.wait_for_selector(
                    "input[type='file']", timeout=5000
                )
                if upload_input:
                    await upload_input.set_input_files(str(self._resume_path))
                    logger.debug("[lever] Resume uploaded")
            except Exception as exc:
                logger.warning("[lever] Resume upload skipped: {}", exc)

        # ── Screenshot before submit ────────────────────────────────────────
        record.screenshot_path = await self._screenshot(page, record.job_id, "before_submit")

        # ── Submit ──────────────────────────────────────────────────────────
        submitted = await self._click(
            page,
            "button[type='submit'], input[type='submit']",
            timeout=8000,
        )
        if not submitted:
            raise RuntimeError("Submit button not found on Lever form")

        await page.wait_for_timeout(3000)
        record.screenshot_path = await self._screenshot(page, record.job_id, "after_submit")

        body_text = (await page.inner_text("body")).lower()
        if "thank you" in body_text or "application received" in body_text or "submitted" in body_text:
            record.status = "applied"
        else:
            record.status = "applied"
            logger.warning("[lever] job_id={} — could not confirm success via page text", record.job_id)
