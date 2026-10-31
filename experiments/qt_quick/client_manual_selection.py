"""Qt manual map picker; emits scoped intent through the existing Session.

No listener, setting writes, recognition, capture or pixel publication lives here.
The coordinator supplies the current pack on its UI thread and routes native
shortcut actions to command(). The runtime remains the sole identity writer.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from manual_selection_model import (PickerState, SELECTION_MODE_LABELS, SELECTION_MODES,
    manual_selection_scope, normalize_selection_mode, option_title, picker_groups,
    plan_manual_selection)


MANUAL_PICKER_ACTION = "manual-map-picker"
# Semantic commands for NativeShortcutBackend. No implicit bindings are installed;
# a user's configured keys always retain their original action.
PICKER_COMMANDS = ("manual-picker-1", "manual-picker-2", "manual-picker-3", "manual-picker-4",
                   "manual-picker-previous", "manual-picker-next", "manual-picker-back",
                   "manual-picker-close")


def _field(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _thumbnail_path(pack, item):
    """Display-only source; its availability never grants identity or geometry."""
    if str(getattr(pack, "mode", "")) == "difficult":
        row = getattr(pack, "metadata", {}).get(str(_field(item, "id", "")), {})
        relative = row.get("manualReference")
        root = getattr(pack, "source_root", None)
        if relative and root:
            path = Path(root) / relative
            if path.is_file():
                return path
    layers = _field(item, "layers", {}) or {}
    for floor in dict.fromkeys((str(getattr(pack, "default_entry_floor", "1")), "1", "2", "B1")):
        value = layers.get(floor)
        if value and Path(value).is_file():
            return Path(value)
    return None


class ManualSelectionService(QObject):
    changed = Signal()
    visibleChanged = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._state = dict(session.snapshot)
        self._catalog_mode = ""
        self._resource_identity = ""
        self._catalog_key = None
        self._rows = {}
        self._floors = ()
        self._default_floor = ""
        self._picker = PickerState([])
        self._scope = None
        self._visible = False
        self._closed = False
        self._revision = 0
        self._message = ""
        self._key_labels = {}
        self._last_snapshot = None
        session.changed.connect(self.sync)

    def refresh_catalog(self, pack, resource_identity, *, thumbnail_provider=None):
        """UI-thread catalog handoff, repeated same-pack refresh is an exact no-op.

        ``resource_identity`` is a stable nonempty runtime resource revision, not
        an author/profile identifier. Change it whenever actual resources change.
        ``thumbnail_provider(item)`` may supply an existing display image path.
        """
        identity = str(resource_identity or "")
        mode = str(getattr(pack, "mode", "")) if pack is not None else ""
        key = (id(pack), identity, mode)
        if key == self._catalog_key:
            return
        self._catalog_key = key
        self._catalog_mode, self._resource_identity = mode, identity
        self._floors = tuple(map(str, getattr(pack, "floors", ())))
        self._default_floor = str(getattr(pack, "default_entry_floor", ""))
        rows = []
        by_id = getattr(pack, "by_id", {})
        for item in getattr(pack, "maps", ()):
            map_id = str(_field(item, "id", "") or "")
            if not map_id or map_id not in by_id:
                continue
            try:
                source = thumbnail_provider(item) if thumbnail_provider is not None else _thumbnail_path(pack, item)
            except (OSError, ValueError, KeyError, TypeError):
                source = None
            url = QUrl.fromLocalFile(str(Path(source).resolve())) if source else QUrl()
            if source:
                # QUrl.toLocalFile() ignores the fragment; Qt's image cache key
                # still distinguishes replacement resources at the same path.
                url.setFragment(identity)
            rows.append(dict(id=map_id, mapId=map_id,
                name=str(_field(item, "name", map_id) or map_id),
                direction=str(_field(item, "direction", "") or ""),
                thumbnailUrl=url.toString(), thumbnailMissing=not bool(source)))
        self._rows = {row["id"]: row for row in rows}
        self._picker = PickerState(picker_groups(rows, mode))
        self._scope = None
        self._revision += 1
        self._message = ""
        self._set_visible(False)
        self._publish()

    def set_key_labels(self, labels):
        """Actual native bindings only; empty labels never advertise fake keys."""
        allowed = {MANUAL_PICKER_ACTION, *PICKER_COMMANDS}
        value = {str(key): str(text) for key, text in dict(labels).items() if key in allowed and text}
        if value != self._key_labels:
            self._key_labels = value
            self._publish()

    def _command_available(self, action):
        commands = self._state.get("commands", {})
        if action in commands:
            return bool(commands[action].get("available"))
        available = self._state.get("availableCommands", ())
        return bool(available.get(action)) if isinstance(available, dict) else action in available

    def _reason(self):
        state = self._state
        if self._closed or state.get("closed"):
            return "选图已关闭"
        if state.get("resourceLocked"):
            return "正在更新地图资源，请稍后选择"
        if state.get("contextPending") or state.get("roundPreparing") and not state.get("roundPrepared"):
            return "正在准备本局或切换环境，请稍后选择"
        if not (state.get("roundActive") or state.get("roundPrepared")):
            return "请先开始本局，再选择地图"
        if not self._rows or not self._resource_identity:
            return "当前模式的地图资源尚未就绪"
        if (self._catalog_mode != state.get("mode")
                or self._resource_identity != str(state.get("resourceIdentity") or "")):
            return "地图资源已变化，正在更新列表"
        if state.get("manualSelectionPending") and state.get("manualSelectionStatus") == "awaiting-material":
            return "已选择地图，正在核对当前楼层与位置"
        if state.get("busy") and state.get("pendingAction") not in {"refresh_map", "map", "capture_now"}:
            return "当前操作尚未完成，请稍后选择"
        if not self._command_available("select_manual_map"):
            return "当前暂不能选图，请等待地图资源就绪"
        return ""

    def _snapshot(self):
        options = []
        for index, item in enumerate(self._picker.options()):
            if self._picker.stage == "groups":
                name, maps = item
                row = dict(kind="group", id=name, name=name, count=len(maps), thumbnailUrl="")
            else:
                row = dict(item, kind="map", name=option_title(item, self._picker.groups[self._picker.group][0]))
            options.append(dict(row, index=index, shortcut=self._key_labels.get(f"manual-picker-{index + 1}", "")))
        selection_mode = normalize_selection_mode(self._state.get("mapSelectionMode"))
        reason = self._reason()
        return dict(visible=self._visible, mode=selection_mode, modeLabel=SELECTION_MODE_LABELS[selection_mode],
            gameMode=self._catalog_mode, stage=self._picker.stage, options=options,
            groupName="" if self._picker.group is None else self._picker.groups[self._picker.group][0],
            page=self._picker.page, pageCount=self._picker.page_count(), revision=self._revision,
            canBack=self._picker.group is not None and not self._picker.single_group,
            canChoose=not bool(reason), canOpen=not bool(reason), reason=reason,
            canSetMode=not self._closed and not self._state.get("closed")
                and not self._state.get("resourceLocked") and not self._state.get("contextPending")
                and not self._state.get("busy") and self._command_available("set_map_selection_mode"),
            currentMapId=str(self._state.get("mapId") or ""),
            selectedMapId=str(self._state.get("manualSelectedMapId") or ""),
            pending=bool(self._state.get("manualSelectionPending")),
            message=self._message or str(self._state.get("manualSelectionReason") or ""),
            keyLabels=dict(self._key_labels))

    snapshot = Property("QVariantMap", lambda self: deepcopy(self._snapshot()), notify=changed)
    visible = Property(bool, lambda self: self._visible, notify=visibleChanged)

    def _publish(self):
        value = self._snapshot()
        if value != self._last_snapshot:
            self._last_snapshot = deepcopy(value)
            self.changed.emit()

    def _set_visible(self, value):
        if self._visible != value:
            self._visible = value
            self.visibleChanged.emit()

    @Slot()
    def sync(self):
        previous = self._state
        self._state = dict(self.session.snapshot)
        old_scope, new_scope = manual_selection_scope(previous), manual_selection_scope(self._state)
        if self._visible and (not (self._state.get("roundActive") or self._state.get("roundPrepared")) or self._state.get("closed")
                or self._state.get("resourceLocked") or self._state.get("contextPending")
                or (old_scope.mode, old_scope.cycle_id, old_scope.resource_identity,
                    old_scope.generation, old_scope.selection_mode)
                != (new_scope.mode, new_scope.cycle_id, new_scope.resource_identity,
                    new_scope.generation, new_scope.selection_mode)):
            self.close()
        elif self._visible and old_scope != new_scope:
            # Auto floor detection stays active. Invalidate already queued UI
            # clicks, but keep the user's route category/page for the new floor.
            self._scope = new_scope
            self._revision += 1
        self._publish()

    @Slot(result=bool)
    def open(self):
        self.sync()
        reason = self._reason()
        if reason:
            self._message = reason
            self._publish()
            return False
        if not self._visible:
            self._scope = manual_selection_scope(self._state)
            self._revision += 1
            self._message = ""
            self._set_visible(True)
            self._publish()
        return True

    @Slot()
    def close(self):
        if self._visible:
            self._revision += 1
        self._scope = None
        self._set_visible(False)
        self._publish()

    @Slot(result=bool)
    def toggle(self):
        if self._visible:
            self.close()
            return True
        return self.open()

    @Slot(str, result=bool)
    def setMode(self, mode):
        self.sync()
        if mode not in SELECTION_MODES or not self._snapshot()["canSetMode"]:
            return False
        if normalize_selection_mode(self._state.get("mapSelectionMode")) != mode:
            self.session.dispatchWithPayload("set_map_selection_mode", {"selectionMode": mode})
        return True

    @Slot(int, result=bool)
    @Slot(int, int, result=bool)
    def choose(self, index, revision=-1):
        self.sync()
        if (not self._visible or self._reason() or self._scope is None
                or revision not in (-1, self._revision)):
            return False
        choice = self._picker.choose(index)
        if choice is None:
            return False
        kind, item = choice
        if kind == "group":
            self._revision += 1
            self._publish()
            return True
        payload = dict(self._scope.payload(), mapId=item["mapId"])
        choice_state = dict(self._state, manualSelectionPending=False)
        plan = plan_manual_selection(choice_state, payload, self._rows,
                                     floors=self._floors, default_floor=self._default_floor)
        if not plan.accepted:
            self._message = plan.message
            self._publish()
            return False
        self.close()
        if plan.changed:
            self.session.dispatchWithPayload("select_manual_map", payload)
        return True

    @Slot(int, result=bool)
    def turn(self, delta):
        if not self._visible or not self._picker.turn(delta):
            return False
        self._revision += 1
        self._publish()
        return True

    @Slot(result=bool)
    def back(self):
        if not self._visible or not self._picker.back():
            return False
        self._revision += 1
        self._publish()
        return True

    @Slot(str, result=bool)
    def command(self, action):
        """Called by the existing native input owner; no QShortcut fallback."""
        if action == MANUAL_PICKER_ACTION:
            return self.toggle()
        if not self._visible:
            return False
        if action in PICKER_COMMANDS[:4]:
            return self.choose(PICKER_COMMANDS.index(action))
        if action == "manual-picker-previous":
            return self.turn(-1)
        if action == "manual-picker-next":
            return self.turn(1)
        if action == "manual-picker-back":
            return self.back()
        if action == "manual-picker-close":
            self.close()
            return True
        return False

    def shutdown(self):
        if not self._closed:
            self._closed = True
            self.close()
            self.session.changed.disconnect(self.sync)
