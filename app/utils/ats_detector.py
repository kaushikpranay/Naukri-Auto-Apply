"""
Utility to detect ATS platform type from a URL.
Extracted from generate_dashboard.py so it can be reused by the ATS runner.
"""

from __future__ import annotations


def detect_ats(url: str) -> str:
    """Return a short ATS platform name derived from the given URL."""
    url_lower = (url or "").lower()
    if "workday" in url_lower or "myworkday" in url_lower:
        return "workday"
    if "greenhouse.io" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower:
        return "lever"
    if "ashbyhq.com" in url_lower:
        return "ashby"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    if "darwinbox" in url_lower:
        return "darwinbox"
    if "icims.com" in url_lower:
        return "icims"
    if "docs.google.com/forms" in url_lower or "forms.gle" in url_lower:
        return "google_form"
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        parts = domain.split(".")
        if len(parts) >= 2:
            return parts[-2]
        return domain or "other"
    except Exception:
        return "other"
