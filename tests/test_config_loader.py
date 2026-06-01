"""
Tests for configuration loader.
"""

from pathlib import Path

import pytest

from app.utils.config_loader import (
    load_settings,
    load_selectors,
    load_search_config,
    resolve_path,
    PROJECT_ROOT,
)


class TestConfigLoader:
    """Test suite for configuration loading."""

    def test_load_settings(self) -> None:
        """settings.yaml should load into AppSettings."""
        settings = load_settings()
        assert settings.browser.profile_dir == "browser_profile"
        assert settings.paths.database == "database/jobs.db"
        assert settings.naukri.base_url == "https://www.naukri.com"

    def test_load_selectors(self) -> None:
        """selectors.yaml should load into SelectorsConfig."""
        selectors = load_selectors()
        assert selectors.login.detection  # non-empty
        assert selectors.search_results.job_card  # non-empty
        assert selectors.job_detail.description  # non-empty

    def test_load_search_config(self) -> None:
        """locations.yaml should load into SearchConfig."""
        config = load_search_config()
        assert len(config.keywords) == 8
        assert len(config.locations) == 10

        # Verify specific entries
        keyword_slugs = [k.slug for k in config.keywords]
        assert "ai-engineer" in keyword_slugs
        assert "fastapi-developer" in keyword_slugs

        location_slugs = [loc.slug for loc in config.locations]
        assert "bangalore" in location_slugs
        assert "remote" in location_slugs

    def test_resolve_path(self) -> None:
        """resolve_path should return absolute path relative to project root."""
        result = resolve_path("database/jobs.db")
        assert result.is_absolute()
        assert str(result).endswith("jobs.db")

    def test_project_root_exists(self) -> None:
        """PROJECT_ROOT should point to an existing directory."""
        assert PROJECT_ROOT.exists()
        assert PROJECT_ROOT.is_dir()

    def test_config_files_exist(self) -> None:
        """All config YAML files should exist."""
        config_dir = PROJECT_ROOT / "config"
        assert (config_dir / "settings.yaml").exists()
        assert (config_dir / "selectors.yaml").exists()
        assert (config_dir / "locations.yaml").exists()
