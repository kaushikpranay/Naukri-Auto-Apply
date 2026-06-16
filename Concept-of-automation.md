# Multi-Platform Automation — Concept (single source of truth)

> This is the **spec** we edit before writing code. We rebuild the system fresh,
> **one feature at a time**, just like using `microservices - System design`. Each feature is built, then
> **tested and approved by the user**, before moving to the next.
>
> Hard constraint: **must NOT break `Naukri-Automation`.** Reuse Naukri's engine by
> **import / read-only**, never by moving or editing a Naukri file, table, or browser
> profile.

---

## 0. Objective & KPI

- **Real KPI = interview calls**, across many job platforms, at **$0 cost**.
- Naukri-Automation is already built (fully automated apply; a human only answers
  unknown questions). This system adds, on top, reusing Naukri's engine:
  1. **External / ATS auto-apply** — Greenhouse & Lever (public APIs for discovery,
     Playwright for form-fill + submit). Workday is link-driven / HITL handoff (hardest, last).
  2. **Link router** — classify any external URL (e.g. a Naukri redirect) by ATS host
     and feed it to the right adapter.
  3. **Wellfound discovery** — scrape openings + the poster + their LinkedIn into a
     list. **No automated outreach** — the user messages people manually.

---

## 1. Locked decisions

| Decision | Choice | Reason |
|---|---|---|
| Wellfound outreach | **Discovery only** — scrape & store; user messages manually | No connect→accept handshake; user keeps reputation control |
| ATS automation boundary | **Full auto-apply**, human-in-loop only for unknowns | Matches Naukri model |
| Email verification | **Popup + wait** (no IMAP automation) | Simplest, safe |
| CAPTCHA | **Popup "solve it → Resume"** | Can't solve reliably/free; route to human |
| Workday accounts | Default email+password from `.env`; reuse same login | User's choice |
| Cost | **$0** — free LLM tiers, public ATS APIs, static dashboard, no proxies/paid enrichment | Hard constraint |
| Codebase strategy | **Composition, not extraction** — new code imports Naukri classes | Guarantees Naukri can't break |
| Build order | **Wellfound → Greenhouse → Lever → Workday** | Easiest/highest-yield first; Workday hardest, last |
| LinkedIn automation | **None.** Capture LinkedIn URLs for manual use only | #1 ban risk; user's real account |
| Auto-submit | **OFF by default** (`MPA_AUTO_SUBMIT=false`) — fill, screenshot, stop at `ready_for_review` | Mirrors Naukri "never auto-submit" rule; user opts in |

---

## 2. Safety contract — "don't break Naukri"

**Reuse = import / read-only, never move or edit.**

| UNTOUCHED (Naukri's working paths) | ADDITIVE ONLY (new code) |
|---|---|
| Naukri entry scripts, evaluator, collector, state machine, selectors | New services + orchestrator entrypoints |
| Naukri tables (`jobs`, `ai_evaluations`, `job_applications`, `question_bank`) | New DB `external.db`: `external_applications`, `wellfound_contacts`, MPA question bank |
| `browser_profile/` (Naukri login session) | **Separate** `browser_profile_external/` |
| Naukri `database/jobs.db` | **Read-only** by the dashboard (proper `file:///` URI) |
| Naukri dashboard CSS (`docs/css/style.css`) | **Linked, not copied** — Naukri file untouched |

- All MPA state lives inside MPA's own folders, so a failure here can't break Naukri.
- Worst-case isolation: if new code fails, it fails alone; Naukri still runs because
  not a single Naukri file or table was modified.

---

## 3. Target architecture — microservices

Each platform is its **own folder/service**. To fix or extend one platform, edit only
its folder. **Services never import each other**; the **orchestrator** wires them.
They share **one database** and run **sequentially** (one browser session, no
collisions).

```
microservices/
  CONCEPT.md              # this file — the spec we edit first
  mpa-core/               # SHARED library (the only shared code)
    mpa_core/
      bootstrap.py        #   puts Naukri on sys.path (the ONLY coupling point)
      config.py           #   MPA settings, isolated paths, candidate-profile (+overrides) loader
      base.py             #   contracts: DiscoveredJob / ApplyResult / ApplyOutcome / adapter protocols
      link_router.py      #   classify external URL -> ATS host, normalize
      http.py             #   stdlib JSON GET (no deps)
      formfill.py         #   generic field discovery + fill + label reading
      answers.py          #   answer bank / learned answers
      hitl/{popup,client} #   mode-based HITL popup (scrollable, type-your-own) + subprocess wrapper
      db/{schema,repository}   # external.db: external_applications + wellfound_contacts + question bank
    data/                 #   the ONE db (external.db) + browser_profile_external/  live here
    config/candidate_overrides.json  # MPA-local email/phone/resume (Naukri profile untouched)
  service-greenhouse/     # ── one folder per platform ──
    service.py            #    exposes ATS, build(), discover()/apply()
    config.yaml           #    this platform's targets/keywords
  service-lever/
  service-workday/
  service-wellfound/      # discovery only
  service-dashboard/
    generate.py           #   UNIFIED dashboard: reads Naukri jobs.db (RO) + MPA external.db
    docs/index.html       #   Overview | Naukri Jobs | Greenhouse | Lever | Workday | Wellfound | Q-Bank
  orchestrator/
    run_all.py            #   auto-loads every service-*/ and runs them in order on one DB
  run-mpa.bat             # double-click launcher (menu)
  test_mpa.py             # tests core + each service (incl. a mock browser fill+submit)
```

### How a service works
`service-<name>/service.py` exposes:
- `ATS` — the platform key (e.g. `"greenhouse"`).
- `build()` — reads this folder's `config.yaml` and returns the service.
- a `discover(limit=…)` method and/or an `async apply(page, job, *, auto_submit, resolver)` method.

The orchestrator **auto-discovers** every `service-*/` folder, so adding a new
platform = drop in a new `service-foo/` with a `service.py` of this shape.

### Core contracts (from `base.py`)
```python
@dataclass(frozen=True)
class DiscoveredJob:
    ats: str; company: str; job_title: str; external_url: str
    location: str = ""; posted_at: str = ""; apply_url: str = ""
    source_job_id: int | None = None; discovered_via: str = "api"  # api | web | naukri_redirect

class ApplyOutcome(str, Enum):
    SUBMITTED="submitted"; READY_FOR_REVIEW="ready_for_review"   # filled, stopped before submit
    WAITING_FOR_USER="waiting_for_user"; BLOCKED_CAPTCHA="blocked_captcha"
    DUPLICATE="duplicate"; FAILED="failed"

@dataclass
class ApplyResult:
    outcome: ApplyOutcome; message=""; screenshot_path=""; fields_filled: list[str]=[]
```

---

## 4. Human-in-the-loop (3 modes, 1 popup)

The popup gains a `mode` field; **absent `mode` = exact Naukri behavior (unaffected)**:
- `unknown_question`   → answer/options flow (scrollable, always-present "type your own answer" box).
- `email_verification` → context + text box → user types code → bot enters it (or "I clicked the link" → Resume → reload).
- `captcha_wait`       → "CAPTCHA detected. Solve in browser, then Resume." → single Resume button.

Browser stays open (`headless:false`); user acts in the real page, clicks Resume, bot continues.

---

## 5. Data model (MPA's own `external.db`)

```sql
external_applications (
  id INTEGER PRIMARY KEY,
  source_job_id INTEGER,            -- FK -> Naukri jobs (NULL if web-discovered)
  ats TEXT,                         -- greenhouse | lever | workday | other
  external_url TEXT,
  normalized_url TEXT UNIQUE,       -- hard dedup key
  discovered_via TEXT,              -- naukri_redirect | api | web
  status TEXT,                      -- queued -> filling -> ready_for_review ->
                                    --   submitted | blocked_captcha | waiting_for_user | duplicate | failed
  account_email TEXT,
  retry_count INTEGER DEFAULT 0,
  submitted_at TEXT
);

wellfound_contacts (
  id INTEGER PRIMARY KEY,
  company TEXT, job_title TEXT,
  job_url TEXT UNIQUE,
  poster_name TEXT, poster_linkedin_url TEXT, poster_wellfound_url TEXT,
  outreach_status TEXT DEFAULT 'new'   -- new | messaged | connected (user updates)
);
```
- **Dedup:** hard key `normalized_url`; soft key `(company + title)` so the same role
  via two URLs collapses. Known link → mark `duplicate`, skip (Naukri's rule).
- Single MPA question bank (`mpa_question_bank`) for learned answers.

---

## 6. ATS reality check (don't treat as one bucket)

| ATS | Account? | Email verify? | Form shape | Discovery (free) | Order |
|---|---|---|---|---|---|
| Greenhouse | No | No | Single page, semi-standard. Use the **embed form URL** `boards.greenhouse.io/embed/job_app?for={board}&token={id}` (renders the standard fillable form even for branded boards) | Public API `boards-api.greenhouse.io` | 1st apply |
| Lever | No | No | Single page, semi-standard. Read custom-question labels as real questions (not `cards[uuid][field0]`) | Public API `api.lever.co/v0/postings` | 2nd |
| Workday | **Yes, per tenant** | **Usually → popup** | Multi-step SPA, anti-bot | No clean API; link-driven | Last |
| Wellfound | login once | n/a | **Discovery only** (scrape poster + LinkedIn) | logged-in browser session | 1st overall |

---

## 7. Build order / roadmap (feature-at-a-time)

We build, then the **user tests & approves**, then next:

1. **mpa-core scaffold** — contracts, config (isolated paths), db schema+repo, link_router, http. Prove Naukri still runs.
2. **Wellfound discovery** — scrape contacts → DB. First visible win; manual outreach starts.
3. **Greenhouse apply** — API discover + embed-form fill (+ optional submit). Real auto-apply volume.
4. **Lever apply** — API discover + fill + custom-question labels.
5. **Unified dashboard** — reads Naukri jobs.db (RO) + external.db; Naukri CSS linked.
6. **Orchestrator** — auto-load services, sequential run, commands: default (discover+apply), `--discover-only`, `--wellfound`, `route <URL>...`.
7. **Workday** — `.env` creds, email-verify popup, CAPTCHA popup, multi-step SPA (hardest, last).
8. **HITL polish** — scrollable popup, type-your-own answer, 3 modes.

Each step ships with a test in `test_mpa.py` (incl. a mock browser fill+submit for apply services).

---

## 8. $0 cost plan

- **LLM:** free tiers (Groq/Gemini) already in Naukri stack.
- **Discovery:** Greenhouse/Lever public APIs; own logged-in browser sessions.
- **Enrichment:** free page signals only (poster name/LinkedIn). No paid enrichment.
- **Hosting:** static dashboard (GitHub Pages / local file).
- **No proxies / no rotating accounts.**

---

## 9. Run commands (target shape)

```bat
set PY=..\Naukri-Automation\.venv\Scripts\python.exe   :: reuse Naukri's venv (Playwright/loguru/pydantic)

%PY% orchestrator\run_all.py --discover-only   :: pull jobs into the one DB (no browser)
%PY% service-dashboard\generate.py             :: build the unified dashboard
%PY% orchestrator\run_all.py                    :: fill forms, stop before submit (default)
set MPA_AUTO_SUBMIT=true & %PY% orchestrator\run_all.py   :: actually SUBMIT
%PY% orchestrator\run_all.py --wellfound        :: scrape Wellfound contacts
%PY% orchestrator\run_all.py route <URL> ...    :: route external links
```

---

## 10. Known prerequisites / open items (carry forward)

- **Phone number** for forms is still blank in `candidate_overrides.json` — user must fill before auto-submitting.
- **Resume path:** `Portfolio/public/resume.pdf` (kept) is the resume used for forms — set this in `mpa-core/config/candidate_overrides.json` when rebuilt.
- **Wellfound login:** run once with `MPA_HEADLESS=false` and sign in to Wellfound in the MPA browser profile before scraping.
- **Auto-submit consent:** the system never clicks final Submit on real applications without the user setting `MPA_AUTO_SUBMIT=true` and answering HITL popups once in a live run.
- Workday free ≤24h discovery and Wellfound poster visibility on a free account are **unverified** — spike before relying on them.
- **Naukri jobs.db** (read-only by dashboard): table `jobs` cols include `job_title, company_name, status, job_url, location, created_at`.
