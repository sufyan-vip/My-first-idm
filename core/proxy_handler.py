"""
Proxy configuration helpers.

Translates the stored settings into the ``proxies`` mapping expected by
``requests`` and validates the configuration before it is used.
"""

from __future__ import annotations

import re
from typing import Optional

from utils.logger import get_logger

log = get_logger("proxy")

PORT_RE = re.compile(r"^\d{1,5}$")


def build_proxies(proxy_type: str, host: str, port: str,
                  username: str = "", password: str = "") -> Optional[dict]:
    """Return a requests ``proxies`` dict or ``None`` when no proxy.

    Supports ``http`` and ``socks5`` (the latter requires ``requests[socks]``
    i.e. ``pysocks`` – installed via requirements).
    """
    ptype = (proxy_type or "none").strip().lower()
    host = (host or "").strip()
    port = (port or "").strip()
    if ptype == "none" or not host:
        return None
    if not PORT_RE.match(port):
        log.warning("invalid proxy port %r – proxy disabled", port)
        return None
    scheme = "socks5" if ptype in ("socks5", "socks") else "http"
    authority = f"{username}:{password}@" if username else ""
    uri = f"{scheme}://{authority}{host}:{port}"
    return {"http": uri, "https": uri}


def validate(proxy_type: str, host: str, port: str) -> Optional[str]:
    """Return an error message, or None when the configuration is valid."""
    ptype = (proxy_type or "none").strip().lower()
    if ptype == "none":
        return None
    host = (host or "").strip()
    port = (port or "").strip()
    if not host:
        return "Proxy host is required"
    if not PORT_RE.match(port):
        return "Proxy port must be a number (1-65535)"
    if not 0 < int(port) < 65536:
        return "Proxy port must be between 1 and 65535"
    if ptype not in ("http", "socks5", "socks"):
        return f"Unknown proxy type: {ptype}"
    return None


def test_connection(proxy_type: str, host: str, port: str,
                    username: str = "", password: str = "",
                    timeout: float = 8.0) -> tuple[bool, str]:
    """Try to reach the internet *through* the proxy.

    Returns ``(ok, message)``.
    """
    error = validate(proxy_type, host, port)
    if error:
        return False, error
    proxies = build_proxies(proxy_type, host, port, username, password)
    try:
        import requests

        resp = requests.get(
            "https://www.google.com/generate_204",
            proxies=proxies, timeout=timeout,
            headers={"User-Agent": "IDM-Pro/1.0 proxy-test"},
        )
        return True, f"OK (HTTP {resp.status_code})"
    except Exception as exc:
        return False, f"Connection failed: {exc.__class__.__name__}"
