"""
URL Normalization utility.

Strips tracking parameters, UTM tags, and recommendation params
from Naukri job URLs to enable reliable deduplication.
"""

from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

# Parameters to strip from URLs
_TRACKING_PARAMS: set[str] = {
    # UTM family
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm",
    # Naukri-specific tracking
    "src",
    "sid",
    "xp",
    "px",
    "nignbevent_src",
    "ref",
    "recommendation",
    "qp",
    "seq",
    # Common trackers
    "fbclid",
    "gclid",
    "gad_source",
    "mc_cid",
    "mc_eid",
    "_ga",
    "_gl",
}


def normalize_url(raw_url: str) -> str:
    """
    Normalize a job URL by removing tracking and recommendation parameters.

    Args:
        raw_url: The original URL with potential tracking params.

    Returns:
        Cleaned URL suitable for deduplication.

    Examples:
        >>> normalize_url("https://www.naukri.com/job/123?utm=abc&src=recommended")
        'https://www.naukri.com/job/123'
        >>> normalize_url("https://www.naukri.com/job/456?k=python&utm_source=email")
        'https://www.naukri.com/job/456?k=python'
    """
    if not raw_url or not raw_url.strip():
        return ""

    parsed = urlparse(raw_url.strip())

    # Lowercase scheme and host
    scheme: str = parsed.scheme.lower()
    netloc: str = parsed.netloc.lower()

    # Parse query params and remove tracking ones
    query_params: dict[str, list[str]] = parse_qs(parsed.query, keep_blank_values=False)
    cleaned_params: dict[str, list[str]] = {
        key: values
        for key, values in query_params.items()
        if key.lower() not in _TRACKING_PARAMS
    }

    # Rebuild query string (sorted for consistency)
    cleaned_query: str = urlencode(cleaned_params, doseq=True) if cleaned_params else ""

    # Remove trailing slash from path
    path: str = parsed.path.rstrip("/") if parsed.path != "/" else "/"

    # Rebuild URL
    normalized: str = urlunparse((
        scheme,
        netloc,
        path,
        parsed.params,
        cleaned_query,
        "",  # strip fragment
    ))

    return normalized
