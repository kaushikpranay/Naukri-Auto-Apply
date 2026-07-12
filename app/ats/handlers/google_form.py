"""
Google Forms handler (docs.google.com/forms, forms.gle).

Google Forms used as job application forms typically have:
  - Short answer fields (name, email, phone, LinkedIn, years experience)
  - Multiple choice / dropdown fields
  - A "Submit" button

This handler maps common profile fields to likely question labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord

# Mapping: partial question label text (lowercase) → profile key
_LABEL_TO_PROFILE: list[tuple[str, str]] = [
    ("first name", "first_name"),
    ("last name", "last_name"),
    ("full name", "_full_name"),
    ("name", "_full_name"),
    ("email", "email"),
    ("phone", "phone"),
    ("mobile", "phone"),
    ("linkedin", "linkedin_profile_url"),
    ("github", "github_profile_url"),
    ("current ctc", "current_ctc"),
    ("expected ctc", "expected_ctc"),
    ("notice period", "notice_period"),
    ("experience", "_experience_years"),
    ("location", "city"),
    ("city", "city"),
]


class GoogleFormHandler(ATSHandler):
    ats_type = "google_form"

    async def _do_apply(
        self,
        page: Page,
        apply_url: str,
        profile: dict[str, Any],
        record: AtsApplicationRecord,
    ) -> None:
        # Build synthetic keys
        full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        exp_years = str(profile.get("experience_years", ""))
        augmented = dict(profile)
        augmented["_full_name"] = full_name
        augmented["_experience_years"] = exp_years

        await page.goto(apply_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        # Find all short-answer / paragraph inputs
        inputs = await page.query_selector_all("div[role='listitem'] input[type='text'], div[role='listitem'] textarea")
        for inp in inputs:
            # Get the label text from nearest label ancestor
            label_el = await inp.evaluate_handle("""el => {
                const item = el.closest('[role=listitem]');
                if (!item) return null;
                return item.querySelector('[data-params], .M7eMe');
            }""")
            try:
                label_text = (await label_el.inner_text()).lower().strip()
            except Exception:
                label_text = ""

            value = None
            for key_fragment, profile_key in _LABEL_TO_PROFILE:
                if key_fragment in label_text:
                    value = augmented.get(profile_key, "")
                    break

            if value:
                await inp.click()
                await inp.fill(str(value))

        record.screenshot_path = await self._screenshot(page, record.job_id, "before_submit")

        submitted = await self._click(page, "div[role='button']:has-text('Submit')", timeout=8000)
        if not submitted:
            submitted = await self._click(page, "span:has-text('Submit')", timeout=3000)
        if not submitted:
            raise RuntimeError("Submit button not found on Google Form")

        await page.wait_for_timeout(3000)
        record.screenshot_path = await self._screenshot(page, record.job_id, "after_submit")

        body_text = (await page.inner_text("body")).lower()
        if "response has been recorded" in body_text or "your response" in body_text or "submitted" in body_text:
            record.status = "applied"
        else:
            record.status = "applied"
            logger.warning("[google_form] job_id={} — submission confirmation not found", record.job_id)
