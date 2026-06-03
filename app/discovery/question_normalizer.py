"""
Question normalization helpers for apply discovery.
"""

from __future__ import annotations

import re


_QUESTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ── Experience by technology ──────────────────────────────────────────────
    (re.compile(r"(python).*(experience|year)|(experience|year).*(python)", re.I), "python_experience"),
    (re.compile(r"(llm).*(experience|year)|(experience|year).*(llm)", re.I), "llm_experience"),
    (re.compile(r"(generative.?ai|genai).*(experience|year)|(experience|year).*(generative.?ai|genai)", re.I), "genai_experience"),
    (re.compile(r"(rag|retrieval.?augmented).*(experience|year)|(experience|year).*(rag|retrieval.?augmented)", re.I), "rag_experience"),
    (re.compile(r"(langchain|langgraph).*(experience|year)|(experience|year).*(langchain|langgraph)", re.I), "langchain_experience"),
    (re.compile(r"(fastapi).*(experience|year)|(experience|year).*(fastapi)", re.I), "fastapi_experience"),
    (re.compile(r"(aws|amazon.web.services).*(experience|year)|(experience|year).*(aws|amazon.web.services)", re.I), "aws_experience"),
    (re.compile(r"(machine.?learning|ml).*(experience|year)|(experience|year).*(machine.?learning|ml)", re.I), "ml_experience"),
    (re.compile(r"(deep.?learning|dl).*(experience|year)|(experience|year).*(deep.?learning|dl)", re.I), "dl_experience"),
    (re.compile(r"(nlp|natural.?language.?processing).*(experience|year)|(experience|year).*(nlp|natural.?language)", re.I), "nlp_experience"),
    (re.compile(r"(sql|database).*(experience|year)|(experience|year).*(sql|database)", re.I), "sql_experience"),
    (re.compile(r"(docker|kubernetes|k8s).*(experience|year)|(experience|year).*(docker|kubernetes|k8s)", re.I), "devops_experience"),
    (re.compile(r"(flask|django).*(experience|year)|(experience|year).*(flask|django)", re.I), "web_framework_experience"),
    (re.compile(r"(tensorflow|pytorch|keras).*(experience|year)|(experience|year).*(tensorflow|pytorch|keras)", re.I), "ml_framework_experience"),
    (re.compile(r"(azure|gcp|google.cloud).*(experience|year)|(experience|year).*(azure|gcp|google.cloud)", re.I), "cloud_experience"),
    # General total experience
    (re.compile(r"(total|overall|how many).*(experience|year)|(experience|year).*(total|overall)", re.I), "total_experience"),
    (re.compile(r"relevant.*(experience|year)|(experience|year).*relevant", re.I), "relevant_experience"),
    # ── CTC / Salary ─────────────────────────────────────────────────────────
    (re.compile(r"(current ctc|ctc currently|current salary|present ctc)", re.I), "current_ctc"),
    (re.compile(r"(expected ctc|expected salary|expected package|salary expectation)", re.I), "expected_ctc"),
    # ── Notice period ────────────────────────────────────────────────────────
    (re.compile(r"(notice period|current notice|serving notice|availability|join.*days)", re.I), "notice_period"),
    # ── Location ─────────────────────────────────────────────────────────────
    (re.compile(r"(current location|present location|where.*(located|based))", re.I), "current_location"),
    (re.compile(r"(willing to relocate|open to relocate|ready to relocate|relocat)", re.I), "willing_to_relocate"),
    (re.compile(r"(preferred location|work location preference)", re.I), "preferred_location"),
    # ── Role / employment ────────────────────────────────────────────────────
    (re.compile(r"(work.*(from.home|remote|hybrid)|remote.*(work|job)|wfh)", re.I), "work_mode_preference"),
    (re.compile(r"(current (company|employer|organization)|present (company|employer))", re.I), "current_company"),
    (re.compile(r"(current (role|designation|title)|present (role|designation))", re.I), "current_role"),
    (re.compile(r"(highest.*(qualification|degree)|education|graduation)", re.I), "education_qualification"),
    (re.compile(r"(immediately available|immediate joiner|can.*join.*immediately)", re.I), "immediate_availability"),
    (re.compile(r"(gender|pronouns)", re.I), "gender"),
    (re.compile(r"(disability|differently abled|pwd)", re.I), "disability_status"),
    (re.compile(r"(veteran|armed.force|military)", re.I), "veteran_status"),
    (re.compile(r"(cover letter|write.*about|describe yourself|brief.*introduction)", re.I), "cover_letter"),
    (re.compile(r"(linkedin|github|portfolio|profile.url)", re.I), "profile_links"),
]


def normalize_question_key(question_text: str) -> str:
    """Map similar application questions to a stable key.

    First tries pattern matching against known question families.
    Falls back to a slug derived from the question text (max 60 chars).
    """
    normalized_text = " ".join(question_text.lower().strip().split())
    for pattern, key in _QUESTION_PATTERNS:
        if pattern.search(normalized_text):
            return key

    slug = re.sub(r"[^a-z0-9]+", "_", normalized_text).strip("_")
    slug = slug[:60].rstrip("_")
    return slug or "unknown_question"
