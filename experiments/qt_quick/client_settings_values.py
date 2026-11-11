"""Toolkit-free settings values, validation, and persisted value decoding."""
from __future__ import annotations

from copy import deepcopy
import math
import os
import sys

# These shared contracts live under app/ and are also imported by the Qt glue.
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app"))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from season_context import CONTEXTS, MODES, KNOWN_MODES, context_key, normalize_season
from client_control_layout import preset_layout, validate_layout
from floor_control_region import normalized_region
from client_manual_map import MANUAL_MAP_PICKER_DEFAULT_HOTKEY, MANUAL_PICKER_ACTION, SELECTION_MODES
from hotkey_bindings import parse_chord, validate_unique
from hotkey_keys import _primary_hotkey_to_vk
from opacity_controls import load_opacities

ACTION_ORDER = ("toggle-panel", "candidate-prev", "candidate-next", "confirm-round",
                "cycle-floor", "end-round", "toggle-sheet-visibility", "start-round", "toggle-round",
                MANUAL_PICKER_ACTION)
LEGACY_ACTIONS = ["F5", "F6", "F7", "F8", "F9", "F10", "F11", "", ""]
FRESH_ACTIONS = ["F9", "F6", "F7", "F8", "", "", "F11", "", "F10",
                 MANUAL_MAP_PICKER_DEFAULT_HOTKEY]
BOOL_DEFAULTS = {"automaticFailureEvidence": True, "referenceSheetVisible": True, "sheetVisibilityEnabled": True,
                 "orbVisible": True, "qtControlsVisible": True, "reduceMotion": False, "uiSoundEnabled": False,
                 "difficultGridEnabled": False, "onboardingCompleted": False, "routePointNumbers": False}
RANGES = {"sheetOpacity": (0., 1.), "referenceOpacity": (0., 1.),
          "interfaceOpacity": (.65, .95), "sheetScale": (.5, 1.5)}
DEFAULTS = {**BOOL_DEFAULTS, "sheetOpacity": .55, "referenceOpacity": .55,
            "interfaceOpacity": .82, "sheetScale": 1., "gameMode": "nightmare", "gameSeason": "s0",
            "mapSelectionMode": "auto",
            "routeProfileId": "wang-qingxin", "modeRouteProfiles": {}, "contextRouteProfiles": {},
            "zhan10CoffinVariantId": "variant-01", "hotkey": "G",
            "actionHotkeys": FRESH_ACTIONS, "gameControlLayout": preset_layout(),
            "sheetPosition": None, "sheetPositionSpace": "unknown", "compactPosition": None,
            "floorControlMode": "auto", "floorControlRegion": None}
FLOOR_CONTROL_MODES = ("auto", "region", "fixed")


def _validate(key, value):
    if key not in DEFAULTS:
        raise ValueError("此设置尚未迁移，不能在这里修改")
    if key in BOOL_DEFAULTS:
        if type(value) is not bool:
            raise ValueError("需要开启或关闭值")
    elif key in RANGES:
        low, high = RANGES[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"需要 {low:g}–{high:g} 之间的数值")
        value = float(value)
    elif key == "gameMode":
        if value not in KNOWN_MODES:
            raise ValueError("未知地图模式")
    elif key == "gameSeason":
        value = normalize_season(value)
    elif key == "contextRouteProfiles":
        if not isinstance(value, dict) or not set(value) <= set(CONTEXTS):
            raise ValueError("按赛季路线需要有效的 season:mode 配置表")
        for profile in value.values():
            _validate("routeProfileId", profile)
    elif key == "mapSelectionMode":
        if value not in SELECTION_MODES:
            raise ValueError("选图方式只能为 auto 或 manual")
    elif key in ("routeProfileId", "zhan10CoffinVariantId"):
        if not isinstance(value, str) or not value or len(value) > 128 or any(ord(c) < 32 for c in value):
            raise ValueError("请输入有效的配置 ID（1–128 个字符）")
    elif key == "modeRouteProfiles":
        if not isinstance(value, dict) or not set(value) <= {"nightmare", "difficult"}:
            raise ValueError("按模式路线必须是 nightmare / difficult 的配置表")
        for profile in value.values():
            _validate("routeProfileId", profile)
    elif key == "gameControlLayout":
        value = validate_layout(value)
    elif key in ("sheetPosition", "compactPosition"):
        if value is not None and (not isinstance(value, list) or len(value) != 2
                                  or any(type(v) is not int or not -(2**31) <= v < 2**31 for v in value)):
            raise ValueError("位置需要两个整数坐标 [x, y]，或空值")
    elif key == "sheetPositionSpace":
        if value not in ("unknown", "qt-logical-v1", "legacy-physical-v1"):
            raise ValueError("位置坐标类型无效")
    elif key == "floorControlMode":
        if value not in FLOOR_CONTROL_MODES:
            raise ValueError("楼层识别方式只能为自动、框选或固定")
    elif key == "floorControlRegion":
        value = _validate_floor_region(value)
    elif key == "hotkey":
        if not isinstance(value, str):
            raise ValueError("快捷键需要文本")
        value = parse_chord(value, _primary_hotkey_to_vk).name
    elif key == "actionHotkeys":
        if not isinstance(value, list) or len(value) != len(ACTION_ORDER) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"功能快捷键必须包含 {len(ACTION_ORDER)} 项，留空表示解绑")
        value = [parse_chord(v, _primary_hotkey_to_vk).name for v in value]
    return deepcopy(value)


def _validate_floor_region(value):
    """None, or the framed floor-button range normalized to the client it was drawn on."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"rect", "clientSize"}:
        raise ValueError("楼层框选范围需要 rect 和 clientSize")
    rect = normalized_region(value["rect"])
    size = value["clientSize"]
    if rect is None:
        raise ValueError("楼层框选范围需要在游戏窗口内")
    if (not isinstance(size, list) or len(size) != 2
            or any(type(v) is not int or not 100 <= v <= 32768 for v in size)):
        raise ValueError("楼层框选的窗口尺寸无效")
    return dict(rect=list(rect), clientSize=list(size))


def _validate_hotkeys(values, raw_actions=None):
    # Slot 10 is Tk's appended manual-map-picker, which Qt now owns as a
    # regular action: it is validated exactly once, as that action. A stored
    # tail Qt could not load stays on disk and blocks shortcut writes until the
    # user binds slot 10 to a valid chord, so it is never silently overwritten.
    if (isinstance(raw_actions, list) and len(raw_actions) == len(ACTION_ORDER)
            and not values["actionHotkeys"][-1]):
        tail = raw_actions[-1]
        if not isinstance(tail, str):
            raise ValueError("“手动选图”快捷键（第 10 项）需要文本；请先修复原配置或在设置里重新绑定")
        parse_chord(tail, _primary_hotkey_to_vk)
    validate_unique({"地图键": parse_chord(values["hotkey"], _primary_hotkey_to_vk),
                     **{key: parse_chord(text, _primary_hotkey_to_vk)
                        for key, text in zip(ACTION_ORDER, values["actionHotkeys"])}})


def _legacy_actions(value, map_name):
    """Project stored 6–10 slot arrays onto Qt's ten slots.

    Older nine-slot files never gain an invented manual-map-picker chord (it
    could collide with a bare ALT or other existing binding); slot 10 stays
    unbound until the user sets it. A ten-slot file keeps its trailing picker
    chord; a tail Qt cannot read loads as unbound and is reported on validation.
    """
    if not isinstance(value, list) or len(value) not in (6, 7, 8, 9, 10):
        return _validate("actionHotkeys", value)
    known, tail = value[:9], value[9:]
    if not all(isinstance(item, str) for item in known):
        raise ValueError("功能快捷键需要文本")
    names = [parse_chord(item, _primary_hotkey_to_vk).name for item in known]
    if len(names) == 6:
        used = {parse_chord(item, _primary_hotkey_to_vk).vk for item in names}
        used.add(parse_chord(map_name, _primary_hotkey_to_vk).vk)
        # Matches the six-action migration in the legacy reader.
        names.append(next(name for name in ["F11", *(f"F{i}" for i in range(12, 25))]
                          if parse_chord(name, _primary_hotkey_to_vk).vk not in used))
    names += [""] * (9 - len(names))
    try:
        names.append(parse_chord(tail[0], _primary_hotkey_to_vk).name if tail else "")
    except (TypeError, ValueError, AttributeError):
        names.append("")
    return names


def decode_settings(raw, existing_file: bool):
    """Decode validated JSON settings into UI values and field errors.

    The caller owns file reading, JSON syntax checks, persistence, and errors.
    """
    values = deepcopy(DEFAULTS)
    values["actionHotkeys"] = deepcopy(LEGACY_ACTIONS if existing_file else FRESH_ACTIONS)
    invalid = {}
    for key in DEFAULTS:
        if key in raw:
            try:
                values[key] = (_legacy_actions(raw[key], values["hotkey"])
                               if key == "actionHotkeys" else _validate(key, raw[key]))
            except (TypeError, ValueError) as error:
                invalid[key] = str(error)
                if key == "automaticFailureEvidence":
                    values[key] = False
    try:
        context_key(values["gameMode"], values["gameSeason"])
    except ValueError as error:
        # Preserve the raw file while exposing a safe, repairable snapshot.
        # A valid S1 ordinary pair remains untouched; S0 cannot inherit it.
        invalid["gameMode"] = str(error)
        values["gameMode"] = DEFAULTS["gameMode"]
    # Snapshot legacy authors into S0 contexts in memory. Reading never
    # rewrites the file; a later context edit persists both S0 identities,
    # so updating the legacy global field cannot leak into another mode.
    for mode in MODES:
        values["contextRouteProfiles"].setdefault(context_key(mode, "s0"),
            values["modeRouteProfiles"].get(mode) or values["routeProfileId"])
    if len(values["actionHotkeys"]) != len(ACTION_ORDER):
        # An existing file without actionHotkeys starts from the nine
        # legacy slots; like a stored nine-slot array, slot 10 is unbound.
        values["actionHotkeys"] = _legacy_actions(values["actionHotkeys"], values["hotkey"])
    stored_actions = raw.get("actionHotkeys")
    if ("actionHotkeys" in invalid and isinstance(stored_actions, list)
            and len(stored_actions) == len(ACTION_ORDER)):
        # A bad known slot must not also drop a readable picker chord when
        # the user repairs the array.
        try:
            values["actionHotkeys"][-1] = parse_chord(stored_actions[-1], _primary_hotkey_to_vk).name
        except (TypeError, ValueError, AttributeError):
            pass
    try:
        _validate_hotkeys(values, raw.get("actionHotkeys"))
    except ValueError as error:
        invalid["hotkey/actionHotkeys"] = str(error)
    # Legacy reference opacity falls back to the stored sheet opacity.
    overlay, reference = load_opacities(raw)
    if "sheetOpacity" not in invalid:
        values["sheetOpacity"] = overlay
    if "referenceOpacity" not in invalid:
        values["referenceOpacity"] = reference
    return values, invalid
