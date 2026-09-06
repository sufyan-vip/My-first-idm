"""
Light-weight network helpers used by the engine to auto-pause downloads
when the internet drops and auto-resume when it is back.
"""

from __future__ import annotations

import socket
from typing import Optional

from utils.logger import get_logger

log = get_logger("network")

#: (host, port, timeout) probes tried in order
_PROBES = (("1.1.1.1", 443, 3.0), ("8.8.8.8", 53, 3.0))


def is_online(timeout: float = 3.0) -> bool:
    """Return True when at least one probe can be reached.

    Uses raw TCP connects – no DNS, no HTTP – so it works even when the DNS
    server is itself the failing component.
    """
    for host, port, probe_timeout in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=probe_timeout):
                return True
        except OSError:
            continue
    return False


def host_ip(url_host: str) -> Optional[str]:
    """Resolve *url_host* to the first IPv4 address (used for diagnostics)."""
    try:
        infos = socket.getaddrinfo(url_host, None, socket.AF_INET, socket.SOCK_STREAM)
        for info in infos:
            return info[4][0]
    except OSError:
        pass
    return None
