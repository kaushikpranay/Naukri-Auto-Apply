"""
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
)
from app.models.form_fill import FormFillReport
from app.models.job import JobData
from app.question_bank.form_filler import FormFiller
from app.utils.config_loader import resolve_path


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

    async def run(
        self,
        page: Page,
        run_id: str | None = None,
        force_job_id: int | None = None,
    ) -> DiscoverySummary:
        """Process shortlisted jobs, or a single forced job.

        Args:
            page: Active browser page.
            run_id: Run identifier (reserved).
            force_job_id: If set, ignore normal filtering and reprocess
                          exactly this job, clearing any prior record.
        """
        summary = DiscoverySummary()

        if force_job_id is not None:
            job = self._repo.get_job_by_id(force_job_id)
            if job is None:
                logger.error("Job {} not found in database", force_job_id)
                return summary
            self._repo.clear_application(force_job_id)
            jobs = [job]
        else:
            jobs = self._repo.get_jobs_for_discovery(
                limit=self._settings.discovery.max_discovery_jobs_per_run
            )

        if not jobs:
            logger.info("No shortlisted APPLY jobs found for discovery.")
            return summary

        logger.info("Found {} shortlisted APPLY job(s) for discovery.", len(jobs))

        for index, job in enumerate(jobs, start=1):
            logger.info("Job Opened: [{} / {}] {} - {}", index, len(jobs), job.company_name, job.job_title)
            started_at = datetime.now()
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
            except Exception as exc:  # noqa: BLE001
                logger.exception("Discovery Failed: job_id={} error={}", job.id, exc)
                self._repo.save_application(
                    ApplicationDiscoveryRecord(
                        job_id=int(job.id or 0),
                        status="discovery_failed",
                        detected_at=datetime.now().isoformat(),
                    )
                )
                summary.failed += 1

            summary.processed += 1

        summary.completed_at = datetime.now()
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
        elif apply_type == "unknown":
            summary.discovered += 1
            summary.unknown_flow += 1
        else:
            summary.requires_review += 1

        logger.info("Apply Type Detected: job_id={} type={}", job.id, apply_type)
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

        await page.goto(job.job_url, wait_until="domcontentloaded")
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
                active_page = page.context.pages[-1]
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
        page_text = await active_page.locator("body").inner_text()
        if (
            "/myapply/saveApply" in url_after
            or "Apply Confirmation" in page_title
            or "Applied to" in page_text
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
        lower = url.lower()
        if "/registration/" in lower or "/createaccount" in lower:
            return "register"
        if "/login/" in lower or "/signin" in lower or "/sign-in" in lower:
            return "login_required"
        if "register" in lower:
            return "register"
        if "login" in lower:
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
            return str(filepath.relative_to(resolve_path(".")))
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

        page_text = await page.locator("body").inner_text()
        lower_page = page_text.lower()
        if "register" in lower_page and "apply" in lower_page:
            return "register"
        if "login" in lower_page and "apply" in lower_page:
            return "login_required"

        return None

    async def _discover_questions_readonly(self, page: Page, job: JobData) -> list[DiscoveredQuestion]:
        """Detect questions on the page — read-only, no interaction."""
        questions: list[DiscoveredQuestion] = []
        containers = await page.locator(self._selectors.discovery.questions.container).count()
        if containers == 0:
            return questions

        for index in range(containers):
            container = page.locator(self._selectors.discovery.questions.container).nth(index)
            if not await container.is_visible():
                continue

            question_text = await self._extract_text_from_locator(
                container, self._selectors.discovery.questions.text
            )
            if not question_text:
                continue

            question_key = normalize_question_key(question_text)
            field_type = await self._detect_field_type(container)
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
            return str(filepath.relative_to(resolve_path(".")))
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
            return str(filepath.relative_to(resolve_path(".")))
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

    async def _detect_field_type(self, container) -> str:
        """Infer the field type for a question container."""
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

        body_text = await page.locator(
            self._selectors.discovery.questions.page_body
        ).inner_text()
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
        if len(pages) > before_pages:
            return pages[-1]
        return page

    def _is_external_link(self, url: str, job_url: str) -> bool:
        if not url:
            return False
        if url.lower().startswith("mailto:"):
            return False

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False

        job_domain = urlparse(job_url).netloc.lower()
        return parsed.netloc.lower() != job_domain

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
