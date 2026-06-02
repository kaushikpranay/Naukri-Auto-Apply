"""
Orchestration for AI job evaluations.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from loguru import logger

from app.database.evaluations_repo import EvaluationsRepository
from app.evaluator.errors import (
    ProviderConfigurationError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderValidationError,
)
from app.evaluator.providers.base_evaluator import BaseEvaluator
from app.models.evaluation import EvaluationResult, JobEvaluation


@dataclass
class EvaluationBatchStats:
    """Summary statistics for a single evaluation batch."""

    evaluated: int = 0
    apply: int = 0
    review: int = 0
    skip: int = 0
    errors: int = 0


class EvaluationService:
    """
    Evaluates pending jobs using a provider chain.
    """

    def __init__(
        self,
        repo: EvaluationsRepository,
        providers: list[BaseEvaluator],
        max_jobs_per_run: int,
        prompt_version: str = "v2.0",
    ) -> None:
        self._repo = repo
        self._providers = providers
        self._max_jobs_per_run = max_jobs_per_run
        self._prompt_version = prompt_version

    def run(self, run_id: str) -> EvaluationBatchStats:
        """Evaluate at most ``max_jobs_per_run`` pending jobs."""
        stats = EvaluationBatchStats()
        jobs = self._repo.get_pending_jobs_for_evaluation(limit=self._max_jobs_per_run)

        if not jobs:
            logger.info("No pending jobs available for evaluation.")
            return stats

        logger.info("Found {} pending job(s) for evaluation.", len(jobs))

        for index, job in enumerate(jobs, start=1):
            logger.info("Evaluating [{}/{}]: {}", index, len(jobs), job.job_title[:80])

            started_at = perf_counter()
            evaluation, provider_used = self._evaluate_with_fallback(job)
            duration = perf_counter() - started_at

            self._store_result(job.id, run_id, evaluation, provider_used)
            self._repo.mark_job_evaluated(int(job.id))

            stats.evaluated += 1
            stats.apply += 1 if evaluation.action == "apply" else 0
            stats.review += 1 if evaluation.action == "review" else 0
            stats.skip += 1 if evaluation.action == "skip" else 0

            logger.info(
                "Evaluation success for job {} via {} in {:.2f}s",
                job.id,
                provider_used,
                duration,
            )

        return stats

    def _evaluate_with_fallback(self, job) -> tuple[EvaluationResult, str]:
        """Try Groq first, then Gemini, then return a review result."""
        last_error: Exception | None = None

        for provider in self._providers:
            logger.info(
                "Provider selected: {} ({}) for job {}",
                provider.provider_name,
                provider.model_id,
                job.id,
            )
            try:
                result = provider.evaluate_job(job)
                return result, provider.provider_name
            except ProviderQuotaError as exc:
                last_error = exc
                logger.warning(
                    "Quota failure from {} for job {}: {}",
                    provider.provider_name,
                    job.id,
                    exc,
                )
            except (ProviderValidationError, ProviderTransientError, ProviderConfigurationError) as exc:
                last_error = exc
                logger.warning(
                    "Provider failure from {} for job {}: {}",
                    provider.provider_name,
                    job.id,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.error(
                    "Unexpected provider failure from {} for job {}: {}",
                    provider.provider_name,
                    job.id,
                    exc,
                )

        logger.warning(
            "All providers failed for job {}. Marking as requires review.",
            job.id,
        )
        return self._requires_review_result(last_error), "Requires Review"

    def _requires_review_result(self, last_error: Exception | None) -> EvaluationResult:
        reason = "Requires review after provider failures."
        if last_error:
            reason = f"{reason} Last error: {last_error}"

        return EvaluationResult(
            interview_probability=0,
            recommended_resume="STARTUP",
            priority="medium",
            action="review",
            confidence=0,
            reason=reason,
            missing_skills=[],
        )

    def _store_result(
        self,
        job_id: int | None,
        run_id: str,
        evaluation: EvaluationResult,
        provider_used: str,
    ) -> None:
        if job_id is None:
            raise ValueError("job_id is required to store an evaluation")

        record = JobEvaluation(
            job_id=int(job_id),
            run_id=run_id,
            model_name=provider_used,
            prompt_version=self._prompt_version,
            interview_probability=evaluation.interview_probability,
            recommended_resume=evaluation.recommended_resume,
            priority=evaluation.priority,
            action=evaluation.action,
            confidence=evaluation.confidence,
            reason=evaluation.reason,
            missing_skills=evaluation.missing_skills,
        )
        self._repo.insert_evaluation(record)
