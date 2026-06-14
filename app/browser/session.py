"""
app/browser/session.py
Browser session manager with persistent Playwright context.

Manages a Chromium browser session that reuses cookies/storage
across runs. Authentication is positively verified — the system
actively looks for logged-in selectors rather than assuming
login based on the absence of login-page selectors.
"""

import subprocess
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
from app.utils.config_loader import load_auth_selectors, resolve_path
from app.utils.screenshot import capture_screenshot


class ProfileNotFoundError(Exception):
    """Raised when the browser profile directory is missing or empty."""
    pass


class SessionExpiredError(Exception):
    """Raised when the Naukri session is expired and login is required."""
    pass


class BrowserSession:
    """
    Persistent Playwright browser session with positive authentication.

    Uses a profile directory to persist cookies, localStorage,
    and session data across runs.

    Usage:
        async with BrowserSession(settings, selectors) as session:
            page = await session.validate_session()
            ...
    """

    def __init__(
        self,
        settings: AppSettings,
        selectors: SelectorsConfig,
    ) -> None:
        self._auth_cfg = load_auth_selectors()
        self._settings: AppSettings = settings
        self._selectors: SelectorsConfig = selectors
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._profile_path: Path = resolve_path(settings.browser.profile_dir)
        self._profile_pre_existed: bool = self._profile_path.exists()

    async def launch(self) -> None:
        """
        Launch the persistent browser context.

        Creates the profile directory if it doesn't exist and
        launches Chromium with the persistent context.
        """
        self._profile_path.mkdir(parents=True, exist_ok=True)

        # Kill any stale Chrome processes using this profile before launching.
        # These are leftover from a previous session that didn't clean up properly.
        try:
            profile_str = str(self._profile_path)
            out = subprocess.check_output(
                [
                    "powershell", "-NonInteractive", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                    f"Where-Object {{$_.CommandLine -like '*{profile_str}*'}}).Count",
                ],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            ).strip()
            if out.isdigit() and int(out) > 0:
                logger.warning("Killing {} stale chrome.exe process(es) using profile before launch.", out)
                subprocess.run(
                    [
                        "powershell", "-NonInteractive", "-Command",
                        f"Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
                        f"Where-Object {{$_.CommandLine -like '*{profile_str}*'}} | "
                        f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}",
                    ],
                    stderr=subprocess.DEVNULL, timeout=10,
                )
        except Exception as probe_exc:
            logger.debug("Profile-in-use probe skipped: {}", probe_exc)

        # Safe to clean up stale lock files — no live Chrome is using this profile.
        for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"):
            lock_path = self._profile_path / lock_file
            if lock_path.exists():
                try:
                    lock_path.unlink(missing_ok=True)
                    logger.debug("Removed stale lock file: {}", lock_path)
                except PermissionError:
                    logger.debug("Lock file in use, skipping: {}", lock_path)

        logger.info("Launching browser with profile: {}", self._profile_path)

        self._playwright = await async_playwright().start()
        try:
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
                    "--window-size=1280,900",
                ],
            )
        except Exception:
            logger.error("Failed to launch browser context — stopping Playwright")
            await self._playwright.stop()
            self._playwright = None
            raise

        self._context.set_default_timeout(self._settings.browser.default_timeout)
        for page in self._context.pages:
            await page.set_viewport_size({"width": 1280, "height": 900})
        logger.info("Browser launched successfully")

    def _validate_profile(self) -> None:
        """
        Verify that the browser profile directory exists and has content.

        Raises:
            ProfileNotFoundError: If the profile is missing or empty.
        """
        if not self._profile_pre_existed:
            logger.error("Browser profile not found at: {}", self._profile_path)
            raise ProfileNotFoundError(
                "Browser profile not found.\n"
                "Run login_setup.py first.\n"
                "  python login_setup.py"
            )

        contents = list(self._profile_path.iterdir())
        if not contents:
            logger.error("Browser profile is empty: {}", self._profile_path)
            raise ProfileNotFoundError(
                "Browser profile is empty.\n"
                "Run login_setup.py first.\n"
                "  python login_setup.py"
            )

        logger.info("Browser profile validated: {} ({} entries)", self._profile_path, len(contents))

    async def validate_session(self) -> Page:
        """
        Navigate to Naukri and positively verify authentication.

        Flow:
          1. Validate that the browser profile exists and has session data.
          2. Navigate to Naukri homepage.
          3. Positively search for authenticated-only selectors.
          4. If found → session valid, return page.
          5. If not found → session expired, raise SessionExpiredError.

        Returns:
            The active Page instance if session is valid.

        Raises:
            ProfileNotFoundError: If the browser profile is missing.
            SessionExpiredError: If no authenticated selectors are found.
        """
        if self._context is None:
            raise RuntimeError("Browser not launched. Call launch() first.")

        self._validate_profile()

        page: Page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        logger.info("Navigating to Naukri to validate session...")
        await page.goto(
            self._settings.naukri.base_url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        await page.wait_for_timeout(self._settings.naukri.page_load_wait)

        current_url: str = page.url

        # ── Positive Authentication Check ────────────────────────────
        auth_cfg = self._auth_cfg
        if auth_cfg.authenticated:
            authenticated_selectors: list[str] = auth_cfg.authenticated
        else:
            authenticated_selectors = [
                s.strip()
                for s in self._selectors.login.authenticated.split(",")
            ]

        found_selector: Optional[str] = None
        errors = []
        for selector in authenticated_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    found_selector = selector
                    break
            except Exception as e:
                errors.append(str(e))
                continue

        if errors and not found_selector:
            logger.warning("Selector errors during auth check: {}", errors)

        # ── Diagnostic Logging ───────────────────────────────────────
        logger.info("Current URL: {}", current_url)
        logger.info("Browser Profile Path: {}", self._profile_path)
        logger.info("Authenticated Selector Found: {}", found_selector or "None")

        if found_selector:
            logger.info("Authenticated: TRUE")
            logger.info("Reason: Authenticated selector found — {}", found_selector)
            logger.info("Session is valid — user is logged in")
            return page

        # ── Authentication Failed — capture evidence, hard stop ──────
        logger.warning("Authenticated: FALSE")
        logger.warning("Reason: No authenticated selector found on the page")
        logger.warning("No authenticated selectors detected — session is invalid")

        await capture_screenshot(
            page,
            reason="auth_failed",
            screenshots_dir=self._settings.paths.screenshots,
        )

        raise SessionExpiredError(
            "Login required.\n"
            "No valid Naukri session detected.\n"
            "Run login_setup.py to create an authenticated profile:\n"
            "  python login_setup.py"
        )

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
