"""Qt-side glue for 手动选图 (manual map selection).

Grouping, option titles and the two-stage page state come from
``app/manual_map_picker_core.py``, shared with the Tk picker (#70), so there is
one copy of those rules. This module adds only Qt-client specifics: the
configurable hotkey action, the native picker keys and thumbnail lookup. It
never imports tkinter or the Tk entrypoint.
"""
from __future__ import annotations

from pathlib import Path
import sys

from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from manual_map_picker_core import (  # noqa: F401  (re-exported for Qt callers)
    MODE_TITLES, NIGHTMARE_GROUP_ORDER, OPTION_KEYS, OTHER_GROUP, PAGE_SIZE, SELECTION_MODE_LABELS,
    SELECTION_MODES, PickerState, map_group, normalize_selection_mode, option_title, picker_groups,
    split_map_name)

MANUAL_PICKER_ACTION = "manual-map-picker"
# Fresh installs start unbound: Alt+1 would conflict with a standalone ALT
# map/action key. Stored ten-slot bindings are preserved by SettingsService.
MANUAL_MAP_PICKER_DEFAULT_HOTKEY = ""
# Only routed while the picker is visible; Escape keeps its existing map-close
# edge as well, matching the Tk picker.
PICKER_KEY_VKS = {"F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
                  "PageUp": 0x21, "PageDown": 0x22, "Backspace": 0x08,
                  "Escape": 0x1B}
PICKER_KEY_NAMES = tuple(PICKER_KEY_VKS)


def manual_map_thumbnail_source(pack, item, mode: str = "nightmare", native=None) -> Path | None:
    """Existing display image for a picker card, or None.

    Mirrors ``ManualMapSelectionMixin._manual_picker_thumbnail_source`` in Tk:
    difficult maps use the native context's full reference (or the pack's
    ``manualReference`` metadata), nightmare maps use the first existing layer
    in entry-floor/1/2/B1 order. Display only; never identity evidence.
    """
    if getattr(pack, "raw", {}).get("season") == "s1":
        display = getattr(native, "display_reference", None)
        return display(item.id, str(getattr(pack, "default_entry_floor", "-1"))) if callable(display) else None
    if mode == "difficult":
        try:
            if native is not None:
                candidate = native.full_reference(item.id)
            else:
                relative = pack.metadata[item.id]["manualReference"]
                candidate = Path(pack.source_root) / relative
            path = Path(candidate) if candidate is not None else None
            return path if path is not None and path.is_file() else None
        except Exception:
            return None
    floors = (str(getattr(pack, "default_entry_floor", "1")), "1", "2", "B1")
    layers = getattr(item, "layers", None) or {}
    for floor in floors:
        try:
            candidate = layers.get(floor)
            if candidate is not None and Path(candidate).is_file():
                return Path(candidate)
        except Exception:
            continue
    return None
