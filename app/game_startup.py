"""Game-start policy and per-install opt-in. No Qt, capture or map imports.

The Run value is both registration and authoritative opt-in. Removing it stops
an existing watcher. Only explicit user action creates it; upgrades never do.
"""
from __future__ import annotations

import hashlib
import json
import ntpath
import os
from pathlib import Path
import subprocess
import tempfile

from packaged_product_identity import QT_EXE

WATCHER_NAME = "XingtuGameWatcher.exe"
MAIN_NAME = QT_EXE
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def title_allowed(title):
    title = str(title or "").strip()
    if title == "第五人格":
        return True
    folded = title.casefold()
    return any(folded == prefix or (folded.startswith(prefix)
               and folded[len(prefix):len(prefix) + 1] in tuple(" -–—|:·[()"))
               for prefix in ("identity v", "identityv"))


def install_key(directory):
    return hashlib.sha256(ntpath.normcase(str(Path(directory).resolve())).encode()).hexdigest()[:24]


def state_directory(directory):
    return Path(os.environ["LOCALAPPDATA"]) / "Xingtu" / "game-startup" / install_key(directory)


def run_name(directory):
    return "XingtuGameWatcher-" + install_key(directory)


def run_command(directory):
    return subprocess.list2cmdline([str(Path(directory).resolve() / WATCHER_NAME)])


def enabled(directory):
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, kind = winreg.QueryValueEx(key, run_name(directory))
        return kind == winreg.REG_SZ and value == run_command(directory)
    except FileNotFoundError:
        return False


def set_enabled(directory, value):
    if type(value) is not bool:
        raise ValueError("Expected a boolean")
    if os.name != "nt":
        raise OSError("游戏启动绑定仅支持 Windows")
    import winreg
    directory = Path(directory).resolve()
    if value and not all((directory / name).is_file() for name in (WATCHER_NAME, MAIN_NAME)):
        raise OSError("当前安装缺少游戏启动助手，请安装完整版本")
    if value:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, run_name(directory), 0, winreg.REG_SZ, run_command(directory))
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
                current, kind = winreg.QueryValueEx(key, run_name(directory))
                if kind != winreg.REG_SZ or current != run_command(directory):
                    raise OSError("游戏启动项内容发生变化，未删除未知命令")
                winreg.DeleteValue(key, run_name(directory))
        except FileNotFoundError:
            pass


EXIT_KEY = r"Software\Xingtu\GameStartup"


def suppressed_games(directory, session):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, EXIT_KEY) as key:
            value, kind = winreg.QueryValueEx(key, run_name(directory) + "-" + str(int(session)))
    except FileNotFoundError:
        return []
    if kind != winreg.REG_SZ or len(value) > 32768:
        raise ValueError("Invalid exit state")
    data = json.loads(value)
    if not isinstance(data, list) or len(data) > 256 or any(
            not isinstance(item, str) or len(item) > 80 for item in data):
        raise ValueError("Invalid exit state")
    return data


def suppress_games(directory, games, session):
    # Elevated main writes HKCU, not an attacker-redirectable filesystem path.
    import winreg
    keys = sorted({f"{int(pid)}:{int(created)}" for pid, created in games})
    if len(keys) > 256:
        raise ValueError("Too many game processes")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, EXIT_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, run_name(directory) + "-" + str(int(session)), 0, winreg.REG_SZ, json.dumps(keys))


class EpisodeLedger:
    """Reserve before ShellExecute, including cancel/failure and existing main.

    Keys are PID + kernel process creation timestamp. Missing windows and unknown
    queries never clear a reservation. Persistent reservations survive restarts.
    """
    def __init__(self, path, alive=None):
        self.path = Path(path)
        self.alive = alive
        try:
            with self.path.open(encoding="utf-8") as stream:
                raw = stream.read(32769)
            if len(raw) > 32768:
                raise ValueError("Oversized game startup state")
            data = json.loads(raw)
        except FileNotFoundError:
            data = []
        if not isinstance(data, list) or len(data) > 256 or any(
                not isinstance(key, str) or len(key) > 80 for key in data):
            raise ValueError("Invalid game startup state; refusing repeated prompts")
        self.keys = data

    def reserve(self, pid, created):
        key = f"{int(pid)}:{int(created)}"
        if key in self.keys:
            return False
        keys = list(self.keys)
        if len(keys) >= 256 and self.alive is not None:
            keys = [item for item in keys if self.alive(item) is not False]
        if len(keys) >= 256:
            raise ValueError("Episode history full; refusing to evict potentially active games")
        keys.append(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="episode-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(keys, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.keys = keys
        return True


def handle_observation(games, main_state, ledger, launch, still_enabled, suppressed=()):
    """Unknown main identity suppresses launch; do not turn denial into absence."""
    fresh = False
    for pid, created in games:
        reserved = ledger.reserve(pid, created)
        fresh = (reserved and f"{int(pid)}:{int(created)}" not in suppressed) or fresh
    if fresh and main_state == "absent" and still_enabled():
        return launch()
    return "suppressed"
