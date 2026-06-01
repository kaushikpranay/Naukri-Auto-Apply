"""
Tests for URL normalization utility.
"""

import pytest

from app.utils.url_normalizer import normalize_url


class TestNormalizeUrl:
    """Test suite for normalize_url()."""

    def test_strips_utm_params(self) -> None:
        """UTM parameters should be removed."""
        url = "https://www.naukri.com/job/123?utm_source=email&utm_medium=cpc"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "naukri.com/job/123" in result

    def test_strips_utm_shorthand(self) -> None:
        """Single utm= parameter should be removed."""
        url = "https://www.naukri.com/job/123?utm=abc"
        result = normalize_url(url)
        assert "utm" not in result

    def test_strips_src_param(self) -> None:
        """src=recommended should be removed."""
        url = "https://www.naukri.com/job/456?src=recommended"
        result = normalize_url(url)
        assert "src" not in result

    def test_preserves_non_tracking_params(self) -> None:
        """Legitimate query params should be preserved."""
        url = "https://www.naukri.com/job/789?k=python&experience=5"
        result = normalize_url(url)
        assert "k=python" in result
        assert "experience=5" in result

    def test_strips_mixed_params(self) -> None:
        """Mix of tracking and legit params."""
        url = "https://www.naukri.com/job/101?k=python&utm_source=email&src=recommended"
        result = normalize_url(url)
        assert "k=python" in result
        assert "utm_source" not in result
        assert "src" not in result

    def test_lowercases_scheme_and_host(self) -> None:
        """Scheme and host should be lowercased."""
        url = "HTTPS://WWW.NAUKRI.COM/job/123"
        result = normalize_url(url)
        assert result.startswith("https://www.naukri.com")

    def test_removes_trailing_slash(self) -> None:
        """Trailing slashes should be removed."""
        url = "https://www.naukri.com/job/123/"
        result = normalize_url(url)
        assert not result.endswith("/") or result.endswith("123")

    def test_removes_fragment(self) -> None:
        """Fragment identifiers should be removed."""
        url = "https://www.naukri.com/job/123#section"
        result = normalize_url(url)
        assert "#" not in result

    def test_empty_url(self) -> None:
        """Empty string should return empty string."""
        assert normalize_url("") == ""
        assert normalize_url("   ") == ""

    def test_idempotent(self) -> None:
        """Normalizing an already-normalized URL should be a no-op."""
        url = "https://www.naukri.com/job/123?k=python"
        first = normalize_url(url)
        second = normalize_url(first)
        assert first == second

    def test_strips_fbclid(self) -> None:
        """Facebook click ID should be removed."""
        url = "https://www.naukri.com/job/123?fbclid=abc123"
        result = normalize_url(url)
        assert "fbclid" not in result

    def test_strips_naukri_tracking(self) -> None:
        """Naukri-specific tracking params should be removed."""
        url = "https://www.naukri.com/job/123?nignbevent_src=abc&sid=xyz&xp=1"
        result = normalize_url(url)
        assert "nignbevent_src" not in result
        assert "sid" not in result
        assert "xp" not in result
