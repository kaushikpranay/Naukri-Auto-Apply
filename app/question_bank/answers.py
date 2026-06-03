"""
Candidate answers registry — auto-populated from config/candidate_profile.json.

Architecture:
    1. _DIRECT_MAPPINGS  — profile JSON fields mapped 1:1 to question_bank keys.
    2. _EXPERIENCE_KEYS  — experience_years spread across all generic experience keys.
    3. _STATIC_ANSWERS   — skill-specific experience levels and compliance fields
                           that are NOT in the profile JSON and must be maintained here.

To update a value, change it in config/candidate_profile.json (for identity /
contact / CTC / notice / experience fields) or in _STATIC_ANSWERS (for
skill-specific experience, preferences, and compliance fields).

The seeder propagates any change to the question_bank table on the next run.
"""

from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Profile path — resolved relative to this file's location
# ---------------------------------------------------------------------------

_PROFILE_PATH: Path = (
    Path(__file__).parent.parent.parent / "config" / "candidate_profile.json"
)

# ---------------------------------------------------------------------------
# Profile field → question_bank key (direct 1-to-1 mappings)
# ---------------------------------------------------------------------------

_DIRECT_MAPPINGS: dict[str, str] = {
    "first_name":           "first_name",
    "last_name":            "last_name",
    "full_name":            "full_name",
    "email":                "email",
    "phone":                "phone",
    "country":              "country",
    "city":                 "current_location",
    "state":                "state",
    "linkedin_profile_url": "linkedin_profile_url",
    "github_url":           "github_url",
    "portfolio_url":        "portfolio_url",
    "current_company":      "current_company",
    "current_role":         "current_role",
    "notice_period":        "notice_period",
    "current_ctc":          "current_ctc",
    "expected_ctc":         "expected_ctc",
}

# experience_years is spread across all generic experience question keys
_EXPERIENCE_KEYS: tuple[str, ...] = (
    "total_experience",
    "relevant_experience",
    "experience_years",
)

# ---------------------------------------------------------------------------
# Static answers — values NOT sourced from candidate_profile.json
# ---------------------------------------------------------------------------

_STATIC_ANSWERS: dict[str, str] = {
    # ── Experience by technology ───────────────────────────────────────────
    "python_experience":        "2",
    "llm_experience":           "1.5",
    "genai_experience":         "1.5",
    "rag_experience":           "1.5",
    "langchain_experience":     "1.5",
    "fastapi_experience":       "2",
    "aws_experience":           "1.5",
    "ml_experience":            "2",
    "dl_experience":            "1",
    "nlp_experience":           "1",
    "sql_experience":           "2",
    "devops_experience":        "0.5",
    "web_framework_experience": "1",
    "ml_framework_experience":  "1",
    "cloud_experience":         "1.5",
    # ── Location preferences ───────────────────────────────────────────────
    "willing_to_relocate":      "Yes",
    "preferred_location":       "Noida, Gurugram, Pune, Kolkata, Hyderabad, Bangalore, Remote",
    # ── Employment preferences ─────────────────────────────────────────────
    "work_mode_preference":     "Work from Office",
    "education_qualification":  "B.Tech in Computer Science",
    "immediate_availability":   "No - 45 days notice period",
    # ── Personal / compliance ──────────────────────────────────────────────
    "gender":                   "Male",
    "disability_status":        "No",
    "veteran_status":           "No",
    # ── Profile / misc ─────────────────────────────────────────────────────
    "profile_links":            "https://github.com/kaushikpranay",
    "cover_letter": (
        "I am an AI Engineer with 2 years of experience specialising in "
        "Generative AI, RAG systems, LangGraph, FastAPI, and AWS. I have built "
        "production-grade LLM applications and agentic workflows and am excited "
        "to bring this expertise to your team."
    ),
}


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def _build_candidate_answers() -> dict[str, str]:
    """
    Build the full answer registry.

    Priority order (highest wins):
        profile JSON  >  derived values  >  _STATIC_ANSWERS
    """
    answers: dict[str, str] = {}

    # 1. Load profile
    try:
        profile: dict = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        profile = {}

    # 2. Direct field mappings from profile
    for profile_key, question_key in _DIRECT_MAPPINGS.items():
        val = profile.get(profile_key)
        if val is not None and str(val).strip():
            answers[question_key] = str(val).strip()

    # 3. Derive full_name from first + last if not explicit in profile
    if "full_name" not in answers:
        first = answers.get("first_name", "")
        last = answers.get("last_name", "")
        if first and last:
            answers["full_name"] = f"{first} {last}"

    # 4. Spread experience_years across all generic experience question keys
    exp = profile.get("experience_years")
    if exp is not None:
        exp_str = str(exp)
        for key in _EXPERIENCE_KEYS:
            if key not in answers:
                answers[key] = exp_str

    # 5. Merge static answers — only fill keys not already set by profile
    for key, val in _STATIC_ANSWERS.items():
        if key not in answers and val:
            answers[key] = val

    return answers


# ---------------------------------------------------------------------------
# Public export
# ---------------------------------------------------------------------------

CANDIDATE_ANSWERS: dict[str, str] = _build_candidate_answers()
