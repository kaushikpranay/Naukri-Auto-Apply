"""
Tests for the AI evaluation orchestration layer.
"""

from pathlib import Path

from app.database.evaluations_repo import EvaluationsRepository
from app.database.repository import JobRepository
from app.evaluator.evaluation_service import EvaluationService, parse_min_experience
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

    def test_experience_filtering_exact_and_near_match(self, tmp_path: Path) -> None:
        """Jobs with exact/near match experience should be evaluated, mismatch skipped."""
        import json
        from loguru import logger
        
        # Create a candidate profile file with 2 years of experience and max threshold of 4
        profile_path = tmp_path / "candidate_profile.json"
        profile_path.write_text(json.dumps({
            "experience_years": 2.0,
            "max_target_min_experience": 4
        }), encoding="utf-8")
        
        job_repo = JobRepository(tmp_path / "jobs.db")
        # 1. Exact match (requires 2 years)
        job_repo.insert_job(
            JobData(
                job_title="Exact Fit Developer",
                company_name="ExactCorp",
                job_url="https://naukri.com/job/exact",
                normalized_url="https://naukri.com/job/exact",
                experience_required="2-5 Yrs",
            )
        )
        # 2. Near match (requires 3 years, short by 1 year)
        job_repo.insert_job(
            JobData(
                job_title="Near Match Developer",
                company_name="NearCorp",
                job_url="https://naukri.com/job/near",
                normalized_url="https://naukri.com/job/near",
                experience_required="3-8 Yrs",
            )
        )
        # 3. Mismatch (requires 5 years, short by 3 years)
        job_repo.insert_job(
            JobData(
                job_title="Mismatch Developer",
                company_name="FarCorp",
                job_url="https://naukri.com/job/far",
                normalized_url="https://naukri.com/job/far",
                experience_required="5-10 Yrs",
            )
        )
        job_repo.close()
        
        repo = EvaluationsRepository(tmp_path / "jobs.db")
        try:
            provider = _SuccessfulFallbackProvider()
            service = EvaluationService(
                repo=repo,
                providers=[provider],
                max_jobs_per_run=10,
                profile_path=profile_path,
            )
            
            logs = []
            sink_id = logger.add(lambda msg: logs.append(msg.record["message"]))
            try:
                stats = service.run("run-experience-test")
            finally:
                logger.remove(sink_id)
            
            assert stats.evaluated == 3
            assert stats.apply == 2
            assert stats.skip == 1
            
            # Check experience evaluation log messages
            assert any("Experience evaluation: candidate_exp=2, job_min_exp=2, threshold=4, decision=evaluate" in log for log in logs)
            assert any("Experience evaluation: candidate_exp=2, job_min_exp=3, threshold=4, decision=evaluate" in log for log in logs)
            assert any("Experience evaluation: candidate_exp=2, job_min_exp=5, threshold=4, decision=skip" in log for log in logs)
            
            evals = repo._conn.execute(
                """
                SELECT job_id, model_name, action, reason
                FROM ai_evaluations
                ORDER BY job_id ASC
                """
            ).fetchall()
            
            assert len(evals) == 3
            assert evals[0]["model_name"] == "Gemini"
            assert evals[0]["action"] == "apply"
            assert evals[1]["model_name"] == "Gemini"
            assert evals[1]["action"] == "apply"
            assert evals[2]["model_name"] == "ExperienceFilter"
            assert evals[2]["action"] == "skip"
            assert evals[2]["reason"] == "Minimum experience exceeds target threshold"
            
        finally:
            repo.close()


def test_parse_min_experience() -> None:
    assert parse_min_experience("0-3 years") == 0
    assert parse_min_experience("2-5 Yrs") == 2
    assert parse_min_experience("3-8 Yrs") == 3
    assert parse_min_experience("5-10 Yrs") == 5
    assert parse_min_experience("3-6 years") == 3
    assert parse_min_experience("3 years") == 3
    assert parse_min_experience("5+ Yrs") == 5
    assert parse_min_experience("") is None
    assert parse_min_experience(None) is None

