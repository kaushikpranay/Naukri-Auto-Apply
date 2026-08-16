"""
tests/test_search_combo_cooldown.py
Unit tests for 24-hour search combo cooldown tracking in SQLite repository.
"""

from datetime import datetime, timedelta
from pathlib import Path
import pytest

from app.database.repository import JobRepository


@pytest.fixture
def repo(tmp_path: Path):
    db_file = tmp_path / "test_jobs.db"
    repository = JobRepository(db_file)
    yield repository
    repository.close()


def test_is_search_combo_recent_never_run(repo: JobRepository):
    """Never-run combo returns False, None."""
    is_recent, last_run = repo.is_search_combo_recent("GenAI Engineer", "Remote")
    assert is_recent is False
    assert last_run is None


def test_record_and_check_recent_search_combo(repo: JobRepository):
    """Recorded combo returns True and valid ISO timestamp within 12h."""
    repo.record_search_combo_run("GenAI Engineer", "Remote", jobs_found=10, jobs_inserted=2)

    is_recent, last_run = repo.is_search_combo_recent("GenAI Engineer", "Remote", max_age_hours=12.0)
    assert is_recent is True
    assert last_run is not None

    # Different combo remains not recent
    is_recent_diff, _ = repo.is_search_combo_recent("FastAPI Developer", "Remote")
    assert is_recent_diff is False


def test_expired_search_combo_returns_false(repo: JobRepository):
    """Combo recorded > 12 hours ago returns False, but returns last_run timestamp."""
    old_time = (datetime.now() - timedelta(hours=13)).isoformat()
    cursor = repo._conn.cursor()
    cursor.execute(
        "INSERT INTO search_combo_runs (keyword, location, last_run_at, jobs_found, jobs_inserted) VALUES (?, ?, ?, ?, ?)",
        ("AI Engineer", "Noida", old_time, 5, 1),
    )
    repo._conn.commit()

    is_recent, last_run = repo.is_search_combo_recent("AI Engineer", "Noida", max_age_hours=12.0)
    assert is_recent is False
    assert last_run == old_time


def test_upsert_search_combo_updates_timestamp(repo: JobRepository):
    """Multiple records on same combo update existing row rather than inserting duplicate."""
    repo.record_search_combo_run("LLM Engineer", "Bangalore", jobs_found=5, jobs_inserted=1)
    repo.record_search_combo_run("LLM Engineer", "Bangalore", jobs_found=8, jobs_inserted=3)

    cursor = repo._conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM search_combo_runs WHERE keyword = ? AND location = ?", ("LLM Engineer", "Bangalore"))
    assert cursor.fetchone()[0] == 1
