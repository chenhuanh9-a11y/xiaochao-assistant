"""Bounded static-index/ZIP transport; the main controller owns UI generations.

No default endpoint, automatic retries, installation, shutdown or restart.
Requires the reviewed v4 star_map_import module for owned-temp cleanup only.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import stat
import tempfile
import threading
import time
from urllib import error, parse, request

from star_map_import import ImportRejected, _no_links, _rollback_published

MAX_INDEX_BYTES = 256 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
MAX_MANUAL_ATTEMPTS = 3
CHUNK_BYTES = 64 * 1024


class DownloadError(RuntimeError):
    def __init__(self, reason, message=None):
        self.reason = reason
        super().__init__(message or reason)


class DownloadCancelled(DownloadError):
    def __init__(self):
        super().__init__("cancelled")


@contextmanager
def _locked_file(path, *, writable=False, expected_identity=None):
    """Windows handle denies replacement AND writes through verification/publish.

    A Path returned later is still a mutable filesystem name: main must run the
    existing importer inspection before use. No cross-platform lock is assumed.
    """
    if os.name != "nt":
        raise DownloadError("windows-locked-publication-required")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    _no_links(path)
    handle = api.CreateFileW(str(path), 0xC0000000 if writable else 0x80000000,
                            1, None, 3, 0x200000, None)  # share READ only
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(int(handle), (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY)
    except Exception:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, "r+b" if writable else "rb") as current:
        info = os.fstat(current.fileno())
        if not stat.S_ISREG(info.st_mode) or expected_identity is not None and (info.st_dev, info.st_ino) != expected_identity:
            raise DownloadError("temporary-file-object-changed")
        yield current


@dataclass
class _Operation:
    generation: int
    cancelled: threading.Event
    external_cancel: threading.Event | None


class MapPackageDownloadService:
    """Synchronous worker API; call from main's worker, never the Tk thread.

    read_index(*, cancel_event=None) -> list[dict]
    download_package(package_id, *, cancel_event=None, progress=None) -> Path
    cancel_active() -> bool
    configured -> bool; attempts_remaining(package_id) -> int

    Public index rows contain packageId/title/summary/bytes/sha256, never URL.
    Progress callback is (receivedBytes, totalBytes). Main must also gate its
    queued callbacks/results by its own current selection/generation.
    """
    def __init__(self, index_url, download_root, *, allow_loopback_http_for_tests=False,
                 io_timeout=10, index_deadline=30, download_deadline=900):
        self.index_url = index_url.strip() if isinstance(index_url, str) else None
        self.download_root = Path(os.path.abspath(download_root))
        self._test_http = allow_loopback_http_for_tests is True
        if not (0 < io_timeout <= 30 and 0 < index_deadline <= 120 and 0 < download_deadline <= 3600):
            raise DownloadError("invalid-timeout")
        self.io_timeout, self.index_deadline, self.download_deadline = io_timeout, index_deadline, download_deadline
        self._inflight = threading.Lock()
        self._state = threading.Lock()
        self._generation = 0
        self._active = None
        self._packages = {}
        self._attempts = {}
        # Default trust store and hostname verification; never disable TLS checks.
        self._tls_context = ssl.create_default_context()
        self._tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        if self.index_url:
            self._validate_url(self.index_url)

    @property
    def configured(self):
        return bool(self.index_url)

    def _validate_url(self, url):
        if not isinstance(url, str) or not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise DownloadError("invalid-url")
        try:
            parts = parse.urlsplit(url)
            port = parts.port
        except ValueError as problem:
            raise DownloadError("invalid-url") from problem
        if not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
            raise DownloadError("url-credentials-fragment-or-host-invalid")
        if parts.scheme != "https":
            if not (self._test_http and parts.scheme == "http" and parts.hostname == "127.0.0.1"):
                raise DownloadError("https-required")
        if port is not None and not 1 <= port <= 65535:
            raise DownloadError("invalid-url-port")
        return url

    def _check(self, operation, deadline=None):
        with self._state:
            cancelled = (self._generation != operation.generation or operation.cancelled.is_set()
                         or operation.external_cancel is not None and operation.external_cancel.is_set())
        if cancelled:
            raise DownloadCancelled()
        if deadline is not None and time.monotonic() > deadline:
            raise DownloadError("request-deadline-exceeded")

    @contextmanager
    def _operation(self, external_cancel):
        if not self.configured:
            raise DownloadError("index-not-configured", "尚未配置授权地图索引地址")
        if not self._inflight.acquire(blocking=False):
            raise DownloadError("request-already-running")
        with self._state:
            self._generation += 1
            operation = _Operation(self._generation, threading.Event(), external_cancel)
            self._active = operation
        try:
            self._check(operation)
            yield operation
        except (error.URLError, http.client.HTTPException, TimeoutError, OSError) as problem:
            self._check(operation)
            raise DownloadError("network-or-file-error", str(problem)) from problem
        finally:
            with self._state:
                if self._active is operation:
                    self._active = None
            self._inflight.release()

    def cancel_active(self):
        with self._state:
            if self._active is None:
                return False
            self._active.cancelled.set()
            self._generation += 1
            return True

    def _open(self, url, operation, deadline):
        class NoRedirectDrain(request.HTTPRedirectHandler):
            # Do not call the inherited handler: it fp.read() drains an unlimited
            # redirect body before opening the next hop, bypassing our bounds.
            def http_error_302(self, req, fp, code, msg, headers):
                return fp
            http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302
        handlers = [request.HTTPSHandler(context=self._tls_context), NoRedirectDrain()]
        if self._test_http:
            handlers.append(request.ProxyHandler({}))  # Local fixtures never leave via an environment proxy.
        opener = request.build_opener(*handlers)
        current_url = url
        for hop in range(6):
            self._check(operation, deadline)
            self._validate_url(current_url)
            response = opener.open(request.Request(current_url, headers={"Accept-Encoding": "identity",
                "User-Agent": "StarMapResourceDownloader/1"}),
                timeout=min(self.io_timeout, max(.001, deadline-time.monotonic())))
            try:
                self._check(operation, deadline)
                self._validate_url(response.geturl())
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise DownloadError("redirect-location-missing")
                    next_url = parse.urljoin(response.geturl(), location)
                    response.close()  # No body read/drain, even close-delimited streams.
                    self._check(operation, deadline)
                    self._validate_url(next_url)
                    if hop == 5:
                        raise DownloadError("redirect-limit")
                    current_url = next_url
                    continue
                if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise DownloadError("unexpected-http-response")
                return response
            except Exception:
                response.close()
                raise
        raise DownloadError("redirect-limit")

    @staticmethod
    def _length(response, limit, exact=None):
        value = response.headers.get("Content-Length")
        if value is None:
            return
        if not re.fullmatch(r"[0-9]+", value) or int(value) > limit or exact is not None and int(value) != exact:
            raise DownloadError("content-length-mismatch")

    @staticmethod
    def _json(data):
        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise DownloadError("duplicate-index-key")
                result[key] = value
            return result
        try:
            return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)
        except (UnicodeError, ValueError) as problem:
            raise DownloadError("invalid-index-json") from problem

    def read_index(self, *, cancel_event=None):
        with self._operation(cancel_event) as operation:
            deadline = time.monotonic() + self.index_deadline
            data = bytearray()
            with self._open(self.index_url, operation, deadline) as response:
                self._length(response, MAX_INDEX_BYTES)
                declared_length = response.headers.get("Content-Length")
                final_index_url = response.geturl()
                while True:
                    self._check(operation, deadline)
                    block = response.read1(min(CHUNK_BYTES, MAX_INDEX_BYTES + 1 - len(data)))
                    self._check(operation, deadline)
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > MAX_INDEX_BYTES:
                        raise DownloadError("index-size-limit")
                if declared_length is not None and len(data) != int(declared_length):
                    raise DownloadError("index-content-length-mismatch")
            metadata = self._json(data)
            if (not isinstance(metadata, dict) or type(metadata.get("schemaVersion")) is not int
                    or metadata["schemaVersion"] != 1 or not isinstance(metadata.get("packages"), list)
                    or not 1 <= len(metadata["packages"]) <= 100):
                raise DownloadError("unsupported-index-schema")
            packages = {}
            for row in metadata["packages"]:
                if (not isinstance(row, dict) or set(row) != {"packageId", "title", "summary", "bytes", "sha256", "url"}
                        or not isinstance(row["packageId"], str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["packageId"])
                        or len(row["packageId"]) > 64 or row["packageId"] in packages
                        or not isinstance(row["title"], str) or not 1 <= len(row["title"]) <= 100
                        or not isinstance(row["summary"], str) or len(row["summary"]) > 1000
                        or type(row["bytes"]) is not int or not 0 < row["bytes"] <= MAX_PACKAGE_BYTES
                        or not isinstance(row["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                        or not isinstance(row["url"], str) or not row["url"]):
                    raise DownloadError("invalid-index-package")
                item = dict(row)
                item["url"] = self._validate_url(parse.urljoin(final_index_url, row["url"]))
                packages[row["packageId"]] = item
            self._check(operation, deadline)
            with self._state:
                if operation.generation != self._generation:
                    raise DownloadCancelled()
                self._packages = packages
                self._attempts = {}
            return [{k: v for k, v in row.items() if k != "url"} for row in packages.values()]

    def attempts_remaining(self, package_id):
        with self._state:
            return max(0, MAX_MANUAL_ATTEMPTS - self._attempts.get(package_id, 0))

    def download_package(self, package_id, *, cancel_event=None, progress=None):
        with self._operation(cancel_event) as operation:
            with self._state:
                if package_id not in self._packages:
                    raise DownloadError("unknown-package-id")
                item = dict(self._packages[package_id])
            _no_links(self.download_root)
            self.download_root.mkdir(parents=True, exist_ok=True)
            final = self.download_root / f"{package_id}-{item['sha256']}.zip"
            _no_links(final)
            if final.exists():
                if final.stat().st_size != item["bytes"]:
                    raise DownloadError("existing-cache-conflict")
                with _locked_file(final) as existing:
                    if os.fstat(existing.fileno()).st_size != item["bytes"]:
                        raise DownloadError("existing-cache-conflict")
                    digest = hashlib.file_digest(existing, "sha256").hexdigest()
                    self._check(operation)
                    if digest != item["sha256"]:
                        raise DownloadError("existing-cache-conflict")
                    return final
            with self._state:
                attempts = self._attempts.get(package_id, 0)
                if attempts >= MAX_MANUAL_ATTEMPTS:
                    raise DownloadError("manual-retry-limit; check index again explicitly")
                self._attempts[package_id] = attempts + 1
            deadline = time.monotonic() + self.download_deadline
            fd, filename = tempfile.mkstemp(prefix="star-map-", suffix=".part", dir=self.download_root)
            temporary = Path(filename)
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            os.close(fd)
            digest, received = hashlib.sha256(), 0
            published = False
            try:
                with _locked_file(temporary, writable=True, expected_identity=identity) as sink:
                    with self._open(item["url"], operation, deadline) as response:
                        self._length(response, item["bytes"], exact=item["bytes"])
                        while True:
                            self._check(operation, deadline)
                            block = response.read1(min(CHUNK_BYTES, item["bytes"] + 1 - received))
                            self._check(operation, deadline)
                            if not block:
                                break
                            received += len(block)
                            if received > item["bytes"]:
                                raise DownloadError("package-size-limit")
                            sink.write(block)
                            digest.update(block)
                            if progress is not None:
                                self._check(operation, deadline)
                                progress(received, item["bytes"])
                    sink.flush()
                    sink.seek(0)
                    self._check(operation, deadline)
                    if (received != item["bytes"] or os.fstat(sink.fileno()).st_size != item["bytes"]
                            or digest.hexdigest() != item["sha256"]
                            or hashlib.file_digest(sink, "sha256").hexdigest() != item["sha256"]):
                        raise DownloadError("package-size-or-sha-mismatch")
                    self._check(operation, deadline)
                    # The same verified file object remains open with no WRITE
                    # or DELETE sharing through link publication, closing the
                    # hash-close-replace-link gap. The native OS rejects a swap.
                    os.link(temporary, final)
                    published = True
                    self._check(operation, deadline)
                    with self._state:
                        self._attempts[package_id] = 0
                    return final
            except Exception:
                # The guarded stream has closed before cleanup. Its original
                # temporary hard link remains alive for ownership-safe rollback.
                if published:
                    _rollback_published(final, identity, digest.hexdigest())
                raise
            finally:
                # Only the file object created by this request is eligible. No
                # directory sweeps or cleanup of other requests' finished ZIPs.
                _rollback_published(temporary, identity, digest.hexdigest())


def prune_installed_downloads(download_root: Path, install_root: Path) -> list[str]:
    """Delete finished downloads whose exact archive is the installed package.

    A cached ``<package>-<sha256>.zip`` is removed only when the package's
    installed receipt names the same archive SHA-256 and no upgrade recovery
    is pending; any other archive stays for a manual retry. Call at startup,
    before any download or import of this session.
    """
    removed = []
    state = install_root / "work" / "map-packages"
    try:
        _no_links(download_root)
        if (state / "pending-upgrade.json").exists() or not download_root.is_dir():
            return removed
        archives = sorted(download_root.glob("*.zip"))
    except (ImportRejected, OSError):
        return removed
    for archive in archives:
        match = re.fullmatch(r"(.+)-([0-9a-f]{64})\.zip", archive.name)
        if match is None:
            continue
        try:
            _no_links(archive)
            receipt = json.loads((state / f"{match[1]}.json").read_text("utf-8"))
            if (not isinstance(receipt, dict) or receipt.get("packageId") != match[1]
                    or receipt.get("archiveSha256") != match[2] or not archive.is_file()):
                continue
            archive.unlink()
        except (ImportRejected, OSError, ValueError):
            continue
        removed.append(archive.name)
    return removed
