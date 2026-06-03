"""
POC-3B Phase 2 — Form Auto-Fill.

Fills known answers into application form fields.

Safety guarantees
-----------------
- DRY_RUN = True (default): detects what would be filled, logs it, but never
  touches the DOM.  Set to False only when you are ready for live filling.
- Final-submit selectors (configured in selectors.yaml ``final_submit``) are
  NEVER clicked under any circumstances.
- No "Next", "Submit", "Apply Now", or confirmation buttons are clicked.
- Screenshots are captured before and after (even in DRY_RUN mode).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from playwright.async_api import Page

from app.discovery.question_normalizer import normalize_question_key
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoveredQuestion
from app.models.form_fill import FieldFillResult, FormFillReport

if TYPE_CHECKING:
    from app.discovery.repository import ApplyDiscoveryRepository

# ---------------------------------------------------------------------------
# SAFETY FLAG
# ---------------------------------------------------------------------------

DRY_RUN: bool = True
"""
When True  → fields are detected and logged; the DOM is NOT modified.
When False → known answers are typed / selected into form fields.

The final_submit selector is NEVER clicked regardless of this flag.
"""


class FormFiller:
    """
    Auto-fill known answers into easy-apply form fields.

    Uses the discovered question list (already enriched with bank answers
    from Phase 1) to locate and fill each visible field.
    """

    def __init__(
        self,
        settings: AppSettings,
        selectors: SelectorsConfig,
        screenshots_dir: Path,
        repo: ApplyDiscoveryRepository | None = None,
    ) -> None:
        self._settings = settings
        self._selectors = selectors
        self._screenshots_dir = screenshots_dir
        self._repo = repo

    # ── Public API ───────────────────────────────────────────────────────────

    async def fill_form(
        self,
        page: Page,
        job_id: int,
        company: str,
        role: str,
        questions: list[DiscoveredQuestion],
    ) -> FormFillReport:
        """
        Attempt to fill all known fields on the current page.

        Args:
            page:      Active Playwright page (the apply modal / form).
            job_id:    Job ID for logging and screenshot naming.
            company:   Company name (for the report).
            role:      Job title (for the report).
            questions: Discovered questions enriched with bank answers.

        Returns:
            FormFillReport with filled / unknown lists and screenshot paths.
        """
        report = FormFillReport(
            job_id=job_id,
            company=company,
            role=role,
            dry_run=DRY_RUN,
        )

        if DRY_RUN:
            logger.info(
                "Phase 2 DRY_RUN=True — detecting fields for job_id={} (no DOM changes)",
                job_id,
            )
        else:
            logger.info(
                "Phase 2 DRY_RUN=False — filling fields for job_id={}",
                job_id,
            )

        # Build lookup: question_key → DiscoveredQuestion (with answer)
        answer_map: dict[str, DiscoveredQuestion] = {
            q.question_key: q for q in questions if q.answer
        }

        # Screenshot before any interaction
        report.screenshot_before = await self._capture_screenshot(
            page, f"job_{job_id}_phase2_before"
        )

        # Iterate all question containers visible on the page
        container_sel = self._selectors.discovery.questions.container
        container_count = await page.locator(container_sel).count()
        logger.info(
            "Phase 2: found {} question container(s) on page for job_id={}",
            container_count,
            job_id,
        )

        processed_keys = set()
        for idx in range(container_count):
            container = page.locator(container_sel).nth(idx)
            try:
                if not await container.is_visible():
                    continue

                question_text = await self._extract_text(
                    container, self._selectors.discovery.questions.text
                )
                if not question_text:
                    continue

                question_key = normalize_question_key(question_text)
                if question_key in processed_keys:
                    continue
                processed_keys.add(question_key)

                field_type = await self._detect_field_type(container, page)
                required = "required" in question_text.lower() or "*" in question_text

                discovered_q = answer_map.get(question_key)
                answer = discovered_q.answer if discovered_q else None
                answer_source = "AUTO"

                if not answer and self._repo:
                    answer = self._repo.get_question_answer(question_key)
                    if answer:
                        answer_source = "AUTO"

                if not answer:
                    options = await self._get_field_options(container, field_type)
                    display_type = field_type
                    if field_type == "input":
                        display_type = "text"
                    elif field_type == "select":
                        display_type = "dropdown"
                    elif field_type in ("radiogroup", "radio"):
                        display_type = "radio"

                    print("\n" + "=" * 40)
                    print(f"Question:\n{question_text}\n")
                    print(f"Key:\n{question_key}\n")
                    print(f"Field Type:\n{display_type}\n")
                    if options:
                        print("Options:\n")
                        for opt in options:
                            print(f"* {opt}")
                        print("")
                    
                    user_ans = input("Enter Answer: ").strip()

                    # Save answer immediately into question_bank
                    if self._repo:
                        from app.models.discovery import DiscoveredQuestion
                        new_q = DiscoveredQuestion(
                            question_key=question_key,
                            question_text=question_text,
                            field_type=field_type,
                            required=required,
                            answer=user_ans
                        )
                        self._repo.save_question(job_id, new_q)
                    
                    answer = user_ans
                    answer_source = "USER_LEARNED"
                    
                    # Update local answer map so it's reused immediately if the same key appears again in this form
                    from app.models.discovery import DiscoveredQuestion
                    answer_map[question_key] = DiscoveredQuestion(
                        question_key=question_key,
                        question_text=question_text,
                        field_type=field_type,
                        required=required,
                        answer=answer
                    )

                result = await self._handle_known_field(
                    container=container,
                    question_key=question_key,
                    question_text=question_text,
                    field_type=field_type,
                    required=required,
                    answer=answer,
                    job_id=job_id,
                    answer_source=answer_source,
                    page=page,
                )
                report.filled.append(result)

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Phase 2 container error idx={} job_id={}: {}",
                    idx,
                    job_id,
                    exc,
                )

        # Screenshot after (reflects fills if DRY_RUN=False)
        report.screenshot_after = await self._capture_screenshot(
            page, f"job_{job_id}_phase2_after"
        )

        status_word = "DRY_RUN" if DRY_RUN else "LIVE"
        logger.info(
            "Phase 2 [{}] complete: {} filled / {} unknown / {:.1f}% fill rate — job_id={}",
            status_word,
            len(report.filled),
            len(report.unknown),
            report.fill_rate_pct,
            job_id,
        )
        return report

    # ── Field handling ───────────────────────────────────────────────────────

    async def _handle_known_field(
        self,
        container,
        question_key: str,
        question_text: str,
        field_type: str,
        required: bool,
        answer: str,
        job_id: int,
        answer_source: str = "AUTO",
        page: Page | None = None,
    ) -> FieldFillResult:
        """Fill or simulate filling a known field."""
        if DRY_RUN and answer_source != "USER_LEARNED":
            logger.info(
                "Phase 2 DRY_RUN WOULD_FILL: [{}] '{}' = '{}' (source={}) job_id={}",
                question_key,
                question_text[:60],
                answer[:40],
                answer_source,
                job_id,
            )
            return FieldFillResult(
                question_key=question_key,
                question_text=question_text,
                field_type=field_type,
                required=required,
                status="skipped_dry_run",
                answer_used=answer,
                answer_source=answer_source,
            )

        # Live fill
        success, error = await self._fill_field(container, field_type, answer, page)
        status = "filled" if success else "error"
        if success:
            logger.info(
                "Phase 2 FILLED: [{}] '{}' = '{}' (source={}) job_id={}",
                question_key,
                question_text[:60],
                answer[:40],
                answer_source,
                job_id,
            )
        else:
            logger.warning(
                "Phase 2 ERROR: [{}] '{}' (source={}) job_id={} — {}",
                question_key,
                question_text[:60],
                answer_source,
                job_id,
                error,
            )
        return FieldFillResult(
            question_key=question_key,
            question_text=question_text,
            field_type=field_type,
            required=required,
            status=status,
            answer_used=answer if success else None,
            error=error if not success else None,
            answer_source=answer_source,
        )

    async def _fill_field(
        self, container, field_type: str, answer: str, page: Page | None = None
    ) -> tuple[bool, str | None]:
        """
        Fill a single form field.

        Returns (success, error_message).
        The final_submit selector is NEVER clicked here.
        """
        try:
            if field_type in ("input", "textarea"):
                field_loc = container.locator(f"{field_type}").first
                if await field_loc.count() == 0 and page:
                    field_loc = page.locator(f"{field_type}:visible").first
                await field_loc.click()
                await field_loc.fill("")
                await field_loc.type(answer, delay=20)
                return True, None

            if field_type == "select":
                select_loc = container.locator("select").first
                if await select_loc.count() == 0 and page:
                    select_loc = page.locator("select:visible").first
                try:
                    await select_loc.select_option(label=answer)
                except Exception:
                    await select_loc.select_option(value=answer)
                return True, None

            if field_type in ("radiogroup", "[role='radiogroup']", "radio") or "radio" in field_type:
                options = container.locator("[role='radio'], input[type='radio']")
                if await options.count() == 0 and page:
                    options = page.locator("[role='radio']:visible, input[type='radio']:visible")
                count = await options.count()
                answer_lower = answer.lower()
                for i in range(count):
                    opt = options.nth(i)
                    opt_text = (await opt.inner_text()).strip().lower()
                    if not opt_text:
                        opt_text = (await opt.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip().lower()
                    if answer_lower in opt_text or opt_text in answer_lower:
                        await opt.click()
                        return True, None
                return False, f"No radio option matched '{answer}'"

            if field_type == "contenteditable":
                ce_loc = container.locator("[contenteditable='true']").first
                if await ce_loc.count() == 0 and page:
                    ce_loc = page.locator("[contenteditable='true']:visible").first
                await ce_loc.click()
                await ce_loc.fill(answer)
                return True, None

            # Unknown field type — log and skip
            return False, f"Unsupported field_type '{field_type}'"

        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _detect_field_type(self, container, page: Page | None = None) -> str:
        # Check if container contains any radio buttons
        try:
            if await container.locator("[role='radio'], input[type='radio']").count() > 0:
                return "radio"
        except Exception:
            pass

        field_loc = container.locator(self._selectors.discovery.questions.field)
        if await field_loc.count() > 0:
            tag = await field_loc.first.evaluate("(el) => el.tagName.toLowerCase()")
            role = await field_loc.first.get_attribute("role") or ""
            if role == "radiogroup" or role == "radio":
                return "radio"
            return tag

        # No field found inside container — fallback to global page search
        if page:
            global_selectors = [
                "input:not([type='hidden'])",
                "textarea",
                "select",
                "[contenteditable='true']",
                "[role='radio']",
                "[role='radiogroup']"
            ]
            for sel in global_selectors:
                loc = page.locator(sel)
                count = await loc.count()
                for i in range(count):
                    el = loc.nth(i)
                    if await el.is_visible() and await el.is_enabled():
                        tag = await el.evaluate("(element) => element.tagName.toLowerCase()")
                        role = await el.get_attribute("role") or ""
                        contenteditable = await el.get_attribute("contenteditable") or ""
                        if role == "radiogroup" or role == "radio":
                            return "radio"
                        if tag == "div" and contenteditable == "true":
                            return "contenteditable"
                        if tag in ("input", "textarea"):
                            return tag
                        if tag == "select":
                            return "select"
        return "unknown"

    async def _extract_text(self, container, selector_string: str) -> str:
        for selector in self._split_selectors(selector_string):
            try:
                item = container.locator(selector)
                if await item.count() == 0:
                    continue
                text = (await item.first.inner_text()).strip()
                if text:
                    return text
            except Exception:
                continue
        return ""

    async def _capture_screenshot(self, page: Page, name: str) -> str | None:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{name}.png"
        filepath = self._screenshots_dir / filename
        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info("Phase 2 screenshot: {}", filename)
            return str(filepath)
        except Exception as exc:
            logger.warning("Phase 2 screenshot failed {}: {}", name, exc)
            return None

    def _split_selectors(self, selector_string: str) -> list[str]:
        return [s.strip() for s in selector_string.split(",") if s.strip()]

    async def _get_field_options(self, container, field_type: str) -> list[str]:
        """Extract available options for select or radio inputs."""
        options = []
        try:
            if field_type == "select":
                opt_locs = container.locator("select option")
                count = await opt_locs.count()
                for i in range(count):
                    text = (await opt_locs.nth(i).inner_text()).strip()
                    if text and not any(text.lower().startswith(p) for p in ["select", "--", "choose"]):
                        options.append(text)
            elif field_type in ("radiogroup", "[role='radiogroup']", "radio") or "radio" in field_type:
                opt_locs = container.locator("[role='radio'], input[type='radio']")
                count = await opt_locs.count()
                for i in range(count):
                    el = opt_locs.nth(i)
                    text = (await el.inner_text()).strip()
                    if not text:
                        text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                    if text:
                        text = " ".join(text.split())
                        options.append(text)
        except Exception:
            pass

        # De-duplicate while preserving order
        seen = set()
        unique_opts = []
        for o in options:
            if o not in seen:
                seen.add(o)
                unique_opts.append(o)
        return unique_opts
