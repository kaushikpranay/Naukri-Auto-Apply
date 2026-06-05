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
                    
                question_text = await self._extract_question_label(
                    container, self._selectors.discovery.questions.text, page
                )
                if not question_text:
                    continue

                logger.info("QUESTION_LABEL_DETECTED: '{}'", question_text[:100])
                    
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

    async def _extract_question_label(
        self, container, selector_string: str, page: Page | None = None
    ) -> str:
        """Extract the actual question label text, never an option value.

        Strategy:
          1. Collect all option texts (radio labels, checkbox labels, select
             options) so we can exclude them.
          2. Try chatbot-message selectors first (div[class*='botMsg'] span,
             div[class*='botMsg']) — these hold the recruiter question.
          3. Try each selector in the configured selector string, skipping
             any text that matches a known option value.
          4. Fallback: get the container's full text and strip all option
             values from it.
        """
        # --- Step 1: gather all option texts for exclusion ---
        option_texts: set[str] = set()
        for opt_sel in [
            "input[type='radio'] + label",
            "label:has(input[type='radio'])",
            "[role='radio']",
            "input[type='checkbox'] + label",
            "label:has(input[type='checkbox'])",
            "[role='checkbox']",
            "select option",
        ]:
            try:
                locs = container.locator(opt_sel)
                cnt = await locs.count()
                for i in range(cnt):
                    t = (await locs.nth(i).inner_text()).strip()
                    if t:
                        option_texts.add(t.lower())
            except Exception:
                pass

        # Also gather option texts from the active chatbot drawer
        if page:
            drawer = await self._resolve_drawer(page)
            if drawer:
                for opt_sel in [
                    "[role='radio']",
                    "input[type='radio'] + label",
                    "[role='checkbox']",
                    "input[type='checkbox'] + label",
                    "select option",
                ]:
                    try:
                        locs = drawer.locator(opt_sel)
                        cnt = await locs.count()
                        for i in range(cnt):
                            t = (await locs.nth(i).inner_text()).strip()
                            if t:
                                option_texts.add(t.lower())
                    except Exception:
                        pass

        def _is_option_text(text: str) -> bool:
            return text.lower().strip() in option_texts

        # --- Step 2: chatbot message selectors (highest priority) ---
        chatbot_selectors = [
            "div[class*='botMsg'] span",
            "div[class*='botMsg']",
            "span[class*='question']",
            "div[class*='question-text']",
            "p[class*='question']",
        ]
        for sel in chatbot_selectors:
            try:
                item = container.locator(sel)
                if await item.count() == 0:
                    continue
                text = (await item.first.inner_text()).strip()
                if text and not _is_option_text(text):
                    return text
            except Exception:
                continue

        # --- Step 3: configured selectors, skipping option values ---
        for selector in self._split_selectors(selector_string):
            try:
                items = container.locator(selector)
                cnt = await items.count()
                for i in range(cnt):
                    text = (await items.nth(i).inner_text()).strip()
                    if text and not _is_option_text(text):
                        return text
            except Exception:
                continue

        # --- Step 4: fallback — container text minus option values ---
        try:
            full_text = (await container.inner_text()).strip()
            if full_text:
                # Remove each option text from the full text
                cleaned = full_text
                for opt in option_texts:
                    cleaned = cleaned.replace(opt, "")
                    # also case-preserved removal
                    for line in full_text.split("\n"):
                        if line.strip().lower() == opt:
                            cleaned = cleaned.replace(line.strip(), "")
                cleaned = " ".join(cleaned.split()).strip()
                if cleaned:
                    return cleaned
        except Exception:
            pass

        return ""

    async def _resolve_drawer(self, page: Page):
        """Find the visible chatbot drawer on the page."""
        for sel in [".chatbot_Drawer", "[class*='chatbot']", "[class*='drawer']", "[class*='modal']"]:
            try:
                loc = page.locator(sel)
                count = await loc.count()
                for i in range(count):
                    el = loc.nth(i)
                    if await el.is_visible():
                        return el
            except Exception:
                continue
        return None

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
        """Extract available options from radio buttons, checkboxes, and select elements only.

        Excludes:
          - Save / Submit / Continue / Skip / Cancel / Next / Close buttons
          - Parent text nodes (combined text from multiple children)
          - Containers whose text is a concatenation of other options
        """
        _EXCLUDED_WORDS = {
            "save", "submit", "skip", "continue", "next", "close",
            "cancel", "apply", "confirm", "back", "done", "ok",
        }

        options: list[str] = []
        try:
            drawer = await self._resolve_drawer(page) if page else None

            # 1. Select <option> elements
            select_locs = container.locator("select option")
            if await select_locs.count() == 0 and drawer:
                select_locs = drawer.locator("select option")

            count = await select_locs.count()
            for i in range(count):
                text = (await select_locs.nth(i).inner_text()).strip()
                if text and not any(text.lower().startswith(p) for p in ["select", "--", "choose"]):
                    options.append(text)

            # 2. Radio button labels
            radio_locs = container.locator("[role='radio'], input[type='radio']")
            if await radio_locs.count() == 0 and drawer:
                radio_locs = drawer.locator("[role='radio']:visible, input[type='radio']:visible")

            count = await radio_locs.count()
            for i in range(count):
                el = radio_locs.nth(i)
                # Prefer aria-label first (clean single-value label)
                text = (await el.get_attribute("aria-label") or "").strip()
                if not text:
                    text = (await el.inner_text()).strip()
                # Only use label sibling, NOT parentElement.innerText
                if not text:
                    try:
                        text = await el.evaluate("""el => {
                            // Check for associated <label>
                            const id = el.getAttribute('id');
                            if (id) {
                                const label = document.querySelector('label[for="' + id + '"]');
                                if (label) return label.innerText.trim();
                            }
                            // Check for wrapping <label>
                            const parent = el.closest('label');
                            if (parent) return parent.innerText.trim();
                            // Check immediate next sibling text
                            const next = el.nextElementSibling;
                            if (next && next.tagName === 'LABEL') return next.innerText.trim();
                            if (next && next.tagName === 'SPAN') return next.innerText.trim();
                            return '';
                        }""")
                    except Exception:
                        text = ""
                if text:
                    text = " ".join(text.split())
                    options.append(text)

            # 3. Checkbox labels
            checkbox_locs = container.locator("input[type='checkbox'], [role='checkbox']")
            if await checkbox_locs.count() == 0 and drawer:
                checkbox_locs = drawer.locator("input[type='checkbox']:visible, [role='checkbox']:visible")

            count = await checkbox_locs.count()
            for i in range(count):
                el = checkbox_locs.nth(i)
                text = (await el.get_attribute("aria-label") or "").strip()
                if not text:
                    text = (await el.inner_text()).strip()
                if not text:
                    try:
                        text = await el.evaluate("""el => {
                            const id = el.getAttribute('id');
                            if (id) {
                                const label = document.querySelector('label[for="' + id + '"]');
                                if (label) return label.innerText.trim();
                            }
                            const parent = el.closest('label');
                            if (parent) return parent.innerText.trim();
                            const next = el.nextElementSibling;
                            if (next && next.tagName === 'LABEL') return next.innerText.trim();
                            if (next && next.tagName === 'SPAN') return next.innerText.trim();
                            return '';
                        }""")
                    except Exception:
                        text = ""
                if text:
                    text = " ".join(text.split())
                    options.append(text)

        except Exception as e:
            logger.warning("Error in _get_field_options: {}", e)

        # --- Filtering ---
        # 1. Remove action-button texts
        filtered: list[str] = []
        for opt in options:
            cleaned_lower = opt.lower().strip()
            if cleaned_lower in _EXCLUDED_WORDS:
                continue
            # Single word that is an excluded keyword
            words = cleaned_lower.split()
            if len(words) == 1 and words[0] in _EXCLUDED_WORDS:
                continue
            filtered.append(opt)

        # 2. De-duplicate while preserving order
        seen: set[str] = set()
        unique_opts: list[str] = []
        for o in filtered:
            if o not in seen:
                seen.add(o)
                unique_opts.append(o)

        # 3. Remove combined text nodes (text that is a concatenation of ≥2 other options)
        if len(unique_opts) > 1:
            final_opts: list[str] = []
            for opt in unique_opts:
                # Check if this option's text contains all other options' text joined
                other_texts = [o for o in unique_opts if o != opt]
                is_combined = False
                if len(other_texts) >= 2:
                    # If opt contains all other option texts, it's a combined node
                    opt_lower = opt.lower()
                    matches = sum(1 for ot in other_texts if ot.lower() in opt_lower)
                    if matches >= 2 and matches == len(other_texts):
                        is_combined = True
                if not is_combined:
                    final_opts.append(opt)
            unique_opts = final_opts

        logger.info("QUESTION_OPTIONS_DETECTED: {}", unique_opts)
        return unique_opts

    async def _interactive_prompt_user(
        self,
        page: Page,
        question_text: str,
        is_case2: bool,
        stored_answer: str = "",
        options: list[str] = None
    ) -> dict:
        """Open a glassmorphic dialog and block execution until the user responds.

        Uses ``page.wait_for_function()`` with timeout=0 (infinite) so that:
          - Automation is fully suspended.
          - No polling loops or dialog re-injection.
          - Only resumes when the user clicks an option or submits text.

        A MutationObserver watches for the dialog being removed by the SPA
        and re-attaches it automatically — keeping control entirely in the
        browser so the Python side just waits on a single function call.
        """
        if options is None:
            options = []

        import json
        options_json = json.dumps(options)
        question_json = json.dumps(question_text)
        stored_ans_json = json.dumps(stored_answer)
        is_case2_json = json.dumps(is_case2)

        # Build premium glassmorphic JS dialog with self-healing MutationObserver
        js_code = f"""
        (() => {{
            // Sentinel: set to null, Python waits for non-null
            window.__ilr = null;

            // Remove existing dialog if any
            const existing = document.getElementById('interactive-learning-dialog');
            if (existing) existing.remove();

            function buildDialog() {{
                const container = document.createElement('div');
                container.id = 'interactive-learning-dialog';
                container.style.cssText = `
                    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
                    background: rgba(15, 15, 25, 0.85); backdrop-filter: blur(12px);
                    -webkit-backdrop-filter: blur(12px); z-index: 9999999;
                    display: flex; align-items: center; justify-content: center;
                    font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                `;

                const dialog = document.createElement('div');
                dialog.style.cssText = `
                    background: #181824; color: #f3f4f6; padding: 32px;
                    border-radius: 24px; border: 1px solid rgba(255,255,255,0.08);
                    box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
                    max-width: 550px; width: 90%; display: flex;
                    flex-direction: column; gap: 24px;
                `;

                // Title
                const title = document.createElement('h2');
                title.innerText = {is_case2_json} ? 'Map Stored Answer to Option' : 'Unknown Question — Your Input Needed';
                title.style.cssText = 'margin:0; font-size:24px; font-weight:700; letter-spacing:-0.025em; color:#38bdf8;';
                dialog.appendChild(title);

                // Question
                const qC = document.createElement('div');
                qC.style.cssText = 'display:flex; flex-direction:column; gap:6px;';
                const qL = document.createElement('div');
                qL.innerText = 'QUESTION';
                qL.style.cssText = 'font-size:11px; font-weight:800; color:#6b7280; letter-spacing:0.05em;';
                qC.appendChild(qL);
                const qT = document.createElement('div');
                qT.innerText = {question_json};
                qT.style.cssText = 'font-weight:600; font-size:17px; line-height:1.5; color:#fff;';
                qC.appendChild(qT);
                dialog.appendChild(qC);

                // Stored answer badge (Case 2 only)
                if ({is_case2_json}) {{
                    const rawDiv = document.createElement('div');
                    rawDiv.style.cssText = 'background:rgba(244,63,94,0.1); border:1px solid rgba(244,63,94,0.2); padding:12px 16px; border-radius:12px;';
                    rawDiv.innerHTML = '<span style="color:#9ca3af;font-size:13px;">Stored Answer:</span> <strong style="color:#fb7185;font-size:15px;margin-left:6px;">' + {stored_ans_json} + '</strong>';
                    dialog.appendChild(rawDiv);
                }}

                const opts = {options_json};

                if (opts && opts.length > 0) {{
                    const oTitle = document.createElement('div');
                    oTitle.innerText = {is_case2_json} ? 'Select the correct option to map to:' : 'Select the correct option:';
                    oTitle.style.cssText = 'font-weight:600; font-size:13px; color:#9ca3af; letter-spacing:0.05em;';
                    dialog.appendChild(oTitle);

                    const oC = document.createElement('div');
                    oC.style.cssText = 'display:flex; flex-direction:column; gap:10px; max-height:240px; overflow-y:auto; padding-right:4px;';

                    opts.forEach(opt => {{
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.innerText = opt;
                        btn.style.cssText = `
                            background: rgba(255,255,255,0.03); color: #e5e7eb;
                            border: 1px solid rgba(255,255,255,0.08); padding: 12px 18px;
                            border-radius: 12px; cursor: pointer; text-align: left;
                            font-size: 14px; font-weight: 500; transition: all 0.2s;
                        `;
                        btn.onmouseover = () => {{ btn.style.background='#0284c7'; btn.style.borderColor='#38bdf8'; btn.style.color='#fff'; }};
                        btn.onmouseout = () => {{ btn.style.background='rgba(255,255,255,0.03)'; btn.style.borderColor='rgba(255,255,255,0.08)'; btn.style.color='#e5e7eb'; }};
                        btn.onclick = () => {{
                            if ({is_case2_json}) {{
                                window.__ilr = {{ answer: {stored_ans_json}, selected_option: opt }};
                            }} else {{
                                window.__ilr = {{ answer: opt, selected_option: opt }};
                            }}
                        }};
                        oC.appendChild(btn);
                    }});
                    dialog.appendChild(oC);
                }} else {{
                    // Free-text input
                    const iL = document.createElement('div');
                    iL.innerText = 'Enter your answer:';
                    iL.style.cssText = 'font-weight:600; font-size:13px; color:#9ca3af;';
                    dialog.appendChild(iL);

                    const inp = document.createElement('input');
                    inp.id = 'custom-answer-input';
                    inp.type = 'text';
                    inp.placeholder = 'Type your answer here...';
                    inp.style.cssText = `
                        background: #0f0f16; color: #fff; border: 1px solid rgba(255,255,255,0.1);
                        padding: 14px; border-radius: 12px; font-size: 15px; outline: none; transition: all 0.2s;
                    `;
                    inp.onfocus = () => {{ inp.style.borderColor='#38bdf8'; inp.style.boxShadow='0 0 0 2px rgba(56,189,248,0.2)'; }};
                    inp.onblur = () => {{ inp.style.borderColor='rgba(255,255,255,0.1)'; inp.style.boxShadow='none'; }};
                    dialog.appendChild(inp);

                    const acts = document.createElement('div');
                    acts.style.cssText = 'display:flex; justify-content:flex-end; gap:12px;';

                    const sBtn = document.createElement('button');
                    sBtn.id = 'confirm-button';
                    sBtn.type = 'button';
                    sBtn.innerText = 'Submit Answer';
                    sBtn.style.cssText = `
                        background: #0284c7; color: #fff; border: none; padding: 12px 24px;
                        border-radius: 12px; cursor: not-allowed; font-weight: 600;
                        font-size: 14px; opacity: 0.5; transition: all 0.2s;
                    `;

                    inp.oninput = () => {{
                        const ok = inp.value.trim().length > 0;
                        sBtn.disabled = !ok;
                        sBtn.style.opacity = ok ? '1' : '0.5';
                        sBtn.style.cursor = ok ? 'pointer' : 'not-allowed';
                    }};
                    inp.onkeydown = (e) => {{
                        if (e.key === 'Enter' && inp.value.trim().length > 0)
                            window.__ilr = {{ answer: inp.value.trim(), selected_option: null }};
                    }};
                    sBtn.onclick = () => {{
                        if (inp.value.trim().length > 0)
                            window.__ilr = {{ answer: inp.value.trim(), selected_option: null }};
                    }};
                    acts.appendChild(sBtn);
                    dialog.appendChild(acts);
                }}

                container.appendChild(dialog);
                return container;
            }}

            // Inject the dialog
            const dlg = buildDialog();
            document.body.appendChild(dlg);

            // Self-healing: MutationObserver re-attaches dialog if SPA removes it
            const observer = new MutationObserver(() => {{
                if (!document.getElementById('interactive-learning-dialog') && window.__ilr === null) {{
                    const rebuilt = buildDialog();
                    document.body.appendChild(rebuilt);
                }}
            }});
            observer.observe(document.body, {{ childList: true, subtree: true }});

            // Store observer ref so cleanup can disconnect it
            window.__ilrObserver = observer;
        }})();
        """

        logger.info("PAUSED_FOR_USER_INPUT | question='{}' options={}", question_text[:80], options)

        # Inject the dialog
        try:
            await page.evaluate(js_code)
        except Exception as e:
            logger.warning("Dialog injection failed: {}", e)
            # Return a safe fallback so automation doesn't crash
            return {"answer": "", "selected_option": None}

        logger.info("POPUP_RENDERED")
        logger.info("WAITING_FOR_USER_RESPONSE — automation is fully suspended, waiting indefinitely...")

        # Block execution until user responds — NO polling, NO timeout
        try:
            await page.wait_for_function(
                "() => window.__ilr !== null",
                timeout=0,  # Wait forever
            )
        except Exception as e:
            logger.error("wait_for_function interrupted: {}", e)
            return {"answer": "", "selected_option": None}

        # Read the response
        response = await page.evaluate("() => window.__ilr")

        logger.info("USER_RESPONSE_RECEIVED: {}", response)

        # Cleanup: remove dialog and disconnect observer
        try:
            await page.evaluate("""() => {
                if (window.__ilrObserver) { window.__ilrObserver.disconnect(); window.__ilrObserver = null; }
                const el = document.getElementById('interactive-learning-dialog');
                if (el) el.remove();
                window.__ilr = null;
            }""")
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



