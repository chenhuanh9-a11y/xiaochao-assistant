"""Assistant side of 手动选图: the setting, the picker window and its commit.

Manual mode only removes automatic identity ranking while no map is locked.
Capture, open/close and floor detection keep running, and a picked map is
committed through ``confirm_route`` like a confirmed candidate, so fitting,
the reference sheet and round lifecycle stay on the shared path.
"""
from __future__ import annotations

from pathlib import Path
import threading

from PIL import Image, ImageOps, ImageTk

from desktop_theme import IMAGE_BG
from manual_map_picker import (PICKER_KEY_CODES, SELECTION_MODE_LABELS, ManualMapPicker,
                               normalize_selection_mode, picker_groups)

MANUAL_PICKER_ACTION = "manual-map-picker"


def manual_identity_skipped(assistant, locked: bool | None = None) -> bool:
    """True while manual mode waits for the player's pick (no identity ranking).

    A plain function so partial assistants (tests, workers) need only the
    attributes it reads.
    """
    if normalize_selection_mode(getattr(assistant, "map_selection_mode", "auto")) != "manual":
        return False
    if locked is None:
        locked = assistant._round_identity_lock_context() is not None
    return not locked


class ManualMapSelectionMixin:
    map_selection_mode = "auto"
    manual_map_picker: ManualMapPicker | None = None
    # Read by the key-listener thread; None while the picker is hidden.
    _manual_picker_key_codes: dict[str, int] | None = None

    # Mode -------------------------------------------------------------------
    def _manual_identity_skipped(self, locked: bool | None = None) -> bool:
        return manual_identity_skipped(self, locked)

    def set_map_selection_mode(self, mode: str) -> dict:
        if getattr(self, "closed", False) or mode not in SELECTION_MODE_LABELS:
            return dict(success=False, reason="invalid-selection-mode", message="当前无法切换选图方式")
        previous = normalize_selection_mode(getattr(self, "map_selection_mode", "auto"))
        if previous != mode:
            self.map_selection_mode = mode
            self._save_settings()
            self._log("map-selection-mode-changed", previousMode=previous, mapSelectionMode=mode)
            if mode == "auto":
                self.close_manual_map_picker()
            self._set_status(self._manual_selection_hint() if mode == "manual"
                             else "选图方式：自动识别 · 打开地图后自动识别", "#5ee0a3")
            sync = getattr(self, "_sync_game_control_view", None)
            if callable(sync):
                sync()
        return dict(success=True, mode=mode, message=f"选图方式：{SELECTION_MODE_LABELS[mode]}")

    def _manual_selection_hint(self) -> str:
        key = self.action_hotkeys.get(MANUAL_PICKER_ACTION, ("", 0))[0]
        how = f"按 {key} " if key else "点游戏控件里的「选择本局地图」"
        return f"手动选图 · {how}选择本局地图"

    # Picker window ----------------------------------------------------------
    def manual_map_picker_visible(self) -> bool:
        picker = self.manual_map_picker
        try:
            return bool(picker is not None and picker.winfo_exists() and picker.winfo_viewable())
        except Exception:
            return False

    def toggle_manual_map_picker(self, source: str = "button") -> None:
        if self.manual_map_picker_visible():
            self.close_manual_map_picker()
        else:
            self.open_manual_map_picker(source=source)

    def open_manual_map_picker(self, source: str = "button") -> bool:
        if getattr(self, "closed", False):
            return False
        if getattr(self, "_map_package_live_request", None) is not None:
            self._set_status("作者资源正在导入，请稍后选图", "#ffb84d")
            return False
        pack = getattr(self, "pack", None)
        if pack is None or not getattr(pack, "maps", None):
            self._set_status("当前模式的地图资源未就绪，暂时不能选图", "#ff8a65")
            return False
        picker = self.manual_map_picker
        if picker is None or not picker.winfo_exists():
            picker = self.manual_map_picker = ManualMapPicker(
                self.root, on_choose=lambda item: self._apply_manual_map_selection(item.id),
                on_close=self.close_manual_map_picker,
                on_need_thumbnails=self._request_manual_picker_thumbnails,
                scale=float(getattr(self, "ui_scale", 1.0) or 1.0))
        self._manual_picker_generation = getattr(self, "_manual_picker_generation", 0) + 1
        self._manual_picker_pack = pack
        picker.present(picker_groups(pack.maps, getattr(self, "game_mode", "nightmare")),
                       getattr(self, "game_mode", "nightmare"))
        self._place_manual_map_picker(picker)
        picker.deiconify()
        picker.lift()
        self._set_noactivate(picker, True, sync_layout=True)
        self._manual_picker_key_codes = dict(PICKER_KEY_CODES)
        self._log("manual-map-picker-opened", source=source, mapCount=len(pack.maps),
                  gameMode=getattr(self, "game_mode", None),
                  mapSelectionMode=getattr(self, "map_selection_mode", "auto"))
        return True

    def close_manual_map_picker(self) -> None:
        self._manual_picker_key_codes = None
        self._manual_picker_generation = getattr(self, "_manual_picker_generation", 0) + 1
        picker = self.manual_map_picker
        try:
            if picker is not None and picker.winfo_exists():
                picker.withdraw()
        except Exception:
            pass

    def _refresh_manual_map_picker(self) -> None:
        """Re-list after a mode switch so a stale pack is never offered."""
        if self.manual_map_picker_visible():
            self.open_manual_map_picker(source="mode-changed")

    def _place_manual_map_picker(self, picker) -> None:
        picker.update_idletasks()
        width, height = picker.winfo_reqwidth(), picker.winfo_reqheight()
        rect = None
        hwnd = getattr(self, "game_hwnd", None)
        if hwnd:
            try:
                from identityv_live_mvp import client_rect
                rect = client_rect(int(hwnd))
            except Exception:
                rect = None
        if rect is None or rect[2] - rect[0] < width or rect[3] - rect[1] < height:
            rect = (0, 0, picker.winfo_screenwidth(), picker.winfo_screenheight())
        left = rect[0] + (rect[2] - rect[0] - width) // 2
        top = rect[1] + (rect[3] - rect[1] - height) // 2
        picker.geometry(f"{left:+d}{top:+d}")

    def _handle_manual_picker_key(self, key: str) -> None:
        picker = self.manual_map_picker
        if not self.manual_map_picker_visible():
            return
        if getattr(self, "_manual_picker_pack", None) is not getattr(self, "pack", None):
            self._refresh_manual_map_picker()
            return
        picker.command(str(key))

    # Commit -----------------------------------------------------------------
    def _apply_manual_map_selection(self, map_id: str) -> bool:
        pack = getattr(self, "pack", None)
        item = getattr(pack, "by_id", {}).get(map_id)
        if item is None or getattr(self, "_manual_picker_pack", None) is not pack:
            self._set_status("地图列表已变化，请重新选择", "#ffb84d")
            self._refresh_manual_map_picker()
            return False
        if getattr(self, "_map_package_live_request", None) is not None:
            self._set_status("作者资源正在导入，请稍后选图", "#ffb84d")
            return False
        self.close_manual_map_picker()
        floors = tuple(str(floor) for floor in getattr(pack, "floors", ()))
        floor_seeded = False
        if getattr(self, "floor_required", False) or str(self.current_floor) not in floors:
            # The pick is the player's identity evidence; the floor is only a
            # seed and the next locked frame still reads the real one.
            self.current_floor = str(self.manual_floor or pack.default_entry_floor)
            self.floor_required = False
            floor_seeded = True
        self._log("manual-map-picker-selected", mapId=map_id, floor=str(self.current_floor),
                  floorSeeded=floor_seeded, mapOpen=bool(getattr(self, "map_is_open", False)),
                  mapSelectionMode=getattr(self, "map_selection_mode", "auto"))
        self.gate.select(map_id)
        self.confirm_route()
        return getattr(self.gate, "confirmed_id", None) == map_id

    # Thumbnails -------------------------------------------------------------
    def _manual_picker_thumbnail_source(self, item) -> Path | None:
        if getattr(self, "game_mode", "nightmare") == "difficult":
            native = getattr(self, "difficult_native_context", None)
            try:
                return Path(native.full_reference(item.id) if native is not None else
                            self.pack.source_root / self.pack.metadata[item.id]["manualReference"])
            except Exception:
                return None
        floors = (str(getattr(self.pack, "default_entry_floor", "1")), "1", "2", "B1")
        for floor in floors:
            path = (getattr(item, "layers", None) or {}).get(floor)
            if path is not None and Path(path).is_file():
                return Path(path)
        return None

    def _request_manual_picker_thumbnails(self, map_ids) -> None:
        picker = self.manual_map_picker
        pack = getattr(self, "pack", None)
        if picker is None or pack is None:
            return
        cache = self.__dict__.setdefault("_manual_picker_photos", {})
        if cache.get("pack") is not pack:
            cache.clear()
            cache["pack"] = pack
        pending = []
        for map_id in map_ids:
            photo = cache.get(map_id)
            if photo is not None:
                picker.set_thumbnail(map_id, photo)
            elif map_id in pack.by_id:
                pending.append((map_id, self._manual_picker_thumbnail_source(pack.by_id[map_id])))
        if not pending:
            return
        generation, size = getattr(self, "_manual_picker_generation", 0), picker.thumbnail_size

        def run() -> None:
            background = Image.new("RGB", size, IMAGE_BG)
            for map_id, path in pending:
                image = background.copy()
                try:
                    with Image.open(path) as source:
                        preview = ImageOps.contain(source.convert("RGBA"), size, Image.Resampling.LANCZOS)
                    image.paste(preview, ((size[0] - preview.width) // 2,
                                          (size[1] - preview.height) // 2), preview)
                except Exception:
                    pass  # The card keeps its blank image; the map stays selectable.
                self.events.put({"kind": "manual-picker-thumbnail", "mapId": map_id, "image": image,
                                 "generation": generation, "pack": pack})

        threading.Thread(target=run, daemon=True, name="manual-picker-thumbnails").start()

    def _receive_manual_picker_thumbnail(self, event: dict) -> None:
        cache = self.__dict__.get("_manual_picker_photos") or {}
        if event.get("pack") is not getattr(self, "pack", None) or cache.get("pack") is not event.get("pack"):
            return
        photo = ImageTk.PhotoImage(event["image"], master=self.root)
        cache[event["mapId"]] = photo
        if (event.get("generation") == getattr(self, "_manual_picker_generation", None)
                and self.manual_map_picker is not None):
            self.manual_map_picker.set_thumbnail(event["mapId"], photo)
