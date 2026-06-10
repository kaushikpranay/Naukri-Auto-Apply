"""
app/collector/job_collector.py
Job collector — scrapes Naukri search results and job detail pages.

Iterates all keyword × location combinations, paginates through results,
extracts job card data, and optionally visits detail pages for full
descriptions and recruiter info.
"""

from urllib.parse import quote_plus

from loguru import logger
from playwright.async_api import Page, ElementHandle

from app.models.config import (
    AppSettings,
    KeywordEntry,
    LocationEntry,
    SearchConfig,
    SelectorsConfig,
)
from app.models.job import JobData
from app.utils.screenshot import capture_screenshot
from app.utils.url_normalizer import normalize_url


class JobCollector:
    """
    Collects job listings from Naukri search results.

    Builds search URLs from keyword/location combinations,
    paginates through results, and extracts structured data.
    """

    def __init__(
        self,
        page: Page,
        settings: AppSettings,
        selectors: SelectorsConfig,
        search_config: SearchConfig,
    ) -> None:
        self._page: Page = page
        self._settings: AppSettings = settings
        self._selectors: SelectorsConfig = selectors
        self._search_config: SearchConfig = search_config


    async def collect_for_search(
        self,
        keyword: KeywordEntry,
        location: LocationEntry,
    ) -> list[JobData]:
        """Collect jobs for a single keyword-location search."""
        jobs: list[JobData] = []
        page_num: int = 1

        while page_num <= self._settings.naukri.max_pages_per_search:
            url: str = self._build_search_url(keyword, location, page_num)
            logger.debug("  Page {}: {}", page_num, url)

            try:
                await self._page.goto(url, wait_until="domcontentloaded")
                await self._page.wait_for_timeout(self._settings.naukri.page_load_wait)
            except Exception as e:
                logger.warning("  Failed to load page {}: {}", page_num, str(e))
                break

            # Check for "no results"
            no_results_selectors: list[str] = [
                s.strip()
                for s in self._selectors.search_results.no_results.split(",")
            ]
            for selector in no_results_selectors:
                try:
                    el = await self._page.query_selector(selector)
                    if el:
                        logger.debug("  No results found on page {}", page_num)
                        return jobs
                except Exception:
                    continue

            # Extract job cards
            page_jobs: list[JobData] = await self._extract_job_cards(
                keyword, location
            )

            if not page_jobs:
                logger.debug("  No job cards found on page {}", page_num)
                break

            jobs.extend(page_jobs)

            # Try to go to next page
            has_next: bool = await self._has_next_page()
            if not has_next:
                break

            page_num += 1

        return jobs

    def _build_search_url(
        self,
        keyword: KeywordEntry,
        location: LocationEntry,
        page: int = 1,
    ) -> str:
        """Build the Naukri search URL for a keyword-location-page combo."""
        url: str = self._settings.naukri.search_url_template.format(
            keyword=keyword.slug,
            location=location.slug,
            keyword_raw=quote_plus(keyword.display),
        )

        if page > 1:
            separator: str = "&" if "?" in url else "?"
            url = f"{url}{separator}pageNo={page}"

        return url

    async def _extract_job_cards(
        self,
        keyword: KeywordEntry,
        location: LocationEntry,
    ) -> list[JobData]:
        """Extract job data from all cards on the current page."""
        jobs: list[JobData] = []

        # Try each card selector (Naukri uses different class names)
        card_selectors: list[str] = [
            s.strip()
            for s in self._selectors.search_results.job_card.split(",")
        ]

        cards: list[ElementHandle] = []
        for selector in card_selectors:
            try:
                cards = await self._page.query_selector_all(selector)
                if cards:
                    logger.debug("  Found {} cards with selector: {}", len(cards), selector)
                    break
            except Exception:
                continue

        if not cards:
            return jobs

        for index, card in enumerate(cards):
            try:
                job: JobData | None = await self._extract_single_card(
                    card, keyword, location
                )
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.debug("  Skipping card {}: {}", index, str(e))
                continue

        return jobs

    async def _extract_single_card(
        self,
        card: ElementHandle,
        keyword: KeywordEntry,
        location: LocationEntry,
    ) -> JobData | None:
        """Extract data from a single job card element."""

        # --- Title & URL ---
        title_text: str = ""
        job_url: str = ""
        title_selectors: list[str] = [
            s.strip()
            for s in self._selectors.search_results.title.split(",")
        ]
        for selector in title_selectors:
            try:
                title_el = await card.query_selector(selector)
                if title_el:
                    title_text = (await title_el.inner_text()).strip()
                    job_url = await title_el.get_attribute("href") or ""
                    break
            except Exception:
                continue

        if not title_text:
            return None

        # Ensure absolute URL
        if job_url and not job_url.startswith("http"):
            job_url = f"{self._settings.naukri.base_url}{job_url}"

        # --- Company ---
        company_name: str = await self._extract_text_from_card(
            card, self._selectors.search_results.company
        )

        # --- Experience ---
        experience: str = await self._extract_text_from_card(
            card, self._selectors.search_results.experience
        )

        # --- Location ---
        job_location: str = await self._extract_text_from_card(
            card, self._selectors.search_results.location
        )

        # --- Posted Date ---
        posted_date: str = await self._extract_text_from_card(
            card, self._selectors.search_results.posted_date
        )

        # Normalize the URL
        if not job_url:
            logger.warning("Skipping card with no URL: title='{}'", title_text)
            return None

        normalized: str = normalize_url(job_url)

        return JobData(
            job_title=title_text,
            company_name=company_name,
            job_url=job_url,
            normalized_url=normalized,
            experience_required=experience,
            location=job_location,
            posted_date=posted_date,
            # These fields require visiting the detail page
            job_description="",
            apply_url="",
            recruiter_name="",
            recruiter_email="",
            search_keyword=keyword.display,
            search_location=location.display,
        )

    async def _extract_text_from_card(
        self,
        card: ElementHandle,
        selector_str: str,
    ) -> str:
        """Extract text from a card element using comma-separated selectors."""
        selectors: list[str] = [s.strip() for s in selector_str.split(",")]
        for selector in selectors:
            try:
                el = await card.query_selector(selector)
                if el:
                    text: str = (await el.inner_text()).strip()
                    if text:
                        return text
            except Exception:
                continue
        return ""

    async def _has_next_page(self) -> bool:
        """Check if a next page button exists and is clickable."""
        next_selectors: list[str] = [
            s.strip()
            for s in self._selectors.pagination.next_button.split(",")
        ]
        for selector in next_selectors:
            try:
                el = await self._page.query_selector(selector)
                if el:
                    is_disabled = await el.get_attribute("disabled")
                    aria_disabled = await el.get_attribute("aria-disabled")
                    class_name = await el.get_attribute("class") or ""
                    if is_disabled is not None or aria_disabled == "true" or "disabled" in class_name:
                        return False
                    return True
            except Exception:
                continue
        return False

    async def enrich_with_details(
        self,
        jobs: list[JobData],
    ) -> list[JobData]:
        """
        Visit each job's detail page to collect full description
        and recruiter information.

        Args:
            jobs: List of jobs with basic card data.

        Returns:
            The same list, enriched with detail-page data.
        """
        total: int = len(jobs)
        for index, job in enumerate(jobs):
            if not job.job_url:
                continue

            logger.debug(
                "  Enriching [{}/{}]: {}",
                index + 1,
                total,
                job.job_title[:60],
            )

            try:
                await self._page.goto(job.job_url, wait_until="domcontentloaded", timeout=30000)
                await self._page.wait_for_timeout(
                    self._settings.naukri.detail_load_wait
                )

                # --- Description ---
                desc_selectors: list[str] = [
                    s.strip()
                    for s in self._selectors.job_detail.description.split(",")
                ]
                for selector in desc_selectors:
                    try:
                        el = await self._page.query_selector(selector)
                        if el:
                            job.job_description = (await el.inner_text()).strip()
                            break
                    except Exception:
                        continue

                # --- Apply URL ---
                apply_selectors: list[str] = [
                    s.strip()
                    for s in self._selectors.job_detail.apply_button.split(",")
                ]
                for selector in apply_selectors:
                    try:
                        el = await self._page.query_selector(selector)
                        if el:
                            href = await el.get_attribute("href")
                            if href:
                                job.apply_url = href
                            break
                    except Exception:
                        continue

                # --- Recruiter Name ---
                name_selectors: list[str] = [
                    s.strip()
                    for s in self._selectors.job_detail.recruiter_name.split(",")
                ]
                for selector in name_selectors:
                    try:
                        el = await self._page.query_selector(selector)
                        if el:
                            job.recruiter_name = (await el.inner_text()).strip()
                            break
                    except Exception:
                        continue

                # --- Recruiter Email ---
                email_selectors: list[str] = [
                    s.strip()
                    for s in self._selectors.job_detail.recruiter_email.split(",")
                ]
                for selector in email_selectors:
                    try:
                        el = await self._page.query_selector(selector)
                        if el:
                            # Handle mailto: links
                            href = await el.get_attribute("href")
                            if href and href.startswith("mailto:"):
                                job.recruiter_email = href.replace("mailto:", "").strip()
                            else:
                                job.recruiter_email = (await el.inner_text()).strip()
                            break
                    except Exception:
                        continue

            except Exception as e:
                logger.debug(
                    "  Could not enrich '{}': {}",
                    job.job_title[:40],
                    str(e),
                )

        return jobs
