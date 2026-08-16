"""
app/utils/network.py
Network connectivity detection and infinite wait utilities.

Provides fast, zero-external-dependency internet connectivity checking
using raw IP socket connections (avoiding DNS resolution hangs when offline),
along with sync and async wait loops that pause execution indefinitely
until internet access is restored.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Sequence

from loguru import logger

# Public DNS servers used for socket-level connectivity probing (no DNS resolution needed)
PROBE_TARGETS: tuple[tuple[str, int], ...] = (
    ("1.1.1.1", 53),  # Cloudflare DNS
    ("8.8.8.8", 53),  # Google DNS
    ("9.9.9.9", 53),  # Quad9 DNS
)

NETWORK_ERROR_SIGNATURES: Sequence[str] = (
    "getaddrinfo failed",
    "err_internet_disconnected",
    "err_name_not_resolved",
    "err_connection_reset",
    "err_connection_refused",
    "err_connection_closed",
    "err_connection_timed_out",
    "err_timed_out",
    "err_network_changed",
    "err_address_unreachable",
    "connection error",
    "connecterror",
    "network is unreachable",
    "no route to host",
    "host is down",
    "max retries exceeded",
    "socket.gaierror",
    "winerror 10054",
    "errno 11001",
)


def is_internet_connected(timeout: float = 3.0) -> bool:
    """
    Check whether an active internet connection is available.
    Probes reliable IP targets directly via TCP sockets to prevent DNS resolution delays.
    """
    for host, port in PROBE_TARGETS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((host, port))
                return True
        except (OSError, socket.error):
            continue
    return False


def is_network_exception(exc: Exception | None) -> bool:
    """
    Determine whether an exception indicates a loss of network connectivity.
    """
    if exc is None:
        return False

    # Check for direct socket/os error types
    if isinstance(exc, (socket.gaierror, socket.herror, socket.timeout, ConnectionError)):
        return True

    err_str = str(exc).lower()
    return any(sig in err_str for sig in NETWORK_ERROR_SIGNATURES)


def wait_for_internet_connection(poll_interval: float = 5.0) -> None:
    """
    Synchronously pause execution indefinitely until an internet connection is established.
    """
    if is_internet_connected():
        return

    logger.warning(
        "INTERNET_DISCONNECTED: Internet connection lost. Pausing execution and waiting indefinitely..."
    )
    cycles = 0
    while not is_internet_connected():
        time.sleep(poll_interval)
        cycles += 1
        if cycles % 6 == 0:  # Log every ~30 seconds
            logger.warning(
                "INTERNET_DISCONNECTED: Still waiting for internet connection to be restored (elapsed ~{}s)...",
                int(cycles * poll_interval),
            )

    logger.info("INTERNET_RESTORED: Internet connection re-established! Resuming execution...")


async def async_wait_for_internet_connection(poll_interval: float = 5.0) -> None:
    """
    Asynchronously pause execution indefinitely until an internet connection is established.
    """
    if is_internet_connected():
        return

    logger.warning(
        "INTERNET_DISCONNECTED: Internet connection lost. Pausing execution and waiting indefinitely..."
    )
    cycles = 0
    while not is_internet_connected():
        await asyncio.sleep(poll_interval)
        cycles += 1
        if cycles % 6 == 0:  # Log every ~30 seconds
            logger.warning(
                "INTERNET_DISCONNECTED: Still waiting for internet connection to be restored (elapsed ~{}s)...",
                int(cycles * poll_interval),
            )

    logger.info("INTERNET_RESTORED: Internet connection re-established! Resuming execution...")
