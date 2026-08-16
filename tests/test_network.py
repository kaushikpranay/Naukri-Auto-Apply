"""
tests/test_network.py
Unit tests for network connectivity detection and wait helpers.
"""

import asyncio
import socket
from unittest.mock import MagicMock, patch
import pytest

from app.utils.network import (
    async_wait_for_internet_connection,
    is_internet_connected,
    is_network_exception,
    wait_for_internet_connection,
)


def test_is_internet_connected_live():
    """Live connectivity test should return a boolean without throwing."""
    result = is_internet_connected(timeout=2.0)
    assert isinstance(result, bool)


def test_is_internet_connected_mock_success():
    """Returns True when socket connection succeeds."""
    with patch("socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value.__enter__.return_value = mock_sock
        mock_sock.connect.return_value = None
        assert is_internet_connected() is True


def test_is_internet_connected_mock_failure():
    """Returns False when all socket connections fail."""
    with patch("socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value.__enter__.return_value = mock_sock
        mock_sock.connect.side_effect = OSError("No route to host")
        assert is_internet_connected() is False


def test_is_network_exception_detection():
    """Detects various types of network and DNS disconnection errors."""
    assert is_network_exception(socket.gaierror(11001, "getaddrinfo failed")) is True
    assert is_network_exception(ConnectionResetError("WinError 10054")) is True
    assert is_network_exception(RuntimeError("playwright._impl._errors.Error: net::ERR_INTERNET_DISCONNECTED")) is True
    assert is_network_exception(Exception("Groq transient error: Connection error.")) is True
    assert is_network_exception(Exception("Gemini transient error: [Errno 11001] getaddrinfo failed")) is True
    assert is_network_exception(ValueError("Invalid json format")) is False
    assert is_network_exception(None) is False


def test_wait_for_internet_connection_recovers():
    """Synchronous wait loops until internet returns."""
    call_counts = 0

    def mock_is_connected(*args, **kwargs):
        nonlocal call_counts
        call_counts += 1
        return call_counts >= 4

    with patch("app.utils.network.is_internet_connected", side_effect=mock_is_connected):
        with patch("time.sleep") as mock_sleep:
            wait_for_internet_connection(poll_interval=0.01)
            assert call_counts >= 4
            assert mock_sleep.call_count >= 2


@pytest.mark.asyncio
async def test_async_wait_for_internet_connection_recovers():
    """Asynchronous wait loops until internet returns."""
    call_counts = 0

    def mock_is_connected(*args, **kwargs):
        nonlocal call_counts
        call_counts += 1
        return call_counts >= 3

    with patch("app.utils.network.is_internet_connected", side_effect=mock_is_connected):
        await async_wait_for_internet_connection(poll_interval=0.01)
        assert call_counts >= 3
