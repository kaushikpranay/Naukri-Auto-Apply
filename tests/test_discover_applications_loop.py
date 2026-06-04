from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from discover_applications import run
from app.models.discovery import DiscoverySummary


@pytest.mark.asyncio
async def test_discover_applications_loop() -> None:
    # Prepare dummy arguments
    args = argparse.Namespace(force=False, job_id=None)

    # Mock settings and selectors loaders
    mock_settings = MagicMock()
    mock_settings.paths.database = "dummy_db"
    mock_settings.paths.exports = "dummy_exports"
    mock_settings.paths.logs = "dummy_logs"
    mock_settings.paths.screenshots = "dummy_screenshots"
    mock_settings.paths.artifacts = "dummy_artifacts"
    mock_settings.logging.rotation = "1 day"
    mock_settings.logging.retention = "30 days"
    mock_settings.discovery.max_discovery_jobs_per_run = 2

    mock_selectors = MagicMock()

    # Mock the database connection cursor.execute calls
    mock_cursor = MagicMock()
    # The loop queries database for COUNT(*). We will return:
    # 1. 5 (first iteration: 5 pending)
    # 2. 3 (second iteration: 3 pending)
    # 3. 1 (third iteration: 1 pending)
    # 4. 0 (fourth iteration: 0 pending -> loop exit)
    mock_cursor.fetchone.side_effect = [[5], [3], [1], [0]]
    
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Mock repository
    mock_repo = MagicMock()
    mock_repo._conn = mock_conn

    # Mock service.run responses
    mock_service_instance = MagicMock()
    
    summary1 = DiscoverySummary(processed=2)
    summary2 = DiscoverySummary(processed=2)
    summary3 = DiscoverySummary(processed=1)
    
    mock_service_instance.run = AsyncMock(side_effect=[summary1, summary2, summary3])

    # Mock other classes/functions called in run()
    with patch("discover_applications.load_settings", return_value=mock_settings), \
         patch("discover_applications.load_selectors", return_value=mock_selectors), \
         patch("discover_applications.setup_logging"), \
         patch("discover_applications.ensure_directories"), \
         patch("discover_applications.ApplyDiscoveryRepository", return_value=mock_repo), \
         patch("discover_applications.QuestionBankSeeder") as mock_seeder_cls, \
         patch("discover_applications.BrowserSession") as mock_browser_session_cls, \
         patch("discover_applications.ApplyDiscoveryService", return_value=mock_service_instance), \
         patch("discover_applications.ApplyDiscoveryExporter") as mock_exporter_cls, \
         patch("discover_applications.QuestionBankLookupService") as mock_lookup_cls, \
         patch("discover_applications.QuestionBankReportExporter") as mock_report_exporter_cls, \
         patch("discover_applications.FormFillReportExporter") as mock_ff_exporter_cls, \
         patch("discover_applications.ApplicationReviewExporter") as mock_review_exporter_cls:

        # Stub BrowserSession async context manager
        mock_session_instance = AsyncMock()
        mock_session_instance.validate_session.return_value = MagicMock()
        mock_browser_session_cls.return_value.__aenter__.return_value = mock_session_instance

        # Run the discovery script entrypoint
        await run(args)

        # Assertions
        # 1. service.run should have been called exactly 3 times (for 5, 3, and 1 pending jobs)
        assert mock_service_instance.run.call_count == 3
        # 2. repo.close should have been called
        mock_repo.close.assert_called_once()
