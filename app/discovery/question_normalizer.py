"""
Question normalization helpers for apply discovery.
"""

from __future__ import annotations

import re


_QUESTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(python).*(experience)|(experience).*(python)", re.I), "python_experience"),
    (re.compile(r"(llm).*(experience)|(experience).*(llm)", re.I), "llm_experience"),
    (re.compile(r"(notice period|current notice period|availability)", re.I), "notice_period"),
    (re.compile(r"(current ctc|ctc currently|current salary)", re.I), "current_ctc"),
    (re.compile(r"(expected ctc|expected salary)", re.I), "expected_ctc"),
    (re.compile(r"(current location|present location)", re.I), "current_location"),
    (re.compile(r"(willing to relocate|open to relocate)", re.I), "willing_to_relocate"),
]


def normalize_question_key(question_text: str) -> str:
    """Map similar application questions to a stable key."""
    normalized_text = " ".join(question_text.lower().strip().split())
    for pattern, key in _QUESTION_PATTERNS:
        if pattern.search(normalized_text):
            return key

    slug = re.sub(r"[^a-z0-9]+", "_", normalized_text).strip("_")
    return slug or "unknown_question"
