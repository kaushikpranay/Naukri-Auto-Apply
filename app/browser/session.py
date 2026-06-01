"""
Browser session manager with persistent Playwright context.

Manages a Chromium browser session that reuses cookies/storage
across runs. Never performs automatic login — if the session has
expired, it halts execution and asks the user to login manually.
"""

from pathlib import Path
from types import TracebackType
from typing import Optional, Self

from loguru import logger
from playwright.async_api import (
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from app.models.config import AppSettings, SelectorsConfig
from app.utils.config_loader import resolve_path
from app.utils.screenshot import capture_screenshot


class SessionExpiredError(Exception):
    """Raised when the Naukri session is expired and login is required."""
    pass


class BrowserSession:
    """
    Persistent Playwright browser session.

    Uses a profile directory to persist cookies, localStorage,
    and session data across runs.

    Usage:
        async with BrowserSession(settings, selectors) as session:
            page = await session.new_page()
            ...
    """

    def __init__(
        self,
        settings: AppSettings,
        selectors: SelectorsConfig,
    ) -> None:
        self._settings: AppSettings = settings
        self._selectors: SelectorsConfig = selectors
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._profile_path: Path = resolve_path(settings.browser.profile_dir)

    async def launch(self) -> None:
        """
        Launch the persistent browser context.

        Creates the profile directory if it doesn't exist and
        launches Chromium with the persistent context.
        """
        self._profile_path.mkdir(parents=True, exist_ok=True)

        logger.info("Launching browser with profile: {}", self._profile_path)

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_path),
            headless=self._settings.browser.headless,
            slow_mo=self._settings.browser.slow_mo,
            viewport={
                "width": self._settings.browser.viewport_width,
                "height": self._settings.browser.viewport_height,
            },
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        self._context.set_default_timeout(self._settings.browser.default_timeout)
        logger.info("Browser launched successfully")

    async def validate_session(self) -> Page:
        """
        Navigate to Naukri and verify the user is logged in.

        Returns:
            The active Page instance if session is valid.

        Raises:
            SessionExpiredError: If the login page is detected.
        """
        if self._context is None:
            raise RuntimeError("Browser not launched. Call launch() first.")

        page: Page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        logger.info("Navigating to Naukri to validate session...")
        await page.goto(
            self._settings.naukri.base_url,
            wait_until="domcontentloaded",
        )

        # Wait for page to settle
        await page.wait_for_timeout(self._settings.naukri.page_load_wait)

        # Check for login page indicators
        login_selectors: list[str] = [
            s.strip()
            for s in self._selectors.login.detection.split(",")
        ]

        for selector in login_selectors:
            try:
                element = await page.query_selector(selector.strip())
                if element:
                    logger.warning("Login page detected (selector: {})", selector)
                    await capture_screenshot(
                        page,
                        reason="login_detected",
                        screenshots_dir=self._settings.paths.screenshots,
                    )
                    raise SessionExpiredError(
                        "Session expired. Please login manually.\n"
                        "1. Run: python main.py\n"
                        "2. Login in the browser window that opens\n"
                        "3. Close the browser\n"
                        "4. Re-run: python main.py"
                    )
            except SessionExpiredError:
                raise
            except Exception:
                # Selector didn't match — continue checking
                continue

        logger.info("Session is valid — user is logged in")
        return page

    async def new_page(self) -> Page:
        """Create a new page in the persistent context."""
        if self._context is None:
            raise RuntimeError("Browser not launched. Call launch() first.")
        return await self._context.new_page()

    async def close(self) -> None:
        """Gracefully close the browser and Playwright."""
        if self._context:
            try:
                await self._context.close()
                logger.debug("Browser context closed")
            except Exception as e:
                logger.warning("Error closing browser context: {}", str(e))
            self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
                logger.debug("Playwright stopped")
            except Exception as e:
                logger.warning("Error stopping Playwright: {}", str(e))
            self._playwright = None

    @property
    def context(self) -> Optional[BrowserContext]:
        """Access the underlying browser context."""
        return self._context

    # ---- Context Manager ----

    async def __aenter__(self) -> Self:
        await self.launch()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.close()
