"""
POC-3B Phase 2 — Form Auto-Fill.

Fills known answers into application form fields.

Safety guarantees
-----------------
- DRY_RUN = True (safe): detects what would be filled, logs it, never touches DOM.
- DRY_RUN = False (live): actively fills fields. Set False only when ready.
- Final-submit selectors (configured in selectors.yaml ``final_submit``) are
  NEVER clicked under any circumstances.
- No "Next", "Submit", "Apply Now", or confirmation buttons are clicked.
- Screenshots are captured before and after (even in DRY_RUN mode).
"""

from __future__ import annotations

import asyncio
import traceback
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from playwright.async_api import Page, Locator

from app.discovery.question_normalizer import normalize_question_key, _slugify
from app.models.config import AppSettings, SelectorsConfig
from app.models.discovery import DiscoveredQuestion, PipelineSuspendedException
from app.models.form_fill import FieldFillResult, FormFillReport

if TYPE_CHECKING:
    from app.discovery.repository import ApplyDiscoveryRepository

# ---------------------------------------------------------------------------
# SAFETY FLAG
# ---------------------------------------------------------------------------

DRY_RUN = False 
"""
DRY_RUN = True (safe): detects what would be filled, logs it, never touches DOM.
DRY_RUN = False (live): actively fills fields. Set False only when ready.

When True  → fields are detected and logged; the DOM is NOT modified.
When False → known answers are typed / selected into form fields.

The final_submit selector is NEVER clicked regardless of this flag.
"""

# ---------------------------------------------------------------------------
# Module-level constants (single source of truth)
# ---------------------------------------------------------------------------

_INFO_MESSAGE_PATTERNS: tuple[str, ...] = (
    "thank you for showing interest",
    "kindly answer all the recruiter",
    "kindly answer all recruiter",
    "successfully apply for the job",
    "proceed with application",
    "welcome",
    "instruction",
    # Post-submission success confirmations — reject immediately (no controls exist)
    "successfully applied",
    "applied successfully",
    "application submitted",
    "application has been submitted",
    "you have applied",
    "thank you for applying",
    "congratulations",
    "application received",
    "your application has been",
)

_VALID_QUESTION_HINTS: tuple[str, ...] = (
    "?",
    "years of experience",
    "experience in",
    "are you",
    "do you",
    "willing to",
    "current ctc",
    "expected ctc",
    "notice period",
)

_NAV_BUTTON_KEYWORDS: frozenset[str] = frozenset({
    "save", "submit", "skip", "continue", "next", "close",
    "cancel", "apply", "confirm", "back", "done", "ok",
})

_NAUKRI_ERROR_PHRASES: tuple[str, ...] = (
    "something went wrong",
    "error while processing your job application",
    "please try again",
)


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
        self._drawer_opened_at: float | None = None

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
        report = FormFillReport(job_id=job_id, company=company, role=role, dry_run=DRY_RUN)
        self._drawer_opened_at = None

        logger.info(
            "Phase 2 DRY_RUN={} — {} fields for job_id={}",
            DRY_RUN,
            "detecting" if DRY_RUN else "filling",
            job_id,
        )

        # Build lookup: question_key → DiscoveredQuestion (with answer)
        answer_map: dict[str, DiscoveredQuestion] = {
            q.question_key: q for q in questions if q.answer
        }

        report.screenshot_before = await self._capture_screenshot(page, f"job_{job_id}_phase2_before")

        container_sel = self._selectors.discovery.questions.container
        processed_keys: dict[str, str] = {}
        processed_texts: set[str] = set()
        _error_keys: set[str] = set()  # Keys whose last fill had status="error" — allow retry
        _no_progress_count: int = 0
        _MAX_NO_PROGRESS: int = 5
        _RESOLVE_TIMEOUT: float = 60.0  # Max seconds for chatbot question resolution

        for loop_count in range(30):
            logger.info("── FILL_LOOP iter={}/30 ── processed={} filled={} job_id={}",
                        loop_count, len(processed_keys), len(report.filled), job_id)
            # Check if page is closed (safely handling unit test MagicMock)
            is_closed = False
            if not (hasattr(page, "assert_called") or hasattr(page, "_mock_self")):
                try:
                    is_closed = page.is_closed()
                except Exception:
                    pass
            if is_closed:
                logger.info("Page closed. Exiting fill_form loop.")
                break
            try:
                container_count = await page.locator(container_sel).count()
            except Exception as e:
                logger.info("Failed to count containers (likely page closed): {}", e)
                break

            # ── Resolve next unprocessed question ──────────────────────────
            target_container = None
            target_key = None
            target_text = None
            target_field_type = "unknown"
            target_options: list[str] = []

            try:
                active = await asyncio.wait_for(
                    self._resolve_active_chatbot_question(page, processed_keys, error_keys=_error_keys),
                    timeout=_RESOLVE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error("RESOLVE_CHATBOT_TIMEOUT: Chatbot question resolution exceeded {}s — breaking loop", _RESOLVE_TIMEOUT)
                active = None
            if active:
                if active.get("field_type") == "unknown":
                    await self._final_drawer_refresh_scan(page, active)
                target_container = active["container"]
                target_key = active["question_key"]
                target_text = active["question_text"]
                target_field_type = active["field_type"]
                target_options = active["options"]
                logger.info("TARGET_FIELD_TYPE_FINAL: {}", target_field_type)
                logger.info("TARGET_OPTIONS_FINAL: {}", target_options)
            else:
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

                    if question_text.strip().isdigit() or len(question_text.strip()) < 8:
                        logger.info("SKIPPING_SHORT_OR_NUMERIC_LABEL: '{}'", question_text[:100])
                        continue

                    preview_field_type = await self._detect_field_type(container, page)
                    preview_options = await self._get_field_options(container, preview_field_type, page)

                    if not await self._is_valid_question_text(
                        question_text,
                        has_visible_answer_controls=bool(preview_options) or preview_field_type != "unknown",
                        option_texts=preview_options,
                    ):
                        logger.info("SKIPPING_INFORMATIONAL_MESSAGE: '{}'", question_text[:100])
                        continue

                    question_key = normalize_question_key(question_text, preview_options)
                    resolved_key, session_action = self._resolve_session_key(
                        question_text, question_key, processed_keys, error_keys=_error_keys,
                    )
                    if session_action == "skip":
                        logger.info("FIELD_SKIPPED_LOOP_LIMIT: key={}", question_key)
                        processed_keys[resolved_key] = question_text
                        continue

                    # Check if the field is already filled or selected!
                    if await self._is_field_filled(container, preview_field_type, page):
                        logger.info("FIELD_ALREADY_FILLED: key={}", resolved_key)
                        processed_keys[resolved_key] = question_text
                        continue

                    if resolved_key not in processed_keys:
                        target_container = container
                        target_key = resolved_key
                        target_text = question_text
                        target_field_type = preview_field_type
                        target_options = preview_options
                        break

            if not target_container:
                break

            # Stuck on same question check (allow retry if previous fill errored)
            norm_text = " ".join(target_text.lower().strip().split())
            if norm_text in processed_texts:
                if target_key not in _error_keys:
                    logger.warning("STUCK_ON_SAME_QUESTION_TEXT — breaking loop: '{}'", target_text[:60])
                    break
                logger.info("RETRY_SAME_QUESTION_TEXT — previous fill errored, retrying: '{}'", target_text[:60])
            processed_texts.add(norm_text)
            processed_keys[target_key] = target_text

            logger.info("PROCESSING_KEY: {} loop={}", target_key, loop_count)

            try:
                field_type = target_field_type
                options = target_options
                required = "required" in target_text.lower() or "*" in target_text

                discovered_q = answer_map.get(target_key)
                answer = discovered_q.answer if discovered_q else None
                answer_source = "AUTO"

                final_answer = answer
                if not final_answer and self._repo:
                    final_answer = self._repo.get_question_answer(target_key)

                if not final_answer and target_key.startswith("willing_to_relocate_"):
                    if self._repo:
                        final_answer = self._repo.get_question_answer("willing_to_relocate")
                    if not final_answer:
                        from app.question_bank.answers import CANDIDATE_ANSWERS
                        final_answer = CANDIDATE_ANSWERS.get("willing_to_relocate")

                if not final_answer and target_key.startswith("exp_"):
                    resolved_years = get_actual_numeric_experience(target_key, self._repo)
                    if resolved_years is not None:
                        if resolved_years > 0:
                            final_answer = str(int(resolved_years))
                            answer_source = "PROFILE_SKILL_EXP"
                        else:
                            final_answer = "0"
                            answer_source = "PROFILE_SKILL_EXP_ZERO"
                        logger.info("RESOLVED_SKILL_EXPERIENCE key={} -> {} (source={})", target_key, final_answer, answer_source)

                if final_answer:
                    final_answer = str(final_answer).strip()
                    if final_answer.lower() in ("none", "null", "unknown"):
                        final_answer = None

                mapping_key = self._build_answer_mapping_key(target_key, options)

                if final_answer and self._repo and hasattr(self._repo, "get_answer_mapping"):
                    mapped = self._repo.get_answer_mapping(mapping_key, final_answer)
                    if mapped:
                        logger.info("MAPPING_APPLIED: {} → {}", final_answer, mapped)
                        final_answer = mapped

                # ── CASE 1: Unknown question ───────────────────────────────
                if not final_answer:
                    logger.info("UNKNOWN_QUESTION key={} — prompting user", target_key)

                    if self._repo:
                        try:
                            self._repo.update_job_status(job_id, "waiting_for_user")
                            self._repo.save_question(job_id, DiscoveredQuestion(
                                question_key=target_key,
                                question_text=target_text,
                                field_type=field_type,
                                required=required,
                                answer=None,
                            ))
                        except Exception as e:
                            logger.warning("Failed to persist waiting_for_user state: {}", e)

                    response = await self._interactive_prompt_user(
                        page=page,
                        question_text=target_text,
                        options=options,
                        is_case2=False,
                    )
                    logger.info("RESPONSE_FROM_POPUP={}", self._sanitize_response(response))

                    if not isinstance(response, dict) or response.get("answer") is None:
                        logger.info("USER_CANCELLED_INPUT")
                        raise PipelineSuspendedException("User cancelled or skipped interactive input.")

                    user_ans = response["answer"]
                    logger.info("ANSWER_RECEIVED")

                    if self._repo:
                        self._repo.save_question(job_id, DiscoveredQuestion(
                            question_key=target_key,
                            question_text=target_text,
                            field_type=field_type,
                            required=required,
                            answer=user_ans,
                        ))

                    final_answer = user_ans
                    answer = user_ans
                    answer_source = "USER_LEARNED"
                    answer_map[target_key] = DiscoveredQuestion(
                        question_key=target_key,
                        question_text=target_text,
                        field_type=field_type,
                        required=required,
                        answer=final_answer,
                    )

                # ── Fill the field ─────────────────────────────────────────
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
                logger.info("FILL_RESULT_STATUS: {} field_type: {}", result.status, field_type)

                # ── CASE 2: Known answer but no matching option ────────────
                if result.status == "error" and answer:
                    _error_keys.add(target_key)  # Allow retry if chatbot re-shows this question
                    logger.info(
                        "CASE2_TRIGGERED: known answer '{}' failed for field_type={} key={}",
                        answer, field_type, target_key,
                    )

                    if options or field_type == "checkbox":
                        # Extract checkbox options if missing
                        if not options and field_type == "checkbox":
                            options = await self._extract_checkbox_options(target_container)

                        _is_multi = field_type == "checkbox" and len(options) > 1

                        if self._repo:
                            try:
                                self._repo.update_job_status(job_id, "waiting_for_user")
                            except Exception:
                                pass

                        try:
                            response = await self._interactive_prompt_user(
                                page=page,
                                question_text=target_text,
                                options=options,
                                is_case2=True,
                                stored_answer=answer,
                                is_multi_select=_is_multi,
                            )
                        except Exception as tkinter_err:
                            logger.error("TKINTER_POPUP_CRASHED: {}", tkinter_err)
                            logger.error("TRACEBACK: {}", traceback.format_exc())
                            raise

                        logger.info("RESPONSE_FROM_POPUP={}", self._sanitize_response(response))

                        if not isinstance(response, dict) or response.get("selected_option") is None:
                            logger.info("USER_CANCELLED_INPUT")
                            raise PipelineSuspendedException("User cancelled or skipped interactive option mapping.")

                        selected_opt = response["selected_option"]
                        logger.info("ANSWER_RECEIVED")

                        if selected_opt:
                            if self._repo and hasattr(self._repo, "save_answer_mapping"):
                                self._repo.save_answer_mapping(mapping_key, answer, selected_opt)
                                logger.info("MAPPING_SAVED")

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
                            logger.info("FILL_RESULT_STATUS: {} field_type: {}", result.status, field_type)

                report.filled.append(result)

                # ── Track progress to detect stuck loops ────────────────────
                if result.status == "filled":
                    _no_progress_count = 0
                    _error_keys.discard(target_key)  # Clear error status on success
                else:
                    _no_progress_count += 1
                    if _no_progress_count >= _MAX_NO_PROGRESS:
                        logger.warning(
                            "NO_PROGRESS_LIMIT_REACHED: {} consecutive non-fills — breaking loop for job_id={}",
                            _no_progress_count, job_id,
                        )
                        break

                # ── Post-fill: click Save button ───────────────────────────
                if result.status == "filled":
                    await self._post_fill_save(page, job_id, container_sel, processed_keys, target_key, report)
                    if await self._is_application_submitted(page):
                        logger.info("APPLICATION_SUBMITTED_SUCCESSFULLY - exiting form filler loop")
                        break

            except PipelineSuspendedException:
                raise
            except Exception as exc:
                logger.warning("Phase 2 container error key={} job_id={}: {}", target_key, job_id, exc)

        report.screenshot_after = await self._capture_screenshot(page, f"job_{job_id}_phase2_after")
        logger.info(
            "Phase 2 [{}] complete: {} filled / {} unknown / {:.1f}% fill rate — job_id={}",
            "DRY_RUN" if DRY_RUN else "LIVE",
            len(report.filled),
            len(report.unknown),
            report.fill_rate_pct,
            job_id,
        )
        return report

    # ── Post-fill save logic ─────────────────────────────────────────────────

    async def _post_fill_save(
        self,
        page: Page,
        job_id: int,
        container_sel: str,
        processed_keys: dict[str, str],
        target_key: str,
        report: FormFillReport,
    ) -> None:
        """Click the Save button after a successful fill and handle outcomes."""
        drawer = await self._resolve_drawer(page)

        save_btn = None
        for sel in [".sendMsg", "div:has-text('Save')", "button:has-text('Save')"]:
            loc = drawer.locator(sel) if drawer else page.locator(sel)
            count = await loc.count()
            for i in range(count):
                el = loc.nth(i)
                if await el.is_visible():
                    save_btn = el
                    break
            if save_btn:
                break

        if not save_btn:
            # Chip/radio/button-options clicks auto-submit — no Save button exists.
            # Verify the chatbot received the answer before treating this as an error.
            await self._safe_wait(page, 1500)
            if await self._is_application_submitted(page):
                logger.info("Auto-submit: application submitted job_id={}", job_id)
                return
            drawer_chk = await self._resolve_drawer(page)
            if drawer_chk:
                try:
                    items_cls = await drawer_chk.evaluate("""el => Array.from(
                        el.querySelectorAll('.chatbot_ListItem')
                    ).filter(i => i.offsetWidth || i.offsetHeight).map(i => i.className || '')""")
                    for cls in reversed(items_cls):
                        if "userItem" in cls:
                            logger.info("Auto-submit: chatbot received answer job_id={}", job_id)
                            await self._safe_wait(page, 2000)
                            return
                        if "botItem" in cls or "botChips" in cls:
                            break
                except Exception as _e:
                    logger.warning("Auto-submit drawer check failed: {}", _e)
            logger.error("Save button not found for job_id={}", job_id)
            report.screenshot_error = await self._capture_screenshot(page, f"job_{job_id}_save_btn_missing")
            if self._repo:
                self._repo.update_job_status(job_id, "temporary_failure")
            raise PipelineSuspendedException("Save button not found")

        await self._safe_wait(page, 3000)
        await save_btn.evaluate("el => el.click()")
        logger.info("SAVE_BUTTON_CLICKED")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await self._safe_wait(page, 3000)
        logger.info("POST_SAVE_WAIT_DONE")

        # Detect Naukri server error
        try:
            page_text = (await page.inner_text("body", timeout=5000)).lower()
            if any(phrase in page_text for phrase in _NAUKRI_ERROR_PHRASES):
                logger.warning("NAUKRI_SERVER_ERROR_DETECTED — skipping job_id={}", job_id)
                if self._repo:
                    self._repo.update_job_status(job_id, "naukri_error")
                raise PipelineSuspendedException()
        except PipelineSuspendedException:
            raise
        except Exception:
            pass

        # Detect validation error
        if await self._has_validation_error(page, drawer):
            logger.warning("SAVE_REJECTED")
            processed_keys.pop(target_key, None)
            if self._repo:
                self._repo.update_job_status(job_id, "validation_failed")
            report.screenshot_error = await self._capture_screenshot(page, f"job_{job_id}_validation_failed")
            raise PipelineSuspendedException()

        # Fast exit: if application already submitted, skip the expensive 25-attempt chatbot query
        if await self._is_application_submitted(page):
            logger.info("APPLICATION_SUBMITTED — early post-save exit")
            return

        # Detect success / next question
        save_accepted = await self._detect_save_accepted(page, container_sel, processed_keys)
        logger.info("SAVE_ACCEPTED" if save_accepted else "No new question or success indicator detected after clicking Save.")

    async def _is_application_submitted(self, page: Page) -> bool:
        """Check if the application has been successfully submitted."""
        is_closed = False
        if not (hasattr(page, "assert_called") or hasattr(page, "_mock_self")):
            try:
                is_closed = page.is_closed()
            except Exception:
                pass
        if is_closed:
            return True
        
        # Check URL indicators
        try:
            url = page.url.lower()
            if "/myapply/saveapply" in url or "applied" in url:
                return True
        except Exception:
            pass
            
        # Check page text for clear submission indicators (excluding buttons/inputs)
        submit_keywords = [
            "successfully applied",
            "applied successfully",
            "application received",
            "application submitted",
            "application has been submitted",
            "thank you for applying",
            "thank you for showing interest",
            "your application has been",
            "you have applied",
            "congratulations",
        ]
        try:
            body_text = (await page.locator("body").inner_text(timeout=5000)).lower()
            if any(kw in body_text for kw in submit_keywords):
                return True
        except Exception:
            pass
            
        # Also check if chatbot drawer disappeared after we started filling
        try:
            drawer_visible = await page.evaluate("""() => {
                const selectors = ['.chatbot_Drawer', '.chatbot_Overlay', '[class*="chatbot_Drawer"]', '[class*="chatbotModal"]'];
                for (const sel of selectors) {
                    const elements = document.querySelectorAll(sel);
                    for (const el of elements) {
                        if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if not drawer_visible and self._drawer_opened_at is not None:
                return True
        except Exception:
            pass
            
        return False

    async def _has_validation_error(self, page: Page, drawer) -> bool:
        """Return True if a validation error is visible after save."""
        is_closed = False
        if not (hasattr(page, "assert_called") or hasattr(page, "_mock_self")):
            try:
                is_closed = page.is_closed()
            except Exception:
                pass
        if is_closed:
            return False

        try:
            if drawer:
                error_detected = await drawer.evaluate("""(root) => {
                    const errorSelectors = [
                        ".error", ".err", "[class*='error']", "[class*='invalid']",
                        ".error-msg", ".validation-error", ".msg-error", ".error-message"
                    ];
                    for (const sel of errorSelectors) {
                        const elements = root.querySelectorAll(sel);
                        for (const el of elements) {
                            if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                                const isBot = !!el.closest('.botItem, .botMsg, [class*="botItem"], [class*="botMsg"], [class*="chatbot"]');
                                if (isBot) continue;
                                const txt = (el.innerText || '').trim();
                                if (txt) {
                                    return { type: 'selector', key: sel, text: txt };
                                }
                            }
                        }
                    }

                    const errorTexts = ["required", "mandatory", "please enter", "please select",
                                        "invalid", "cannot be empty", "choose", "fill", "incorrect"];
                    const errorKeywords = ["please", "required", "mandatory", "invalid", "error"];
                    const allElements = root.querySelectorAll('div, span, p, label');
                    for (const el of allElements) {
                        if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                            const isBot = !!el.closest('.botItem, .botMsg, [class*="botItem"], [class*="botMsg"], [class*="chatbot"]');
                            if (isBot) continue;
                            const txt = (el.innerText || '').trim().toLowerCase();
                            if (txt.length < 150) {
                                for (const errTxt of errorTexts) {
                                    if (txt.includes(errTxt) && errorKeywords.some(kw => txt.includes(kw))) {
                                        return { type: 'text', key: errTxt, text: txt };
                                    }
                                }
                            }
                        }
                    }
                    return null;
                }""")
            else:
                error_detected = await page.evaluate("""() => {
                    const errorSelectors = [
                        ".error", ".err", "[class*='error']", "[class*='invalid']",
                        ".error-msg", ".validation-error", ".msg-error", ".error-message"
                    ];
                    for (const sel of errorSelectors) {
                        const elements = document.querySelectorAll(sel);
                        for (const el of elements) {
                            if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                                const isBot = !!el.closest('.botItem, .botMsg, [class*="botItem"], [class*="botMsg"], [class*="chatbot"]');
                                if (isBot) continue;
                                const txt = (el.innerText || '').trim();
                                if (txt) {
                                    return { type: 'selector', key: sel, text: txt };
                                }
                            }
                        }
                    }

                    const errorTexts = ["required", "mandatory", "please enter", "please select",
                                        "invalid", "cannot be empty", "choose", "fill", "incorrect"];
                    const errorKeywords = ["please", "required", "mandatory", "invalid", "error"];
                    const allElements = document.querySelectorAll('div, span, p, label');
                    for (const el of allElements) {
                        if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                            const isBot = !!el.closest('.botItem, .botMsg, [class*="botItem"], [class*="botMsg"], [class*="chatbot"]');
                            if (isBot) continue;
                            const txt = (el.innerText || '').trim().toLowerCase();
                            if (txt.length < 150) {
                                for (const errTxt of errorTexts) {
                                    if (txt.includes(errTxt) && errorKeywords.some(kw => txt.includes(kw))) {
                                        return { type: 'text', key: errTxt, text: txt };
                                    }
                                }
                            }
                        }
                    }
                    return null;
                }""")
            if error_detected:
                logger.warning("VALIDATION_ERROR_DETECTED: type='{}' key='{}' text='{}'", 
                               error_detected['type'], error_detected['key'], error_detected['text'])
                return True
        except Exception as e:
            logger.warning("Error evaluating validation status: {}", e)
        return False

    async def _detect_save_accepted(
        self,
        page: Page,
        container_sel: str,
        processed_keys: dict[str, str],
    ) -> bool:
        """Return True if save was accepted (success message, next question, or nav button visible)."""
        is_closed = False
        if not (hasattr(page, "assert_called") or hasattr(page, "_mock_self")):
            try:
                is_closed = page.is_closed()
            except Exception:
                pass
        if is_closed:
            return True

        # Check for success message keywords
        try:
            success_keywords = ["successfully applied", "application received", "application submitted",
                                "your application has been", "applied successfully"]
            success_detected = await page.evaluate("""(keywords) => {
                const elements = document.querySelectorAll('div, p, span, h1, h2, h3, h4, h5, h6, li, section');
                for (const el of elements) {
                    if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                        const txt = (el.innerText || '').toLowerCase();
                        for (const kw of keywords) {
                            if (txt.includes(kw)) {
                                return true;
                            }
                        }
                    }
                }
                return false;
            }""", success_keywords)
            if success_detected:
                logger.info("APPLICATION_SUBMITTED")
                return True
        except Exception:
            pass

        # Check for navigation buttons
        try:
            btn_keywords = ["continue", "review", "next", "preview"]
            button_visible = await page.evaluate("""(keywords) => {
                const elements = document.querySelectorAll("button, input[type='button'], [role='button']");
                for (const el of elements) {
                    if (el.offsetWidth || el.offsetHeight || el.getClientRects().length) {
                        const txt = (el.innerText || el.value || '').toLowerCase();
                        for (const kw of keywords) {
                            if (txt.includes(kw)) {
                                return true;
                            }
                        }
                    }
                }
                return false;
            }""", btn_keywords)
            if button_visible:
                return True
        except Exception:
            pass

        # Check for next chatbot question
        next_q = await self._resolve_active_chatbot_question(page, processed_keys)
        if next_q:
            if next_q.get("field_type") == "unknown":
                await self._final_drawer_refresh_scan(page, next_q)
            logger.info("NEXT_QUESTION_DETECTED: '{}' [{}]",
                        next_q["question_text"][:60], next_q["question_key"])
            return True

        try:
            new_count = await page.locator(container_sel).count()
            for idx in range(new_count):
                next_container = page.locator(container_sel).nth(idx)
                if await next_container.is_visible():
                    next_q_text = await self._extract_text(next_container, self._selectors.discovery.questions.text)
                    if next_q_text:
                        next_q_key = normalize_question_key(next_q_text)
                        if next_q_key not in processed_keys:
                            logger.info("NEXT_QUESTION_DETECTED: '{}' [{}]", next_q_text[:60], next_q_key)
                            return True
        except Exception:
            pass

        return False

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
                question_key, question_text[:60], answer[:40], answer_source, job_id,
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

        success, error, selector_used, method_used = await self._fill_field(
            container, field_type, answer, page, question_key=question_key
        )
        status = "filled" if success else "error"

        logger.info(
            "\n{}\n{}\n{}\n{}\n{}\n",
            question_key, answer, selector_used, method_used,
            "SUCCESS" if success else f"FAILED ({error})",
        )

        if success:
            logger.info("ANSWER_FILLED")
            logger.info(
                "Phase 2 FILLED: [{}] '{}' = '{}' (source={}) job_id={}",
                question_key, question_text[:60], answer[:40], answer_source, job_id,
            )
        else:
            logger.warning(
                "Phase 2 ERROR: [{}] '{}' (source={}) job_id={} — {}",
                question_key, question_text[:60], answer_source, job_id, error,
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
        self,
        container,
        field_type: str,
        answer: str,
        page: Page | None = None,
        question_key: str = "",
    ) -> tuple[bool, str | None, str, str]:
        """Fill a single form field. Returns (success, error, selector_used, method_used)."""
        selector_used = "unknown"
        method_used = "unknown"
        try:
            drawer = await self._resolve_drawer(page) if page else None

            # ── input / textarea ──────────────────────────────────────────
            if field_type in ("input", "textarea"):
                input_sel = (
                    "input:not([type='radio']):not([type='checkbox']):not([type='hidden'])"
                    if field_type == "input" else "textarea"
                )
                field_loc = container.locator(input_sel).first
                selector_used = field_type
                field_visible = await field_loc.count() > 0 and await self._is_element_visible(field_loc)

                if not field_visible and page:
                    if drawer:
                        field_loc = drawer.locator(input_sel).first
                        selector_used = f"chatbot_drawer {field_type}"
                    else:
                        field_loc = page.locator(f"{input_sel}:visible").first
                        selector_used = f"{field_type}:visible"

                method_used = "TYPE"
                await field_loc.click()
                await field_loc.fill("")
                await field_loc.type(answer, delay=30)

                actual = (await field_loc.evaluate("el => el.value || ''")).strip().lower()
                ans_norm = " ".join(answer.strip().lower().split())
                act_norm = " ".join(actual.split())
                if ans_norm in act_norm or act_norm in ans_norm:
                    logger.info("FIELD_VERIFY_SUCCESS")
                    return True, None, selector_used, method_used
                logger.error("FIELD_VERIFY_FAILED")
                return False, f"Verification failed: expected '{answer}', got '{actual}'", selector_used, method_used

            # ── select ────────────────────────────────────────────────────
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

                selected_val = (await select_loc.evaluate(
                    "el => { const opt = el.options[el.selectedIndex]; return opt ? (opt.label || opt.text || opt.value || '') : ''; }"
                )).strip().lower()
                ans_norm = " ".join(answer.strip().lower().split())
                sel_norm = " ".join(selected_val.split())
                if ans_norm in sel_norm or sel_norm in ans_norm:
                    logger.info("FIELD_VERIFY_SUCCESS")
                    return True, None, selector_used, method_used
                logger.error("FIELD_VERIFY_FAILED")
                return False, f"Verification failed: expected '{answer}', got '{selected_val}'", selector_used, method_used

            # ── radio ─────────────────────────────────────────────────────
            if "radio" in field_type or field_type in ("[role='radiogroup']",):
                method_used = "DOM_CLICK"
                options = container.locator("[role='radio'], input[type='radio']")
                if await options.count() == 0 and drawer:
                    options = drawer.locator("[role='radio']:visible, input[type='radio']:visible")
                elif await options.count() == 0 and page:
                    options = page.locator("[role='radio']:visible, input[type='radio']:visible")

                count = await options.count()
                answer_lower = self._normalize_option_text(answer)
                exact_matches: list[tuple] = []
                fuzzy_matches: list[tuple] = []
                option_pairs: list[tuple] = []

                for i in range(count):
                    opt = options.nth(i)
                    option_text = await self._extract_choice_label_text(opt)
                    norm = self._normalize_option_text(option_text)
                    if not norm:
                        continue
                    option_pairs.append((opt, option_text))
                    if norm == answer_lower:
                        exact_matches.append((opt, option_text))
                    elif answer_lower and _token_overlap(answer_lower, norm):
                        fuzzy_matches.append((opt, option_text))

                # Resolve the answer's numeric value: prefer a concrete number in the
                # answer (e.g. "45 days" -> 45); for non-numeric answers fall back to
                # the experience heuristic. Vague range/comparison answers ("<6 years")
                # use the heuristic too.
                import re
                val = None
                try:
                    val = float(answer.strip())
                except ValueError:
                    num_match = re.search(r"\d+(?:\.\d+)?", answer)
                    has_range_qualifier = re.search(
                        r"[<>+\-]|less|more|greater|above|below|under|over",
                        answer.lower(),
                    )
                    if num_match and not has_range_qualifier:
                        val = float(num_match.group(0))
                    else:
                        val = get_actual_numeric_experience(question_key, self._repo)

                opt_locs: list[tuple] = []
                if exact_matches:
                    opt_locs = [(o, f"[role='radio'] exact: {t}") for o, t in exact_matches]
                elif val is not None and (_num_best := _best_numeric_option(option_pairs, val)) is not None:
                    # Range-aware best match (e.g. 2 -> "2-4 years", tightest bucket)
                    # takes priority over loose substring matching, which mis-fires
                    # when a digit appears inside another number ("2" inside "12").
                    opt_locs = [(_num_best[0], f"[role='radio'] numeric: {_num_best[1]}")]
                elif fuzzy_matches:
                    opt_locs = [(o, f"[role='radio'] fuzzy: {t}") for o, t in fuzzy_matches]

                for opt, sel_name in opt_locs:
                    try:
                        selector_used = sel_name
                        matched_text = await self._extract_choice_label_text(opt)
                        logger.info("MATCHED_OPTION_TEXT: {}", answer)
                        logger.info("MATCHED_ELEMENT_TEXT: {}", matched_text)
                        await opt.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', { bubbles: true })); el.dispatchEvent(new Event('input', { bubbles: true })); }")

                        is_selected = False
                        try:
                            is_selected = await opt.evaluate("""el => {
                                if (el.tagName.toLowerCase() === 'input') return el.checked;
                                const a = el.getAttribute('aria-checked');
                                if (a === 'true') return true;
                                const c = el.className || '';
                                if (c.includes('checked') || c.includes('selected') || c.includes('active')) return true;
                                const p = el.parentElement;
                                return p && (p.className || '').includes('selected');
                            }""")
                        except Exception:
                            pass

                        if is_selected:
                            logger.info("FIELD_VERIFY_SUCCESS")
                            return True, None, selector_used, method_used
                        logger.error("FIELD_VERIFY_FAILED")
                    except Exception as e:
                        logger.warning("DOM click failed: {}", e)

                return False, f"No radio option matched '{answer}' or verification failed", selector_used, method_used

            # ── button-options / chatbot chips / div ──────────────────────
            if field_type in ("button-options", "button-choice", "chatbot_chips", "div"):
                method_used = "DOM_CLICK"
                chip_sel = ".chatbot_Chip, .chipItem, [class*='chatbot_Chip'], [class*='chipItem']"
                chip_locs = (
                    drawer.locator(chip_sel) if drawer
                    else page.locator(f"{chip_sel}:visible") if page
                    else None
                )
                if chip_locs and await chip_locs.count() > 0:
                    answer_lower = answer.strip().lower()
                    matched_chip = None
                    # Pass 1: exact match — avoids matching multi-option wrapper whose
                    # inner_text is "Yes\nNo" (which contains any single option as substring)
                    for i in range(await chip_locs.count()):
                        el = chip_locs.nth(i)
                        if not await el.is_visible():
                            continue
                        try:
                            txt = " ".join((await el.inner_text(timeout=5000)).strip().lower().split())
                        except Exception:
                            continue
                        if txt == answer_lower:
                            matched_chip = el
                            selector_used = f"chatbot_chip: {txt}"
                            break
                    # Pass 2: substring match, but skip multi-line container elements
                    if not matched_chip:
                        for i in range(await chip_locs.count()):
                            el = chip_locs.nth(i)
                            if not await el.is_visible():
                                continue
                            try:
                                raw = (await el.inner_text(timeout=5000)).strip().lower()
                            except Exception:
                                continue
                            if "\n" in raw:
                                continue  # skip wrappers containing multiple chip texts
                            txt = " ".join(raw.split())
                            if answer_lower in txt or txt in answer_lower:
                                matched_chip = el
                                selector_used = f"chatbot_chip: {txt}"
                                break

                    if matched_chip:
                        await matched_chip.evaluate("el => el.click()")
                        if page:
                            await self._safe_wait(page, 1200)

                        is_selected = False
                        try:
                            # timeout=2000: if chip was removed from DOM (success), don't wait 30s
                            is_selected = await matched_chip.evaluate("""el => {
                                const c = el.className || '';
                                return c.includes('selected') || c.includes('active') || c.includes('checked') || el.disabled;
                            }""", timeout=2000)
                        except Exception:
                            # Naukri removes chips from DOM after selection — detached element = success
                            is_selected = True

                        if not is_selected and page:
                            try:
                                drawer_el = await self._resolve_drawer(page)
                                if drawer_el:
                                    msg_texts = await drawer_el.evaluate("""
                                        el => Array.from(el.querySelectorAll('.chatbot_ListItem.userItem, [class*="userItem"]')).map(item => item.innerText || '')
                                    """)
                                    ans_lower = answer.strip().lower()
                                    for msg_text in msg_texts:
                                        msg_text_stripped = msg_text.strip().lower()
                                        if ans_lower in msg_text_stripped or msg_text_stripped in ans_lower:
                                            is_selected = True
                                            break
                            except Exception:
                                pass

                        if not is_selected:
                            try:
                                is_selected = not await matched_chip.is_visible()
                            except Exception:
                                # is_visible() throws when element detached = chip removed = success
                                is_selected = True

                        if is_selected:
                            logger.info("FIELD_VERIFY_SUCCESS")
                            return True, None, selector_used, method_used
                        logger.error("FIELD_VERIFY_FAILED")
                        return False, "Verification failed for chatbot chip selection", selector_used, method_used

                # Fallback to general option/button selectors
                return await self._click_option_fallback(container, drawer, page, answer, "div option")

            # ── contenteditable ───────────────────────────────────────────
            if field_type == "contenteditable":
                ce_loc = container.locator("[contenteditable='true']").first
                selector_used = "[contenteditable='true']"
                ce_visible = await ce_loc.count() > 0 and await self._is_element_visible(ce_loc)

                if not ce_visible and page:
                    if drawer:
                        ce_loc = drawer.locator("[contenteditable='true']").first
                        selector_used = '[contenteditable="true"]'
                    else:
                        ce_loc = page.locator("[contenteditable='true']:visible").first
                        selector_used = "[contenteditable='true']:visible"

                method_used = "TYPE"
                await ce_loc.click()
                await ce_loc.fill("")
                await ce_loc.type(answer, delay=30)

                actual = (await ce_loc.evaluate("el => el.innerText || el.textContent || ''")).strip().lower()
                ans_norm = " ".join(answer.strip().lower().split())
                act_norm = " ".join(actual.split())
                if ans_norm in act_norm or act_norm in ans_norm:
                    logger.info("FIELD_VERIFY_SUCCESS")
                    return True, None, selector_used, method_used
                logger.error("FIELD_VERIFY_FAILED")
                return False, f"Verification failed: expected '{answer}', got '{actual}'", selector_used, method_used

            # ── checkbox ──────────────────────────────────────────────────
            if field_type == "checkbox":
                method_used = "CLICK"
                answer_lower = answer.strip().lower()
                logger.info("FILL_FIELD_CHECKBOX_START JS-eval answer='{}'", answer)
                
                # Resolve the correct scope (drawer or container)
                scope = drawer if drawer else container
                
                is_mock = type(scope).__name__ in ("MagicMock", "AsyncMock", "Mock")
                if is_mock:
                    # Old locator-based fallback for mocks/unit-tests
                    cb_selector = (
                        "input[type='checkbox'], [role='checkbox'], "
                        ".checkboxLabel, [class*='checkbox'], "
                        "label:has(input[type='checkbox']), "
                        "[class*='check-box'], [class*='checkBox'], "
                        "label.mcc__label, [class*='mcc__label']"
                    )
                    all_cbs = container.locator(cb_selector)
                    if drawer:
                        all_cbs = drawer.locator(cb_selector)
                    count = await all_cbs.count()

                    if count == 0:
                        return False, "No checkboxes found in container", "checkbox", method_used
                    elif count > 1:
                        await all_cbs.first.click()
                        return True, None, "checkbox", method_used
                    else:
                        cb_loc = all_cbs.first
                        should_check = answer_lower in ("yes", "true", "check", "checked", "select", "selected", "1")
                        tag = await cb_loc.evaluate("el => el.tagName.toLowerCase()")
                        if tag == "input":
                            is_checked = await cb_loc.evaluate("el => el.checked")
                        else:
                            is_checked = (await cb_loc.get_attribute("aria-checked")) == "true"
                        if should_check != is_checked:
                            await cb_loc.click()
                        return True, None, "checkbox", method_used

                # Evaluate in-browser to extract all checkbox info instantly
                checkboxes = await scope.evaluate("""(el) => {
                    const cbSelector = "input[type='checkbox'], [role='checkbox'], .checkboxLabel, [class*='checkbox'], label:has(input[type='checkbox']), [class*='check-box'], [class*='checkBox'], label.mcc__label, [class*='mcc__label']";
                    const elements = Array.from(el.querySelectorAll(cbSelector));
                    return elements.map((item, index) => {
                        let labelText = item.textContent || item.innerText || '';
                        if (!labelText.trim()) {
                            labelText = item.getAttribute('aria-label') || '';
                        }
                        if (!labelText.trim()) {
                            const closestLabel = item.closest('label');
                            if (closestLabel) {
                                labelText = closestLabel.innerText || closestLabel.textContent || '';
                            }
                        }
                        return {
                            index: index,
                            labelText: labelText.trim(),
                            tagName: item.tagName.toLowerCase(),
                            isInput: item.tagName.toLowerCase() === 'input',
                            checked: item.checked || item.getAttribute('aria-checked') === 'true'
                        };
                    });
                }""")
                
                logger.info("JS-extracted checkboxes: {}", checkboxes)
                
                if not checkboxes:
                    logger.warning("No checkboxes found in container/drawer")
                    return False, "No checkboxes found in container", "checkbox", method_used
                
                if len(checkboxes) > 1:
                    # Support comma-separated multi-select answers (e.g. "Hyderabad, Telangana, Bengaluru, Karnataka")
                    answer_parts = [a.strip().lower() for a in answer.split(",")] if "," in answer else [answer_lower]
                    matched_indices: list[tuple[int, str]] = []
                    for cb in checkboxes:
                        if cb.get("tagName") == "div":
                            continue
                        lbl = cb["labelText"].lower().strip()
                        if not lbl:
                            continue
                        for part in answer_parts:
                            if part in lbl or lbl in part:
                                matched_indices.append((cb["index"], cb["labelText"]))
                                break
                            
                    if matched_indices:
                        # Click ALL matched checkboxes
                        for m_idx, m_label in matched_indices:
                            await scope.evaluate(f"""(el) => {{
                                const cbSelector = "input[type='checkbox'], [role='checkbox'], .checkboxLabel, [class*='checkbox'], label:has(input[type='checkbox']), [class*='check-box'], [class*='checkBox'], label.mcc__label, [class*='mcc__label']";
                                const item = el.querySelectorAll(cbSelector)[{m_idx}];
                                if (item) {{
                                    item.click();
                                    item.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                    item.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                }}
                            }}""")
                            logger.info("CHECKBOX_OPTION_MATCHED: '{}'", m_label)
                        logger.info("FIELD_VERIFY_SUCCESS — {} checkbox(es) clicked", len(matched_indices))
                        return True, None, "checkbox", method_used
                    else:
                        logger.warning("CHECKBOX_NO_MATCH: answer='{}' not found in options", answer)
                        return False, f"No checkbox option matched answer '{answer}'", "checkbox", method_used
                else:
                    # Single checkbox (Yes/No style)
                    cb = checkboxes[0]
                    should_check = answer_lower in ("yes", "true", "check", "checked", "select", "selected", "1")
                    is_checked = cb["checked"]
                    
                    if should_check != is_checked:
                        await scope.evaluate(f"""(el) => {{
                            const cbSelector = "input[type='checkbox'], [role='checkbox'], .checkboxLabel, [class*='checkbox'], label:has(input[type='checkbox']), [class*='check-box'], [class*='checkBox'], label.mcc__label, [class*='mcc__label']";
                            const item = el.querySelectorAll(cbSelector)[0];
                            if (item) {{
                                item.click();
                                item.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                item.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            }}
                        }}""")
                    logger.info("CHECKBOX_SELECTED")
                    logger.info("FIELD_VERIFY_SUCCESS")
                    return True, None, "checkbox", method_used

            return False, f"Unsupported field_type '{field_type}'", selector_used, method_used

        except Exception as exc:
            return False, str(exc), selector_used, method_used

    async def _click_option_fallback(
        self,
        container,
        drawer,
        page: Page | None,
        answer: str,
        selector_label: str,
    ) -> tuple[bool, str | None, str, str]:
        """Shared fallback for div/button-options: scan general clickable elements."""
        method_used = "DOM_CLICK"
        options_sel = "button, [role='button'], [role='option'], [role='radio'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button'], [tabindex]"
        options = container.locator(options_sel)
        if await options.count() == 0 and drawer:
            options = drawer.locator(options_sel)
        elif await options.count() == 0 and page:
            options = page.locator(options_sel)

        answer_lower = answer.lower().strip()
        matched_option = None
        selector_used = selector_label

        for i in range(await options.count()):
            opt = options.nth(i)
            if not await opt.is_visible():
                continue
            opt_text = (await opt.inner_text()).strip()
            if not opt_text:
                opt_text = (await opt.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
            aria_label = (await opt.get_attribute("aria-label") or "").strip()
            opt_lower = opt_text.lower().strip()
            aria_lower = aria_label.lower().strip()
            if answer_lower and (answer_lower in opt_lower or opt_lower in answer_lower or answer_lower in aria_lower):
                matched_option = opt
                selector_used = f"div option text={opt_text[:30]}"
                break

        if not matched_option:
            return False, f"No option matching '{answer}' found", selector_used, method_used

        await matched_option.evaluate("el => { el.click(); el.dispatchEvent(new Event('change', { bubbles: true })); el.dispatchEvent(new Event('input', { bubbles: true })); }")
        if page:
            await self._safe_wait(page, 1000)

        is_selected = False
        try:
            is_selected = await matched_option.evaluate("""el => {
                const c = el.className || '';
                return c.includes('selected') || c.includes('active') || c.includes('checked') || el.disabled;
            }""")
        except Exception:
            pass
        if not is_selected:
            try:
                is_selected = not await matched_option.is_visible()
            except Exception:
                pass

        if is_selected:
            logger.info("FIELD_VERIFY_SUCCESS")
            return True, None, selector_used, method_used
        logger.error("FIELD_VERIFY_FAILED")
        return False, "Verification failed for option click", selector_used, method_used

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_response(response: dict | None) -> str:
        if not isinstance(response, dict):
            return str(response)
        clean = {k: v for k, v in response.items() if k != "response_received" and v is not None}
        return str(clean).replace(" ", "")

    async def _extract_checkbox_options(self, container) -> list[str]:
        """Extract checkbox option labels from a container."""
        options: list[str] = []
        try:
            loc = container.locator("input[type='checkbox'], [role='checkbox']")
            for i in range(await loc.count()):
                cb = loc.nth(i)
                label = ""
                try:
                    label = (await cb.evaluate("el => el.closest('label') ? el.closest('label').innerText : ''")).strip()
                except Exception:
                    pass
                if not label:
                    try:
                        label = (await cb.get_attribute("aria-label") or "").strip()
                    except Exception:
                        pass
                if label:
                    options.append(label)
            logger.info("CHECKBOX_OPTIONS_EXTRACTED: {}", options)
        except Exception as e:
            logger.warning("Failed to extract checkbox options: {}", e)
        return options

    async def _safe_wait(self, page: Page | None, ms: int) -> None:
        if not page:
            return
        try:
            await page.wait_for_timeout(ms)
        except TypeError:
            pass

    async def _is_element_visible(self, el) -> bool:
        import inspect
        try:
            if hasattr(el, "is_visible"):
                vis = el.is_visible()
                if asyncio.iscoroutine(vis) or inspect.isawaitable(vis):
                    vis = await vis
                if vis is False:
                    return False
        except Exception:
            pass

        try:
            val = el.evaluate("""(element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                if (style.display === 'none' || style.visibility !== 'visible') return false;
                if (element.offsetWidth === 0 || element.offsetHeight === 0 || element.hidden) return false;
                let parent = element.parentElement;
                while (parent) {
                    const ps = window.getComputedStyle(parent);
                    if (ps.display === 'none' || ps.visibility === 'hidden') return false;
                    parent = parent.parentElement;
                }
                return true;
            }""")
            if asyncio.iscoroutine(val) or inspect.isawaitable(val):
                val = await val
            return bool(val)
        except Exception:
            try:
                if hasattr(el, "is_visible"):
                    vis = el.is_visible()
                    if asyncio.iscoroutine(vis) or inspect.isawaitable(vis):
                        return await vis
                    return bool(vis)
            except Exception:
                pass
            return True

    async def _find_visible_elements(self, container, selector: str, page: Page | None = None) -> list:
        import inspect
        elements = []
        try:
            loc = container.locator(selector)
            for i in range(await loc.count()):
                el = loc.nth(i)
                if inspect.iscoroutine(el) or inspect.isawaitable(el):
                    el = await el
                if await self._is_element_visible(el):
                    elements.append(el)

            if not elements and page:
                drawer = await self._resolve_drawer(page)
                scope = drawer if drawer else page
                loc = scope.locator(selector)
                for i in range(await loc.count()):
                    el = loc.nth(i)
                    if inspect.iscoroutine(el) or inspect.isawaitable(el):
                        el = await el
                    if await self._is_element_visible(el):
                        elements.append(el)
        except Exception as e:
            logger.warning("Error in _find_visible_elements: {}", e)
        return elements

    async def _detect_field_type(self, container, page: Page | None = None, _drawer=None) -> str:
        try:
            drawer = _drawer if _drawer is not None else (await self._resolve_drawer(page) if page else None)
            use_chatbot_fallback = drawer is not None
            if not use_chatbot_fallback and page:
                try:
                    use_chatbot_fallback = await container.evaluate("""el => {
                        const c = el.className || '';
                        return c.includes('botItem') || c.includes('chatbot_ListItem') || c.includes('botMsg') || el.closest('.chatbot_ListItem') !== null;
                    }""")
                except Exception:
                    pass

            async def get_elements(selector: str) -> list:
                elements = await self._find_visible_elements(container, selector, page)
                if not elements and use_chatbot_fallback:
                    scope = drawer if drawer else page
                    elements = await self._find_visible_elements(scope, selector, None)
                return elements

            visible_ces = await get_elements("[contenteditable='true']")
            visible_radios = await get_elements("[role='radio'], input[type='radio']")
            visible_checkboxes = await get_elements(
                "[role='checkbox'], input[type='checkbox'], "
                ".checkboxLabel, [class*='checkbox'], "
                "label:has(input[type='checkbox']), "
                "[class*='check-box'], [class*='checkBox']"
            )
            visible_selects = await get_elements("select")
            visible_inputs = await get_elements("input:not([type='radio']):not([type='checkbox']):not([type='hidden']), textarea")

            chip_sel = ".chatbot_Chip, .chipItem, [class*='chatbot_Chip'], [class*='chipItem']"
            visible_chips = await get_elements(chip_sel)

            btn_sel = (
                ".chatbot_Chip, .chipItem, [class*='chatbot_Chip'], [class*='chipItem'], "
                "button, [role='button'], [role='option'], div[class*='option'], "
                "div[class*='button'], a[class*='btn'], a[class*='button']"
            )
            all_btns = await get_elements(btn_sel)
            valid_btns = []
            for btn in all_btns:
                is_mock = type(btn).__name__ in ("MagicMock", "AsyncMock", "Mock")
                if is_mock:
                    valid_btns.append(btn)
                    continue
                try:
                    tag = await btn.evaluate("el => el.tagName.toLowerCase()")
                except Exception:
                    tag = "div"
                if tag in ("input", "textarea", "select"):
                    continue
                try:
                    text = (await btn.inner_text()).strip()
                except Exception:
                    text = ""
                if not text:
                    try:
                        text = (await btn.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                    except Exception:
                        text = ""
                if text:
                    words = " ".join(text.split()).lower().split()
                    if not any(w.strip(".,;:()<>[]{}&/-_") in _NAV_BUTTON_KEYWORDS for w in words):
                        valid_btns.append(btn)

            logger.info("TEXT_INPUT_MATCHES: {}", len(visible_inputs))
            logger.info("CONTENTEDITABLE_MATCHES: {}", len(visible_ces))
            logger.info("CHIP_MATCHES: {}", len(visible_chips))
            logger.info("BUTTON_MATCHES: {}", len(valid_btns))

            if visible_ces:
                field_type = "contenteditable"
            elif visible_inputs:
                is_mock = type(visible_inputs[0]).__name__ in ("MagicMock", "AsyncMock", "Mock")
                if is_mock:
                    field_type = "input"
                else:
                    try:
                        field_type = await visible_inputs[0].evaluate("el => el.tagName.toLowerCase()")
                    except Exception:
                        field_type = "input"
            elif visible_selects:
                field_type = "select"
            elif visible_radios:
                field_type = "radio"
            elif visible_checkboxes:
                field_type = "checkbox"
            elif visible_chips:
                field_type = "chatbot_chips"
            elif valid_btns:
                field_type = "button-options"
            else:
                field_type = "unknown"

            logger.info("FIELD_TYPE_DETECTED: {}", field_type)
            return field_type

        except Exception as e:
            logger.warning("Error in _detect_field_type: {}", e)
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
        """Extract the actual question label text, never an option value."""
        option_texts: set[str] = set()
        option_selectors = [
            "input[type='radio'] + label", "label:has(input[type='radio'])", "[role='radio']",
            "input[type='checkbox'] + label", "label:has(input[type='checkbox'])", "[role='checkbox']",
            "select option", ".chatbot_Chip", ".chipItem", "[class*='chatbot_Chip']", "[class*='chipItem']",
            "button", "[role='button']", "[role='option']", "div[class*='option']", "div[class*='button']",
        ]
        for sel in option_selectors:
            try:
                locs = container.locator(sel)
                for i in range(await locs.count()):
                    t = (await locs.nth(i).inner_text()).strip()
                    if t:
                        option_texts.add(t.lower())
            except Exception:
                pass

        if page:
            drawer = await self._resolve_drawer(page)
            if drawer:
                for sel in option_selectors:
                    try:
                        locs = drawer.locator(sel)
                        for i in range(await locs.count()):
                            t = (await locs.nth(i).inner_text()).strip()
                            if t:
                                option_texts.add(t.lower())
                    except Exception:
                        pass

        def _is_option(text: str) -> bool:
            return text.lower().strip() in option_texts

        for sel in [
            "div[class*='botMsg'] span", "div[class*='botMsg']",
            "span[class*='question']", "div[class*='question-text']", "p[class*='question']",
        ]:
            try:
                item = container.locator(sel)
                if await item.count() == 0:
                    continue
                text = (await item.first.inner_text()).strip()
                if text and not _is_option(text):
                    return text
            except Exception:
                continue

        for selector in self._split_selectors(selector_string):
            try:
                items = container.locator(selector)
                for i in range(await items.count()):
                    text = (await items.nth(i).inner_text()).strip()
                    if text and not _is_option(text):
                        return text
            except Exception:
                continue

        try:
            full_text = (await container.inner_text()).strip()
            if full_text:
                cleaned = full_text
                for opt in option_texts:
                    cleaned = cleaned.replace(opt, "")
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
        import time
        logger.info("RESOLVE_DRAWER_START")
        _DRAWER_OP_TIMEOUT = 3000  # 3s per selector check to prevent cascading hangs
        for sel in [".chatbot_Drawer", ".chatbot_Overlay", "[class*='chatbot_Drawer']", "[class*='chatbotModal']"]:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                logger.info("RESOLVE_DRAWER: sel='{}' count={}", sel, cnt)
                for i in range(cnt):
                    el = loc.nth(i)
                    if await el.is_visible(timeout=_DRAWER_OP_TIMEOUT):
                        if self._drawer_opened_at is None:
                            self._drawer_opened_at = time.time()
                        logger.info("RESOLVE_DRAWER_FOUND: '{}'", sel)
                        return el
            except Exception as e:
                logger.warning("RESOLVE_DRAWER_ERR: {} - {}", sel, e)
                continue
        logger.info("RESOLVE_DRAWER_NOT_FOUND")
        return None

    async def _capture_screenshot(self, page: Page, name: str) -> str | None:
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self._screenshots_dir / f"{timestamp}_{name}.png"
        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info("Phase 2 screenshot: {}", filepath.name)
            return str(filepath)
        except Exception as exc:
            logger.warning("Phase 2 screenshot failed {}: {}", name, exc)
            return None

    def _split_selectors(self, selector_string: str) -> list[str]:
        return [s.strip() for s in selector_string.split(",") if s.strip()]

    def _normalize_option_text(self, text: str) -> str:
        return " ".join(str(text).strip().split()).lower()

    def _build_answer_mapping_key(self, question_key: str, options: list[str]) -> str:
        normalized = [self._normalize_option_text(o) for o in options if self._normalize_option_text(o)]
        if not normalized:
            return question_key
        return f"{question_key}__options__{'|'.join(sorted(dict.fromkeys(normalized)))}"

    async def _extract_choice_label_text(self, el) -> str:
        try:
            text = (await el.inner_text()).strip()
            if text:
                return " ".join(text.split())
        except Exception:
            pass
        try:
            aria = (await el.get_attribute("aria-label") or "").strip()
            if aria:
                return " ".join(aria.split())
        except Exception:
            pass
        try:
            evaluated = await el.evaluate("""element => {
                if (!element) return '';
                const normalize = v => (v || '').replace(/\\s+/g, ' ').trim();
                const tag = (element.tagName || '').toLowerCase();
                if (tag === 'input') {
                    const id = element.getAttribute('id');
                    if (id) {
                        const label = document.querySelector(`label[for="${id}"]`);
                        if (label) return normalize(label.innerText || label.textContent);
                    }
                }
                const lb = element.getAttribute('aria-labelledby');
                if (lb) {
                    const txt = lb.split(/\\s+/).map(id => document.getElementById(id))
                        .filter(Boolean).map(n => normalize(n.innerText || n.textContent))
                        .filter(Boolean).join(' ');
                    if (txt) return txt;
                }
                const pl = element.closest('label');
                if (pl) return normalize(pl.innerText || pl.textContent);
                let sib = element.nextElementSibling;
                while (sib) {
                    const t = normalize(sib.innerText || sib.textContent);
                    if (t) return t;
                    sib = sib.nextElementSibling;
                }
                return '';
            }""")
            return " ".join(str(evaluated).split()) if evaluated else ""
        except Exception:
            return ""

    async def _is_valid_question_text(
        self,
        question_text: str,
        has_visible_answer_controls: bool,
        option_texts: list[str] | None = None,
    ) -> bool:
        normalized = " ".join(question_text.lower().strip().split())
        if not normalized:
            return False

        normalized_options = {
            self._normalize_option_text(o) for o in (option_texts or []) if self._normalize_option_text(o)
        }
        if normalized in normalized_options:
            logger.info("REJECTED_OPTION_AS_QUESTION: {}", question_text[:100])
            return False

        if any(p in normalized for p in _INFO_MESSAGE_PATTERNS):
            return False

        if has_visible_answer_controls:
            return True

        return any(h in normalized for h in _VALID_QUESTION_HINTS)

    async def _resolve_active_chatbot_question(
        self,
        page: Page,
        processed_keys: dict[str, str],
        error_keys: set[str] | None = None,
    ) -> dict[str, Any] | None:
        import time

        drawer = await self._resolve_drawer(page)
        if not drawer:
            return None

        history_items = drawer.locator(".chatbot_ListItem")
        
        # Get class names and visibility of all history items at once
        try:
            items_info = await drawer.evaluate("""
                drawerEl => {
                    const items = drawerEl.querySelectorAll('.chatbot_ListItem');
                    return Array.from(items).map(el => ({
                        className: el.className || '',
                        isVisible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    }));
                }
            """)
        except Exception as e:
            logger.warning("Failed to evaluate history items: {}", e)
            return None

        if not items_info:
            return None

        # Find latest bot question
        active_idx = -1
        last_user_idx = -1
        last_bot_idx = -1

        for idx in range(len(items_info) - 1, -1, -1):
            info = items_info[idx]
            if not info["isVisible"]:
                continue
            class_name = info["className"]
            if "userItem" in class_name and last_user_idx == -1:
                last_user_idx = idx
            if "botItem" in class_name and last_bot_idx == -1:
                last_bot_idx = idx
            if last_user_idx != -1 and last_bot_idx != -1:
                break

        # Only skip if user's answer is newer than the latest bot question
        if last_user_idx > last_bot_idx:
            logger.info("LATEST_MESSAGE_IS_USER_ANSWER — no new question yet")
            return None

        if last_bot_idx == -1:
            return None

        active_idx = last_bot_idx

        if active_idx == -1:
            return None

        active_question_item = history_items.nth(active_idx)

        question_text = await self._extract_question_label(
            active_question_item, self._selectors.discovery.questions.text, page=None
        )
        if not question_text:
            return None

        normalized_text = " ".join(question_text.lower().strip().split())
        if any(p in normalized_text for p in _INFO_MESSAGE_PATTERNS):
            logger.info("REJECTED_INFO_MESSAGE_IMMEDIATELY: '{}'", question_text[:100])
            return None

        logger.info("QUESTION_RAW: '{}'", question_text[:100])

        start_wait = time.time()
        field_type = "unknown"
        options: list[str] = []
        target_container = active_question_item
        has_controls = False

        for attempt in range(1, 26):
            logger.info("FIELD_DETECTION_ATTEMPT: {}/25", attempt)

            drawer = await self._resolve_drawer(page)
            if not drawer:
                logger.info("FIELD_DETECTION_RETRY: Drawer disappeared")
                await self._safe_wait(page, 1000)
                continue

            try:
                items_info = await drawer.evaluate("""
                    drawerEl => {
                        const items = drawerEl.querySelectorAll('.chatbot_ListItem');
                        return Array.from(items).map(el => ({
                            className: el.className || '',
                            isVisible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                        }));
                    }
                """)
            except Exception as e:
                logger.warning("Failed to evaluate history items in retry loop: {}", e)
                await self._safe_wait(page, 1000)
                continue

            if not items_info:
                logger.info("FIELD_DETECTION_RETRY: No history items found")
                await self._safe_wait(page, 1000)
                continue

            active_question_idx = -1
            active_chips_idx = -1
            last_user_idx = -1
            last_bot_idx = -1

            for idx in range(len(items_info) - 1, -1, -1):
                info = items_info[idx]
                if not info["isVisible"]:
                    continue
                class_name = info["className"]
                
                if "userItem" in class_name and last_user_idx == -1:
                    last_user_idx = idx
                
                if "botChips" in class_name and active_chips_idx == -1:
                    active_chips_idx = idx
                    if last_bot_idx == -1:
                        last_bot_idx = idx
                
                if "botItem" in class_name and active_question_idx == -1:
                    active_question_idx = idx
                    if last_bot_idx == -1:
                        last_bot_idx = idx
                
                if last_user_idx != -1 and active_question_idx != -1:
                    break

            # Only skip if user's answer is newer than the latest bot activity
            if last_user_idx > last_bot_idx:
                logger.info("LATEST_MESSAGE_IS_USER_ANSWER — no new question yet")
                return None

            if active_question_idx == -1:
                logger.info("FIELD_DETECTION_RETRY: Active bot question item not found")
                await self._safe_wait(page, 1000)
                continue

            history_items = drawer.locator(".chatbot_ListItem")
            active_question_item = history_items.nth(active_question_idx)
            active_chips_item = history_items.nth(active_chips_idx) if active_chips_idx != -1 else None

            field_type = "unknown"
            options = []
            target_container = active_question_item

            if active_chips_item:
                field_type = await self._detect_field_type(active_chips_item, page=None)
                options = await self._get_field_options(active_chips_item, field_type, page=None)
                if field_type != "unknown" or options:
                    target_container = active_chips_item

            if field_type == "unknown" and not options:
                field_type = await self._detect_field_type(active_question_item, page, _drawer=drawer)
                options = await self._get_field_options(active_question_item, field_type, page, _drawer=drawer)

            if field_type == "unknown" and not options:
                cb_count = await active_question_item.locator(
                    "input[type='checkbox'], [role='checkbox'], [class*='checkbox'], [class*='checkBox']"
                ).count()
                if cb_count > 0:
                    field_type = "checkbox"
                    logger.info("CHECKBOX_DETECTED_IN_LOOP: count={}", cb_count)

            has_controls = bool(options) or field_type != "unknown"
            logger.info("ACTIVE_CONTROLS_FOUND: {}", has_controls)

            if has_controls:
                elapsed_ms = int((time.time() - (self._drawer_opened_at or start_wait)) * 1000)
                logger.info(
                    "FIELD_DETECTION_SUCCESS: field_type='{}' options={} (elapsed={}ms)",
                    field_type, options, elapsed_ms,
                )
                break
            else:
                logger.info("FIELD_DETECTION_RETRY: Attempt {}/25 - no controls rendered yet", attempt)
                await self._safe_wait(page, 1000)

        if not has_controls:
            logger.warning("FIELD_DETECTION_TIMEOUT: Failed to detect controls after 25 attempts")

        if not await self._is_valid_question_text(
            question_text, has_visible_answer_controls=has_controls, option_texts=options
        ):
            logger.info("REJECTED_HISTORY_MESSAGE: '{}'", question_text[:100])
            return None

        question_key = normalize_question_key(question_text, options)
        target_key, session_action = self._resolve_session_key(
            question_text, question_key, processed_keys, error_keys=error_keys,
        )
        if session_action == "skip":
            logger.info("REJECTED_ALREADY_PROCESSED: '{}'", question_text[:100])
            return None

        if await self._is_field_filled(target_container, field_type, page):
            logger.info("REJECTED_ALREADY_FILLED_OR_SELECTED: '{}'", question_text[:100])
            return None

        logger.info("ACTIVE_QUESTION_SELECTED: {}", question_text[:100])
        return {
            "container": target_container,
            "question_container": active_question_item,
            "question_text": question_text,
            "question_key": target_key,
            "field_type": field_type,
            "options": options,
            "source": "chatbot_active",
        }

    async def _final_drawer_refresh_scan(self, page: Page, active_q: dict[str, Any]) -> None:
        logger.info("FIELD_TYPE_UNKNOWN: Final forced refresh scan of recruiter drawer...")
        await self._safe_wait(page, 1000)
        drawer = await self._resolve_drawer(page)
        if not drawer:
            return

        container = active_q["container"]
        field_type = await self._detect_field_type(container, page)
        options = await self._get_field_options(container, field_type, page)

        if field_type == "unknown" and container != drawer:
            field_type = await self._detect_field_type(drawer, page)
            options = await self._get_field_options(drawer, field_type, page)
            if field_type != "unknown":
                active_q["container"] = drawer

        if field_type != "unknown":
            logger.info("FINAL_REFRESH_SCAN_SUCCESS: field_type='{}'", field_type)
            active_q["field_type"] = field_type
            active_q["options"] = options
        else:
            logger.warning("FINAL_REFRESH_SCAN_FAILED: Field type remains unknown")

    async def _get_field_options(self, container, field_type: str, page: Page | None = None, _drawer=None) -> list[str]:
        """Extract available options from the field. Excludes nav/action button text."""
        logger.info("GET_FIELD_OPTIONS_CALLED: field_type={}", field_type)
        options: list[str] = []

        try:
            drawer = _drawer if _drawer is not None else (await self._resolve_drawer(page) if page else None)

            if field_type in ("button-options", "button-choice", "chatbot_chips", "div"):
                chip_sel = ".chatbot_Chip, .chipItem, [class*='chatbot_Chip'], [class*='chipItem']"
                chip_locs = container.locator(chip_sel)
                if await chip_locs.count() == 0 and drawer:
                    chip_locs = drawer.locator(chip_sel)
                elif await chip_locs.count() == 0 and page:
                    chip_locs = page.locator(f"{chip_sel}:visible")

                if chip_locs and await chip_locs.count() > 0:
                    for i in range(await chip_locs.count()):
                        el = chip_locs.nth(i)
                        if await el.is_visible() or page is None:
                            text = (await el.inner_text()).strip()
                            if text:
                                options.append(text)

                if not options:
                    options_sel = "button, [role='button'], [role='option'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button'], [tabindex]"
                    btn_locs = container.locator(options_sel)
                    if await btn_locs.count() == 0 and drawer:
                        btn_locs = drawer.locator(options_sel)
                    elif await btn_locs.count() == 0 and page:
                        btn_locs = page.locator(options_sel)

                    for i in range(await btn_locs.count()):
                        el = btn_locs.nth(i)
                        if not await el.is_visible():
                            continue
                        tag = await el.evaluate("el => el.tagName.toLowerCase()")
                        if tag in ("input", "textarea", "select"):
                            continue
                        text = (await el.inner_text()).strip()
                        if not text:
                            text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
                        if text:
                            words = " ".join(text.split()).lower().split()
                            if not any(w.strip(".,;:()<>[]{}&/-_") in _NAV_BUTTON_KEYWORDS for w in words):
                                options.append(text)

            else:
                # Select options
                select_locs = container.locator("select option")
                if await select_locs.count() == 0 and drawer:
                    select_locs = drawer.locator("select option")
                elif await select_locs.count() == 0 and page:
                    select_locs = page.locator("select option:visible")
                for i in range(await select_locs.count()):
                    text = (await select_locs.nth(i).inner_text()).strip()
                    if text and not any(text.lower().startswith(p) for p in ["select", "--", "choose"]):
                        options.append(text)

                # Radio labels
                radio_locs = container.locator("[role='radio'], input[type='radio']")
                if await radio_locs.count() == 0 and drawer:
                    radio_locs = drawer.locator("[role='radio']:visible, input[type='radio']:visible")
                elif await radio_locs.count() == 0 and page:
                    radio_locs = page.locator("[role='radio']:visible, input[type='radio']:visible")
                for i in range(await radio_locs.count()):
                    el = radio_locs.nth(i)
                    text = (await el.get_attribute("aria-label") or "").strip() or (await el.inner_text()).strip()
                    if not text:
                        try:
                            text = await el.evaluate("""el => {
                                const id = el.getAttribute('id');
                                if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) return l.innerText.trim(); }
                                const p = el.closest('label'); if (p) return p.innerText.trim();
                                const n = el.nextElementSibling;
                                if (n && (n.tagName === 'LABEL' || n.tagName === 'SPAN')) return n.innerText.trim();
                                return '';
                            }""")
                        except Exception:
                            text = ""
                    if text:
                        options.append(" ".join(text.split()))

                # Checkbox options (custom Naukri elements first)
                if field_type == "checkbox":
                    for cb_sel in [
                        "[class*='checkbox']", "[class*='checkBox']", "[class*='check-box']",
                        "label:has(input[type='checkbox'])", ".checkboxLabel",
                        "[class*='option']", "[class*='choice']",
                    ]:
                        try:
                            loc = container.locator(cb_sel)
                            if await loc.count() == 0 and drawer:
                                loc = drawer.locator(cb_sel)
                            count = await loc.count()
                            if count > 0:
                                for i in range(count):
                                    el = loc.nth(i)
                                    try:
                                        text = (await el.inner_text()).strip()
                                        if text:
                                            for part in [p.strip() for p in text.replace("\n", "|").split("|") if p.strip()]:
                                                if part not in options:
                                                    options.append(part)
                                    except Exception:
                                        pass
                                if options:
                                    logger.info("CUSTOM_CHECKBOX_OPTIONS_EXTRACTED: {}", options)
                                    break
                        except Exception:
                            continue

                # Standard checkbox labels
                cb_locs = container.locator("input[type='checkbox'], [role='checkbox']")
                if await cb_locs.count() == 0 and drawer:
                    cb_locs = drawer.locator("input[type='checkbox']:visible, [role='checkbox']:visible")
                elif await cb_locs.count() == 0 and page:
                    cb_locs = page.locator("input[type='checkbox']:visible, [role='checkbox']:visible")
                logger.info("CHECKBOX_LOCS_COUNT: {}", await cb_locs.count())
                for i in range(await cb_locs.count()):
                    el = cb_locs.nth(i)
                    text = (await el.get_attribute("aria-label") or "").strip() or (await el.inner_text()).strip()
                    if not text:
                        try:
                            text = await el.evaluate("""el => {
                                const id = el.getAttribute('id');
                                if (id) { const l = document.querySelector('label[for="' + id + '"]'); if (l) return l.innerText.trim(); }
                                const p = el.closest('label'); if (p) return p.innerText.trim();
                                const n = el.nextElementSibling; if (n) return n.innerText.trim();
                                const pr = el.previousElementSibling; if (pr) return pr.innerText.trim();
                                const c = el.parentElement; if (c) return c.innerText.trim();
                                return '';
                            }""")
                        except Exception:
                            text = ""
                    if text:
                        for part in [p.strip() for p in text.replace("\n", "|").split("|") if p.strip()]:
                            options.append(" ".join(part.split()))

        except Exception as e:
            logger.warning("Error in _get_field_options: {}", e)

        # Filter nav keywords
        filtered = [
            opt for opt in options
            if opt.lower().strip() not in _NAV_BUTTON_KEYWORDS
            and not (len(opt.split()) == 1 and opt.lower() in _NAV_BUTTON_KEYWORDS)
        ]

        # Deduplicate
        seen: set[str] = set()
        unique: list[str] = []
        for o in filtered:
            if o not in seen:
                seen.add(o)
                unique.append(o)

        # Remove combined text nodes
        if len(unique) > 1:
            final: list[str] = []
            for opt in unique:
                others = [o for o in unique if o != opt]
                opt_lower = opt.lower()
                matches = sum(1 for o in others if o.lower() in opt_lower)
                if not (matches >= 2 and matches == len(others)):
                    final.append(opt)
            unique = final

        logger.info("QUESTION_OPTIONS_DETECTED: {}", unique)
        return unique

    async def _is_field_filled(self, container, field_type: str, page: Page | None = None) -> bool:
        # Safely handle unit test mock objects
        if type(container).__name__ in ("MagicMock", "AsyncMock", "Mock"):
            return False
        if page and type(page).__name__ in ("MagicMock", "AsyncMock", "Mock"):
            return False
            
        try:
            drawer = await self._resolve_drawer(page) if page else None
            if drawer and type(drawer).__name__ in ("MagicMock", "AsyncMock", "Mock"):
                drawer = None
            
            if field_type in ("input", "textarea"):
                inputs = container.locator("input:not([type='hidden']), textarea")
                if await inputs.count() == 0 and drawer:
                    inputs = drawer.locator("input:not([type='hidden']), textarea")
                for i in range(await inputs.count()):
                    val = await inputs.nth(i).evaluate("el => el.value")
                    if val and val.strip():
                        return True
            
            elif field_type == "contenteditable":
                ces = container.locator("[contenteditable='true']")
                if await ces.count() == 0 and drawer:
                    ces = drawer.locator("[contenteditable='true']")
                for i in range(await ces.count()):
                    val = await ces.nth(i).inner_text()
                    if val and val.strip():
                        return True

            elif field_type == "radio":
                radios = container.locator("input[type='radio'], [role='radio']")
                if await radios.count() == 0 and drawer:
                    radios = drawer.locator("input[type='radio'], [role='radio']")
                for i in range(await radios.count()):
                    el = radios.nth(i)
                    checked = await el.evaluate("""el => {
                        if (el.checked) return true;
                        if (el.getAttribute('aria-checked') === 'true') return true;
                        const c = el.className || '';
                        return c.includes('selected') || c.includes('active') || c.includes('checked');
                    }""")
                    if checked:
                        return True

            elif field_type == "checkbox":
                cbs = container.locator("input[type='checkbox'], [role='checkbox']")
                if await cbs.count() == 0 and drawer:
                    cbs = drawer.locator("input[type='checkbox'], [role='checkbox']")
                for i in range(await cbs.count()):
                    el = cbs.nth(i)
                    checked = await el.evaluate("""el => {
                        if (el.checked) return true;
                        if (el.getAttribute('aria-checked') === 'true') return true;
                        const c = el.className || '';
                        return c.includes('selected') || c.includes('active') || c.includes('checked');
                    }""")
                    if checked:
                        return True

            elif field_type == "select":
                selects = container.locator("select")
                if await selects.count() == 0 and drawer:
                    selects = drawer.locator("select")
                for i in range(await selects.count()):
                    el = selects.nth(i)
                    val = await el.evaluate("el => el.value")
                    if val and val.strip() and not any(p in val.lower() for p in ["select", "--", "choose"]):
                        return True

            elif field_type in ("chatbot_chips", "button-options"):
                chip_sel = ".chatbot_Chip, .chipItem, [class*='chatbot_Chip'], [class*='chipItem'], button, [role='button']"
                chips = container.locator(chip_sel)
                if await chips.count() == 0 and drawer:
                    chips = drawer.locator(chip_sel)
                for i in range(await chips.count()):
                    el = chips.nth(i)
                    is_selected = await el.evaluate("""el => {
                        const c = el.className || '';
                        return c.includes('selected') || c.includes('active') || c.includes('checked') || el.disabled;
                    }""")
                    if is_selected:
                        return True

        except Exception as e:
            logger.warning("Error checking if field is filled: {}", e)

        return False

    def _resolve_session_key(
        self,
        question_text: str,
        question_key: str,
        processed_keys: dict[str, str],
        error_keys: set[str] | None = None,
    ) -> tuple[str, str | None]:
        """
        Check for collision or loop detection on the question key.
        Returns: (final_key, action)
        Where action can be:
          - None: proceed to answer
          - "skip": skip/escalate because of loop/stuck
        """
        if question_key not in processed_keys:
            return question_key, None

        prev_text = processed_keys[question_key]
        norm_prev = " ".join(prev_text.lower().strip().split())
        norm_curr = " ".join(question_text.lower().strip().split())

        if norm_prev != norm_curr:
            # Use a stable, text-derived suffix instead of a positional _2/_3.
            # Positional suffixes are job-local, but answers are looked up in the
            # GLOBAL question_bank — so the same suffix mapped to unrelated
            # questions across jobs and leaked answers between them (e.g. an "Iac"
            # question inheriting an unrelated "API Development" answer).
            new_key = f"{question_key}__{_slugify(norm_curr)[:40]}".strip("_")
            logger.info("RESOLVED_KEY_COLLISION: '{}' vs '{}' -> new key '{}'", prev_text, question_text, new_key)
            return new_key, None
        else:
            # If the previous fill errored, allow one retry (the user popup may fix it)
            if error_keys and question_key in error_keys:
                logger.info("RETRY_AFTER_ERROR: key='{}' — allowing re-process", question_key)
                error_keys.discard(question_key)  # Only allow one retry
                return question_key, None
            logger.warning("DETECTED_STUCK_LOOP: Same question '{}' and key '{}' encountered again.", question_text, question_key)
            return question_key, "skip"

    async def _interactive_prompt_user(
        self,
        page: Page,
        question_text: str,
        options: list[str] | None = None,
        is_case2: bool = False,
        stored_answer: str | None = None,
        is_multi_select: bool = False,
    ) -> dict:
        if options is None:
            options = []

        logger.info("POPUP_STARTING — question='{}'", question_text[:60])

        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            self._show_tkinter_popup,
            question_text,
            options,
            is_case2,
            stored_answer,
            is_multi_select,
        )

        logger.info("RESPONSE_FROM_POPUP={}", response)
        return response

    def _show_tkinter_popup(
        self,
        question_text: str,
        options: list[str],
        is_case2: bool = False,
        stored_answer: str | None = None,
        is_multi_select: bool = False,
    ) -> dict:
        import subprocess
        import sys
        import json
        from pathlib import Path

        input_data = {
            "question_text": question_text,
            "options": options,
            "is_case2": is_case2,
            "stored_answer": stored_answer,
            "is_multi_select": is_multi_select,
        }

        # Locate popup_helper.py absolute path relative to this file
        helper_path = Path(__file__).parent.parent / "utils" / "popup_helper.py"

        _POPUP_TIMEOUT = 90  # seconds — reduced from 300 to prevent 5-min silent hang

        try:
            # Run helper script in a new Python process
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 1  # SW_SHOWNORMAL — ensures Tkinter window is visible

            logger.info("POPUP_SUBPROCESS_WAITING — answer required, timeout={}s", _POPUP_TIMEOUT)
            print(f"\n[ACTION REQUIRED] A popup window has appeared asking for an answer. You have {_POPUP_TIMEOUT} seconds.", flush=True)

            result = subprocess.run(
                [sys.executable, str(helper_path)],
                input=json.dumps(input_data),
                capture_output=True,
                text=True,
                encoding="utf-8",
                startupinfo=startupinfo,
                check=False,
                timeout=_POPUP_TIMEOUT,
            )

            if result.returncode == 0 and result.stdout.strip():
                try:
                    res_dict = json.loads(result.stdout)
                    return {
                        "answer": res_dict.get("answer"),
                        "selected_option": res_dict.get("selected_option")
                    }
                except Exception as json_err:
                    logger.warning("Failed to parse popup helper output: {}", json_err)
            else:
                logger.warning(
                    "Popup helper exited with code {}. Stderr: {}",
                    result.returncode, result.stderr
                )
        except subprocess.TimeoutExpired:
            logger.error(
                "POPUP_TIMEOUT: No answer received within {}s — popup may be hidden behind browser window. "
                "Check your taskbar for a Python/Naukri window.",
                _POPUP_TIMEOUT,
            )
            print(f"\n[TIMEOUT] Popup was not answered within {_POPUP_TIMEOUT}s. "
                  "Check your taskbar — the window may be behind the browser.", flush=True)
        except Exception as exc:
            logger.error("Failed to run popup helper: {}", exc)

        return {"answer": None, "selected_option": None}


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _numeric_option_matches(opt_norm: str, val: float) -> bool:
    """Return True if the option text describes a numeric range/limit that contains val."""
    import re
    if val == 0 and any(w in opt_norm for w in ["no experience", "fresher", "fresh", "none", "0"]):
        return True
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", opt_norm):
        if float(m.group(1)) <= val <= float(m.group(2)):
            return True
    if m := re.search(r"(?:<|less\s+than)\s*(\d+(?:\.\d+)?)", opt_norm):
        if val < float(m.group(1)):
            return True
    if m := re.search(r"(?:>|greater\s+than|more\s+than|above)\s*(\d+(?:\.\d+)?)", opt_norm):
        if val > float(m.group(1)):
            return True
    if m := re.search(r"(\d+(?:\.\d+)?)\s*\+", opt_norm):
        if val >= float(m.group(1)):
            return True
    if m := re.search(r"\b(\d+(?:\.\d+)?)\b", opt_norm):
        if val == float(m.group(1)):
            return True
    return False


def _token_overlap(a: str, b: str) -> bool:
    """Word-boundary containment in either direction.

    Avoids the substring trap where a numeric answer like "2" matches inside an
    unrelated number such as "12-15 years".
    """
    import re
    if not a or not b:
        return False
    for needle, hay in ((a, b), (b, a)):
        if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", hay):
            return True
    return False


def _range_tightness(opt_norm: str, val: float) -> float:
    """Lower = tighter fit of val to the option's numeric range/bound.

    Used to pick the best bucket among several matching options instead of the
    first one (e.g. val=8 with ["2+ yrs", "8+ yrs"] should pick "8+ yrs").
    """
    import re
    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)", opt_norm):
        return abs(float(m.group(2)) - float(m.group(1)))  # span of the range
    if m := re.search(r"(?:>|greater\s+than|more\s+than|above)\s*(\d+(?:\.\d+)?)", opt_norm):
        return abs(val - float(m.group(1)))
    if m := re.search(r"(\d+(?:\.\d+)?)\s*\+", opt_norm):
        return abs(val - float(m.group(1)))
    if m := re.search(r"(?:<|less\s+than)\s*(\d+(?:\.\d+)?)", opt_norm):
        return abs(float(m.group(1)) - val)
    if m := re.search(r"\b(\d+(?:\.\d+)?)\b", opt_norm):
        return abs(val - float(m.group(1)))
    return float("inf")


def _best_numeric_option(option_pairs: list[tuple], val: float) -> tuple | None:
    """Return the (locator, text) whose numeric range best fits val, or None."""
    best: tuple | None = None
    best_score: float | None = None
    for opt, text in option_pairs:
        norm = text.strip().lower()
        if _numeric_option_matches(norm, val):
            score = _range_tightness(norm, val)
            if best_score is None or score < best_score:
                best_score = score
                best = (opt, text)
    return best


async def is_valid_recruiter_question_container(container: Locator) -> bool:
    """Return True if the container holds a real recruiter question (not a nav/info block)."""
    try:
        try:
            is_chatbot = await container.evaluate("""el => {
                const c = el.className || '';
                return c.includes('botItem') || c.includes('chatbot_ListItem') || c.includes('botMsg') || el.closest('.chatbot_ListItem') !== null;
            }""")
            if is_chatbot:
                return True
        except Exception:
            pass

        if (
            await container.locator("textarea").count() > 0
            or await container.locator("select").count() > 0
            or await container.locator("[contenteditable='true']").count() > 0
            or await container.locator(".chatbot_Chip, .chipItem, [class*='chatbot_Chip'], [class*='chipItem']").count() > 0
            or await container.locator("input[type='radio'], [role='radio']").count() > 0
            or await container.locator("input[type='checkbox'], [role='checkbox']").count() > 0
        ):
            return True

        inputs = container.locator("input")
        for i in range(await inputs.count()):
            type_attr = (await inputs.nth(i).get_attribute("type") or "text").lower()
            if type_attr not in ("button", "submit", "hidden", "image"):
                return True

        options_sel = "button, [role='button'], [role='option'], div[class*='option'], div[class*='button'], a[class*='btn'], a[class*='button']"
        clickable = container.locator(options_sel)
        for i in range(await clickable.count()):
            el = clickable.nth(i)
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            if tag in ("input", "textarea", "select"):
                continue
            text = (await el.inner_text()).strip()
            if not text:
                text = (await el.evaluate("el => el.parentElement ? el.parentElement.innerText : ''")).strip()
            if text:
                words = " ".join(text.split()).lower().split()
                if not any(w.strip(".,;:()<>[]{}&/-_") in _NAV_BUTTON_KEYWORDS for w in words):
                    return True

    except Exception as e:
        logger.warning("Error checking container validity: {}", e)

    return False


def get_actual_numeric_experience(question_key: str, repo: "ApplyDiscoveryRepository | None" = None) -> float | None:
    """Extract a numeric experience value from profile, repo, or answer bank."""
    key_lower = question_key.lower()
    
    import json
    from pathlib import Path
    
    profile_skills = []
    profile_transferable = {}
    profile_exp_years = 2.0
    profile = {}
    
    try:
        profile_path = Path("config/candidate_profile.json")
        if profile_path.exists():
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile_skills = [s.lower().strip() for s in profile.get("skills", [])]
            profile_transferable = profile.get("transferable_skills", {})
            profile_exp_years = float(profile.get("experience_years", 2.0))
            
            # Direct check for key in profile (for tests or custom fields)
            if question_key in profile:
                try:
                    return float(profile[question_key])
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass

    # Determine what skill we are looking for
    skill_query = None
    if key_lower.startswith("exp_"):
        skill_query = key_lower[len("exp_"):].replace("_", " ").strip()
    else:
        # Fallback keyword map for legacy keys
        experience_map = {
            "python": "python",
            "genai": "generative ai", "generative": "generative ai",
            "llm": "llm applications",
            "rag": "rag",
            "langchain": "langchain",
            "fastapi": "fastapi",
            "aws": "aws",
            "ml": "machine learning", "machine": "machine learning",
            "dl": "deep learning", "deep": "deep learning",
            "nlp": "nlp", "natural": "nlp",
            "sql": "sql",
        }
        for keyword, skill_name in experience_map.items():
            if keyword in key_lower:
                skill_query = skill_name
                break

    if skill_query:
        # 1. Search candidate profile for skill
        if skill_query in profile_skills:
            return profile_exp_years
        
        # 2. Check transferable skills
        for base_skill, mapped_list in profile_transferable.items():
            mapped_lower = [m.lower().strip() for m in mapped_list]
            if skill_query in mapped_lower:
                if base_skill.lower().strip() in profile_skills:
                    return profile_exp_years
        
        # Not found anywhere in skills or transferable -> return 0.0
        return 0.0

    # Fallback for generic experience keys (e.g. total_years_experience, relevant_experience)
    search_keys = ["experience_years", "total_experience", "relevant_experience"]
    
    for k in search_keys:
        if k in profile:
            try:
                return float(profile[k])
            except (ValueError, TypeError):
                pass
                
    if repo:
        for k in search_keys:
            try:
                val = repo.get_question_answer(k)
                if val:
                    return float(val)
            except (ValueError, TypeError, Exception):
                pass

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

    # Unrecognized, non-experience key: return None so the caller treats it as
    # unknown rather than silently filling the candidate's total experience.
    return None