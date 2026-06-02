# Naukri Job Collector — POC-1

A production-grade Python application that collects job listings from Naukri.com and stores them locally in SQLite + Excel.

## Features

- **Persistent Browser Session** — Reuses Playwright Chromium session across runs (login once, run forever)
- **Multi-Keyword Search** — Searches 8 AI/Python keywords across 10 Indian cities
- **Full Data Extraction** — Collects title, company, description, recruiter info, and more
- **URL Normalization** — Strips UTM/tracking params for reliable deduplication
- **SQLite Storage** — Persistent database with automatic deduplication on normalized URL
- **Excel Export** — Auto-generates timestamped `.xlsx` files with formatted columns
- **Config-Driven** — All selectors, keywords, and locations in YAML files
- **Structured Logging** — Loguru with file rotation and colored console output
- **Error Screenshots** — Automatic screenshots on login detection or exceptions

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright Browser

```bash
playwright install chromium
```

### 3. First Run — Manual Login

```bash
python main.py
```

On the first run, the browser will open to Naukri.com. Since no session exists:
1. The app will detect the login page
2. A screenshot will be saved to `screenshots/`
3. The app will exit with a message

**To login:**
1. Run `python main.py` again
2. When the browser opens, manually login to Naukri
3. Close the browser
4. Your session is now saved in `browser_profile/`

### 4. Subsequent Runs

```bash
python main.py
```

The app will reuse your saved session and collect jobs automatically.

## Output

```
╔══════════════════════════════════════════════════╗
║       NAUKRI JOB COLLECTOR — POC-1              ║
╚══════════════════════════════════════════════════╝

==================================================
  COLLECTION SUMMARY
==================================================
  Jobs Found:    83
  Inserted:      51
  Duplicates:    32
  Export:        Success
  Export File:   exports/jobs_2026_06_01.xlsx
==================================================
```

## Project Structure

```
├── app/
│   ├── browser/          # Playwright session management
│   │   └── session.py
│   ├── collector/        # Job scraping engine
│   │   └── job_collector.py
│   ├── database/         # SQLite repository
│   │   └── repository.py
│   ├── export/           # Excel exporter
│   │   └── excel_exporter.py
│   ├── models/           # Pydantic data models
│   │   ├── config.py
│   │   └── job.py
│   └── utils/            # Utilities
│       ├── config_loader.py
│       ├── screenshot.py
│       └── url_normalizer.py
│
├── config/               # YAML configuration
│   ├── settings.yaml     # Global settings
│   ├── selectors.yaml    # CSS selectors
│   └── locations.yaml    # Keywords & locations
│
├── browser_profile/      # Persistent browser data (auto-created)
├── database/             # Legacy database location kept for migration support
├── exports/              # Excel exports (auto-created)
├── logs/                 # Log files (auto-created)
├── screenshots/          # Error screenshots (auto-created)
├── tests/                # Test suite
│
├── main.py               # CLI entry point
├── requirements.txt      # Python dependencies
└── README.md
```

## Configuration

### `config/settings.yaml`
Global settings — browser options, paths, timeouts, Naukri URLs.

### `config/selectors.yaml`
All CSS selectors used for scraping. Update these when Naukri changes its DOM — no code changes needed.

### `config/locations.yaml`
Search keywords and locations with their URL slugs.

## Database

SQLite database at `jobs.db`:

| Field | Type | Constraint |
|---|---|---|
| `id` | INTEGER | PRIMARY KEY |
| `job_title` | TEXT | NOT NULL |
| `company_name` | TEXT | NOT NULL |
| `job_description` | TEXT | — |
| `job_url` | TEXT | NOT NULL |
| `normalized_url` | TEXT | UNIQUE |
| `apply_url` | TEXT | — |
| `experience_required` | TEXT | — |
| `location` | TEXT | — |
| `posted_date` | TEXT | — |
| `recruiter_name` | TEXT | — |
| `recruiter_email` | TEXT | — |
| `created_at` | TEXT | NOT NULL |

## Running Tests

```bash
pytest tests/ -v
```

## Tech Stack

- **Python 3.12**
- **Playwright** — Browser automation
- **Pydantic** — Data validation
- **SQLite** — Local storage
- **Pandas + OpenPyXL** — Excel export
- **Loguru** — Structured logging
- **PyYAML** — Configuration
