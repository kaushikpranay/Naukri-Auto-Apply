"""
Bootstrap script to create an authenticated Naukri browser profile.

Usage:
    python login_setup.py

Flow:
    1. Launches a headed Chromium browser with a fresh profile.
    2. Opens Naukri.com.
    3. Waits for you to log in manually.
    4. Press ENTER in the terminal when done.
    5. Validates authentication by searching for logged-in selectors.
    6. If valid → saves the profile.
    7. If invalid → shows error, discards session, exits.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from playwright.async_api import async_playwright

from app.models.config import AppSettings, SelectorsConfig
from app.utils.config_loader import load_selectors, load_settings, resolve_path


def print_banner() -> None:
    print()
    print("=" * 60)
    print("       NAUKRI LOGIN SETUP")
    print("=" * 60)
    print()
    print("This script will help you create an authenticated browser profile.")
    print()
    print("Steps:")
    print("  1. A browser window will open.")
    print("  2. Log in to your Naukri account manually.")
    print("  3. After logging in, return to this terminal.")
    print("  4. Press ENTER to validate and save the session.")
    print()
    print("Make sure you complete the login before pressing ENTER.")
    print("=" * 60)
    print()


async def run() -> None:
    settings: AppSettings = load_settings()
    selectors: SelectorsConfig = load_selectors()

    profile_path = resolve_path(settings.browser.profile_dir)
    logger.info("Profile will be saved at: {}", profile_path)

    logger.info("Launching browser...")
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            slow_mo=settings.browser.slow_mo,
            viewport={
                "width": settings.browser.viewport_width,
                "height": settings.browser.viewport_height,
            },
            args=[
                f"--window-size={settings.browser.viewport_width},{settings.browser.viewport_height}",
            ],
        )
        context.set_default_timeout(settings.browser.default_timeout)

        page = context.pages[0] if context.pages else await context.new_page()

        logger.info("Opening Naukri...")
        await page.goto(
            settings.naukri.base_url,
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(settings.naukri.page_load_wait)

        print()
        print("Browser is open. Log in to Naukri, then press ENTER here.")
        input("Press ENTER after logging in...")

        # ── Validate Authentication ──────────────────────────────────
        logger.info("Validating authentication...")
        await page.wait_for_timeout(2000)

        authenticated_selectors: list[str] = [
            s.strip()
            for s in selectors.login.authenticated.split(",")
        ]

        found_selector: str | None = None
        for selector in authenticated_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    found_selector = selector
                    break
            except Exception:
                continue

        if found_selector:
            logger.info("Authenticated selector found: {}", found_selector)
            print()
            print("✓ Login validation passed!")
            print("  Profile saved successfully at:")
            print(f"  {profile_path}")
            print()
            print("You can now run automation scripts:")
            print("  python main.py")
            print("  python discover_applications.py")
            print("  python evaluate_jobs.py")
            print()
        else:
            logger.error("No authenticated selector found on the page")
            print()
            print("✗ Login validation failed.")
            print("  Could not verify authentication.")
            print()
            print("Try again:")
            print("  python login_setup.py")
            print()
            sys.exit(1)

        await context.close()
        logger.info("Browser closed. Profile saved.")


def main() -> None:
    print_banner()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user. Exiting...")
        sys.exit(1)
    except Exception as exc:
        logger.exception("Fatal error: {}", exc)
        print(f"\n✗ Fatal error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
