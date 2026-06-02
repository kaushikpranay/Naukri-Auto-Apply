"""
Normalization helpers for provider JSON output.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


_ALLOWED_RESUMES: set[str] = {"GENAI", "APPLIED_AI", "PYTHON", "ML", "STARTUP"}
_PRIORITY_MAP: dict[str, str] = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}
_ACTION_MAP: dict[str, str] = {
    "APPLY": "apply",
    "REVIEW": "review",
    "SKIP": "skip",
}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_percentage(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")

    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(f"{field_name} cannot be empty")
        numeric = float(value)
    else:
        numeric = float(value)

    # Accept probability-style inputs like 0.85 and store them as 85.
    if 0.0 <= numeric <= 1.0:
        numeric *= 100.0

    percentage = int(round(numeric))
    if percentage < 0 or percentage > 100:
        raise ValueError(f"{field_name} must be between 0 and 100")
    return percentage


def _normalize_resume(value: Any) -> str:
    raw = _as_text(value).upper()
    normalized = re.sub(r"[^A-Z0-9]+", "_", raw).strip("_")
    normalized = normalized.replace("RESUME_", "")
    normalized = normalized.replace("_PDF", "")
    if normalized in _ALLOWED_RESUMES:
        return normalized

    if normalized in {"GENAI", "APPLIED_AI", "PYTHON", "ML", "STARTUP"}:
        return normalized

    logger.warning("Unknown recommended_resume value %r; converting to STARTUP", value)
    return "STARTUP"


def _normalize_priority(value: Any) -> str:
    normalized = _as_text(value).upper()
    if normalized not in _PRIORITY_MAP:
        raise ValueError(f"Invalid priority value: {value!r}")
    return _PRIORITY_MAP[normalized]


def _normalize_action(value: Any) -> str:
    normalized = _as_text(value).upper()
    if normalized not in _ACTION_MAP:
        raise ValueError(f"Invalid action value: {value!r}")
    return _ACTION_MAP[normalized]


def _normalize_missing_skills(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    return [str(value).strip()]


def normalize_provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize raw provider payload into the canonical evaluation schema.
    """
    required_fields = [
        "interview_probability",
        "recommended_resume",
        "priority",
        "action",
        "confidence",
        "reason",
    ]
    for field in required_fields:
        if field not in payload:
            raise ValueError(f"Missing required field: {field}")

    reason = _as_text(payload["reason"])
    if not reason:
        raise ValueError("reason cannot be empty")

    return {
        "interview_probability": _normalize_percentage(
            payload["interview_probability"], "interview_probability"
        ),
        "recommended_resume": _normalize_resume(payload["recommended_resume"]),
        "priority": _normalize_priority(payload["priority"]),
        "action": _normalize_action(payload["action"]),
        "confidence": _normalize_percentage(payload["confidence"], "confidence"),
        "reason": reason,
        "missing_skills": _normalize_missing_skills(payload.get("missing_skills")),
    }
