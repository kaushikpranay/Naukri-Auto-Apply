"""
Base evaluator implementation shared by all AI providers.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from string import Template
from typing import Any

from loguru import logger

from app.evaluator.errors import (
    ProviderConfigurationError,
    ProviderError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderValidationError,
)
from app.evaluator.response_normalizer import normalize_provider_payload
from app.models.evaluation import EvaluationResult
from app.models.job import JobData


class BaseEvaluator(ABC):
    """Abstract base class for provider-backed evaluators."""

    provider_name: str
    model_id: str
    max_attempts: int = 2

    def __init__(self, prompt_path: Path, profile_path: Path) -> None:
        self._prompt_template = Template(prompt_path.read_text(encoding="utf-8"))
        profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
        self._candidate_profile_text = json.dumps(profile_data, indent=2, ensure_ascii=False)

    def evaluate_job(self, job: JobData) -> EvaluationResult:
        """
        Evaluate a single job using the provider implementation.

        JSON/schema failures are retried once. Quota failures are surfaced
        immediately so the pipeline can switch providers.
        """
        prompt = self._render_prompt(job)
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                raw_response = self._generate_response(prompt)
                payload = self._parse_response(raw_response)
                normalized_payload = normalize_provider_payload(payload)
                return EvaluationResult(**normalized_payload)
            except ProviderQuotaError:
                raise
            except ProviderConfigurationError:
                raise
            except ProviderValidationError as exc:
                last_error = exc
                logger.warning(
                    "{} JSON validation failure on attempt {}/{} for job {}: {}",
                    self.provider_name,
                    attempt,
                    self.max_attempts,
                    job.id,
                    exc,
                )
            except ProviderTransientError as exc:
                last_error = exc
                logger.warning(
                    "{} transient failure on attempt {}/{} for job {}: {}",
                    self.provider_name,
                    attempt,
                    self.max_attempts,
                    job.id,
                    exc,
                )
            except ProviderError as exc:
                last_error = exc
                logger.warning(
                    "{} provider failure on attempt {}/{} for job {}: {}",
                    self.provider_name,
                    attempt,
                    self.max_attempts,
                    job.id,
                    exc,
                )
            except Exception as exc:
                last_error = ProviderTransientError(str(exc))
                logger.warning(
                    "{} unexpected failure on attempt {}/{} for job {}: {}",
                    self.provider_name,
                    attempt,
                    self.max_attempts,
                    job.id,
                    exc,
                )

        message = f"{self.provider_name} failed to return valid JSON after {self.max_attempts} attempts"
        if last_error:
            message = f"{message}: {last_error}"
        raise ProviderValidationError(message)

    def _render_prompt(self, job: JobData) -> str:
        return self._prompt_template.safe_substitute(
            candidate_profile=self._candidate_profile_text,
            job_title=job.job_title,
            job_description=job.job_description or "No description provided.",
            experience_required=job.experience_required or "Unknown",
            location=job.location or "Unknown",
        )

    def _parse_response(self, raw_response: str) -> dict[str, Any]:
        text = raw_response.strip()
        text = self._strip_code_fences(text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            extracted = self._extract_json_object(text)
            if extracted is None:
                raise ProviderValidationError(f"Invalid JSON from {self.provider_name}: {exc}") from exc
            try:
                payload = json.loads(extracted)
            except json.JSONDecodeError as inner_exc:
                raise ProviderValidationError(
                    f"Invalid JSON from {self.provider_name}: {inner_exc}"
                ) from inner_exc

        if not isinstance(payload, dict):
            raise ProviderValidationError(
                f"{self.provider_name} returned a non-object JSON payload"
            )
        return payload

    def _strip_code_fences(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
            if stripped.endswith("```"):
                stripped = stripped[:-3].strip()
        return stripped

    def _extract_json_object(self, text: str) -> str | None:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        return match.group(0) if match else None

    @abstractmethod
    def _generate_response(self, prompt: str) -> str:
        """Return the raw text response from the provider."""
