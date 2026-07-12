"""
Fallback handler for unrecognized ATS platforms.
Logs the URL and skips — no automation attempted.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from playwright.async_api import Page

from app.ats.base_handler import ATSHandler
from app.ats.models import AtsApplicationRecord


class UnknownHandler(ATSHandler):
    ats_type = "unknown"

    async def _do_apply(
        self,
        page: Page,
        apply_url: str,
        profile: dict[str, Any],
        record: AtsApplicationRecord,
    ) -> None:
        logger.warning("[unknown] No handler for URL: {} — skipping", apply_url)
        record.status = "skipped"
        record.error = "No handler implemented for this ATS platform"
