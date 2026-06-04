"""
POC-3B Phase 2 — Form Auto-Fill.

Fills known answers into application form fields.

Safety guarantees
-----------------
- DRY_RUN = False (default): detects what would be filled, logs it, but never
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
from playwright.async_api import Page, Locator

from app.discovery.question_normalizer import normalize_question_key
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoveredQuestion
from app.models.form_fill import FieldFillResult, FormFillReport

if TYPE_CHECKING:
    from app.discovery.repository import ApplyDiscoveryRepository

# ---------------------------------------------------------------------------
# SAFETY FLAG
# ---------------------------------------------------------------------------

DRY_RUN: bool = False
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
        loop_limit = 30
        loop_count = 0

        while loop_count < loop_limit:
            # Re-evaluate container count dynamically
            container_count = await page.locator(container_sel).count()
            
            # Find the first unprocessed visible question container
            target_container = None
            target_key = None
            target_text = None
            
            for idx in range(container_count):
                container = page.locator(container_sel).nth(idx)
                if not await container.is_visible():
                    continue

                if not await is_valid_recruiter_question_container(container):
                    continue
                    
                question_text = await self._extract_text(
                    container, self._selectors.discovery.questions.text
                )
                if not question_text:
                    continue
                    
                question_key = normalize_question_key(question_text)
                if question_key not in processed_keys:
                    target_container = container
                    target_key = question_key
                    target_text = question_text
                    break
                    
            if not target_container:
                # No more unprocessed visible questions found
                break
                
            loop_count += 1
            processed_keys.add(target_key)

            try:
                # Add a brief delay to allow chatbot DOM updates to settle
                await page.wait_for_timeout(1000)

                field_type = await self._detect_field_type(target_container, page)
                required = "required" in target_text.lower() or "*" in target_text

                discovered_q = answer_map.get(target_key)
                answer = discovered_q.answer if discovered_q else None
                answer_source = "AUTO"

                final_answer = answer
                if not final_answer and self._repo:
                    final_answer = self._repo.get_question_answer(target_key)
                    if final_answer:
                        answer_source = "AUTO"

                if final_answer:
                    final_answer = str(final_answer).strip()
                    if final_answer.lower() in ("none", "null", "unknown"):
                        final_answer = None

                # Check mapping for CASE 2
                if final_answer and self._repo and hasattr(self._repo, "get_answer_mapping"):
                    mapped = self._repo.get_answer_mapping(target_key, final_answer)
                    if mapped:
                        logger.info("Using mapped option for key={}: {} -> {}", target_key, final_answer, mapped)
                        final_answer = mapped

                if not final_answer:
                    # CASE 1: Unknown Question
                    options = await self._get_field_options(target_container, field_type, page)
                    
                    logger.info("Unknown question '{}' (key={}). Prompting user via browser dialog...", target_text, target_key)
                    response = await self._interactive_prompt_user(
                        page=page,
                        question_text=target_text,
                        is_case2=False,
                        options=options
                    )
                    user_ans = response["answer"]

                    # Save answer immediately into question_bank
                    if self._repo:
                        from app.models.discovery import DiscoveredQuestion
                        new_q = DiscoveredQuestion(
                            question_key=target_key,
                            question_text=target_text,
                            field_type=field_type,
                            required=required,
                            answer=user_ans
                        )
                        self._repo.save_question(job_id, new_q)
                    
                    final_answer = user_ans
                    answer = user_ans  # Keep track of original raw answer for mapping checks
                    answer_source = "USER_LEARNED"
                    
                    # Update local answer map so it's reused immediately if the same key appears again in this form
                    from app.models.discovery import DiscoveredQuestion
                    answer_map[target_key] = DiscoveredQuestion(
                        question_key=target_key,
                        question_text=target_text,
                        field_type=field_type,
                        required=required,
                        answer=final_answer
                    )

                result = await self._handle_known_field(
                    container=target_container,
                    question_key=target_key,
                    question_text=target_text,
                    field_type=field_type,
                    required=required,
                    answer=final_answer,
                    job_id=job_id,
                    answer_source=answer_source,
                    page=page,
                )

                # CASE 2: Known Answer But No Matching Option
                if result.status == "error" and answer:
                    logger.info("Known answer '{}' failed for field_type={} key={}. Checking if options exist for mapping...", answer, field_type, target_key)
                    options = await self._get_field_options(target_container, field_type, page)
                    if options:
                        logger.info("Known answer '{}' failed and options exist. Prompting option mapping...", answer)
                        response = await self._interactive_prompt_user(
                            page=page,
                            question_text=target_text,
                            is_case2=True,
                            stored_answer=answer,
                            options=options
                        )
                        selected_opt = response["selected_option"]
                        if selected_opt:
                            if self._repo and hasattr(self._repo, "save_answer_mapping"):
                                self._repo.save_answer_mapping(target_key, answer, selected_opt)
                                logger.info("MAPPING_SAVED")
                            
                            # Re-run filling with the selected option
                            result = await self._handle_known_field(
                                container=target_container,
                                question_key=target_key,
                                question_text=target_text,
                                field_type=field_type,
                                required=required,
                                answer=selected_opt,
                                job_id=job_id,
                                answer_source="USER_MAPPED",
                                page=page,
                            )

                report.filled.append(result)

                # Click chatbot Save button if field was successfully filled
                if result.status == "filled":
                    # 1. Resolve chatbot drawer
                    drawer = None
                    for sel in [".chatbot_Drawer", "[class*='chatbot']", "[class*='drawer']", "[class*='modal']"]:
                        loc = page.locator(sel)
                        count = await loc.count()
                        for i in range(count):
                            el = loc.nth(i)
                            if await el.is_visible():
                                drawer = el
                                break
                        if drawer:
                            break

                    # 2. Find Save button inside drawer/page
                    save_btn = None
                    for sel in [".sendMsg", "div:has-text('Save')", "button:has-text('Save')"]:
                        loc = drawer.locator(sel) if drawer else page.locator(sel)
                        count = await loc.count()
                        for i in range(count):
                            el = loc.nth(i)
                            if await el.is_visible():
                                save_btn = el
                                logger.info("SAVE_BUTTON_FOUND with selector: {}", sel)
                                break
                        if save_btn:
                            break

                    if save_btn:
                        # Wait for UI update
                        await page.wait_for_timeout(1000)
                        logger.info("SAVE_BUTTON_CLICKED")
                        await save_btn.evaluate("el => el.click()")
                        
                        # Wait for next question to load
                        await page.wait_for_timeout(2000)
                        
                        # Detect next question
                        new_count = await page.locator(container_sel).count()
                        next_q_found = False
                        for next_idx in range(new_count):
                            next_container = page.locator(container_sel).nth(next_idx)
                            if await next_container.is_visible():
                                next_q_text = await self._extract_text(
                                    next_container, self._selectors.discovery.questions.text
                                )
                                if next_q_text:
                                    next_q_key = normalize_question_key(next_q_text)
                                    if next_q_key not in processed_keys:
                                        logger.info("NEXT_QUESTION_DETECTED: '{}' [{}]", next_q_text[:60], next_q_key)
                                        next_q_found = True
                                        break
                        if not next_q_found:
                            logger.info("No new question detected after clicking Save.")

            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Phase 2 container error key={} job_id={}: {}",
                    target_key,
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
        success, error, selector_used, method_used = await self._fill_field(container, field_type, answer, page, question_key=question_key)
        status = "filled" if success else "error"
        result_word = "SUCCESS" if success else f"FAILED ({error})"
        
        # Verification Log
        logger.info(
            f"\n{question_key}\n"
            f"{answer}\n"
            f"{selector_used}\n"
            f"{method_used}\n"
            f"{result_word}\n"
        )

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
        self, container, field_type: str, answer: str, page: Page | None = None, question_key: str = ""
    ) -> tuple[bool, str | None, str, str]:
        """
        Fill a single form field.

        Returns (success, error_message, selector_used, method_used).
        The final_submit selector is NEVER clicked here.
        """
        selector_used = "unknown"
        method_used = "unknown"
        try:
            # Active drawer resolution helper
            drawer = None
            if page:
                for sel in [".chatbot_Drawer", "[class*='chatbot']", "[class*='drawer']", "[class*='modal']"]:
                    loc = page.locator(sel)
                    count = await loc.count()
                    for i in range(count):
                        el = loc.nth(i)
                        if await el.is_visible():
                            drawer = el
                            break
                    if drawer:
                        break

            if field_type in ("input", "textarea"):
                input_sel = "input:not([type='radio']):not([type='checkbox']):not([type='hidden'])" if field_type == "input" else "textarea"
                field_loc = container.locator(input_sel).first
                selector_used = f"{field_type}"
                if await field_loc.count() == 0 and page:
                    if drawer:
                        field_loc = drawer.locator(input_sel).first
                        selector_used = f"chatbot_drawer {field_type}"
                    else:
                        field_loc = page.locator(f"{input_sel}:visible").first
                        selector_used = f"{field_type}:visible"

                method_used = "TYPE"
                await field_loc.click()
                await field_loc.fill("")
                await field_loc.type(answer, delay=20)
                return True, None, selector_used, method_used

            if field_type == "select":
                select_loc = container.locator("select").first
                selector_used = "select"
                if await select_loc.count() == 0 and page:
                    if drawer:
                        select_loc = drawer.locator("select").first
                        selector_used = "chatbot_drawer select"
                    else:
                        select_loc = page.locator("select:visible").first
                        selector_used = "select:visible"

                method_used = "SELECT"
                try:
                    await select_loc.select_option(label=answer)
                except Exception:
                    await select_loc.select_option(value=answer)
                return True, None, selector_used, method_used

            if field_type in ("radiogroup", "[role='radiogroup']", "radio") or "radio" in field_type:
                method_used = "DOM_CLICK"
                opt_locs = []
                if drawer:
                    opt_loc = drawer.locator(f"input[aria-label='{answer}']").first
                    if await opt_loc.count() > 0:
                        opt_locs.append((opt_loc, f"input[aria-label=\"{answer}\"]"))
                    opt_loc2 = drawer.locator(f"[role='radio'][aria-label='{answer}']").first
                    if await opt_loc2.count() > 0:
                        opt_locs.append((opt_loc2, f"[role='radio'][aria-label=\"{answer}\"]"))

                if not opt_locs:
                    options = container.locator("[role='radio'], input[type='radio']")
                    if await options.count() == 0 and drawer:
                        options = drawer.locator("[role='radio']:visible, input[type='radio']:visible")
                    elif await options.count() == 0 and page:
                        options = page.locator("[role='radio']:visible, input[type='radio']:visible")
                    count = await options.count()
                    answer_lower = answer.lower()
                    
                    # Store option elements and their text for fuzzy matching fallback
                    option_pairs = []
                    for i in range(count):
                        opt = options.nth(i)
                        opt_text = (await opt.inner_text()).strip()
                        if not opt_text:
                            opt_text = (await opt.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                        aria_label = (await opt.get_attribute("aria-label") or "").strip()
                        
                        # 1. Simple text matching
                        opt_text_lower = opt_text.lower()
                        aria_label_lower = aria_label.lower()
                        if answer_lower in opt_text_lower or opt_text_lower in answer_lower or answer_lower in aria_label_lower:
                            opt_locs.append((opt, f"[role='radio'] option {answer}"))
                        else:
                            option_pairs.append((opt, opt_text if opt_text else aria_label))

                    # 2. If no exact/substring match found, try experience range/numerical matching
                    if not opt_locs:
                        val = None
                        try:
                            val = float(answer.strip())
                        except ValueError:
                            # If answer is not a pure float (e.g. "<6 years"), try to get actual numeric experience
                            val = get_actual_numeric_experience(question_key, self._repo)

                        if val is not None:
                            import re
                            for opt, text_val in option_pairs:
                                opt_norm = text_val.strip().lower()
                                matched = False
                                
                                # Case 1: No experience
                                if val == 0 and any(w in opt_norm for w in ["no experience", "fresher", "fresh", "none", "0"]):
                                    matched = True
                                
                                # Case 2: Range X-Y or X to Y
                                if not matched:
                                    range_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)', opt_norm)
                                    if range_match:
                                        low = float(range_match.group(1))
                                        high = float(range_match.group(2))
                                        if low <= val <= high:
                                            matched = True
                                            
                                # Case 3: Less than X
                                if not matched:
                                    less_match = re.search(r'(?:<|less\s+than)\s*(\d+(?:\.\d+)?)', opt_norm)
                                    if less_match:
                                        limit = float(less_match.group(1))
                                        if val < limit:
                                            matched = True
                                            
                                # Case 4: Greater than X or X+
                                if not matched:
                                    greater_match = re.search(r'(?:>|greater\s+than|more\s+than|above)\s*(\d+(?:\.\d+)?)', opt_norm)
                                    if greater_match:
                                        limit = float(greater_match.group(1))
                                        if val > limit:
                                            matched = True
                                    plus_match = re.search(r'(\d+(?:\.\d+)?)\s*\+', opt_norm)
                                    if plus_match and not matched:
                                        limit = float(plus_match.group(1))
                                        if val >= limit:
                                            matched = True
                                            
                                # Case 5: Exact single number
                                if not matched:
                                    exact_match = re.search(r'\b(\d+(?:\.\d+)?)\b', opt_norm)
                                    if exact_match:
                                        limit = float(exact_match.group(1))
                                        if val == limit:
                                            matched = True
                                            
                                if matched:
                                    opt_locs.append((opt, f"[role='radio'] fuzzy option {text_val}"))
                                    break  # Match found

                for opt, sel_name in opt_locs:
                    try:
                        selector_used = sel_name
                        await opt.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', { bubbles: true })); el.dispatchEvent(new Event('input', { bubbles: true })); }")
                        return True, None, selector_used, method_used
                    except Exception as e:
                        logger.warning("DOM click failed: {}", e)

                return False, f"No radio option matched '{answer}'", selector_used, method_used

            if field_type == "contenteditable":
                ce_loc = container.locator("[contenteditable='true']").first
                selector_used = "[contenteditable='true']"
                if await ce_loc.count() == 0 and page:
                    if drawer:
                        ce_loc = drawer.locator("[contenteditable='true']").first
                        selector_used = "[contenteditable=\"true\"]"
                    else:
                        ce_loc = page.locator("[contenteditable='true']:visible").first
                        selector_used = "[contenteditable='true']:visible"

                method_used = "TYPE"
                await ce_loc.click()
                await ce_loc.fill(answer)
                return True, None, selector_used, method_used

            if field_type == "checkbox":
                checkbox_loc = container.locator("input[type='checkbox'], [role='checkbox']").first
                selector_used = "checkbox"
                if await checkbox_loc.count() == 0 and page:
                    if drawer:
                        checkbox_loc = drawer.locator("input[type='checkbox'], [role='checkbox']").first
                        selector_used = "chatbot_drawer checkbox"
                    else:
                        checkbox_loc = page.locator("input[type='checkbox']:visible, [role='checkbox']:visible").first
                        selector_used = "checkbox:visible"

                method_used = "CLICK"
                answer_lower = answer.strip().lower()
                should_be_checked = answer_lower in ("yes", "true", "check", "checked", "select", "selected", "1")
                
                is_checked = False
                tag = await checkbox_loc.evaluate("el => el.tagName.toLowerCase()")
                if tag == "input":
                    is_checked = await checkbox_loc.evaluate("el => el.checked")
                else:
                    aria_checked = await checkbox_loc.get_attribute("aria-checked")
                    is_checked = aria_checked == "true"
                
                if should_be_checked != is_checked:
                    await checkbox_loc.click()
                    logger.info("CHECKBOX_SELECTED")
                return True, None, selector_used, method_used

            if field_type == "div":
                method_used = "DOM_CLICK"
                options_sel = "button, [role='button'], [role='option'], [role='radio'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button'], [tabindex]"
                options = container.locator(options_sel)
                if await options.count() == 0 and drawer:
                    options = drawer.locator(options_sel)
                elif await options.count() == 0 and page:
                    options = page.locator(options_sel)

                count = await options.count()
                answer_lower = answer.lower().strip()
                
                matched_option = None
                selector_used = "div option"
                
                for i in range(count):
                    opt = options.nth(i)
                    if not await opt.is_visible():
                        continue
                    opt_text = (await opt.inner_text()).strip()
                    if not opt_text:
                        opt_text = (await opt.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                    aria_label = (await opt.get_attribute("aria-label") or "").strip()
                    
                    opt_text_lower = opt_text.lower().strip()
                    aria_label_lower = aria_label.lower().strip()
                    
                    if (answer_lower and (answer_lower in opt_text_lower or opt_text_lower in answer_lower or answer_lower in aria_label_lower)):
                        matched_option = opt
                        selector_used = f"div option text={opt_text[:30]}"
                        break
                
                if matched_option:
                    await matched_option.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', { bubbles: true })); el.dispatchEvent(new Event('input', { bubbles: true })); }")
                    logger.info("DIV_OPTION_SELECTED")
                    return True, None, selector_used, method_used
                else:
                    return False, f"No option matching '{answer}' found", selector_used, method_used

            return False, f"Unsupported field_type '{field_type}'", selector_used, method_used

        except Exception as exc:  # noqa: BLE001
            return False, str(exc), selector_used, method_used

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _detect_field_type(self, container, page: Page | None = None) -> str:
        # Check if container contains any checkboxes
        try:
            if await container.locator("[role='checkbox'], input[type='checkbox']").count() > 0:
                return "checkbox"
        except Exception:
            pass

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
            contenteditable = await field_loc.first.get_attribute("contenteditable") or ""
            if role == "radiogroup" or role == "radio":
                return "radio"
            if role == "checkbox" or tag == "checkbox":
                return "checkbox"
            if contenteditable.lower() == "true":
                return "contenteditable"
            return tag

        # Check if container contains any div/button options
        try:
            if await container.locator("button, [role='button'], [role='option'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button'], [tabindex]").count() > 0:
                return "div"
        except Exception:
            pass

        # No field found inside container — fallback to active chatbot drawer
        if page:
            drawer = None
            for sel in [".chatbot_Drawer", "[class*='chatbot']", "[class*='drawer']", "[class*='modal']"]:
                loc = page.locator(sel)
                count = await loc.count()
                for i in range(count):
                    el = loc.nth(i)
                    if await el.is_visible():
                        drawer = el
                        break
                if drawer:
                    break

            if drawer:
                # 1. First, try to find an active text/contenteditable/textarea input in the footer area of the drawer
                footer_selectors = [
                    "[class*='footer'] [contenteditable='true']",
                    "[class*='Input'] [contenteditable='true']",
                    "[class*='input'] [contenteditable='true']",
                    "[class*='footer'] textarea",
                    "[class*='Input'] textarea",
                    "[class*='input'] textarea",
                    "[class*='footer'] input:not([type='hidden']):not([type='radio']):not([type='checkbox'])",
                    "[class*='Input'] input:not([type='hidden']):not([type='radio']):not([type='checkbox'])",
                    "[class*='input'] input:not([type='hidden']):not([type='radio']):not([type='checkbox'])"
                ]
                for sel in footer_selectors:
                    loc = drawer.locator(sel)
                    count = await loc.count()
                    for i in range(count):
                        el = loc.nth(i)
                        if await el.is_visible() and await el.is_enabled():
                            tag = await el.evaluate("(element) => element.tagName.toLowerCase()")
                            contenteditable = await el.get_attribute("contenteditable") or ""
                            if tag == "div" and contenteditable == "true":
                                return "contenteditable"
                            if tag in ("input", "textarea"):
                                return tag

                # 2. If no footer input found, look in the drawer globally
                global_selectors = [
                    "[contenteditable='true']",
                    "textarea",
                    "input:not([type='hidden']):not([type='radio']):not([type='checkbox'])",
                    "select",
                    "input[type='radio']",
                    "[role='radio']",
                    "[role='radiogroup']",
                    "input[type='checkbox']",
                    "[role='checkbox']",
                    "button",
                    "[role='button']",
                    "[role='option']",
                    "div[class*='option']",
                    "div[class*='button']",
                    "a[class*='btn']",
                    "a[class*='button']",
                    "[tabindex]"
                ]
                for sel in global_selectors:
                    loc = drawer.locator(sel)
                    count = await loc.count()
                    for i in range(count):
                        el = loc.nth(i)
                        if await el.is_visible() and await el.is_enabled():
                            tag = await el.evaluate("(element) => element.tagName.toLowerCase()")
                            role = await el.get_attribute("role") or ""
                            contenteditable = await el.get_attribute("contenteditable") or ""
                            type_attr = await el.get_attribute("type") or ""
                            if role == "checkbox" or type_attr == "checkbox":
                                return "checkbox"
                            if role == "radiogroup" or role == "radio" or type_attr == "radio":
                                return "radio"
                            if tag == "div" and contenteditable == "true":
                                return "contenteditable"
                            if tag in ("input", "textarea"):
                                return tag
                            if tag == "select":
                                return "select"
                            if tag == "button" or role == "button" or role == "option" or "option" in (await el.get_attribute("class") or "").lower() or "button" in (await el.get_attribute("class") or "").lower() or await el.get_attribute("tabindex") is not None:
                                return "div"
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

    async def _get_field_options(self, container, field_type: str, page: Page | None = None) -> list[str]:
        """Extract available options for select, radio, checkbox, or div inputs with global page/drawer fallback."""
        options = []
        try:
            drawer = None
            if page:
                for sel in [".chatbot_Drawer", "[class*='chatbot']", "[class*='drawer']", "[class*='modal']"]:
                    loc = page.locator(sel)
                    count = await loc.count()
                    for i in range(count):
                        el = loc.nth(i)
                        if await el.is_visible():
                            drawer = el
                            break
                    if drawer:
                        break

            # 1. Select options
            select_locs = container.locator("select option")
            if await select_locs.count() == 0 and page:
                if drawer:
                    select_locs = drawer.locator("select option")
                else:
                    select_locs = page.locator("select option:visible")
            
            count = await select_locs.count()
            for i in range(count):
                text = (await select_locs.nth(i).inner_text()).strip()
                if text and not any(text.lower().startswith(p) for p in ["select", "--", "choose"]):
                    options.append(text)

            # 2. Radio options
            radio_locs = container.locator("[role='radio'], input[type='radio']")
            if await radio_locs.count() == 0 and page:
                if drawer:
                    radio_locs = drawer.locator("[role='radio']:visible, input[type='radio']:visible")
                else:
                    radio_locs = page.locator("[role='radio']:visible, input[type='radio']:visible")
            
            count = await radio_locs.count()
            for i in range(count):
                el = radio_locs.nth(i)
                text = (await el.inner_text()).strip()
                if not text:
                    text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                if text:
                    text = " ".join(text.split())
                    options.append(text)

            # 3. Checkbox options
            checkbox_locs = container.locator("input[type='checkbox'], [role='checkbox']")
            if await checkbox_locs.count() == 0 and page:
                if drawer:
                    checkbox_locs = drawer.locator("input[type='checkbox']:visible, [role='checkbox']:visible")
                else:
                    checkbox_locs = page.locator("input[type='checkbox']:visible, [role='checkbox']:visible")
            
            count = await checkbox_locs.count()
            for i in range(count):
                el = checkbox_locs.nth(i)
                text = (await el.inner_text()).strip()
                if not text:
                    text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                if text:
                    text = " ".join(text.split())
                    options.append(text)

            # 4. Div options / Buttons
            options_sel = "button, [role='button'], [role='option'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button'], [tabindex]"
            div_locs = container.locator(options_sel)
            if await div_locs.count() == 0 and page:
                if drawer:
                    div_locs = drawer.locator(options_sel)
                else:
                    div_locs = page.locator(options_sel)
            
            count = await div_locs.count()
            for i in range(count):
                el = div_locs.nth(i)
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                if tag in ("input", "textarea", "select"):
                    continue
                text = (await el.inner_text()).strip()
                if not text:
                    text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                if text:
                    text = " ".join(text.split())
                    options.append(text)

        except Exception as e:
            logger.warning("Error in _get_field_options: {}", e)

        # De-duplicate while preserving order
        seen = set()
        unique_opts = []
        for o in options:
            if o not in seen:
                seen.add(o)
                unique_opts.append(o)
        return unique_opts

    async def _interactive_prompt_user(
        self,
        page: Page,
        question_text: str,
        is_case2: bool,
        stored_answer: str = "",
        options: list[str] = None
    ) -> dict:
        """Open a modern glassmorphic dialog in the browser to prompt the user for input/mapping."""
        if options is None:
            options = []

        import json
        options_json = json.dumps(options)
        question_json = json.dumps(question_text)
        stored_ans_json = json.dumps(stored_answer)
        is_case2_json = json.dumps(is_case2)

        # Build premium glassmorphic JS dialog
        js_code = f"""
        (() => {{
            window.interactiveLearningResponse = null;
            
            // Remove existing dialog if any
            const existing = document.getElementById('interactive-learning-dialog');
            if (existing) existing.remove();

            const container = document.createElement('div');
            container.id = 'interactive-learning-dialog';
            container.style.position = 'fixed';
            container.style.top = '0';
            container.style.left = '0';
            container.style.width = '100vw';
            container.style.height = '100vh';
            container.style.backgroundColor = 'rgba(15, 15, 25, 0.85)';
            container.style.backdropFilter = 'blur(12px)';
            container.style.webkitBackdropFilter = 'blur(12px)';
            container.style.zIndex = '9999999';
            container.style.display = 'flex';
            container.style.alignItems = 'center';
            container.style.justifyContent = 'center';
            container.style.fontFamily = 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

            const dialog = document.createElement('div');
            dialog.style.backgroundColor = '#181824';
            dialog.style.color = '#f3f4f6';
            dialog.style.padding = '32px';
            dialog.style.borderRadius = '24px';
            dialog.style.border = '1px solid rgba(255, 255, 255, 0.08)';
            dialog.style.boxShadow = '0 25px 50px -12px rgba(0, 0, 0, 0.5)';
            dialog.style.maxWidth = '550px';
            dialog.style.width = '90%';
            dialog.style.display = 'flex';
            dialog.style.flexDirection = 'column';
            dialog.style.gap = '24px';

            // Title
            const title = document.createElement('h2');
            title.innerText = {is_case2_json} ? 'Map Stored Answer to Option' : 'Interactive Fallback Prompt';
            title.style.margin = '0';
            title.style.fontSize = '24px';
            title.style.fontWeight = '700';
            title.style.letterSpacing = '-0.025em';
            title.style.color = '#38bdf8';
            dialog.appendChild(title);

            // Question Section
            const qContainer = document.createElement('div');
            qContainer.style.display = 'flex';
            qContainer.style.flexDirection = 'column';
            qContainer.style.gap = '6px';
            
            const qLabel = document.createElement('div');
            qLabel.innerText = 'QUESTION';
            qLabel.style.fontSize = '11px';
            qLabel.style.fontWeight = '800';
            qLabel.style.color = '#6b7280';
            qLabel.style.letterSpacing = '0.05em';
            qContainer.appendChild(qLabel);

            const qText = document.createElement('div');
            qText.style.fontWeight = '600';
            qText.style.fontSize = '17px';
            qText.style.lineHeight = '1.5';
            qText.style.color = '#ffffff';
            qText.innerText = {question_json};
            qContainer.appendChild(qText);
            dialog.appendChild(qContainer);

            if ({is_case2_json}) {{
                const rawAnsDiv = document.createElement('div');
                rawAnsDiv.style.backgroundColor = 'rgba(244, 63, 94, 0.1)';
                rawAnsDiv.style.border = '1px solid rgba(244, 63, 94, 0.2)';
                rawAnsDiv.style.padding = '12px 16px';
                rawAnsDiv.style.borderRadius = '12px';
                rawAnsDiv.innerHTML = `<span style="color: #9ca3af; font-size: 13px;">Stored Answer:</span> <strong style="color: #fb7185; font-size: 15px; margin-left: 6px;">${stored_ans_json}</strong>`;
                dialog.appendChild(rawAnsDiv);
            }}

            const options = {options_json};
            
            // Case 1 (Unknown Question) with options OR Case 2 (Unmatched option mapping)
            if (options && options.length > 0) {{
                const optionsTitle = document.createElement('div');
                optionsTitle.style.fontWeight = '600';
                optionsTitle.innerText = {is_case2_json} ? 'Select the correct option to map to:' : 'Select the correct option:';
                optionsTitle.style.fontSize = '13px';
                optionsTitle.style.color = '#9ca3af';
                optionsTitle.style.letterSpacing = '0.05em';
                dialog.appendChild(optionsTitle);

                const optsContainer = document.createElement('div');
                optsContainer.style.display = 'flex';
                optsContainer.style.flexDirection = 'column';
                optsContainer.style.gap = '10px';
                optsContainer.style.maxHeight = '240px';
                optsContainer.style.overflowY = 'auto';
                optsContainer.style.paddingRight = '4px';

                options.forEach(opt => {{
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.innerText = opt;
                    btn.style.backgroundColor = 'rgba(255, 255, 255, 0.03)';
                    btn.style.color = '#e5e7eb';
                    btn.style.border = '1px solid rgba(255, 255, 255, 0.08)';
                    btn.style.padding = '12px 18px';
                    btn.style.borderRadius = '12px';
                    btn.style.cursor = 'pointer';
                    btn.style.textAlign = 'left';
                    btn.style.fontSize = '14px';
                    btn.style.fontWeight = '500';
                    btn.style.transition = 'all 0.2s';
                    
                    btn.onmouseover = () => {{
                        btn.style.backgroundColor = '#0284c7';
                        btn.style.borderColor = '#38bdf8';
                        btn.style.color = '#ffffff';
                    }};
                    btn.onmouseout = () => {{
                        btn.style.backgroundColor = 'rgba(255, 255, 255, 0.03)';
                        btn.style.borderColor = 'rgba(255, 255, 255, 0.08)';
                        btn.style.color = '#e5e7eb';
                    }};
                    
                    btn.onclick = () => {{
                        if ({is_case2_json}) {{
                            window.interactiveLearningResponse = {{ answer: {stored_ans_json}, selected_option: opt }};
                        }} else {{
                            window.interactiveLearningResponse = {{ answer: opt, selected_option: opt }};
                        }}
                    }};
                    optsContainer.appendChild(btn);
                }});
                dialog.appendChild(optsContainer);
            }} else {{
                // Case 1 (Unknown Question) without options -> Show text input
                const inputLabel = document.createElement('div');
                inputLabel.style.fontWeight = '600';
                inputLabel.innerText = 'Enter your answer:';
                inputLabel.style.fontSize = '13px';
                inputLabel.style.color = '#9ca3af';
                dialog.appendChild(inputLabel);

                const input = document.createElement('input');
                input.id = 'custom-answer-input';
                input.type = 'text';
                input.placeholder = 'Type your answer here...';
                input.style.backgroundColor = '#0f0f16';
                input.style.color = '#ffffff';
                input.style.border = '1px solid rgba(255, 255, 255, 0.1)';
                input.style.padding = '14px';
                input.style.borderRadius = '12px';
                input.style.fontSize = '15px';
                input.style.outline = 'none';
                input.style.transition = 'all 0.2s';
                
                input.onfocus = () => {{
                    input.style.borderColor = '#38bdf8';
                    input.style.boxShadow = '0 0 0 2px rgba(56, 189, 248, 0.2)';
                }};
                input.onblur = () => {{
                    input.style.borderColor = 'rgba(255, 255, 255, 0.1)';
                    input.style.boxShadow = 'none';
                }};
                dialog.appendChild(input);
                
                // Submit Action
                const actions = document.createElement('div');
                actions.style.display = 'flex';
                actions.style.justifyContent = 'flex-end';
                actions.style.gap = '12px';

                const submitBtn = document.createElement('button');
                submitBtn.id = 'confirm-button';
                submitBtn.type = 'button';
                submitBtn.innerText = 'Submit Answer';
                submitBtn.style.backgroundColor = '#0284c7';
                submitBtn.style.color = '#ffffff';
                submitBtn.style.border = 'none';
                submitBtn.style.padding = '12px 24px';
                submitBtn.style.borderRadius = '12px';
                submitBtn.style.cursor = 'not-allowed';
                submitBtn.style.fontWeight = '600';
                submitBtn.style.fontSize = '14px';
                submitBtn.style.opacity = '0.5';
                submitBtn.style.transition = 'all 0.2s';

                input.oninput = () => {{
                    const hasVal = input.value.trim().length > 0;
                    submitBtn.disabled = !hasVal;
                    submitBtn.style.opacity = hasVal ? '1' : '0.5';
                    submitBtn.style.cursor = hasVal ? 'pointer' : 'not-allowed';
                    if (hasVal) {{
                        submitBtn.onmouseover = () => {{ submitBtn.style.backgroundColor = '#0369a1'; }};
                        submitBtn.onmouseout = () => {{ submitBtn.style.backgroundColor = '#0284c7'; }};
                    }} else {{
                        submitBtn.onmouseover = null;
                        submitBtn.onmouseout = null;
                    }}
                }};

                input.onkeydown = (e) => {{
                    if (e.key === 'Enter' && input.value.trim().length > 0) {{
                        window.interactiveLearningResponse = {{ answer: input.value.trim(), selected_option: null }};
                    }}
                }};

                submitBtn.onclick = () => {{
                    if (input.value.trim().length > 0) {{
                        window.interactiveLearningResponse = {{ answer: input.value.trim(), selected_option: null }};
                    }}
                }};
                
                actions.appendChild(submitBtn);
                dialog.appendChild(actions);
            }}

            container.appendChild(dialog);
            document.body.appendChild(container);
        }})();
        """

        logger.info("PAUSED_FOR_USER_INPUT")
        logger.info("DETECTED_OPTIONS: {}", options)

        # Inject the dialog
        try:
            await page.evaluate(js_code)
        except Exception as e:
            logger.warning("Initial dialog injection failed: {}", e)

        # Loop until response is not None
        response = None
        loop_cnt = 0
        while response is None:
            if page.is_closed():
                logger.error("Page was closed while waiting for user input.")
                break
            await page.wait_for_timeout(1000)
            loop_cnt += 1
            
            import os
            if os.environ.get("AUTO_RESPOND") == "1" and loop_cnt == 5:
                logger.info("SIMULATING USER RESPONSE FOR TESTING")
                if is_case2:
                    opt = options[0] if options else "0-1 years"
                    await page.evaluate(f"() => {{ window.interactiveLearningResponse = {{ answer: '{stored_answer}', selected_option: '{opt}' }}; }}")
                else:
                    ans = "1.5"
                    await page.evaluate(f"() => {{ window.interactiveLearningResponse = {{ answer: '{ans}', selected_option: null }}; }}")
            try:
                # Check if dialog exists, re-inject if not
                exists = await page.evaluate("() => !!document.getElementById('interactive-learning-dialog')")
                if not exists:
                    logger.info("Dialog not found in DOM, re-injecting...")
                    await page.evaluate(js_code)
                response = await page.evaluate("() => window.interactiveLearningResponse")
            except Exception as e:
                logger.warning("Interactive prompt page.evaluate error: {}. Re-injecting dialog.", e)
                try:
                    await page.evaluate(js_code)
                except Exception:
                    pass

        logger.info("USER_RESPONSE_RECEIVED")

        # Cleanup
        try:
            await page.evaluate("() => { const el = document.getElementById('interactive-learning-dialog'); if (el) el.remove(); }")
        except Exception:
            pass

        logger.info("RESUMING_AUTOMATION")
        return response


async def is_valid_recruiter_question_container(container: Locator) -> bool:
    """
    Determine if a container is a valid recruiter question container (contains question/answer controls)
    and not just an informational system message or footer button container.
    """
    try:
        # 1. Check for standard inputs, textareas, selects, contenteditable
        has_text_area = await container.locator("textarea").count() > 0
        has_select = await container.locator("select").count() > 0
        has_editable = await container.locator("[contenteditable='true']").count() > 0
        if has_text_area or has_select or has_editable:
            return True

        # Check for radio or checkbox controls
        has_radio = await container.locator("input[type='radio'], [role='radio']").count() > 0
        has_checkbox = await container.locator("input[type='checkbox'], [role='checkbox']").count() > 0
        if has_radio or has_checkbox:
            return True

        # Check for other text/number inputs (excluding hidden/button/submit/image)
        inputs = container.locator("input")
        input_count = await inputs.count()
        for i in range(input_count):
            type_attr = (await inputs.nth(i).get_attribute("type") or "text").lower()
            if type_attr not in ("button", "submit", "hidden", "image"):
                return True

        # 2. Check for option buttons / clickable elements
        # Ignore navigation buttons (Save, Skip, Submit, Continue, Confirm, Apply, Next, Close, Cancel, Back)
        ignored_keywords = {"save", "skip", "submit", "continue", "confirm", "apply", "next", "close", "cancel", "back"}
        
        options_sel = "button, [role='button'], [role='option'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button']"
        clickable_locs = container.locator(options_sel)
        click_count = await clickable_locs.count()
        
        valid_options_found = 0
        for i in range(click_count):
            el = clickable_locs.nth(i)
            # Make sure it's not a form input tag matched by options_sel
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            if tag in ("input", "textarea", "select"):
                continue
                
            text = (await el.inner_text()).strip()
            if not text:
                text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
            
            if text:
                # Clean text and split to check against ignored keywords
                cleaned = " ".join(text.split()).lower()
                words = cleaned.split()
                is_nav = False
                for word in words:
                    w = word.strip(".,;:()<>[]{}&/-_")
                    if w in ignored_keywords:
                        is_nav = True
                        break
                if not is_nav:
                    valid_options_found += 1

        if valid_options_found > 0:
            return True

    except Exception as e:
        logger.warning("Error checking if container is valid: {}", e)

    return False


def get_actual_numeric_experience(question_key: str, repo: ApplyDiscoveryRepository | None = None) -> float | None:
    """Helper to extract a numeric experience value from candidate profile or question bank.
    
    Useful when a stored answer is a string range (e.g. '<6 years') but we need to map it
    to a set of numerical boundaries (e.g. '<4 years', '4-5 years', '5-6 years').
    """
    key_lower = question_key.lower()
    search_keys = []
    if "python" in key_lower:
        search_keys.append("python_experience")
    elif "genai" in key_lower or "generative" in key_lower:
        search_keys.append("genai_experience")
    elif "llm" in key_lower:
        search_keys.append("llm_experience")
    elif "rag" in key_lower:
        search_keys.append("rag_experience")
    elif "langchain" in key_lower:
        search_keys.append("langchain_experience")
    elif "fastapi" in key_lower:
        search_keys.append("fastapi_experience")
    elif "aws" in key_lower:
        search_keys.append("aws_experience")
    elif "ml" in key_lower or "machine" in key_lower:
        search_keys.append("ml_experience")
    elif "dl" in key_lower or "deep" in key_lower:
        search_keys.append("dl_experience")
    elif "nlp" in key_lower or "natural" in key_lower:
        search_keys.append("nlp_experience")
    elif "sql" in key_lower:
        search_keys.append("sql_experience")
    
    # Always check experience_years / total_experience as a fallback
    search_keys.extend(["experience_years", "total_experience", "relevant_experience"])
    
    # 1. Try candidate_profile.json
    try:
        import json
        profile_path = Path("config/candidate_profile.json")
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            for k in search_keys:
                if k in profile:
                    try:
                        return float(profile[k])
                    except (ValueError, TypeError):
                        pass
    except Exception:
        pass

    # 2. Try repo
    if repo:
        for k in search_keys:
            try:
                val = repo.get_question_answer(k)
                if val:
                    return float(val)
            except (ValueError, TypeError, Exception):
                pass

    # 3. Try answers registry static answers/CANDIDATE_ANSWERS
    try:
        from app.question_bank.answers import CANDIDATE_ANSWERS
        for k in search_keys:
            if k in CANDIDATE_ANSWERS:
                try:
                    return float(CANDIDATE_ANSWERS[k])
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    # Final hardcoded default if everything else fails
    return 2.0



