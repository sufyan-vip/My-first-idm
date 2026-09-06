"""
Multi-threaded download engine.

Architecture
------------
::

    DownloadEngine (QObject, main thread, 1 Hz QTimer)
        ├── owns  DownloadTask (one thread = one download)
        │             ├── probes the URL (HEAD / ranged GET / FTP SIZE)
        │             ├── plans segments & allocates the .part file
        │             └── owns  SegmentWorker (one thread per byte-range)
        │                           └── streams chunks → part file (offset writes)
        ├── queue management   (start next when a slot frees, priority order)
        ├── scheduler          (promote due "scheduled" downloads)
        ├── bandwidth limiter  (global token bucket shared by all workers)
        └── network monitor    (auto-pause offline / auto-resume online)

The engine never blocks the UI thread: all I/O happens in worker threads,
the UI consumes :class:`EngineSnapshot` objects emitted once per second.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core import queue_manager, resume_manager
from core.bandwidth_limiter import BandwidthLimiter
from core.protocol_handler import (
    CHUNK_SIZE,
    FtpSource,
    FtpSourcePaused,
    ProbeError,
    ProbeResult,
    build_session,
    probe_url,
)
from core.scheduler import Scheduler
from core.segment_manager import (
    SegmentRange,
    allocate_part_file,
    finalize,
    optimal_segment_count,
    split_ranges,
    verify_size,
)
from core.speed_calculator import SpeedCalculator
from database.models import Download, EngineSnapshot, HistoryItem, Segment
from requests.exceptions import ChunkedEncodingError as _ChunkedEncodingError
from utils.constants import (
    DEFAULT_USER_AGENT,
    Priority,
    PostAction,
    Status,
)
from utils.file_utils import file_checksums, human_speed, move_or_replace, unique_path
from utils.logger import get_logger
from utils.network_utils import is_online
from utils.notifications import play_sound
from utils.system_utils import open_file, open_folder, run_antivirus_scan, shutdown_pc

log = get_logger("engine")

READ_CHUNK = 64 * 1024       # bytes read per socket call – small enough for
                             # smooth progress + responsive pause checks
PERSIST_EVERY = 1            # persist progress every tick (1 s) – cheap and
                             # keeps the DB current for crash recovery
NETWORK_CHECK_EVERY = 10     # seconds between connectivity checks
READ_TIMEOUT = 20            # seconds between data chunks


class TaskPaused(Exception):
    """Internal: a segment worker observed a pause."""


class _TaskFailed(Exception):
    """Internal: aborts the whole task with a user-friendly message."""


def _friendly(exc: Exception) -> str:
    """Translate an exception into a short human-readable error message."""
    name = exc.__class__.__name__
    text = str(exc)
    if "Timeout" in name:
        return f"Timed out waiting for data ({READ_TIMEOUT}s without a chunk)"
    if name in ("SSLError", "ProxyError"):
        return f"Secure connection problem: {text[:120]}"
    if name in ("ConnectionError", "NewConnectionError", "ConnectTimeout"):
        return f"Could not connect to the server: {text[:120]}"
    if name == "ChunkedEncodingError":
        return "Connection was reset by the server mid-transfer"
    if "403" in text:
        return "The server refused the request (403 Forbidden)"
    if "404" in text:
        return "File not found on the server (404)"
    if "416" in text:
        return "Byte range rejected (416) – the file may have changed on the server"
    if "429" in text:
        return "Server is rate-limiting requests (429) – try again later"
    if name == "FileNotFoundError":
        return "Save location does not exist or is not writable"
    if name == "PermissionError":
        return "Permission denied – check the folder access rights"
    if name == "NotADirectoryError":
        return "The selected save path is a file, not a folder"
    if "No space left" in text or name == "OSError" and "ENOSPC" in text:
        return "The disk is full – free up space and resume again"
    return f"{name}: {text}"[:300] or name


# ===========================================================================
# Segment worker
# ===========================================================================

class SegmentWorker(threading.Thread):
    """Downloads one byte range of a file over HTTP(S)."""

    def __init__(self, task: "DownloadTask", seg: Segment,
                 session_factory) -> None:
        super().__init__(daemon=True, name=f"seg{seg.segment_index}-d{task.dl.id}")
        self.task = task
        self.seg = seg
        self._session_factory = session_factory
        self._fh = None
        self._session = None

    # ------------------------------------------------------------- lifecycle

    def _chunk_size(self) -> int:
        """Read size per iteration.

        When a speed limit is active the chunks shrink to ~rate/16 so the
        pause check (which runs between chunks) stays responsive even at
        very low limits; otherwise we use the big 256 KiB default.
        """
        rate = self.task.limiter.rate
        if rate <= 0:
            return READ_CHUNK
        return max(2 * 1024, min(READ_CHUNK, rate // 16))

    #: status codes worth retrying (server-side / transient)
    RETRYABLE = {408, 429, 500, 502, 503, 504}

    def _open(self, pos: int):
        """Open a fresh ranged GET at offset *pos*; returns the response."""
        headers = {"Range": f"bytes={pos}-{self.seg.end_byte}"}
        resp = self._session.get(self.task.final_url, headers=headers,
                                 stream=True, timeout=(10, READ_TIMEOUT))
        if resp.status_code in (416,):
            # already fully downloaded
            self.seg.downloaded_bytes = self.seg.total
            self.seg.status = "completed"
            resp.close()
            return None
        if resp.status_code == 200 and pos > self.seg.start_byte:
            resp.close()
            raise _TaskFailed(
                "Server ignored the Range header mid-download – "
                "cannot resume this segment safely")
        if resp.status_code in self.RETRYABLE:
            resp.close()
            # raise a *normal* error so the retry loop kicks in
            raise ConnectionError(
                f"Server replied HTTP {resp.status_code} – will retry")
        if resp.status_code >= 400:
            resp.close()
            raise _TaskFailed(f"HTTP {resp.status_code} while downloading "
                               f"segment {self.seg.segment_index}")
        return resp

    def run(self) -> None:
        task, seg = self.task, self.seg
        if task.stopped():
            return
        self.seg.status = "downloading"
        log.info("download #%d segment %d started (bytes %d-%d)",
                 task.dl.id, seg.segment_index, seg.start_byte, seg.end_byte)

        pos = seg.start_byte + seg.downloaded_bytes
        remaining = seg.total - seg.downloaded_bytes
        attempt = 0

        while remaining > 0 and not task.stopped():
            if task.pause_event.is_set():
                # sleep until resumed (or stopped)
                while task.pause_event.is_set() and not task.stopped():
                    time.sleep(0.2)
                if task.stopped():
                    break
                self._close_response()
                attempt = 0
                continue

            try:
                if self._fh is None:
                    self._fh = open(seg.temp_file, "r+b")
                if self._session is None:
                    self._session = self._session_factory()

                resp = self._open(pos)
                if resp is None:
                    continue  # 416 → segment completed above

                # decode_content=False is critical: ranged bytes are written
                # verbatim, so no gzip/deflate decoding may happen
                resp.raw.decode_content = False
                while remaining > 0 and not task.stopped():
                    if task.pause_event.is_set():
                        break
                    chunk = resp.raw.read(min(self._chunk_size(), remaining))
                    if not chunk:
                        break  # server finished/closed early → re-open below
                    task.limiter.wait(len(chunk))
                    self._fh.seek(pos)
                    self._fh.write(chunk)
                    seg.downloaded_bytes += len(chunk)
                    task.add_bytes(len(chunk))
                    pos += len(chunk)
                    remaining -= len(chunk)
                resp.close()
                self._session = None  # keep-alive reused on next open
                attempt = 0
            except TaskPaused:
                self._close_response()
                continue
            except _TaskFailed:
                raise
            except Exception as exc:
                self._close_response()
                if task.stopped():
                    break
                if task.pause_event.is_set():
                    continue
                attempt += 1
                if attempt > task.retries:
                    seg.status = "failed"
                    raise _TaskFailed(
                        f"Segment {seg.segment_index} failed after "
                        f"{attempt - 1} retries: {_friendly(exc)}") from exc
                seg.status = "pending"
                # exponential backoff: 1s, 2s, 4s, … capped at 10s
                delay = min(2 ** (attempt - 1), 10)
                log.warning("download #%d segment %d retry %d/%d in %ds (%s)",
                            task.dl.id, seg.segment_index, attempt,
                            task.retries, delay, _friendly(exc))
                for _ in range(delay * 5):
                    if task.stopped():
                        break
                    time.sleep(0.2)

        # ------------------------------------------------------------ outcome
        self._close_handle()
        if task.stopped():
            return
        if remaining > 0:
            seg.status = "failed"
            raise _TaskFailed(
                f"Segment {seg.segment_index} stopped with {remaining:,} bytes "
                f"left (connection problems)")
        seg.status = "completed"
        log.info("download #%d segment %d complete", task.dl.id, seg.segment_index)

    # --------------------------------------------------------------- helpers

    def _close_response(self) -> None:
        # responses are closed in the main loop; this is a safety net
        self._session = None

    def _close_handle(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None


class FtpWorker(threading.Thread):
    """Single-stream worker for FTP (no byte ranges over one connection)."""

    def __init__(self, task: "DownloadTask", seg: Segment, source: FtpSource) -> None:
        super().__init__(daemon=True, name=f"ftp-d{task.dl.id}")
        self.task = task
        self.seg = seg
        self._source = source

    def _write(self, chunk: bytes, offset: int) -> None:
        self._fh.seek(offset)
        self._fh.write(chunk)
        self.seg.downloaded_bytes = offset - self.seg.start_byte + len(chunk)
        self.task.add_bytes(len(chunk))

    def run(self) -> None:
        task = self.task
        if task.stopped():
            return
        self.seg.status = "downloading"
        attempt = 0
        pos = self.seg.start_byte + self.seg.downloaded_bytes
        remaining = self.seg.total - self.seg.downloaded_bytes

        while remaining > 0 and not task.stopped():
            if task.pause_event.is_set():
                while task.pause_event.is_set() and not task.stopped():
                    time.sleep(0.2)
                if task.stopped():
                    break
                attempt = 0
                continue
            try:
                with open(self.seg.temp_file, "r+b") as fh:
                    self._fh = fh
                    self._source.stream(
                        start=pos,
                        total=remaining,
                        write=self._write,
                        is_paused=lambda: task.pause_event.is_set()
                        or task.stopped(),
                        chunk_size=CHUNK_SIZE,
                    )
                    remaining = 0
            except FtpSourcePaused:
                pass
            except _TaskFailed:
                raise
            except Exception as exc:
                if task.stopped():
                    break
                attempt += 1
                if attempt > task.retries:
                    raise _TaskFailed(
                        f"FTP transfer failed after {attempt - 1} retries: "
                        f"{_friendly(exc)}") from exc
                delay = min(2 ** (attempt - 1), 10)
                log.warning("download #%d FTP retry %d/%d in %ds (%s)",
                            task.dl.id, attempt, task.retries, delay,
                            _friendly(exc))
                for _ in range(delay * 5):
                    if task.stopped():
                        break
                    time.sleep(0.2)
            finally:
                self._fh = None

        if task.stopped():
            return
        if remaining > 0:
            raise _TaskFailed("FTP transfer stopped with bytes remaining")
        self.seg.status = "completed"


# ===========================================================================
# Download task (one thread per download)
# ===========================================================================

class DownloadTask(threading.Thread):
    """Orchestrates one download: probe → plan → workers → finalize."""

    def __init__(self, engine: "DownloadEngine", dl: Download) -> None:
        super().__init__(daemon=True, name=f"task-d{dl.id}")
        self.engine = engine
        self.dl = dl
        self.limiter = engine.limiter
        self.retries = max(0, engine.config.get_int("retries", 5))
        self.timeout = max(5, engine.config.get_int("timeout", 30))
        self.pause_event = threading.Event()
        self._stop_event = threading.Event()
        self.speed_calc = SpeedCalculator()
        self.segments: list[Segment] = []
        self.state = "starting"          # starting|downloading|paused|canceled|completed|failed
        self.result_error = ""
        self.final_url = dl.url
        self.part_file = resume_manager.part_file_for(dl)
        self.created = time.monotonic()
        self._workers: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._paused_by_network = False
        self._delete_on_stop = False
        self._probe: Optional[ProbeResult] = None
        self._ftp: Optional[FtpSource] = None

    # ------------------------------------------------------------- small API

    def stopped(self) -> bool:
        """True when a stop/cancel was requested (workers poll this)."""
        return self._stop_event.is_set()

    def is_paused(self) -> bool:
        return self.pause_event.is_set()

    def downloaded_bytes(self) -> int:
        with self._lock:
            return sum(s.downloaded_bytes for s in self.segments)

    def segments_completed(self) -> int:
        return sum(1 for s in self.segments
                   if s.downloaded_bytes >= s.total)

    def segments_active(self) -> int:
        return sum(1 for s in self.segments if s.status == "downloading")

    def add_bytes(self, n: int) -> None:
        self.speed_calc.add_bytes(n)

    def snapshot(self) -> EngineSnapshot:
        dl = self.dl
        done = self.downloaded_bytes()
        remaining = max(0, dl.file_size - done)
        return EngineSnapshot(
            id=dl.id or 0,
            status=dl.status,
            file_name=dl.file_name,
            url=self.final_url,
            save_path=dl.save_path,
            category=dl.category,
            priority=dl.priority,
            file_size=dl.file_size,
            downloaded_size=done,
            progress=dl.percent,
            speed=self.speed_calc.speed,
            avg_speed=self.speed_calc.avg_speed,
            eta_seconds=self.speed_calc.eta(remaining),
            segments_total=len(self.segments),
            segments_active=self.segments_active(),
            segments_completed=self.segments_completed(),
            error_message=self.result_error or dl.error_message,
            scheduled_time=dl.scheduled_time,
            created_at=dl.created_at,
            completed_at=dl.completed_at,
            checksum_sha256=dl.checksum_sha256,
            checksum_md5=dl.checksum_md5,
            speed_history=self.speed_calc.history,
        )

    # ------------------------------------------------------------ lifecycle

    def pause(self, by_network: bool = False) -> None:
        if self.state not in ("downloading", "starting"):
            return
        self.pause_event.set()
        self._paused_by_network = by_network
        self.state = "paused"
        self.speed_calc.reset_window()
        self._persist()
        log.info("download #%d paused (%s)", self.dl.id,
                 "network lost" if by_network else "user")

    def resume(self) -> None:
        if self.state != "paused":
            return
        self.pause_event.clear()
        self._paused_by_network = False
        self.state = "downloading"
        self.speed_calc.reset_window()
        log.info("download #%d resumed", self.dl.id)

    def request_stop(self, delete_files: bool = False) -> None:
        self._stop_event.set()
        self.pause_event.clear()   # unblock workers sleeping in pause
        self.state = "canceled"
        self._delete_on_stop = delete_files
        if self._ftp is not None:
            try:
                self._ftp.close()
            except Exception:
                pass
        # the task thread performs the actual part-file deletion (it may
        # still be allocating it – doing it here would race)
        log.info("download #%d stop requested (delete_files=%s)",
                 self.dl.id, delete_files)

    # ------------------------------------------------------------------ main

    def run(self) -> None:
        try:
            self._probe_and_plan()
            self._allocate_part()
            self._spawn_workers()
            for w in self._workers:
                w.join()
            if self.stopped():
                self.state = "canceled"
                return
            if self._all_done():
                self._finalize_file()
                self.state = "completed"
            else:
                self.state = "failed"
                if not self.result_error:
                    self.result_error = "Unknown error"
        except _TaskFailed as f:
            self.state = "failed"
            self.result_error = str(f)
        except TaskPaused:
            self.state = "paused"
        except Exception as exc:  # noqa: BLE001 – the engine must never crash
            self.state = "failed"
            self.result_error = _friendly(exc)
            log.error("download #%d failed: %s\n%s", self.dl.id,
                      self.result_error, traceback.format_exc())
        finally:
            self._close_all()
            # deferred part-file cleanup (stop may have raced with allocation)
            if self._delete_on_stop and self.state in ("canceled",):
                try:
                    if os.path.exists(self.part_file):
                        os.remove(self.part_file)
                except OSError:
                    pass
            log.info("download #%d task ended (state=%s)",
                     self.dl.id, self.state)

    # --------------------------------------------------------------- phases

    def _probe_and_plan(self) -> None:
        dl = self.dl
        # fast resume path: we already know the size and the part file exists
        blob = resume_manager.parse_resume_data(dl.resume_data)
        if blob and os.path.exists(self.part_file) and dl.file_size > 0:
            self.final_url = blob.get("final_url") or dl.url
            self.segments = resume_manager.segments_from_resume(dl)
            if self.segments:
                dl.ranges_supported = bool(blob.get("ranges_supported", True))
                return

        headers = dict(dl.custom_headers_dict())
        if dl.referer:
            headers["Referer"] = dl.referer
        auth = (dl.auth_user, dl.auth_pass) if dl.auth_user else None
        probe = probe_url(
            dl.url,
            timeout=self.timeout,
            verify_ssl=True,
            proxies=self.engine.config.proxy_dict(),
            extra_headers=headers or None,
            auth=auth,
            user_agent=dl.user_agent or self.engine.config.get(
                "user_agent") or DEFAULT_USER_AGENT,
        )
        self._probe = probe
        self.final_url = probe.url
        self.dl.protocol = probe.protocol

        if probe.size > 0:
            if dl.file_size > 0 and probe.size != dl.file_size:
                log.warning("download #%d size changed on server: %d -> %d",
                            dl.id, dl.file_size, probe.size)
                self._discard_part()
                dl.downloaded_size = 0
            dl.file_size = probe.size
        # keep the user-chosen file name; only fill when empty
        if not dl.file_name and probe.filename:
            dl.file_name = probe.filename

        if probe.protocol == "ftp":
            dl.ranges_supported = False
        else:
            dl.ranges_supported = probe.accept_ranges

        if dl.file_size <= 0:
            # unknown size: single stream, progress in bytes only
            dl.segments = 1
            self.segments = [Segment(
                download_id=dl.id or 0, segment_index=0,
                start_byte=0, end_byte=0, downloaded_bytes=0,
                status="pending", temp_file=self.part_file,
            )]
            return

        if not dl.ranges_supported:
            count = 1
        else:
            user_max = self.engine.config.get_int("max_segments", 0)
            count = optimal_segment_count(dl.file_size, user_max)
            if dl.segments and dl.segments > 1 and not blob:
                # user explicitly chose a segment count in the dialog
                count = min(max(1, dl.segments), count * 2, 32)
        dl.segments = count
        self.segments = resume_manager.segments_from_resume(dl)
        # fresh plan (resume blob was empty) → all zeroed; re-check part file
        if os.path.exists(self.part_file) and not blob:
            # a stale part file from a deleted download – discard it
            self._discard_part()
            for s in self.segments:
                s.downloaded_bytes = 0
                s.status = "pending"

    def _allocate_part(self) -> None:
        dl = self.dl
        folder = os.path.dirname(dl.save_path) or "."
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            raise _TaskFailed(f"Cannot create save folder {folder}: {exc}") from exc
        if dl.file_size > 0:
            allocate_part_file(self.part_file, dl.file_size)
        else:
            open(self.part_file, "ab").close()

    def _spawn_workers(self) -> None:
        dl = self.dl
        self.state = "downloading"
        self.speed_calc.reset_window()

        if dl.protocol == "ftp":
            self._ftp = FtpSource(self.final_url)
            seg = self.segments[0]
            worker = FtpWorker(self, seg, self._ftp)
        else:
            def session_factory():
                headers = dict(dl.custom_headers_dict())
                if dl.user_agent:
                    headers["User-Agent"] = dl.user_agent
                auth = (dl.auth_user, dl.auth_pass) if dl.auth_user else None
                return build_session(
                    proxies=self.engine.config.proxy_dict(),
                    verify_ssl=True,
                    headers=headers or None,
                    auth=auth,
                    pool_size=4,
                )

            workers = [SegmentWorker(self, seg, session_factory)
                       for seg in self.segments]
            self._workers = workers
            self.engine.db.replace_segments(self.segments)
        if dl.protocol == "ftp":
            self._workers = [worker]
            self.engine.db.replace_segments(self.segments)
        for w in self._workers:
            w.start()

    def _all_done(self) -> bool:
        if not self.segments:
            return False
        return all(s.downloaded_bytes >= s.total for s in self.segments)

    def _finalize_file(self) -> None:
        dl = self.dl
        if dl.file_size > 0:
            if not verify_size(self.part_file, dl.file_size):
                raise _TaskFailed("File size mismatch after download – "
                                   "data may be corrupted")

        # checksums (streaming; constant memory)
        try:
            sums = file_checksums(self.part_file)
            dl.checksum_sha256 = sums["sha256"]
            dl.checksum_md5 = sums["md5"]
        except OSError as exc:
            log.warning("checksum failed for #%d: %s", dl.id, exc)

        folder = os.path.dirname(dl.save_path) or "."
        policy = self.engine.config.get("duplicate_policy", "rename")
        final_path, existed = unique_path(folder, dl.file_name, policy,
                                          expected_size=dl.file_size)
        dl.save_path = final_path
        if policy == "skip" and existed:
            # keep the existing file, just drop our part
            try:
                os.remove(self.part_file)
            except OSError:
                pass
            return
        move_or_replace(self.part_file, final_path)

        dl.completed_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.engine.db.add_history(HistoryItem(
            url=self.final_url, file_name=dl.file_name,
            file_size=dl.file_size, save_path=final_path,
            category=dl.category, status=Status.COMPLETED,
        ))
        if self.engine.config.get_bool("antivirus_scan") and dl.category:
            run_antivirus_scan(final_path)

    # --------------------------------------------------------------- helpers

    def _discard_part(self) -> None:
        try:
            if os.path.exists(self.part_file):
                os.remove(self.part_file)
        except OSError:
            pass

    def _persist(self) -> None:
        """Save resume data + progress to the database (crash safety)."""
        try:
            self.dl.resume_data = resume_manager.build_resume_data(
                self.dl, self.segments, self.final_url)
            self.dl.downloaded_size = self.downloaded_bytes()
            self.dl.speed = round(self.speed_calc.speed, 1)
            self.dl.progress = round(self.dl.percent, 2)
            self.engine.db.update_download(self.dl)
        except Exception as exc:  # noqa: BLE001
            log.warning("persist failed for #%d: %s", self.dl.id, exc)

    def _close_all(self) -> None:
        for w in self._workers:
            try:
                if w.is_alive():
                    w.join(timeout=3)
            except Exception:
                pass
        if self._ftp is not None:
            try:
                self._ftp.close()
            except Exception:
                pass


# ===========================================================================
# Engine
# ===========================================================================

class DownloadEngine(QObject):
    """Owns every active task and drives the queue/scheduler/network loops."""

    stats_updated = pyqtSignal()                    # 1 Hz – UI refresh
    download_completed = pyqtSignal(int, str)       # id, final path
    download_failed = pyqtSignal(int, str)          # id, error message
    download_paused = pyqtSignal(int)
    download_resumed = pyqtSignal(int)
    download_canceled = pyqtSignal(int)
    download_started = pyqtSignal(int)
    network_changed = pyqtSignal(bool)              # True == online
    all_downloads_finished = pyqtSignal()

    def __init__(self, db, config, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.db = db
        self.config = config
        # burst is derived from the rate (half a second of traffic)
        self.limiter = BandwidthLimiter(config.speed_limit_bytes())
        self.tasks: dict[int, DownloadTask] = {}
        self._lock = threading.Lock()
        self._online = True
        self._total_speed_history: deque[float] = deque(maxlen=120)
        self._persist_tick = 0
        self._finished_notified: set[int] = set()

        self.scheduler = Scheduler(
            list_downloads=lambda: db.list_downloads(
                statuses=(Status.SCHEDULED,)),
            promote=self._promote_scheduled,
        )

        # 1 Hz main loop
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._net_counter = 0

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self.limiter.set_rate(self.config.speed_limit_bytes())
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        with self._lock:
            tasks = list(self.tasks.values())
        for task in tasks:
            task.request_stop()
            task.join(timeout=5)
        log.info("engine stopped (%d tasks)", len(tasks))

    def on_settings_changed(self, key: str, _value: str) -> None:
        if key in ("speed_limit", "speed_limit_unit"):
            self.limiter.set_rate(self.config.speed_limit_bytes())

    # ------------------------------------------------------------ commands

    def add(self, dl: Download) -> int:
        """Insert a download record; the queue loop will start it in time."""
        dl_id = self.db.add_download(dl)
        self.scheduler.untrack(dl_id)
        log.info("engine: download #%d accepted (%s, %s)",
                 dl_id, dl.status, dl.file_name)
        return dl_id

    def get_task(self, dl_id: int) -> Optional[DownloadTask]:
        with self._lock:
            return self.tasks.get(dl_id)

    def _start(self, dl: Download) -> bool:
        with self._lock:
            if dl.id in self.tasks:
                return False
            task = DownloadTask(self, dl)
            self.tasks[dl.id] = task
        dl.status = Status.DOWNLOADING
        dl.error_message = ""
        self.db.update_download_fields(dl.id, status=dl.status)
        task.start()
        self.download_started.emit(dl.id or 0)
        return True

    def start_download(self, dl_id: int) -> None:
        dl = self.db.get_download(dl_id)
        if dl is None or dl.status not in (Status.QUEUED, Status.PAUSED):
            return
        self._start(dl)
        if dl.status == Status.PAUSED:
            task = self.get_task(dl_id)
            # started as fresh; the task resumes from resume_data on its own

    def pause_download(self, dl_id: int) -> None:
        task = self.get_task(dl_id)
        if task is None:
            # record exists but no task (app restarted) – mark paused in DB
            self.db.update_download_fields(dl_id, status=Status.PAUSED)
            self.download_paused.emit(dl_id)
            return
        if task.state in ("downloading", "starting"):
            task.pause()
            self.db.update_download_fields(dl_id, status=Status.PAUSED)
            self.download_paused.emit(dl_id)

    def resume_download(self, dl_id: int) -> None:
        task = self.get_task(dl_id)
        if task is not None and task.is_paused():
            task.resume()
            self.db.update_download_fields(dl_id, status=Status.DOWNLOADING)
            self.download_resumed.emit(dl_id)
            return
        dl = self.db.get_download(dl_id)
        if dl is None or dl.status not in (Status.PAUSED, Status.QUEUED):
            return
        self.start_download(dl_id)

    def cancel_download(self, dl_id: int, delete_files: bool = True) -> None:
        """Stop a download.  ``delete_files=True`` also removes the .part file."""
        task = self.get_task(dl_id)
        if task is not None:
            task.request_stop(delete_files=delete_files)
            self.tasks.pop(dl_id, None)
            if delete_files and os.path.exists(task.part_file):
                try:
                    os.remove(task.part_file)
                except OSError:
                    pass
        dl = self.db.get_download(dl_id)
        if dl is not None:
            self.db.update_download_fields(dl_id, status=Status.CANCELED, speed=0)
        self.scheduler.untrack(dl_id)
        self.download_canceled.emit(dl_id)

    def delete_download(self, dl_id: int, delete_files: bool = True) -> None:
        """Remove a download record (and its .part file) permanently.

        The *final* file is never touched unless it does not exist yet
        (i.e. the download never completed) – completed files belong to the
        user.
        """
        task = self.get_task(dl_id)
        if task is not None:
            task.request_stop()
            self.tasks.pop(dl_id, None)
        dl = self.db.get_download(dl_id)
        if delete_files and dl is not None:
            try:
                part = resume_manager.part_file_for(dl)
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
        self.db.delete_download(dl_id, delete_segments=True)
        self.scheduler.untrack(dl_id)

    def pause_all(self) -> None:
        with self._lock:
            tasks = list(self.tasks.values())
        for task in tasks:
            if task.state in ("downloading", "starting"):
                task.pause()
                self.db.update_download_fields(
                    task.dl.id, status=Status.PAUSED)
                self.download_paused.emit(task.dl.id or 0)

    def resume_all(self) -> None:
        for dl in self.db.list_downloads(status=Status.PAUSED):
            self.resume_download(dl.id)

    def re_download(self, dl: Download) -> int:
        folder = os.path.dirname(dl.save_path) or self.config.download_dir()
        final_path, _ = unique_path(folder, dl.file_name,
                                    self.config.get("duplicate_policy", "rename"),
                                    expected_size=dl.file_size)
        new_dl = Download(
            url=dl.url,
            file_name=dl.file_name,
            save_path=final_path,
            file_size=dl.file_size,
            priority=dl.priority,
            segments=dl.segments,
            category=dl.category,
            user_agent=dl.user_agent,
            custom_headers=dl.custom_headers,
            auth_user=dl.auth_user,
            auth_pass=dl.auth_pass,
            referer=dl.referer,
            protocol=dl.protocol,
            ranges_supported=dl.ranges_supported,
        )
        return self.add(new_dl)

    # ------------------------------------------------------------ queries

    def snapshot(self, dl_id: int) -> Optional[EngineSnapshot]:
        task = self.get_task(dl_id)
        if task is not None:
            return task.snapshot()
        dl = self.db.get_download(dl_id)
        if dl is None:
            return None
        return EngineSnapshot(
            id=dl.id or 0, status=dl.status, file_name=dl.file_name,
            url=dl.url, save_path=dl.save_path, category=dl.category,
            priority=dl.priority, file_size=dl.file_size,
            downloaded_size=dl.downloaded_size, progress=dl.percent,
            speed=0.0, avg_speed=0.0, eta_seconds=float("inf"),
            segments_total=dl.segments, segments_active=0,
            segments_completed=0, error_message=dl.error_message,
            scheduled_time=dl.scheduled_time, created_at=dl.created_at,
            completed_at=dl.completed_at,
            checksum_sha256=dl.checksum_sha256, checksum_md5=dl.checksum_md5,
        )

    def all_snapshots(self) -> list[EngineSnapshot]:
        out = []
        for dl in self.db.list_downloads():
            snap = self.snapshot(dl.id)
            if snap:
                out.append(snap)
        return out

    @property
    def total_speed(self) -> float:
        with self._lock:
            return sum(t.speed_calc.speed for t in self.tasks.values())

    def total_speed_history(self) -> list[float]:
        return list(self._total_speed_history)

    def is_busy(self) -> bool:
        with self._lock:
            return any(t.state == "downloading" for t in self.tasks.values())

    # ------------------------------------------------------------- internal

    def _promote_scheduled(self, dl: Download) -> None:
        self.db.update_download_fields(dl.id, status=Status.QUEUED)

    def _tick(self) -> None:
        self._persist_tick += 1
        self._net_counter += 1
        try:
            self._process_task_states()
            self._fill_queue()
            if self._persist_tick % PERSIST_EVERY == 0:
                self._persist_all()
            if self._net_counter >= NETWORK_CHECK_EVERY:
                self._net_counter = 0
                self._check_network()
            if self._persist_tick % 5 == 0:
                for dl in self.scheduler.tick():
                    pass
            speed = self.total_speed
            self._total_speed_history.append(round(speed, 1))
            self.stats_updated.emit()
        except Exception:  # noqa: BLE001 – UI must survive engine bugs
            log.error("engine tick crashed:\n%s", traceback.format_exc())

    def _process_task_states(self) -> None:
        """Detect state transitions of finished tasks and act on them."""
        with self._lock:
            items = list(self.tasks.items())
        for dl_id, task in items:
            if task.state in ("downloading", "paused", "starting"):
                task.dl.status = {
                    "downloading": Status.DOWNLOADING,
                    "paused": Status.PAUSED,
                    "starting": Status.DOWNLOADING,
                }[task.state]
                continue
            # terminal states → clean up.  The task's in-memory record is
            # the source of truth (it holds final size/checksum/renamed path)
            self.tasks.pop(dl_id, None)
            dl = self.db.get_download(dl_id)
            if dl is None:
                continue
            for field_name in ("file_size", "downloaded_size", "file_name",
                               "save_path", "resume_data", "checksum_sha256",
                               "checksum_md5", "completed_at", "segments",
                               "ranges_supported", "protocol"):
                setattr(dl, field_name, getattr(task.dl, field_name))
            # the live segment counters are always up to date
            dl.downloaded_size = task.downloaded_bytes()
            # persist final per-segment state
            try:
                self.db.replace_segments(task.segments)
            except Exception:  # noqa: BLE001
                pass
            if task.state == "completed" and dl_id not in self._finished_notified:
                self._finished_notified.add(dl_id)
                dl.status = Status.COMPLETED
                dl.error_message = ""
                dl.completed_at = dl.completed_at or \
                    datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                dl.progress = 100.0
                dl.speed = 0.0
                self.db.update_download(dl)
                log.info("download #%d COMPLETED: %s", dl_id, dl.save_path)
                self._post_action(dl)
                self.download_completed.emit(dl_id, dl.save_path)
            elif task.state == "failed" and dl.status != Status.FAILED:
                dl.status = Status.FAILED
                dl.speed = 0.0
                dl.error_message = task.result_error
                self.db.update_download(dl)
                log.info("download #%d FAILED: %s", dl_id, task.result_error)
                self.download_failed.emit(dl_id, task.result_error)
            elif task.state == "canceled" and dl.status != Status.CANCELED:
                dl.status = Status.CANCELED
                dl.speed = 0.0
                self.db.update_download(dl)
                self.download_canceled.emit(dl_id)
            # finished → check "shutdown when all done"
            if not self.is_busy() and not self._pending_exists():
                self._maybe_shutdown()
                self.all_downloads_finished.emit()

    def _pending_exists(self) -> bool:
        rows = self.db.list_downloads(statuses=(
            Status.QUEUED, Status.SCHEDULED, Status.DOWNLOADING, Status.PAUSED))
        return bool(rows)

    def _fill_queue(self) -> None:
        max_concurrent = max(1, self.config.get_int("max_concurrent", 3))
        with self._lock:
            active = sum(1 for t in self.tasks.values()
                         if t.state in ("downloading", "starting"))
        if active >= max_concurrent:
            return
        pending = self.db.list_downloads(statuses=(Status.QUEUED,
                                                   Status.SCHEDULED))
        # promote due scheduled ones first
        for dl in pending:
            if dl.status == Status.SCHEDULED and queue_manager.is_due(dl):
                self.db.update_download_fields(dl.id, status=Status.QUEUED)
        pending = [dl for dl in pending if dl.status == Status.QUEUED]
        next_dl = queue_manager.pick_next(pending)
        if next_dl is not None:
            self._start(next_dl)

    def _persist_all(self) -> None:
        with self._lock:
            tasks = list(self.tasks.values())
        for task in tasks:
            if task.state in ("downloading", "paused", "starting"):
                task._persist()

    def _check_network(self) -> None:
        online = is_online()
        if online == self._online:
            return
        self._online = online
        self.network_changed.emit(online)
        if not online:
            log.warning("network lost – auto-pausing active downloads")
            for dl_id, task in list(self.tasks.items()):
                if task.state == "downloading":
                    task.pause(by_network=True)
                    self.db.update_download_fields(
                        dl_id, status=Status.PAUSED,
                        error_message="Paused: network connection lost")
            self.download_paused.emit(0)
        else:
            log.info("network restored – auto-resuming downloads")
            for dl_id, task in list(self.tasks.items()):
                if task.is_paused() and task._paused_by_network:
                    task.resume()
                    self.db.update_download_fields(
                        dl_id, status=Status.DOWNLOADING, error_message="")
            self.download_resumed.emit(0)

    def _post_action(self, dl: Download) -> None:
        # Desktop notifications are emitted by the UI layer. Keeping them out
        # of the engine prevents duplicate Windows toasts for one completed file.
        if self.config.get_bool("sound_enabled", True):
            play_sound("complete")
        action = dl.post_action or self.config.get("post_action", PostAction.NONE)
        if action == PostAction.OPEN_FILE:
            open_file(dl.save_path)
        elif action == PostAction.OPEN_FOLDER:
            open_folder(os.path.dirname(dl.save_path), dl.file_name)

    def _maybe_shutdown(self) -> None:
        if self.config.get("post_action", PostAction.NONE) != PostAction.SHUTDOWN:
            return
        if self._pending_exists():
            return
        log.info("all downloads finished – shutting down PC as configured")
        shutdown_pc(delay_seconds=30)

    # ------------------------------------------------------------ startup

    def restore_incomplete(self, auto_resume: bool = True) -> list[Download]:
        """Crash recovery: everything 'downloading' becomes 'paused'.

        Returns the recovered downloads (for the UI to display/ask about).
        """
        self.db.mark_downloading_as_paused()
        recovered = self.db.restore_incomplete()
        if auto_resume:
            for dl in recovered:
                if dl.status == Status.PAUSED:
                    self.start_download(dl.id)
        return recovered
