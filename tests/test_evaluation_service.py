"""
Tests for the AI evaluation orchestration layer.
"""

from pathlib import Path

from app.database.evaluations_repo import EvaluationsRepository
from app.database.repository import JobRepository
from app.evaluator.evaluation_service import EvaluationService
from app.evaluator.errors import ProviderQuotaError
from app.models.evaluation import EvaluationResult
from app.models.job import JobData


class _QuotaFailingProvider:
    provider_name = "Groq"
    model_id = "groq-test"

    def evaluate_job(self, job: JobData) -> EvaluationResult:  # noqa: ARG002
        raise ProviderQuotaError("quota exceeded")


class _SuccessfulFallbackProvider:
    provider_name = "Gemini"
    model_id = "gemini-test"

    def evaluate_job(self, job: JobData) -> EvaluationResult:
        return EvaluationResult(
            interview_probability=88,
            recommended_resume="GENAI",
            priority="high",
            action="apply",
            confidence=81,
            reason=f"Strong fit for {job.job_title}.",
            missing_skills=["Azure OpenAI"],
        )


def _create_repo_with_jobs(db_path: Path) -> EvaluationsRepository:
    job_repo = JobRepository(db_path)
    job_repo.insert_job(
        JobData(
            job_title="AI Engineer",
            company_name="TestCorp",
            job_url="https://naukri.com/job/1",
            normalized_url="https://naukri.com/job/1",
        )
    )
    job_repo.insert_job(
        JobData(
            job_title="AI Engineer",
            company_name="TestCorp",
            job_url="https://naukri.com/job/2",
            normalized_url="https://naukri.com/job/2",
        )
    )
    job_repo.insert_job(
        JobData(
            job_title="ML Engineer",
            company_name="OtherCorp",
            job_url="https://naukri.com/job/3",
            normalized_url="https://naukri.com/job/3",
        )
    )
    job_repo.close()
    return EvaluationsRepository(db_path)


class TestEvaluationService:
    """Test suite for provider switching and evaluation storage."""

    def test_quota_failure_falls_back_to_next_provider(self, tmp_path: Path) -> None:
        """Groq quota failure should fall back to Gemini and store the result."""
        repo = _create_repo_with_jobs(tmp_path / "jobs.db")
        try:
            service = EvaluationService(
                repo=repo,
                providers=[_QuotaFailingProvider(), _SuccessfulFallbackProvider()],
                max_jobs_per_run=20,
            )

            stats = service.run("run-123")

            assert stats.evaluated == 2
            assert stats.apply == 2
            assert stats.review == 0
            assert stats.skip == 0

            rows = repo._conn.execute(
                """
                SELECT job_id, model_name, interview_probability, recommended_resume,
                       priority, action, confidence, reason
                FROM ai_evaluations
                ORDER BY job_id ASC
                """
            ).fetchall()
            assert len(rows) == 2
            assert rows[0]["model_name"] == "Gemini"
            assert rows[0]["interview_probability"] == 88
            assert rows[0]["recommended_resume"] == "GENAI"
            assert rows[1]["job_id"] == 3
        finally:
            repo.close()
