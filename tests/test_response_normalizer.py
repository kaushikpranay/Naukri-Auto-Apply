"""
Tests for provider response normalization.
"""

import pytest

from app.evaluator.response_normalizer import normalize_provider_payload


class TestResponseNormalizer:
    """Test suite for percentage scaling and output shape."""

    def test_probability_style_scores_are_scaled_to_percentage(self) -> None:
        """Scores in the 0..1 range should be stored on a 0..100 scale."""
        payload = normalize_provider_payload(
            {
                "interview_probability": 0.85,
                "recommended_resume": "genai",
                "priority": "HIGH",
                "action": "APPLY",
                "confidence": 0.72,
                "reason": "Strong fit.",
                "missing_skills": ["Azure OpenAI"],
            }
        )

        assert payload["interview_probability"] == 85
        assert payload["confidence"] == 72

    def test_whole_number_scores_are_preserved(self) -> None:
        """Whole-number inputs should stay on the same 0..100 scale."""
        payload = normalize_provider_payload(
            {
                "interview_probability": 100,
                "recommended_resume": "ML",
                "priority": "MEDIUM",
                "action": "REVIEW",
                "confidence": 1,
                "reason": "Needs review.",
                "missing_skills": [],
            }
        )

        assert payload["interview_probability"] == 100
        assert payload["confidence"] == 100

    def test_missing_required_field_raises(self) -> None:
        """Normalization should still fail if a required field is missing."""
        with pytest.raises(ValueError):
            normalize_provider_payload(
                {
                    "interview_probability": 0.5,
                    "recommended_resume": "GENAI",
                    "priority": "HIGH",
                    "action": "APPLY",
                    "reason": "Missing confidence.",
                }
            )
