"""
tests/test_discovery_fixes.py
Unit tests verifying the 5 discovery pipeline bug fixes:
1. increment_retry_count() auto-fails on reaching max_retry_count for non-terminal statuses only (quota_exhausted preserved).
2. PipelineSuspendedException caught cleanly in service.run() loop without crashing and increments retry_count.
3. clear_application() does NOT reset retry_count (regression guard).
4. service.py fallback branch increments retry_count only for non-terminal apply_type values.
5. get_pending_discovery_count() and get_jobs_for_discovery() include 'unknown' in retryable status filter and exclude exhausted retries.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.repository import JobRepository
from app.discovery.repository import ApplyDiscoveryRepository
from app.discovery.service import ApplyDiscoveryService, _DiscoveryOutcome
from app.models.discovery import ApplicationDiscoveryRecord, PipelineSuspendedException
from app.models.job import JobData
from app.utils.config_loader import load_selectors, load_settings


_CREATE_AI_EVALUATIONS_SQL = """
CREATE TABLE IF NOT EXISTS ai_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    action TEXT NOT NULL,
    fit_score REAL,
    interview_probability REAL,
    confidence REAL,
    reasoning TEXT,
    evaluation_date TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


@pytest.fixture
def test_dbs(tmp_path: Path):
    """Fixture providing initialized JobRepository and ApplyDiscoveryRepository on a temporary DB."""
    db_file = tmp_path / "test_discovery.db"
    job_repo = JobRepository(db_file)
    disc_repo = ApplyDiscoveryRepository(db_file)
    job_repo._conn.execute(_CREATE_AI_EVALUATIONS_SQL)
    job_repo._conn.commit()
    yield job_repo, disc_repo, db_file
    job_repo.close()
    disc_repo.close()


def _insert_test_job(
    conn: sqlite3.Connection,
    job_id: int,
    title: str = "AI Engineer",
    company: str = "TestCorp",
    status: str = "evaluated",
    retry_count: int = 0,
    eval_action: str = "APPLY",
) -> None:
    now = datetime.now().isoformat()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO jobs (id, job_title, company_name, job_url, normalized_url, status, retry_count, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            title,
            company,
            f"https://www.naukri.com/job-{job_id}",
            f"https://www.naukri.com/job-{job_id}",
            status,
            retry_count,
            now,
        ),
    )
    cursor.execute(
        """
        INSERT INTO ai_evaluations (job_id, action, fit_score, interview_probability, evaluation_date)
        VALUES (?, ?, 85, 0.9, ?)
        """,
        (job_id, eval_action, now),
    )
    conn.commit()


# ── 1. increment_retry_count() auto-fail and needs_human_review rules ────────


@pytest.mark.parametrize(
    "generic_non_terminal_status",
    ["unknown", "temporary_failure", "browser_error"],
)
def test_increment_retry_count_auto_fails_generic_non_terminals(test_dbs, generic_non_terminal_status):
    """When retry_count reaches max_retry_count on a generic retryable status, job becomes 'failed'."""
    job_repo, disc_repo, _ = test_dbs
    job_id = 101
    _insert_test_job(job_repo._conn, job_id=job_id, status=generic_non_terminal_status, retry_count=2)

    new_count = disc_repo.increment_retry_count(job_id, max_retry_count=3)
    assert new_count == 3

    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT status, failure_reason, retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert row["failure_reason"] == "max_retry_count exceeded"
    assert row["retry_count"] == 3


@pytest.mark.parametrize(
    "unmapped_question_status",
    ["unknown_question", "waiting_for_user"],
)
def test_increment_retry_count_routes_unmapped_questions_to_needs_human_review(test_dbs, unmapped_question_status):
    """When retry_count reaches max_retry_count on unmapped question statuses, job becomes 'needs_human_review'."""
    job_repo, disc_repo, _ = test_dbs
    job_id = 103
    _insert_test_job(job_repo._conn, job_id=job_id, status=unmapped_question_status, retry_count=2)

    new_count = disc_repo.increment_retry_count(job_id, max_retry_count=3)
    assert new_count == 3

    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT status, failure_reason, retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "needs_human_review"
    assert row["failure_reason"] == "unmapped question requires human review"
    assert row["retry_count"] == 3


@pytest.mark.parametrize(
    "terminal_status",
    ["quota_exhausted", "applied_successfully", "already_applied", "external_portal"],
)
def test_increment_retry_count_preserves_terminal_statuses(test_dbs, terminal_status):
    """Terminal statuses (including quota_exhausted) must NEVER be overwritten to 'failed' on max retries."""
    job_repo, disc_repo, _ = test_dbs
    job_id = 102
    _insert_test_job(job_repo._conn, job_id=job_id, status=terminal_status, retry_count=2)

    new_count = disc_repo.increment_retry_count(job_id, max_retry_count=3)
    assert new_count == 3

    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT status, failure_reason, retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == terminal_status
    assert row["failure_reason"] is None
    assert row["retry_count"] == 3


# ── 2. PipelineSuspendedException handling in service.run() ──────────────────


@pytest.mark.asyncio
async def test_pipeline_suspended_exception_caught_and_increments_retry_count(test_dbs):
    """PipelineSuspendedException during discovery must be caught without crashing and increment retry_count."""
    job_repo, disc_repo, _ = test_dbs
    job_id = 201
    _insert_test_job(job_repo._conn, job_id=job_id, status="evaluated", retry_count=0)

    settings = load_settings()
    selectors = load_selectors()
    service = ApplyDiscoveryService(disc_repo, settings, selectors)

    # Mock _discover_job to raise PipelineSuspendedException
    service._discover_job = AsyncMock(side_effect=PipelineSuspendedException("User skipped question input"))

    # Mock active browser page and context
    mock_page = AsyncMock()
    mock_page.is_closed = MagicMock(return_value=False)
    mock_guardian = AsyncMock()
    mock_guardian.is_closed = MagicMock(return_value=False)
    mock_context = MagicMock()
    mock_context.pages = []
    mock_context.new_page = AsyncMock(return_value=mock_guardian)
    mock_page.context = mock_context

    summary = await service.run(page=mock_page, force_job_id=job_id)

    assert summary.processed == 1
    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["retry_count"] == 1


# ── 3. clear_application() regression guard ──────────────────────────────────


def test_clear_application_does_not_reset_retry_count(test_dbs):
    """clear_application() sets status='pending' but must NOT reset accumulated retry_count to 0."""
    job_repo, disc_repo, _ = test_dbs
    job_id = 301
    _insert_test_job(job_repo._conn, job_id=job_id, status="unknown", retry_count=2)

    disc_repo.clear_application(job_id)

    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT status, retry_count FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 2


# ── 4. service.py fallback branch increments retry_count for non-terminals only ─


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "apply_type, expect_increment",
    [
        ("unknown", True),
        ("unknown_question", True),
        ("waiting_for_user", True),
        ("temporary_failure", True),
        ("browser_error", True),
        ("already_applied", False),
        ("external_portal", False),
        ("applied_successfully", False),
    ],
)
async def test_service_fallback_retry_count_gating(test_dbs, apply_type, expect_increment):
    """service.py fallback branch must increment retry_count ONLY for retryable non-terminal apply_types."""
    job_repo, disc_repo, _ = test_dbs
    job_id = 401
    _insert_test_job(job_repo._conn, job_id=job_id, status="evaluated", retry_count=0)

    settings = load_settings()
    selectors = load_selectors()
    service = ApplyDiscoveryService(disc_repo, settings, selectors)

    outcome_record = ApplicationDiscoveryRecord(
        job_id=job_id,
        apply_type=apply_type,
        apply_url="https://www.naukri.com/apply",
        status="discovered",
        detected_at=datetime.now().isoformat(),
    )
    service._discover_job = AsyncMock(return_value=_DiscoveryOutcome(record=outcome_record, questions=[]))

    mock_page = AsyncMock()
    mock_page.is_closed = MagicMock(return_value=False)
    mock_guardian = AsyncMock()
    mock_guardian.is_closed = MagicMock(return_value=False)
    mock_context = MagicMock()
    mock_context.pages = []
    mock_context.new_page = AsyncMock(return_value=mock_guardian)
    mock_page.context = mock_context

    await service.run(page=mock_page, force_job_id=job_id)

    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT retry_count, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    expected_retry = 1 if expect_increment else 0
    assert row["retry_count"] == expected_retry
    assert row["status"] == apply_type


# ── 5. Query inclusion of 'unknown' in retryable queue ────────────────────────


def test_discovery_queue_includes_unknown_in_retryable_status(test_dbs):
    """get_pending_discovery_count and get_jobs_for_discovery must include 'unknown' jobs and exclude exhausted."""
    job_repo, disc_repo, _ = test_dbs

    # Job 1: Unknown retryable (retry_count = 1) -> Tier 0
    _insert_test_job(job_repo._conn, job_id=501, title="Unknown Retryable", status="unknown", retry_count=1)

    # Job 2: Unknown exhausted (retry_count = 3) -> Excluded
    _insert_test_job(job_repo._conn, job_id=502, title="Unknown Exhausted", status="unknown", retry_count=3)

    # Job 3: Fresh evaluated (retry_count = 0) -> Tier 1
    _insert_test_job(job_repo._conn, job_id=503, title="Fresh Evaluated", status="evaluated", retry_count=0)

    # Job 4: Needs human review -> Excluded
    _insert_test_job(job_repo._conn, job_id=504, title="Needs Review", status="needs_human_review", retry_count=0)

    # Pending count should include 501 and 503 (total 2), excluding 502 and 504
    pending_count = disc_repo.get_pending_discovery_count(max_retry_count=3)
    assert pending_count == 2

    # Discovery list should prioritize Tier 0 (501) before Tier 1 (503), excluding 504
    jobs = disc_repo.get_jobs_for_discovery(limit=10, max_retry_count=3)
    returned_ids = [j.id for j in jobs]
    assert returned_ids == [501, 503]


# ── 6. Walk-in "I am interested" button classification ────────────────────────


@pytest.mark.asyncio
async def test_walk_in_button_classified_as_walk_in(test_dbs):
    """Buttons with text 'I am interested' or 'Walk-in' must be classified as 'walk_in' apply_type."""
    job_repo, disc_repo, _ = test_dbs
    settings = load_settings()
    selectors = load_selectors()
    service = ApplyDiscoveryService(disc_repo, settings, selectors)

    mock_page = AsyncMock()
    detected_type = await service._detect_button_apply_type(mock_page, "I am interested")
    assert detected_type == "walk_in"

    detected_type_walkin = await service._detect_button_apply_type(mock_page, "Walk-in Interview")
    assert detected_type_walkin == "walk_in"


# ── 7. has_unknown question sets status='needs_human_review' ──────────────────


@pytest.mark.asyncio
async def test_has_unknown_question_sets_needs_human_review(test_dbs):
    """When discovery returns unknown questions (has_unknown=True), status must be 'needs_human_review'."""
    from app.models.discovery import DiscoveredQuestion
    from app.models.form_fill import FailureType

    job_repo, disc_repo, _ = test_dbs
    job_id = 701
    _insert_test_job(job_repo._conn, job_id=job_id, status="evaluated", retry_count=0)

    settings = load_settings()
    selectors = load_selectors()
    service = ApplyDiscoveryService(disc_repo, settings, selectors)

    outcome_record = ApplicationDiscoveryRecord(
        job_id=job_id,
        apply_type="unknown_question",
        apply_url="https://www.naukri.com/apply",
        status="discovered",
        detected_at=datetime.now().isoformat(),
    )
    unknown_q = DiscoveredQuestion(
        question_key="unrecognized_skill",
        question_text="How many years with Deloying in Cloud?",
        field_type="unknown",
    )
    service._discover_job = AsyncMock(return_value=_DiscoveryOutcome(record=outcome_record, questions=[unknown_q]))

    mock_page = AsyncMock()
    mock_page.is_closed = MagicMock(return_value=False)
    mock_guardian = AsyncMock()
    mock_guardian.is_closed = MagicMock(return_value=False)
    mock_context = MagicMock()
    mock_context.pages = []
    mock_context.new_page = AsyncMock(return_value=mock_guardian)
    mock_page.context = mock_context

    await service.run(page=mock_page, force_job_id=job_id)

    cursor = job_repo._conn.cursor()
    row = cursor.execute("SELECT status, failure_type, failure_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "needs_human_review"
    assert row["failure_type"] == FailureType.UNRECOGNIZED_QUESTION
    assert "unrecognized_skill" in row["failure_reason"]


