"""
Protocol layer: HTTP/HTTPS (requests) and FTP (ftplib).

Responsibilities
----------------
* **probe_url** – a fast HEAD (falling back to a 1-byte ranged GET) that
  reports file size, ``Accept-Ranges`` support, content type and the
  server-provided file name.
* **build_session** – a tuned ``requests.Session`` (pooled connections,
  keep-alive, proxies, auth, custom headers, timeouts) shared by all
  segment threads of one download.
* **FtpSource** – a small abstraction so the engine can treat FTP the same
  way as HTTP (single segment, REST-based resume).
"""

from __future__ import annotations

import ftplib
import re
import socket
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.url_parser import decode_disposition_header, url_filename
from utils.constants import DEFAULT_USER_AGENT
from utils.logger import get_logger

log = get_logger("protocol")

MAX_REDIRECTS = 5
CHUNK_SIZE = 256 * 1024  # 256 KiB read per socket call


class ProbeError(Exception):
    """Raised when the URL cannot be probed at all."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    url: str                                  # final URL after redirects
    size: int = -1                            # -1 == unknown
    content_type: str = ""
    accept_ranges: bool = False
    filename: Optional[str] = None            # from Content-Disposition
    status_code: int = 0
    protocol: str = "http"
    extra_headers: dict = field(default_factory=dict)


def _warn_no_ssl(verify: bool) -> None:
    if not verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _friendly_http_error(resp: requests.Response) -> str:
    codes = {
        400: "Bad request (400) – the server rejected the URL",
        401: "Authentication required (401) – provide credentials",
        403: "Forbidden (403) – the server refused this download",
        404: "Not found (404) – the file does not exist",
        405: "Method not allowed (405)",
        408: "Request timeout (408)",
        416: "Range not satisfiable (416) – file size may have changed",
        429: "Too many requests (429) – try again later",
        500: "Server error (500)",
        502: "Bad gateway (502)",
        503: "Service unavailable (503) – server busy",
        504: "Gateway timeout (504)",
    }
    return codes.get(resp.status_code, f"HTTP error {resp.status_code}")


def _check_redirects(resp: requests.Response) -> None:
    if len(resp.history) > MAX_REDIRECTS:
        raise ProbeError(f"Too many redirects (more than {MAX_REDIRECTS})")


def probe_url(
    url: str,
    timeout: float = 20.0,
    verify_ssl: bool = True,
    proxies: Optional[dict] = None,
    extra_headers: Optional[dict] = None,
    auth: Optional[tuple[str, str]] = None,
    user_agent: str = "",
) -> ProbeResult:
    """Probe *url* and return its metadata.  Raises ``ProbeError`` on failure."""
    _warn_no_ssl(verify_ssl)

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "ftp":
        return _probe_ftp(url, timeout)
    if scheme not in ("http", "https"):
        raise ProbeError(f"Unsupported protocol: {scheme or 'unknown'}")

    headers = {"User-Agent": user_agent or DEFAULT_USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)

    session = requests.Session()
    session.proxies = proxies or {}
    session.verify = verify_ssl
    try:
        # ------------------------------------------------------------- HEAD
        head_size = -1
        head_ctype = ""
        head_filename = None
        head_url = url
        head_code = 0
        head_ranges = None  # None == unknown, must verify
        try:
            resp = session.head(
                url, headers=headers, auth=auth, timeout=timeout,
                allow_redirects=True,
            )
            _check_redirects(resp)
            if resp.status_code < 400:
                head_code = resp.status_code
                head_url = resp.url
                size = resp.headers.get("Content-Length")
                head_size = int(size) if size and size.isdigit() else -1
                head_ctype = resp.headers.get("Content-Type", "") or ""
                head_filename = decode_disposition_header(
                    resp.headers.get("Content-Disposition", ""))
                value = (resp.headers.get("Accept-Ranges") or "").strip().lower()
                if value:
                    head_ranges = "bytes" in value
                resp.close()
        except requests.RequestException as exc:
            # some servers drop HEAD entirely – fall through to ranged GET
            log.debug("HEAD failed (%s), trying ranged GET", exc)

        # HEAD answered fully (status + explicit Accept-Ranges) → done
        if head_code < 400 and head_ranges is not None:
            return ProbeResult(
                url=head_url,
                size=head_size,
                content_type=head_ctype or "",
                accept_ranges=head_ranges,
                filename=head_filename or url_filename(url),
                status_code=head_code,
                protocol=scheme,
            )

        # ------------------------------------------ ranged GET (bytes=0-0)
        # Primary probe when HEAD failed; also used to *verify* range
        # support when the server did not declare Accept-Ranges.
        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        resp = session.get(
            head_url if head_code < 400 else url,
            headers=range_headers, auth=auth, timeout=timeout,
            allow_redirects=True, stream=True,
        )
        _check_redirects(resp)
        try:
            if resp.status_code >= 400:
                raise ProbeError(_friendly_http_error(resp), resp.status_code)
            ctype = resp.headers.get("Content-Type", "") or head_ctype
            filename = decode_disposition_header(
                resp.headers.get("Content-Disposition", "")) or head_filename
            if resp.status_code == 206:
                m = re.search(r"total=(\d+)", resp.headers.get("Content-Range", ""))
                size = int(m.group(1)) if m else head_size
                ranges = True
            else:
                size = resp.headers.get("Content-Length")
                size = int(size) if size and size.isdigit() else head_size
                ranges = False
            return ProbeResult(
                url=resp.url,
                size=size,
                content_type=ctype or "",
                accept_ranges=ranges,
                filename=filename or url_filename(url),
                status_code=resp.status_code,
                protocol=scheme,
            )
        finally:
            resp.close()
    finally:
        session.close()


def _accepts_ranges(resp: requests.Response) -> bool:
    value = (resp.headers.get("Accept-Ranges") or "").strip().lower()
    if value:
        return "bytes" in value
    # many servers omit the header but still honour Range – assume yes
    return True


def _probe_ftp(url: str, timeout: float) -> ProbeResult:
    parsed = urlparse(url)
    source = FtpSource(url)
    try:
        size = source.size(timeout=timeout)
    except (ftplib.all_errors, OSError) as exc:
        raise ProbeError(f"FTP error: {exc}") from exc
    finally:
        source.close()
    return ProbeResult(
        url=url, size=size, accept_ranges=True,  # FTP supports REST offsets
        filename=url_filename(url) or "download",
        protocol="ftp",
    )


# ---------------------------------------------------------------------------
# Session factory (shared by all segment threads of one download)
# ---------------------------------------------------------------------------

def build_session(
    proxies: Optional[dict] = None,
    verify_ssl: bool = True,
    headers: Optional[dict] = None,
    auth: Optional[tuple[str, str]] = None,
    pool_size: int = 32,
) -> requests.Session:
    """Create a connection-pooled session with keep-alive.

    The pool is sized to the maximum segment count so every thread can hold
    a persistent connection – this is what makes multi-segment downloads
    fast (no per-chunk TCP/TLS handshake).
    """
    _warn_no_ssl(verify_ssl)
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=pool_size,
        pool_maxsize=pool_size,
        max_retries=Retry(
            total=0,                       # retries are handled by the engine
            connect=None,
            read=None,
            status=None,
            backoff_factor=0,
            raise_on_status=False,
        ),
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.proxies = proxies or {}
    session.verify = verify_ssl
    if headers:
        session.headers.update(headers)
    if auth:
        session.auth = auth
    return session


# ---------------------------------------------------------------------------
# FTP
# ---------------------------------------------------------------------------

class FtpSource:
    """Minimal binary-mode FTP client with resume (REST) support."""

    def __init__(self, url: str) -> None:
        self.url = url
        parsed = urlparse(url)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 21
        user = parsed.username or "anonymous"
        password = parsed.password or ""
        # unquote the path; FTP URLs keep it raw
        self.path = unquote(parsed.path) or "/"
        self.user = user
        self.password = password or "anonymous@idm"
        self._ftp: Optional[ftplib.FTP] = None

    # ------------------------------------------------------------ lifecycle

    def _connect(self, timeout: float = 30.0) -> ftplib.FTP:
        if self._ftp is not None:
            return self._ftp
        ftp = ftplib.FTP(timeout=timeout)
        ftp.connect(self.host, self.port, timeout=timeout)
        ftp.login(self.user, self.password)
        ftp.set_pasv(True)
        self._ftp = ftp
        return ftp

    def close(self) -> None:
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except Exception:
                try:
                    self._ftp.close()
                except Exception:
                    pass
            self._ftp = None

    # ------------------------------------------------------------ operations

    def size(self, timeout: float = 30.0) -> int:
        ftp = self._connect(timeout)
        return int(ftp.size(self.path))

    def stream(
        self,
        start: int,
        total: int,
        write: Callable[[bytes, int], None],
        is_paused: Callable[[], bool],
        chunk_size: int = CHUNK_SIZE,
        timeout: float = 60.0,
    ) -> None:
        """Stream bytes ``[start, start+total)`` of the remote file.

        ``write(chunk, absolute_offset)`` is called per chunk; raising
        ``FTPSourcePaused`` (or any exception) aborts the transfer.
        """
        ftp = self._connect(timeout)
        conn = ftp.transfercmd("RETR", rest=start if start else None)
        offset = start
        try:
            while offset < start + total:
                if is_paused():
                    raise FtpSourcePaused()
                to_read = min(chunk_size, start + total - offset)
                chunk = conn.recv(to_read)
                if not chunk:
                    if offset >= start + total:
                        break
                    raise ConnectionError("FTP connection closed unexpectedly")
                write(chunk, offset)
                offset += len(chunk)
        finally:
            try:
                conn.close()
            except Exception:
                pass


class FtpSourcePaused(Exception):
    """Raised from ``FtpSource.stream`` when a pause is requested."""
