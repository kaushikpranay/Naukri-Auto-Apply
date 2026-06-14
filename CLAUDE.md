# Naukri-Auto-Apply — Project Intelligence

## Stack
Python 3.12 | Playwright-async | SQLite-WAL | Groq(Llama-3)+Gemini | Loguru | Pydantic | Tkinter | PyYAML

## Entry Points
```
daily_run.py              → Full pipeline orchestrator (cron daily)
main.py                   → Scrape only
evaluate_jobs.py          → LLM eval only
discover_applications.py  → Apply only
retry_failed_jobs.py      → Retry failed
one_time_discover_application.py  → Reconcile + dry-run
one_time_reconcile_and_apply.py   → Reconcile + resume support
click_apply_and_debug.py          → Debug chatbot DOM
```

## Pipeline Order
```
main.py → evaluate_jobs.py → discover_applications.py → generate_dashboard.py
```

## Directory Map
```
app/browser/session.py           → Chromium launcher, cookie persistence
app/collector/job_collector.py   → Scraper engine
app/database/repository.py       → JobRepository, dedup, 15-day cleanup
app/database/evaluations_repo.py → EvaluationsRepository
app/database/migrations.py       → Auto-migrations
app/discovery/service.py         → Playwright apply flow, modal/chatbot nav
app/discovery/repository.py      → Save apps, question bank, mappings
app/evaluator/evaluation_service.py     → Prompt builder, batch manager
app/evaluator/providers/groq_provider.py   → Groq API
app/evaluator/providers/gemini_provider.py → Gemini API
app/question_bank/form_filler.py    → DOM classifier, chatbot parser
app/question_bank/lookup_service.py → Maps questions → answers
app/question_bank/answers.py        → Fallback answer registry
app/question_bank/seeder.py         → Seeds DB from candidate_profile
app/utils/popup_helper.py           → Tkinter subprocess GUI (DPI-aware)
app/utils/config_loader.py          → Safe YAML/JSON loaders
app/models/                         → Pydantic schemas (job, discovery, config)
app/export/                         → Excel/report exporters
config/settings.yaml                → Timeouts, limits, paths
config/selectors.yaml               → All CSS selectors
config/auth_selectors.yaml          → Auth detection selectors
config/locations.yaml               → Search keywords × locations matrix
config/candidate_profile.json       → Resume: skills, experience, target_roles
```

## Database: database/jobs.db (WAL, busy_timeout=30000ms)

### Tables & Key Columns
```
jobs                  → id, job_title, company_name, normalized_url(UNIQUE), status, retry_count
ai_evaluations        → job_id(UNIQUE FK), action(APPLY/SKIP/REVIEW), interview_probability, confidence
job_applications      → job_id(UNIQUE FK), apply_type, status, screenshots, redirect_chain, quota_message
question_bank         → question_key(UNIQUE), question_text, answer, field_type, usage_count
job_application_questions → job_id+question_key+question_text(UNIQUE), answer, required
answer_mappings       → question_key+raw_answer(UNIQUE), selected_option
job_reconciliations   → job_id(PK), status, error_category, stack_trace, failing_stage
```

## State Machine
```
pending → queued → evaluated → queued → applied_successfully
                                      → external_portal
                                      → quota_exhausted
                                      → unknown_question → waiting_for_user → queued
                                      → temporary_failure (retry eligible)
                                      → browser_error (retry eligible)
                                      → failed (retry_count >= max)
```

### Transition Guards
- evaluated → queued: only if action=APPLY
- queued → failed: only if retry_count >= max_retry_count(3)
- applied_successfully: TERMINAL — no reverse allowed
- quota_exhausted: TERMINAL — no reverse allowed

## Form Filler Logic
```
Question encountered
  → normalize to question_key
  → lookup question_bank DB
      → Found + options match  → auto-fill
      → Found + options mismatch → Tkinter Case2 (option mapping popup)
      → Not found              → Tkinter Case1 (new answer popup)
                                    → save to question_bank → auto-fill
```

### Question Key Normalization
```
"notice period" / "how soon can you join" → notice_period
"experience in python" / "years using python" → python_experience
"current CTC" / "current salary" → current_ctc
Direct match in candidate_profile.json → return value directly
```

## Tkinter Subprocess Flow
```
FormFiller detects unknown Q
  → JSON(question, options) → subprocess.run(popup_helper.py) via stdin
  → User submits answer in DPI-aware topmost window
  → GUI writes {"answer": "...", "selected_option": "..."} to stdout
  → Parent reads stdout → updates question_bank or answer_mappings
  → Continues form fill
```

## Chatbot Drawer (.chatbot_Drawer)
- Detect: `.chatbot_Drawer`, `.chatbot_Overlay`
- Parse only active bubble (not full page)
- Element types: chips(`li.botItem`,`li.chip`), inputs, textareas, contenteditable
- Strip option text from question label
- RULE: Never auto-click final submit button

## Safety Rules (NEVER VIOLATE)
- No auto-submit: system fills forms, stops before final submit button
- No reverse from terminal states: applied_successfully, quota_exhausted
- max_retry_count=3 hard limit
- max_discovery_jobs_per_run=10
- max_ai_evaluations_per_run=25

## Config Values (settings.yaml)
```
browser.default_timeout = 30000ms
browser.slow_mo = 500ms
browser.headless = false
naukri.max_pages_per_search = 5
evaluation.max_retry_count = 3
discovery.max_discovery_jobs_per_run = 10
```

## Recent Feature Added
- Dashboard now shows **count of jobs in hidden/collapsed sections**
- Location: generate_dashboard.py + docs/

## Known Failure Modes
- sqlite3.OperationalError: database is locked → WAL files missing or hung process
- SessionExpiredError → re-run login_setup.py
- Selector not found → Naukri UI changed, update config/selectors.yaml
- Tkinter popup not appearing → DPI scaling issue on popup_helper.py
- Chatbot drawer loads dynamically → wait for `.chatbot_Drawer` before parsing

## Claude Code Rules
- Output format: file:line | bug | prod-risk | fix
- Order: High risk first
- No summaries, no explanations unless asked
- No refactoring unless asked — fix only
- Check state transitions before changing any status update logic
- Never touch terminal state logic without explicit instruction
