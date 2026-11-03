"""In-app tutorial data and progress; QML owns presentation, nothing here touches the game."""
from copy import deepcopy
import json
import os
from pathlib import Path
from PySide6.QtCore import QObject, Property, QThreadPool, QUrl, Qt, Signal, Slot
from backend import Work
from client_settings import ACTION_ORDER

TUTORIAL_ROOT = Path(__file__).resolve().with_name("tutorial")
CHAPTER_FILE = Path(__file__).resolve().with_name("tutorial-chapters.json")
ACTION_NAMES = {"map": "游戏地图键", "toggle-panel": "游戏控件", "toggle-round": "本局开始 / 结束",
                "toggle-sheet-visibility": "小抄显影", "manual-map-picker": "手动选图"}
PAGES = {"home": "回到主页面", "settings": "去设置页", "gallery": "去地图资源", "custom": "去自定义路线",
         "help": "去帮助页"}


def load_chapters(path=CHAPTER_FILE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data["chapters"], data["faq"]
    except (OSError, ValueError, KeyError, TypeError):
        # Optional help content must not prevent the client from starting.
        return [], []


def _write_state(path, payload):
    temp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


class TutorialService(QObject):
    """Chapters with current bindings, seen chapters and the one-time home invite.

    Losing or failing to write the state file only re-shows hints; it never
    blocks the tutorial. Writes run on a single worker and are coalesced.
    """
    changed = Signal()
    openRequested = Signal(str)

    def __init__(self, state_path, *, settings=None, image_root=TUTORIAL_ROOT, chapters=None, parent=None):
        super().__init__(parent)
        self.state_path = Path(state_path)
        self.settings = settings
        self.image_root = Path(image_root)
        self._chapters, self._faq = chapters if chapters is not None else load_chapters()
        self._ids = {chapter["id"] for chapter in self._chapters}
        self._done, self._invited = [], False
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            done = data.get("done")
            if isinstance(done, list):
                self._done = list(dict.fromkeys(value for value in done if isinstance(value, str) and value in self._ids))
            self._invited = data.get("invited") is True
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._jobs = set()
        self._dirty = False
        self._closed = False
        if settings is not None:
            settings.changed.connect(self._settings_changed)
        self._binding_values = self._bindings()

    def _bindings(self):
        values = self.settings.persistedValues if self.settings is not None else {}
        actions = dict(zip(ACTION_ORDER, values.get("actionHotkeys") or []))
        actions["map"] = values.get("hotkey", "")
        return actions

    def _settings_changed(self):
        values = self._bindings()
        if values != self._binding_values:
            self._binding_values = values
            self.changed.emit()

    def _rows(self):
        rows = deepcopy(self._chapters)
        for chapter in rows:
            chapter["done"] = chapter["id"] in self._done
            chapter["pageAction"] = PAGES.get(chapter.get("page") or "", "")
            for step in chapter["steps"]:
                step["imageUrl"] = QUrl.fromLocalFile(str(self.image_root / step["image"])).toString()
                step["bindings"] = "；".join(
                    f"{ACTION_NAMES.get(key, key)} {self._binding_values.get(key) or '未设置'}" for key in step.get("keys", []))
        return rows

    chapters = Property("QVariantList", _rows, notify=changed)
    faq = Property("QVariantList", lambda s: deepcopy(s._faq), constant=True)
    done = Property("QVariantList", lambda s: list(s._done), notify=changed)
    inviteVisible = Property(bool, lambda s: not s._invited, notify=changed)
    pendingWrites = Property(bool, lambda s: bool(s._jobs) or s._dirty, notify=changed)

    @Slot()
    @Slot(str)
    def open(self, chapter=""):
        if not self._closed:
            self.openRequested.emit(chapter if chapter in self._ids else "")

    @Slot(str)
    def markDone(self, chapter):
        if not self._closed and chapter in self._ids and chapter not in self._done:
            self._done.append(chapter)
            self._save()

    @Slot()
    def dismissInvite(self):
        if not self._closed and not self._invited:
            self._invited = True
            self._save()

    def _save(self):
        self.changed.emit()
        if self._closed:
            return
        if self._jobs:
            self._dirty = True
            return
        self._start_write()

    def _start_write(self):
        self._dirty = False
        payload = dict(version=1, done=list(self._done), invited=self._invited)
        job = Work(lambda: _write_state(self.state_path, payload))
        self._jobs.add(job)

        def finished(_value, _error):
            # A failed write keeps the in-memory state; the next change retries.
            self._jobs.discard(job)
            if self._dirty and not self._closed:
                self._save()
            else:
                self.changed.emit()
        job.signals.done.connect(finished, Qt.QueuedConnection)
        self._pool.start(job)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        # Normal window close drains via pendingWrites with the event loop live.
        # Direct shutdown cannot rely on queued done callbacks to flush _dirty.
        if self._dirty:
            self._start_write()
        self._pool.waitForDone()
        self._jobs.clear()
        self.changed.emit()
