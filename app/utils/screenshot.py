"""
Screenshot utility.

Captures browser screenshots on login detection and exceptions.
Saves timestamped PNG files to the screenshots directory.
"""

from datetime import datetime
from pathlib import Path

from loguru import logger
from playwright.async_api import Page

from app.utils.config_loader import resolve_path


async def capture_screenshot(
    page: Page,
    reason: str,
    screenshots_dir: str = "screenshots",
) -> Path:
    """
    Capture a screenshot from the current browser page.

    Args:
        page: Playwright Page instance.
        reason: Short description of why the screenshot was taken
                (e.g., 'login_detected', 'exception').
        screenshots_dir: Relative path to screenshots directory.

    Returns:
        Path to the saved screenshot file.
    """
    dir_path: Path = resolve_path(screenshots_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    timestamp: str = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_reason: str = reason.replace(" ", "_").lower()[:50]
    filename: str = f"{timestamp}_{safe_reason}.png"
    filepath: Path = dir_path / filename

    try:
        await page.screenshot(path=str(filepath), full_page=True)
        logger.info("Screenshot saved: {} (reason: {})", filepath.name, reason)
    except Exception as e:
        logger.error("Failed to capture screenshot: {}", str(e))
        # Don't re-raise — screenshot failure should not crash the app

    return filepath
