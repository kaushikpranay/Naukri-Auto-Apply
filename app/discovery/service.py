"""
#app/discovery/service.py
Apply discovery orchestration.

Purely read-only. No forms filled, no resumes uploaded,
no submit buttons clicked. Only screenshots, HTML capture,
candidate element logging, URL tracking, and apply-flow
classification.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from playwright.async_api import Page

from app.discovery.question_normalizer import normalize_question_key
from app.discovery.repository import ApplyDiscoveryRepository
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import (
    ApplicationDiscoveryRecord,
    DiscoverySummary,
    DiscoveredQuestion,
    QuotaExhaustedStop,
)
from app.models.form_fill import FormFillReport
from app.models.job import JobData
from app.question_bank.form_filler import FormFiller
from app.utils.config_loader import resolve_path, PROJECT_ROOT


@dataclass
class _DiscoveryOutcome:
    record: ApplicationDiscoveryRecord
    questions: list[DiscoveredQuestion]
    form_fill_report: FormFillReport | None = None


class ApplyDiscoveryService:
    """Open jobs, detect apply flows, and capture evidence. No auto-apply."""

    _APPLY_CANDIDATE_SELECTORS: list[str] = [
        "button:has-text('Apply')",
        "a:has-text('Apply')",
        "button[class*='apply']",
        "a[class*='apply']",
        "button[aria-label*='apply']",
        "a[aria-label*='apply']",
        "input[value*='Apply']",
        "button:has-text('Easy Apply')",
        "a:has-text('Easy Apply')",
        "button:has-text('Register')",
        "a:has-text('Register')",
        "a[href*='apply']",
        "button:has-text('Login')",
        "a:has-text('Login')",
    ]

    # ── Quota exhaustion detection ────────────────────────────────────────────
    _QUOTA_PHRASES: list[str] = [
        "quota exhausted",
        "job quota exhausted",
        "application limit reached",
        "maximum applications reached",
        "there was an error while processing your request",
        "you have reached the maximum",
        "apply limit",
        "daily apply limit",
        "reached your limit",
        "applications limit",
    ]
    _QUOTA_CONSECUTIVE_THRESHOLD: int = 3

    def __init__(
        self,
        repo: ApplyDiscoveryRepository,
        settings: AppSettings,
        selectors: SelectorsConfig,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._selectors = selectors
        self._screenshots_dir: Path = resolve_path(settings.paths.screenshots)
        self._artifacts_dir: Path = resolve_path(settings.paths.artifacts)
        self._form_filler = FormFiller(settings, selectors, self._screenshots_dir, self._repo)
        self._quota_consecutive: int = 0  # reset on every service.run() call from the loop

    async def run(
        self,
        page: Page,
        run_id: str | None = None,
        force_job_id: int | None = None,
        only_retryable: bool = False,
    ) -> DiscoverySummary:
        """Process shortlisted jobs, or a single forced job.

        Args:
            page: Active browser page.
            run_id: Run identifier (reserved).
            force_job_id: If set, ignore normal filtering and reprocess
                          exactly this job, clearing any prior record.
            only_retryable: If True, only process retryable jobs.

        Raises:
            QuotaExhaustedStop: If 3 consecutive quota_exhausted events are
                                detected, the loop should stop immediately.
        """
        self._quota_consecutive = 0
        summary = DiscoverySummary()

        if force_job_id is not None:
            job = self._repo.get_job_by_id(force_job_id)
            if job is None:
                logger.error("Job {} not found in database", force_job_id)
                return summary
            jobs = [job]
        elif only_retryable:
            jobs = self._repo.get_retryable_jobs(
                limit=self._settings.discovery.max_discovery_jobs_per_run
            )
        else:
            jobs = self._repo.get_jobs_for_discovery(
                limit=self._settings.discovery.max_discovery_jobs_per_run
            )

        if not jobs:
            logger.info("No shortlisted APPLY jobs found for discovery.")
            return summary

        logger.info("Found {} shortlisted APPLY job(s) for discovery.", len(jobs))

        for index, job in enumerate(jobs, start=1):
            if page.is_closed():
                logger.error("Browser page is closed. Aborting discovery batch to prevent cascading failures.")
                break
            logger.info("Job Opened: [{} / {}] {} - {}", index, len(jobs), job.company_name, job.job_title)
            started_at = datetime.now()
            # Clear existing application to avoid duplicate entries and allow fresh discovery
            if job.status == "applied_successfully":
                logger.warning("Skipping job_id={} — jobs.status is applied_successfully", job.id)
                continue
            existing = self._repo.get_application(job.id)
            if existing and existing.apply_type == "applied_successfully":
                logger.warning("Skipping job_id={} — job_applications.apply_type is applied_successfully", job.id)
                continue
            self._repo.clear_application(job.id)
            try:
                outcome = await self._discover_job(page, job)
                self._repo.save_application(outcome.record)
                for question in outcome.questions:
                    self._repo.save_question(job.id or 0, question)
                self._log_outcome(outcome, job, summary)
                # Accumulate Phase 2 form fill reports
                if outcome.form_fill_report is not None:
                    summary.form_fill_reports.append(outcome.form_fill_report)
                logger.info(
                    "Discovery Finished: job_id={} apply_type={} duration={:.2f}s",
                    job.id,
                    outcome.record.apply_type or outcome.record.status,
                    (datetime.now() - started_at).total_seconds(),
                )

                # Determine retryable status or successful status update
                has_unknown = False
                has_error = False
                if outcome.form_fill_report is not None:
                    if len(outcome.form_fill_report.unknown) > 0:
                        has_unknown = True
                    if any(f.status == "error" for f in outcome.form_fill_report.filled):
                        has_error = True
                else:
                    if any(q.field_type == "unknown" for q in outcome.questions):
                        has_unknown = True

                if outcome.record.apply_type == "quota_exhausted":
                    self._repo.update_job_status(job.id, "quota_exhausted")
                elif has_unknown:
                    self._repo.update_job_status(job.id, "unknown_question")
                elif has_error:
                    self._repo.increment_retry_count(job.id)
                    self._repo.update_job_status(job.id, "temporary_failure")
                else:
                    # Successful or non-retryable apply type
                    status_val = outcome.record.apply_type or "pending"
                    self._repo.update_job_status(job.id, status_val)
                    if status_val == "login_required":
                        from app.browser.session import SessionExpiredError
                        logger.error("Session expired during discovery. Aborting run.")
                        raise SessionExpiredError("Login required detected during apply.")

                # ── Consecutive quota protection ──────────────────────────
                if outcome.record.apply_type == "quota_exhausted":
                    self._quota_consecutive += 1
                    logger.warning(
                        "QUOTA_EXHAUSTED_DETECTED: job_id={} consecutive={} message='{}'",
                        job.id, self._quota_consecutive,
                        outcome.record.quota_message or "",
                    )
                    if self._quota_consecutive >= self._QUOTA_CONSECUTIVE_THRESHOLD:
                        summary.quota_stopped = True
                        summary.processed += 1
                        raise QuotaExhaustedStop(
                            f"{self._quota_consecutive} consecutive quota exhaustion events detected.",
                            summary=summary,
                        )
                else:
                    # Any successful classification resets the counter
                    if outcome.record.apply_type not in ("discovery_failed", None):
                        self._quota_consecutive = 0

            except QuotaExhaustedStop:
                raise  # propagate to caller without wrapping
            except Exception as exc:  # noqa: BLE001
                logger.exception("Discovery Failed: job_id={} error={}", job.id, exc)
                # Capture diagnostic screenshot before recording failure
                try:
                    from app.utils.screenshot import capture_screenshot
                    await capture_screenshot(
                        page,
                        reason=f"discovery_failed_job_{job.id}",
                        screenshots_dir=str(self._screenshots_dir),
                    )
                except Exception as screenshot_exc:
                    logger.warning("Failed to capture error screenshot: {}", screenshot_exc)
                self._repo.save_application(
                    ApplicationDiscoveryRecord(
                        job_id=int(job.id or 0),
                        status="discovery_failed",
                        detected_at=datetime.now().isoformat(),
                    )
                )
                import playwright.async_api
                if isinstance(exc, (playwright.async_api.Error, asyncio.TimeoutError)):
                    self._repo.update_job_status(job.id, "browser_error")
                else:
                    self._repo.update_job_status(job.id, "temporary_failure")
                self._repo.increment_retry_count(job.id)
                summary.failed += 1
                if page.is_closed():
                    logger.error("Browser page was closed/crashed during processing. Aborting discovery batch.")
                    break

            summary.processed += 1

            # Close any extra tabs opened during apply (prevents tab accumulation)
            try:
                for extra in list(page.context.pages):
                    if extra is not page and not extra.is_closed():
                        await extra.close()
            except Exception:
                pass

        summary.completed_at = datetime.now()
        logger.info("Discovery batch complete — closing browser after all jobs processed.")
        return summary

    def _log_outcome(
        self,
        outcome: _DiscoveryOutcome,
        job: JobData,
        summary: DiscoverySummary,
    ) -> None:
        apply_type = outcome.record.apply_type or "unknown"

        if apply_type == "already_applied":
            summary.already_applied += 1
        elif apply_type == "easy_apply":
            summary.discovered += 1
            summary.easy_apply += 1
        elif apply_type == "external_portal":
            summary.discovered += 1
            summary.external_portal += 1
        elif apply_type == "email":
            summary.discovered += 1
            summary.email += 1
        elif apply_type == "register":
            summary.discovered += 1
            summary.needs_register += 1
        elif apply_type == "login_required":
            summary.discovered += 1
            summary.login_required += 1
        elif apply_type == "quota_exhausted":
            summary.quota_exhausted += 1
        elif apply_type == "unknown":
            summary.discovered += 1
            summary.unknown_flow += 1
        else:
            summary.requires_review += 1

        logger.info("Apply Type Detected: job_id={} type={}", job.id, apply_type)
        if outcome.record.quota_message:
            logger.warning("Quota Message: job_id={} msg='{}'", job.id, outcome.record.quota_message)
        if outcome.record.button_text:
            logger.info("Button Text: job_id={} text={}", job.id, outcome.record.button_text)
        if outcome.record.email:
            logger.info("Email Found: job_id={} email={}", job.id, outcome.record.email)
        if outcome.record.apply_url:
            logger.info("Apply URL: job_id={} url={}", job.id, outcome.record.apply_url)
        if outcome.record.url_before and outcome.record.url_after:
            logger.info("URL Chain: {} -> {} (redirects={})",
                        outcome.record.url_before, outcome.record.url_after, outcome.record.redirect_count)
        logger.info(
            "Discovery Complete: job_id={} questions_found={}",
            job.id,
            len(outcome.questions),
        )

    async def _discover_job(self, page: Page, job: JobData) -> _DiscoveryOutcome:
        """Inspect a single job page and classify the application flow."""
        job_id = int(job.id or 0)
        logger.info("Job URL: {}", job.job_url)

        await page.goto(job.job_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(self._settings.naukri.page_load_wait)

        hr_name = await self._extract_text(page, self._selectors.job_detail.recruiter_name)
        recruiter_email = await self._extract_email(page)
        if not hr_name:
            hr_name = job.recruiter_name or None
        if not recruiter_email:
            recruiter_email = job.recruiter_email or None

        url_before = page.url
        screenshot_before = await self._capture_screenshot(page, f"job_{job_id}_before")
        html_before_path = await self._save_html(page, job_id, suffix="before")

        elements_path = await self._log_all_apply_elements(page, job_id)

        trigger_locator = await self._find_apply_trigger(page)

        if trigger_locator is None:
            if await self._is_already_applied_visible(page):
                return _DiscoveryOutcome(
                    record=ApplicationDiscoveryRecord(
                        job_id=job_id,
                        apply_type="already_applied",
                        apply_url=job.apply_url or None,
                        email=recruiter_email,
                        hr_name=hr_name,
                        url_before=url_before,
                        status="discovered",
                        screenshot_before=screenshot_before,
                        html_before_path=html_before_path,
                        elements_path=elements_path,
                        detected_at=datetime.now().isoformat(),
                    ),
                    questions=[],
                )
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type="unknown",
                    apply_url=job.apply_url or None,
                    email=recruiter_email,
                    hr_name=hr_name,
                    url_before=url_before,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    html_before_path=html_before_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                ),
                questions=[],
            )

        button_text = (await trigger_locator.inner_text()).strip()[:100]
        button_selector_str = await self._get_selector_string(trigger_locator)
        button_href = await trigger_locator.get_attribute("href")

        logger.info("Apply Button Text: {}", button_text)
        logger.info("Selector: {}", button_selector_str)
        if button_href:
            logger.info("URL: {}", button_href)

        if button_href:
            lower_href = button_href.lower()
            if lower_href.startswith("mailto:"):
                email = button_href.replace("mailto:", "").strip()
                logger.info("Email Found: job_id={} email={}", job_id, email)
                return _DiscoveryOutcome(
                    record=ApplicationDiscoveryRecord(
                        job_id=job_id,
                        apply_type="email",
                        apply_url=button_href,
                        email=email,
                        hr_name=hr_name,
                        button_text=button_text,
                        button_selector=button_selector_str,
                        url_before=url_before,
                        status="discovered",
                        screenshot_before=screenshot_before,
                        html_before_path=html_before_path,
                        elements_path=elements_path,
                        detected_at=datetime.now().isoformat(),
                    ),
                    questions=[],
                )

            if self._is_external_link(button_href, job.job_url):
                logger.info("External Link Found: job_id={} url={}", job_id, button_href)
                return _DiscoveryOutcome(
                    record=ApplicationDiscoveryRecord(
                        job_id=job_id,
                        apply_type="external_portal",
                        apply_url=button_href,
                        email=recruiter_email,
                        hr_name=hr_name,
                        button_text=button_text,
                        button_selector=button_selector_str,
                        url_before=url_before,
                        status="discovered",
                        screenshot_before=screenshot_before,
                        html_before_path=html_before_path,
                        elements_path=elements_path,
                        detected_at=datetime.now().isoformat(),
                    ),
                    questions=[],
                )

            detected_type = self._detect_apply_type_by_url(button_href)
            if detected_type:
                logger.info("Apply type detected by URL (pre-click): {}", detected_type)
                return _DiscoveryOutcome(
                    record=ApplicationDiscoveryRecord(
                        job_id=job_id,
                        apply_type=detected_type,
                        apply_url=button_href,
                        email=recruiter_email,
                        hr_name=hr_name,
                        button_text=button_text,
                        button_selector=button_selector_str,
                        url_before=url_before,
                        status="discovered",
                        screenshot_before=screenshot_before,
                        html_before_path=html_before_path,
                        elements_path=elements_path,
                        detected_at=datetime.now().isoformat(),
                    ),
                    questions=[],
                )

        detect_apply_type = await self._detect_button_apply_type(page, button_text)
        if detect_apply_type in ("register", "login_required"):
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type=detect_apply_type,
                    apply_url=job.apply_url or None,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    html_before_path=html_before_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                ),
                questions=[],
            )

        # ── Click Apply — with redirect chain tracking ──────────────
        before_pages = len(page.context.pages)
        before_url = page.url
        redirect_chain: list[str] = [before_url]

        await trigger_locator.click()
        await page.wait_for_timeout(self._settings.naukri.detail_load_wait)

        active_page = await self._active_page(page, before_pages)
        current_url = active_page.url
        if current_url not in redirect_chain:
            redirect_chain.append(current_url)

        # Poll for delayed redirects (up to 5s)
        for _ in range(10):
            await asyncio.sleep(0.5)
            latest = active_page.url
            if latest != redirect_chain[-1]:
                redirect_chain.append(latest)
            if len(page.context.pages) > before_pages:
                active_page = await self._active_page(page, before_pages)
                if active_page.url != redirect_chain[-1]:
                    redirect_chain.append(active_page.url)

        url_after = redirect_chain[-1]
        redirect_count = len(redirect_chain) - 1

        logger.info("Redirect Chain: {} -> {} ({} hops)",
                     redirect_chain[0], url_after, redirect_count)
        logger.info("URL After Click: {}", url_after)
        logger.info("Redirect Count: {}", redirect_count)

        screenshot_after = await self._capture_screenshot(active_page, f"job_{job_id}_after")
        html_path = await self._save_html(active_page, job_id, suffix="after")

        redirect_json = json.dumps(redirect_chain)

        # Compute debug values
        page_title = await active_page.title()
        modal_detected = await self._is_easy_apply_modal_visible(active_page)

        forms_count = 0
        inputs_count = 0
        radio_count = 0
        dropdown_count = 0
        buttons_count = 0
        try:
            counts = await active_page.evaluate("""
                () => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetWidth > 0 && el.offsetHeight > 0;
                    };
                    
                    const forms = Array.from(document.querySelectorAll('form')).filter(isVisible).length;
                    const inputs = Array.from(document.querySelectorAll('input:not([type="radio"]):not([type="checkbox"]), textarea')).filter(isVisible).length;
                    const radios = Array.from(document.querySelectorAll('input[type="radio"], [role="radio"]')).filter(isVisible).length;
                    const dropdowns = Array.from(document.querySelectorAll('select, [role="listbox"]')).filter(isVisible).length;
                    const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"]')).filter(isVisible).length;
                    
                    return { forms, inputs, radios, dropdowns, buttons };
                }
            """)
            forms_count = counts.get("forms", 0)
            inputs_count = counts.get("inputs", 0)
            radio_count = counts.get("radios", 0)
            dropdown_count = counts.get("dropdowns", 0)
            buttons_count = counts.get("buttons", 0)
        except Exception as e:
            logger.error("Failed to calculate debug element counts: {}", e)

        # ── Post-Click Classification ─────────────────────────────────
        # 0. CHECK FOR QUOTA EXHAUSTION (before any other classifier)
        quota_msg = await self._detect_quota_exhaustion(active_page)
        if quota_msg:
            logger.warning(
                "QUOTA_EXHAUSTED: job_id={} message='{}'",
                job_id, quota_msg,
            )
            screenshot_after = await self._capture_screenshot(active_page, f"job_{job_id}_quota")
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type="quota_exhausted",
                    quota_message=quota_msg,
                    apply_url=url_after,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    url_after=url_after,
                    redirect_count=redirect_count,
                    redirect_chain=redirect_json,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    html_before_path=html_before_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                    page_title=page_title,
                    modal_detected=modal_detected,
                    forms_count=forms_count,
                    inputs_count=inputs_count,
                    radio_count=radio_count,
                    dropdown_count=dropdown_count,
                    buttons_count=buttons_count,
                ),
                questions=[],
            )

        # 1. IF external company URL detected: external_portal
        if self._is_external_link(url_after, job.job_url) or await self._selector_exists(
            active_page, self._selectors.discovery.apply_flow.external_portal_marker
        ):
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type="external_portal",
                    apply_url=url_after,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    url_after=url_after,
                    redirect_count=redirect_count,
                    redirect_chain=redirect_json,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    html_before_path=html_before_path,
                    html_path=html_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                    page_title=page_title,
                    modal_detected=modal_detected,
                    forms_count=forms_count,
                    inputs_count=inputs_count,
                    radio_count=radio_count,
                    dropdown_count=dropdown_count,
                    buttons_count=buttons_count,
                ),
                questions=[],
            )

        # 2. ELIF saveApply confirmation page detected: applied_successfully
        try:
            page_text = await active_page.locator("main, .job-apply-content, #app, body").first.inner_text()
        except Exception as _pt_exc:
            logger.warning("Failed to read page text for apply classification job_id={}: {}", job_id, _pt_exc)
            page_text = ""
        if (
            "/myapply/saveApply" in url_after
            or "Apply Confirmation" in page_title
            or "successfully applied" in page_text.lower()
            or "application submitted" in page_text.lower()
        ):
            logger.info("Applied successfully: job_id={} title={}", job_id, job.job_title)
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type="applied_successfully",
                    apply_url=url_after,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    url_after=url_after,
                    redirect_count=redirect_count,
                    redirect_chain=redirect_json,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    html_before_path=html_before_path,
                    html_path=html_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                    page_title=page_title,
                    modal_detected=modal_detected,
                    forms_count=forms_count,
                    inputs_count=inputs_count,
                    radio_count=radio_count,
                    dropdown_count=dropdown_count,
                    buttons_count=buttons_count,
                ),
                questions=[],
            )

        # 3. ELIF question modal/form detected: easy_apply
        if modal_detected:
            screenshot_modal = await self._capture_screenshot(active_page, f"job_{job_id}_apply_modal")
            questions = await self._discover_questions_readonly(active_page, job)

            # ── POC-3B Phase 2: fill known answers ──────────────────────────
            form_fill_report = await self._form_filler.fill_form(
                page=active_page,
                job_id=job_id,
                company=job.company_name or "",
                role=job.job_title or "",
                questions=questions,
            )

            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type="easy_apply",
                    apply_url=url_after,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    url_after=url_after,
                    redirect_count=redirect_count,
                    redirect_chain=redirect_json,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    screenshot_modal=screenshot_modal,
                    html_before_path=html_before_path,
                    html_path=html_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                    page_title=page_title,
                    modal_detected=modal_detected,
                    forms_count=forms_count,
                    inputs_count=inputs_count,
                    radio_count=radio_count,
                    dropdown_count=dropdown_count,
                    buttons_count=buttons_count,
                ),
                questions=questions,
                form_fill_report=form_fill_report,
            )

        # 4. ELIF job-specific applied indicator detected: already_applied
        if await self._is_already_applied_visible(active_page):
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type="already_applied",
                    apply_url=url_after,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    url_after=url_after,
                    redirect_count=redirect_count,
                    redirect_chain=redirect_json,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    html_before_path=html_before_path,
                    html_path=html_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                    page_title=page_title,
                    modal_detected=modal_detected,
                    forms_count=forms_count,
                    inputs_count=inputs_count,
                    radio_count=radio_count,
                    dropdown_count=dropdown_count,
                    buttons_count=buttons_count,
                ),
                questions=[],
            )

        # 5. ELSE: unknown (or check registration/login)
        post_click_type = await self._detect_post_click_apply_type(active_page, button_text)
        if post_click_type in ("register", "login_required"):
            return _DiscoveryOutcome(
                record=ApplicationDiscoveryRecord(
                    job_id=job_id,
                    apply_type=post_click_type,
                    apply_url=url_after,
                    email=recruiter_email,
                    hr_name=hr_name,
                    button_text=button_text,
                    button_selector=button_selector_str,
                    url_before=url_before,
                    url_after=url_after,
                    redirect_count=redirect_count,
                    redirect_chain=redirect_json,
                    status="discovered",
                    screenshot_before=screenshot_before,
                    screenshot_after=screenshot_after,
                    html_before_path=html_before_path,
                    html_path=html_path,
                    elements_path=elements_path,
                    detected_at=datetime.now().isoformat(),
                    page_title=page_title,
                    modal_detected=modal_detected,
                    forms_count=forms_count,
                    inputs_count=inputs_count,
                    radio_count=radio_count,
                    dropdown_count=dropdown_count,
                    buttons_count=buttons_count,
                ),
                questions=[],
            )

        return _DiscoveryOutcome(
            record=ApplicationDiscoveryRecord(
                job_id=job_id,
                apply_type="unknown",
                apply_url=url_after,
                email=recruiter_email,
                hr_name=hr_name,
                button_text=button_text,
                button_selector=button_selector_str,
                url_before=url_before,
                url_after=url_after,
                redirect_count=redirect_count,
                status="discovered",
                screenshot_before=screenshot_before,
                screenshot_after=screenshot_after,
                html_before_path=html_before_path,
                html_path=html_path,
                elements_path=elements_path,
                detected_at=datetime.now().isoformat(),
                page_title=page_title,
                modal_detected=modal_detected,
                forms_count=forms_count,
                inputs_count=inputs_count,
                radio_count=radio_count,
                dropdown_count=dropdown_count,
                buttons_count=buttons_count,
            ),
            questions=[],
        )

    def _detect_apply_type_by_url(self, url: str) -> str | None:
        """Detect apply type by URL patterns (registration, login, etc.)."""
        if not url:
            return None
        path = urlparse(url).path.lower()
        if "/registration/" in path or "/createaccount" in path:
            return "register"
        if "/login/" in path or "/signin" in path or "/sign-in" in path:
            return "login_required"
        return None

    async def _log_all_apply_elements(self, page: Page, job_id: int) -> str | None:
        """Find every potential apply element on the page and export to JSON."""
        elements: list[dict] = []
        for selector in self._APPLY_CANDIDATE_SELECTORS:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                for idx in range(count):
                    el = locator.nth(idx)
                    try:
                        visible = await el.is_visible()
                        text = (await el.inner_text()).strip()[:100]
                        href = await el.get_attribute("href")
                        css_selector = await self._get_selector_string(el)
                        tag = await el.evaluate("(el) => el.tagName.toLowerCase()")
                        elements.append({
                            "candidate_index": idx,
                            "selector": selector,
                            "tag": tag,
                            "text": text,
                            "href": href or "",
                            "visible": visible,
                            "computed_selector": css_selector,
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        logger.info("Found {} candidate apply element(s)", len(elements))
        for el in elements:
            logger.info("  Candidate: text='{}' href='{}' visible={} selector='{}'",
                        el["text"], el["href"], el["visible"], el["computed_selector"])

        if not elements:
            return None

        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job_id}_elements.json"
        filepath = self._artifacts_dir / filename
        try:
            filepath.write_text(
                json.dumps({"job_id": job_id, "elements": elements}, indent=2),
                encoding="utf-8",
            )
            logger.info("Elements exported: {} ({} candidates)", filename, len(elements))
            try:
                return str(filepath.relative_to(PROJECT_ROOT))
            except ValueError:
                return str(filepath)
        except Exception as e:
            logger.error("Failed to export elements for job {}: {}", job_id, e)
            return None

    async def _detect_button_apply_type(self, page: Page, button_text: str) -> str | None:
        """Check button text for non-standard apply types before clicking."""
        lower = button_text.lower()
        if "register" in lower:
            return "register"
        if "login" in lower:
            return "login_required"
        if "sign in" in lower or "signin" in lower:
            return "login_required"
        return None

    async def _detect_post_click_apply_type(self, page: Page, button_text: str) -> str | None:
        """Classify the page after clicking the apply button."""
        lower = button_text.lower()
        if "register" in lower:
            return "register"
        if "login" in lower or "sign" in lower:
            return "login_required"

        try:
            page_text = await page.locator("main, #app, .job-desc, body").first.inner_text()
        except Exception as _e:
            logger.warning("Failed to get page text for post-click classify: {}", _e)
            try:
                page_text = await page.locator("body").inner_text()
            except Exception as _e2:
                logger.warning("Failed to get body text for post-click classify: {}", _e2)
                page_text = ""
        
        lower_page = page_text.lower()
        if "register to apply" in lower_page or "register and apply" in lower_page:
            return "register"
        if "login to apply" in lower_page or "sign in to apply" in lower_page:
            return "login_required"

        return None

    async def _detect_quota_exhaustion(self, page: Page) -> str | None:
        """Scan the page for quota/limit exhaustion messages after Apply click.

        Checks (in order):
          1. Toast notifications
          2. Error banners / alert boxes
          3. Modal dialog text
          4. Visible inline page text near the apply area
          5. Any visible text in the body (last resort)

        Returns the matched message text (first match) or None.
        """
        # Selectors that typically surface quota/error messages on Naukri
        error_selectors = [
            # Toast / snackbar containers (common patterns)
            "[class*='toast']",
            "[class*='snack']",
            "[class*='notification']",
            "[class*='alert']",
            "[role='alert']",
            "[role='status']",
            # Error / warning banners
            "[class*='error']",
            "[class*='warning']",
            "[class*='banner']",
            # Modal / dialog
            "[role='dialog']",
            "[class*='modal']",
            "[class*='popup']",
            "[class*='overlay']",
            # Naukri-specific inline feedback containers
            "[class*='applyBtn']",
            "[class*='apply-btn']",
            "[class*='job-apply']",
            "[class*='applyContainer']",
            ".quota-msg",
            ".limit-msg",
        ]

        checked_texts: list[str] = []

        for selector in error_selectors:
            try:
                locators = page.locator(selector)
                count = await locators.count()
                for i in range(count):
                    loc = locators.nth(i)
                    if not await loc.is_visible():
                        continue
                    try:
                        text = (await loc.inner_text()).strip()
                    except Exception:
                        continue
                    if not text or text in checked_texts:
                        continue
                    checked_texts.append(text)
                    lower_text = text.lower()
                    for phrase in self._QUOTA_PHRASES:
                        if phrase in lower_text:
                            logger.warning(
                                "Quota phrase '{}' found via selector '{}' | text: '{}'",
                                phrase, selector, text[:120],
                            )
                            return text[:200]
            except Exception:
                continue

        # Last resort: scan entire body (cheaper than iterating all elements)
        try:
            body_text = await page.locator("body").inner_text()
            lower_body = body_text.lower()
            for phrase in self._QUOTA_PHRASES:
                if phrase in lower_body:
                    # Extract surrounding context (up to 200 chars)
                    idx = lower_body.index(phrase)
                    start = max(0, idx - 40)
                    end = min(len(body_text), idx + len(phrase) + 80)
                    snippet = body_text[start:end].strip()
                    logger.warning(
                        "Quota phrase '{}' found in page body | snippet: '{}'",
                        phrase, snippet,
                    )
                    return snippet[:200]
        except Exception as _body_exc:
            logger.warning("Quota body scan failed (page likely closed): {}", _body_exc)

        return None

    async def _discover_questions_readonly(self, page: Page, job: JobData) -> list[DiscoveredQuestion]:
        """Detect questions on the page — read-only, no interaction."""
        questions: list[DiscoveredQuestion] = []
        from app.question_bank.form_filler import FormFiller, is_valid_recruiter_question_container

        helper = self._form_filler
        active_chatbot_question = await helper._resolve_active_chatbot_question(page, {})
        if active_chatbot_question:
            if active_chatbot_question.get("field_type") == "unknown":
                await helper._final_drawer_refresh_scan(page, active_chatbot_question)
            question_text = active_chatbot_question["question_text"]
            question_key = active_chatbot_question["question_key"]
            field_type = active_chatbot_question["field_type"]
            required = await self._detect_required(
                active_chatbot_question["question_container"],
                question_text,
            )
            logger.info("Question Detected: {} [{}]", question_text, question_key)

            stored_answer = self._repo.get_question_answer(question_key)
            question = DiscoveredQuestion(
                question_key=question_key,
                question_text=question_text,
                field_type=field_type,
                required=required,
                answer=stored_answer or None,
            )
            questions.append(question)
            return questions

        containers = await page.locator(self._selectors.discovery.questions.container).count()
        if containers == 0:
            return questions

        for index in range(containers):
            container = page.locator(self._selectors.discovery.questions.container).nth(index)
            if not await container.is_visible():
                continue

            if not await is_valid_recruiter_question_container(container):
                continue

            question_text = await self._extract_text_from_locator(
                container, self._selectors.discovery.questions.text
            )
            if not question_text:
                continue

            field_type = await self._detect_field_type(container, page)
            options = await helper._get_field_options(container, field_type, page)
            if not await helper._is_valid_question_text(
                question_text,
                has_visible_answer_controls=bool(options) or field_type != "unknown",
                option_texts=options,
            ):
                logger.info("Skipping informational message during discovery: {}", question_text[:100])
                continue

            question_key = normalize_question_key(question_text, options)
            required = await self._detect_required(container, question_text)
            logger.info("Question Detected: {} [{}]", question_text, question_key)

            stored_answer = self._repo.get_question_answer(question_key)
            question = DiscoveredQuestion(
                question_key=question_key,
                question_text=question_text,
                field_type=field_type,
                required=required,
                answer=stored_answer or None,
            )
            questions.append(question)

        return questions

    async def _capture_screenshot(self, page: Page, name: str) -> str | None:
        """Save a timestamped screenshot, return relative path."""
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{name}.png"
        filepath = self._screenshots_dir / filename
        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info("Screenshot saved: {} (purpose: {})", filename, name)
            try:
                return str(filepath.relative_to(PROJECT_ROOT))
            except ValueError:
                return str(filepath)
        except Exception as e:
            logger.error("Failed to capture screenshot {}: {}", name, e)
            return None

    async def _save_html(self, page: Page, job_id: int, suffix: str = "") -> str | None:
        """Save raw page HTML for debugging, return relative path."""
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{suffix}" if suffix else ""
        filename = f"job_{job_id}{suffix}.html"
        filepath = self._artifacts_dir / filename
        try:
            html = await page.content()
            filepath.write_text(html, encoding="utf-8")
            logger.info("HTML saved: {} ({:.1f} KB)", filename, len(html) / 1024)
            try:
                return str(filepath.relative_to(PROJECT_ROOT))
            except ValueError:
                return str(filepath)
        except Exception as e:
            logger.error("Failed to save HTML for job {}: {}", job_id, e)
            return None

    async def _capture_html_before(self, page: Page, job_id: int) -> str | None:
        """Fallback to capture HTML before click if not already saved."""
        return await self._save_html(page, job_id, suffix="before")

    async def _get_selector_string(self, locator) -> str:
        """Get a best-effort CSS selector string from a locator."""
        try:
            return await locator.evaluate("""
                (el) => {
                    if (el.id) return '#' + el.id;
                    if (el.className && typeof el.className === 'string')
                        return el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).join('.');
                    return el.tagName.toLowerCase();
                }
            """)
        except Exception:
            return "unknown"

    async def _detect_field_type(self, container, page: Page | None = None) -> str:
        """Infer the field type for a question container, delegating to the form filler logic."""
        if hasattr(self, "_form_filler") and self._form_filler:
            return await self._form_filler._detect_field_type(container, page)

        field_locator = container.locator(self._selectors.discovery.questions.field)
        if await field_locator.count() == 0:
            return "unknown"
        tag_name = await field_locator.first.evaluate("(el) => el.tagName.toLowerCase()")
        return tag_name

    async def _detect_required(self, container, question_text: str) -> bool:
        """Detect whether a question is required."""
        try:
            if "*" in question_text:
                return True
            required_locator = container.locator(
                self._selectors.discovery.questions.required_marker
            )
            return await required_locator.count() > 0
        except Exception:
            return False

    async def _extract_email(self, page: Page) -> str | None:
        """Extract an email from visible mailto links or page text."""
        if await self._selector_exists(page, self._selectors.discovery.apply_flow.email_link):
            href = await self._extract_attribute(page, self._selectors.discovery.apply_flow.email_link, "href")
            if href and href.lower().startswith("mailto:"):
                return href.replace("mailto:", "").strip()

        try:
            body_text = await page.locator(
                self._selectors.discovery.questions.page_body
            ).inner_text(timeout=5000)
        except Exception:
            return None
        for token in body_text.split():
            if "@" in token and "." in token:
                cleaned = token.strip(".,;:()<>[]{}")
                if "@" in cleaned and "." in cleaned:
                    return cleaned
        return None

    async def _extract_text(self, page: Page, selector_string: str) -> str:
        """Return the first visible text matching any selector in the list."""
        return await self._extract_text_from_locator(page, selector_string)

    async def _extract_text_from_locator(self, locator, selector_string: str) -> str:
        for selector in self._split_selectors(selector_string):
            try:
                item = locator.locator(selector)
                if await item.count() == 0:
                    continue
                text = (await item.first.inner_text()).strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    async def _extract_attribute(self, page: Page, selector_string: str, attribute: str) -> str | None:
        for selector in self._split_selectors(selector_string):
            try:
                item = page.locator(selector)
                if await item.count() == 0:
                    continue
                value = await item.first.get_attribute(attribute)
                if value:
                    return value
            except Exception:
                continue
        return None

    async def _selector_exists(self, page: Page, selector_string: str) -> bool:
        for selector in self._split_selectors(selector_string):
            try:
                locator = page.locator(selector)
                if await locator.count() > 0 and await locator.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _is_already_applied_visible(self, page: Page) -> bool:
        """Check if a job-specific 'already applied' indicator is visible, ignoring GNB/header/nav."""
        selector_string = self._selectors.discovery.apply_flow.already_applied
        for selector in self._split_selectors(selector_string):
            try:
                locators = page.locator(selector)
                count = await locators.count()
                for i in range(count):
                    loc = locators.nth(i)
                    if not await loc.is_visible():
                        continue

                    is_ignored = await loc.evaluate("""
                        (el) => {
                            const ignoredSelectors = [
                                '.nI-gNb-header', '.nI-gNb-drawer', 'nav',
                                '[class*="nI-gNb-"]', '[id*="nI-gNb-"]',
                                '[class*="gnb"]', '[id*="gnb"]',
                                '.nI-gNb-backdrop', '.naukri-drawer'
                            ];
                            
                            for (const sel of ignoredSelectors) {
                                if (el.closest(sel)) return true;
                            }
                            
                            let parent = el;
                            while (parent) {
                                if (parent.tagName && parent.tagName.toLowerCase() === 'a') {
                                    const href = parent.getAttribute('href') || '';
                                    const hrefLower = href.toLowerCase();
                                    if (
                                        hrefLower.includes('historypage') || 
                                        hrefLower.includes('myapply') ||
                                        hrefLower.includes('savedjobs') || 
                                        hrefLower.includes('recommended') ||
                                        hrefLower.includes('nvites')
                                    ) {
                                        return true;
                                    }
                                }
                                parent = parent.parentElement;
                            }
                            return false;
                        }
                    """)
                    if not is_ignored:
                        return True
            except Exception:
                continue
        return False

    async def _first_visible_locator(self, page: Page, selector_string: str):
        for selector in self._split_selectors(selector_string):
            try:
                locator = page.locator(selector)
                if await locator.count() > 0 and await locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        return None

    async def _active_page(self, page: Page, before_pages: int) -> Page:
        """Return the most likely active page after clicking apply."""
        pages = page.context.pages
        new_pages = pages[before_pages:]
        for p in reversed(new_pages):
            if not p.is_closed() and "naukri" in p.url.lower():
                return p
        if new_pages:
            return new_pages[-1]
        return page

    def _is_external_link(self, url: str, job_url: str) -> bool:
        if not url or url.lower().startswith("mailto:"):
            return False
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
    
        def root_domain(netloc: str) -> str:
            parts = netloc.lower().split(".")
            return ".".join(parts[-2:]) if len(parts) >= 2 else netloc.lower()
    
        return root_domain(parsed.netloc) != root_domain(urlparse(job_url).netloc)

    def _split_selectors(self, selector_string: str) -> list[str]:
        return [selector.strip() for selector in selector_string.split(",") if selector.strip()]

    async def _find_apply_trigger(self, page: Page) -> Locator | None:
        """Find the primary apply button/link on the page, ignoring header/nav and status links."""
        selector_string = self._selectors.discovery.apply_flow.trigger
        for selector in self._split_selectors(selector_string):
            try:
                locators = page.locator(selector)
                count = await locators.count()
                for i in range(count):
                    loc = locators.nth(i)
                    if not await loc.is_visible():
                        continue
                    
                    text = (await loc.inner_text()).strip()
                    lower_text = text.lower()
                    
                    # Ignore common navigation / status / agent texts
                    ignored_texts = [
                        "application status", "saved jobs", "recommended jobs", 
                        "neo-ai job agent", "explore", "profile", "jobs"
                    ]
                    if any(t in lower_text for t in ignored_texts):
                        continue
                        
                    is_ignored = await loc.evaluate("""
                        (el) => {
                            const ignoredSelectors = [
                                '.nI-gNb-header', '.nI-gNb-drawer', 'nav',
                                '[class*="nI-gNb-"]', '[id*="nI-gNb-"]',
                                '[class*="gnb"]', '[id*="gnb"]',
                                '.nI-gNb-backdrop', '.naukri-drawer'
                            ];
                            for (const sel of ignoredSelectors) {
                                if (el.closest(sel)) return true;
                            }
                            return false;
                        }
                    """)
                    if is_ignored:
                        continue
                        
                    return loc
            except Exception:
                continue
        return None

    async def _is_easy_apply_modal_visible(self, page: Page) -> bool:
        """Check if an easy apply modal, chatbot drawer, or application popup is visible, ignoring GNB."""
        selector_string = self._selectors.discovery.apply_flow.easy_apply_marker
        for selector in self._split_selectors(selector_string):
            try:
                locators = page.locator(selector)
                count = await locators.count()
                for i in range(count):
                    loc = locators.nth(i)
                    if not await loc.is_visible():
                        continue
                    
                    is_ignored = await loc.evaluate("""
                        (el) => {
                            const ignoredSelectors = [
                                '.nI-gNb-header', '.nI-gNb-drawer', 'nav',
                                '[class*="nI-gNb-"]', '[id*="nI-gNb-"]',
                                '[class*="gnb"]', '[id*="gnb"]',
                                '.nI-gNb-backdrop', '.naukri-drawer'
                            ];
                            for (const sel of ignoredSelectors) {
                                if (el.closest(sel)) return true;
                            }
                            return false;
                        }
                    """)
                    if not is_ignored:
                        return True
            except Exception:
                continue
        return False
