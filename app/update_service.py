"""Update notices for the desktop window: check, validate, remember choices.

Reads the published Windows manifest (the same one the earlier PC client
uses). Nothing is downloaded or installed here: delivery is browser-only, so
the window opens the official share pages and the user installs manually.
Network work runs on a daemon thread; results are delivered through
``poll()`` on the Tk thread.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from urllib.parse import urlsplit
import urllib.request

MANIFEST_URL = os.environ.get("XINGTU_UPDATE_MANIFEST_URL",
                              "https://api.xingchenmap.cn/updates/windows.json")
HISTORY_URL = os.environ.get("XINGTU_UPDATE_HISTORY_URL",
                             "https://api.xingchenmap.cn/updates/windows/history.json")
ALLOWED_HOSTS = {"api.xingchenmap.cn"}
DOWNLOAD_HOSTS = {"quark": "pan.quark.cn", "uc": "drive.uc.cn"}
MAX_BYTES = 256 * 1024
TIMEOUT_SECONDS = 15
RECHECK_SECONDS = 2 * 60 * 60
RETRY_SECONDS = (30, 60, 120, 300)
VERSION = re.compile(r"\d+(?:\.\d+){1,3}")


def version_key(text: str) -> tuple[int, ...]:
    """Numeric dotted version; suffixes after '-' or '+' are ignored."""
    match = VERSION.match(str(text or "").strip().lstrip("vV"))
    if not match:
        return ()
    parts = [int(part) for part in match.group(0).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    new, old = version_key(candidate), version_key(current)
    return bool(new) and (not old or new > old)


@dataclass(frozen=True)
class Release:
    version: str
    version_code: int
    notes: str
    published_at: str
    size: int | None = None
    pages: dict = field(default_factory=dict)  # {"quark": url, "uc": url}

    @property
    def published_date(self) -> str:
        return self.published_at[:10] if self.published_at else ""


def _https_page(value, host: str) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urlsplit(value)
    return value if parts.scheme == "https" and parts.hostname == host else None


def parse_release(data) -> Release:
    """Validate one manifest; raise ValueError with a short reason."""
    if not isinstance(data, dict):
        raise ValueError("清单格式不正确")
    if data.get("schemaVersion") != 1 or data.get("platform") != "windows":
        raise ValueError("清单版本或平台不匹配")
    if any(key in data for key in ("downloadUrl", "installerUrl", "apkUrl")):
        raise ValueError("清单包含不受支持的直接下载地址")
    version = data.get("version")
    code = data.get("versionCode")
    if not isinstance(version, str) or not version_key(version) or not isinstance(code, int) or code < 1:
        raise ValueError("清单版本号无效")
    notes = data.get("releaseNotes")
    if not isinstance(notes, str) or len(notes) > 4000:
        raise ValueError("更新公告无效")
    pages = {name: url for name, host in DOWNLOAD_HOSTS.items()
             if (url := _https_page(data.get("downloadPageUrl" if name == "quark" else "ucDownloadPageUrl"), host))}
    size = data.get("size")
    return Release(version=version, version_code=code, notes=notes.strip(),
                   published_at=str(data.get("publishedAt") or ""),
                   size=size if isinstance(size, int) and size > 0 else None, pages=pages)


def parse_history(data) -> list[Release]:
    rows = data.get("releases") if isinstance(data, dict) else None
    releases = []
    for row in rows if isinstance(rows, list) else ():
        try:
            releases.append(parse_release(row))
        except ValueError:
            continue
    return sorted(releases, key=lambda release: version_key(release.version), reverse=True)[:20]


def fetch_json(url: str):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError("更新地址不受信任")
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "XingtuDesktop/update-check"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
            raise ValueError("更新地址被重定向到其他站点")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("更新清单过大")
    return json.loads(body.decode("utf-8"))


class UpdateService:
    """Owns update state; the UI reads ``snapshot()`` and calls the actions."""

    def __init__(self, current_version: str, state_path: Path, *, fetch=fetch_json):
        self.current_version = current_version
        self.state_path = Path(state_path)
        self._fetch = fetch
        self._events: queue.Queue = queue.Queue()
        self._checking = False
        self._failures = 0
        self.phase = "idle"  # idle | checking | latest | available | error
        self.message = ""
        self.latest: Release | None = None
        self.history: list[Release] = []
        self.checked_at = 0.0
        self.next_check_at = 0.0
        self._prompted: set[str] = set()
        self._stored = self._load()

    # Persistence -----------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._stored, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.state_path)
        except OSError:
            pass  # Reminder preferences are best-effort; never block the window.

    @property
    def skipped_version(self) -> str:
        return str(self._stored.get("skippedVersion") or "")

    @property
    def seen_notes_version(self) -> str:
        return str(self._stored.get("seenNotesVersion") or "")

    # Actions ---------------------------------------------------------------
    def check(self, *, manual=False) -> bool:
        if self._checking:
            return False
        self._checking = True
        self.phase = "checking"
        self.message = "正在检查更新…"

        def run():
            event = dict(manual=manual)
            try:
                event["release"] = parse_release(self._fetch(MANIFEST_URL))
                try:
                    event["history"] = parse_history(self._fetch(HISTORY_URL))
                except Exception:  # History is optional; the latest notice still counts.
                    event["history"] = None
            except Exception as error:  # Network, TLS, JSON or validation.
                event["error"] = str(error) or type(error).__name__
            self._events.put(event)
        threading.Thread(target=run, name="xingtu-update-check", daemon=True).start()
        return True

    def poll(self, now: float | None = None) -> dict | None:
        """Apply a finished check (Tk thread). Returns the event, if any."""
        now = time.time() if now is None else now
        try:
            event = self._events.get_nowait()
        except queue.Empty:
            if not self._checking and self.next_check_at and now >= self.next_check_at:
                self.check()
            return None
        self._checking = False
        self.checked_at = now
        if "error" in event:
            self._failures += 1
            # check() replaced the cached phase with "checking". Restore a
            # finished state so a failed background refresh cannot lock the UI.
            self.phase = ("error" if event["manual"] or self.latest is None else
                          "available" if is_newer(self.latest.version, self.current_version) else "latest")
            self.message = f"检查更新失败：{event['error']}"
            self.next_check_at = now + RETRY_SECONDS[min(self._failures, len(RETRY_SECONDS)) - 1]
            return event
        self._failures = 0
        self.latest = event["release"]
        if event.get("history") is not None:
            self.history = event["history"]
        if not any(release.version == self.latest.version for release in self.history):
            self.history.insert(0, self.latest)
        available = is_newer(self.latest.version, self.current_version)
        self.phase = "available" if available else "latest"
        self.message = (f"发现新版本 v{self.latest.version}" if available
                        else f"已是最新版本（v{self.current_version}）")
        self.next_check_at = now + RECHECK_SECONDS
        return event

    def should_prompt(self) -> bool:
        """Startup-style reminder: once per session per version, unless skipped."""
        release = self.latest
        return (self.phase == "available" and release is not None
                and release.version != self.skipped_version and release.version not in self._prompted)

    def mark_prompted(self) -> None:
        if self.latest is not None:
            self._prompted.add(self.latest.version)

    def skip_latest(self) -> None:
        if self.latest is not None:
            self._stored["skippedVersion"] = self.latest.version
            self._save()

    def mark_notes_seen(self) -> None:
        if self.latest is not None and self.seen_notes_version != self.latest.version:
            self._stored["seenNotesVersion"] = self.latest.version
            self._save()

    @property
    def has_unread(self) -> bool:
        """Badge: a newer version whose notes the user has not opened yet."""
        return (self.phase == "available" and self.latest is not None
                and self.latest.version != self.seen_notes_version)
