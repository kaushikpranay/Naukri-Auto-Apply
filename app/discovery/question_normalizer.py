"""
discovery/question_normalizer.py
Question normalization helpers for apply discovery.
"""

from __future__ import annotations

import re


_QUESTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ── Experience by technology ──────────────────────────────────────────────
    (re.compile(r"(python).*(experience|year)|(experience|year).*(python)", re.I), "python_experience"),
    (re.compile(r"(llm).*(experience|year)|(experience|year).*(llm)", re.I), "llm_experience"),
    (re.compile(r"(generative.?ai|genai).*(experience|year)|(experience|year).*(generative.?ai|genai)", re.I), "genai_experience"),
    (re.compile(r"\b(rag|retrieval.?augmented)\b.*(experience|year)|(experience|year).*\b(rag|retrieval.?augmented)\b", re.I), "rag_experience"),
    (re.compile(r"(langchain|langgraph).*(experience|year)|(experience|year).*(langchain|langgraph)", re.I), "langchain_experience"),
    (re.compile(r"(fastapi).*(experience|year)|(experience|year).*(fastapi)", re.I), "fastapi_experience"),
    (re.compile(r"\b(aws|amazon.web.services)\b.*(experience|year)|(experience|year).*\b(aws|amazon.web.services)\b", re.I), "aws_experience"),
    (re.compile(r"\b(machine.?learning|ml)\b.*(experience|year)|(experience|year).*\b(machine.?learning|ml)\b", re.I), "ml_experience"),
    (re.compile(r"\b(deep.?learning|dl)\b.*(experience|year)|(experience|year).*\b(deep.?learning|dl)\b", re.I), "dl_experience"),
    (re.compile(r"(nlp|natural.?language.?processing).*(experience|year)|(experience|year).*(nlp|natural.?language)", re.I), "nlp_experience"),
    (re.compile(r"\b(sql|database)\b.*(experience|year)|(experience|year).*\b(sql|database)\b", re.I), "sql_experience"),
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
    (re.compile(r"(in.?person.*interview|office.*interview|available.*interview.*office|available.*in.?person)", re.I), "available_person_interview_at_office_location"),
    (re.compile(r"(availability.*(?:attend|interview)|(?:attend|interview).*availability)", re.I), "interview_availability"),
    (re.compile(r"(notice period|current notice|serving notice|join.*days|days.*to.*join)", re.I), "notice_period"),
    # ── Location ─────────────────────────────────────────────────────────────
    (re.compile(r"(current location|present location|where.*(located|based))", re.I), "current_location"),
    (re.compile(r"(preferred location|work location preference)", re.I), "preferred_location"),
    # ── Role / employment ────────────────────────────────────────────────────
    (re.compile(r"(work.*(from.home|remote|hybrid)|remote.*(work|job)|wfh)", re.I), "work_mode_preference"),
    (re.compile(r"(current (company|employer|organization)|present (company|employer))", re.I), "current_company"),
    (re.compile(r"(current (role|designation|title)|present (role|designation))", re.I), "current_role"),
    (re.compile(r"(pass.*out|passed.*out|when.*graduate|graduation.*year|batch.*year|year.*pass.*out)", re.I), "when_did_pass_out_college"),
    (re.compile(r"(highest.*(qualification|degree)|education|graduation)", re.I), "education_qualification"),
    (re.compile(r"(immediately available|immediate joiner|can.*join.*immediately)", re.I), "immediate_availability"),
    (re.compile(r"(gender|pronouns)", re.I), "gender"),
    (re.compile(r"(disability|differently abled|pwd)", re.I), "disability_status"),
    (re.compile(r"(veteran|armed.force|military)", re.I), "veteran_status"),
    (re.compile(r"(career break)", re.I), "career_break"),
    (re.compile(r"(cover letter|write.*about|describe yourself|brief.*introduction)", re.I), "cover_letter"),
    (re.compile(r"(linkedin|github|portfolio|profile.url)", re.I), "profile_links"),
]


_STOP_WORDS = frozenset({"are", "you", "is", "the", "a", "an", "do", "does", "have", "your", "to", "in", "of", "for", "on", "with", "how", "many", "what", "please", "specify", "select"})


def _stable_slug(text: str) -> str:
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", text).split() if w not in _STOP_WORDS]
    return "_".join(words)[:60].rstrip("_") or "unknown_question"


def _normalize_option_values(options: list[str] | None) -> list[str]:
    if not options:
        return []

    normalized: list[str] = []
    for option in options:
        cleaned = " ".join(str(option).strip().lower().split())
        if cleaned:
            normalized.append(cleaned)
    return normalized


def _is_yes_no_option_set(options: list[str]) -> bool:
    if not options:
        return False

    ignored = {"skip this question", "skip", "none of the above"}
    meaningful = [opt for opt in options if opt not in ignored]
    if not meaningful:
        return False

    return set(meaningful).issubset({"yes", "no", "y", "n", "true", "false"})


def _resolve_relocation_question_key(
    normalized_text: str,
    normalized_options: list[str],
) -> str | None:
    if "relocat" not in normalized_text:
        return None

    if _is_yes_no_option_set(normalized_options):
        return "willing_to_relocate"

    city_selector_pattern = re.compile(
        r"(select|choose).*(city|location)|(city|location).*(residing|reside|relocat|move)",
        re.I,
    )
    yes_no_relocation_pattern = re.compile(
        r"are you .*?(residing|located|based).*?(or|and).*?relocat",
        re.I,
    )

    if city_selector_pattern.search(normalized_text):
        return "relocation_city_preference"

    if yes_no_relocation_pattern.search(normalized_text):
        return "willing_to_relocate"

    if normalized_options and not _is_yes_no_option_set(normalized_options):
        return "relocation_city_preference"

    return "willing_to_relocate"


def normalize_question_key(question_text: str, options: list[str] | None = None) -> str:
    """Map similar application questions to a stable key.

    First tries pattern matching against known question families.
    Falls back to a slug derived from the question text (max 60 chars).
    """
    normalized_text = " ".join(question_text.lower().strip().split())
    normalized_options = _normalize_option_values(options)

    def _slugify(t: str) -> str:
        cleaned = re.sub(r"[^a-z0-9+#\.]+", "_", t.lower().strip())
        cleaned = cleaned.replace("+", "p").replace("#", "sharp").replace(".", "dot")
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned

    # Rule 1: Skill/tool experience questions
    if "experience in" in normalized_text or "experience with" in normalized_text:
        match = re.search(r"experience (?:in|with)\s+([a-z0-9\s+#\.\-]+)", normalized_text)
        if match:
            skill = match.group(1).strip()
            skill = re.sub(r"[^a-zA-Z0-9+#\.]+$", "", skill)
            if skill:
                return f"exp_{_slugify(skill)}"

    # Rule 2: Location/relocation questions
    if "relocate to" in normalized_text:
        match = re.search(r"relocate to\s+([a-z0-9\s+#\.\-]+)", normalized_text)
        if match:
            city = match.group(1).strip()
            city = re.sub(r"[^a-zA-Z0-9]+$", "", city)
            if city:
                return f"willing_to_relocate_{_slugify(city)}"

    # Rule 3: Yes/no preference questions
    if "open for" in normalized_text:
        match = re.search(r"open for\s+([a-z0-9\s+#\.\-]+)", normalized_text)
        if match:
            condition = match.group(1).strip()
            condition = re.sub(r"[^a-zA-Z0-9]+$", "", condition)
            if condition:
                return f"open_for_{_slugify(condition)}"

    # Rule 4: Generic total experience
    if any(p in normalized_text for p in [
        "total years of experience", "total experience",
        "overall experience", "total work experience",
        "relevant experience", "years of relevant"
    ]):
        return "total_years_experience"

    relocation_key = _resolve_relocation_question_key(
        normalized_text,
        normalized_options,
    )
    if relocation_key:
        return relocation_key

    for pattern, key in _QUESTION_PATTERNS:
        if pattern.search(normalized_text):
            if key == "total_experience":
                return "total_years_experience"
            return key

    return _stable_slug(normalized_text)
