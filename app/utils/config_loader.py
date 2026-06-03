"""
Configuration loader.

Reads YAML config files and returns validated Pydantic models.
All paths are resolved relative to the project root directory.
"""

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from app.models.config import AppSettings, AuthSelectors, SearchConfig, SelectorsConfig

# Project root is two levels up from this file (app/utils/config_loader.py → project/)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR: Path = PROJECT_ROOT / "config"


def _load_yaml(filepath: Path) -> dict[str, Any]:
    """
    Load and parse a YAML file.

    Args:
        filepath: Absolute path to the YAML file.

    Returns:
        Parsed YAML content as a dictionary.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        yaml.YAMLError: If the YAML is malformed.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Configuration file not found: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)

    logger.debug("Loaded config: {}", filepath.name)
    return data or {}


def load_settings() -> AppSettings:
    """Load and validate settings.yaml."""
    data: dict[str, Any] = _load_yaml(CONFIG_DIR / "settings.yaml")
    return AppSettings(**data)


def load_selectors() -> SelectorsConfig:
    """Load and validate selectors.yaml."""
    data: dict[str, Any] = _load_yaml(CONFIG_DIR / "selectors.yaml")
    return SelectorsConfig(**data)


def load_auth_selectors() -> AuthSelectors:
    """Load and validate auth_selectors.yaml."""
    path = CONFIG_DIR / "auth_selectors.yaml"
    if not path.exists():
        logger.warning("auth_selectors.yaml not found, using empty defaults")
        return AuthSelectors()
    data: dict[str, Any] = _load_yaml(path)
    return AuthSelectors(**data)


def load_search_config() -> SearchConfig:
    """Load and validate locations.yaml (keywords + locations)."""
    data: dict[str, Any] = _load_yaml(CONFIG_DIR / "locations.yaml")
    return SearchConfig(**data)


def resolve_path(relative_path: str) -> Path:
    """
    Resolve a relative path against the project root.

    Args:
        relative_path: Path string relative to project root.

    Returns:
        Absolute Path object.
    """
    candidate = Path(relative_path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def ensure_directories(settings: AppSettings) -> None:
    """
    Create all required directories if they don't exist.

    Args:
        settings: Application settings with path configuration.
    """
    dirs_to_create: list[Path] = [
        resolve_path(settings.paths.exports),
        resolve_path(settings.paths.logs),
        resolve_path(settings.paths.screenshots),
        resolve_path(settings.paths.artifacts),
        resolve_path(settings.paths.database).parent,
    ]

    for dir_path in dirs_to_create:
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.debug("Ensured directory: {}", dir_path)
