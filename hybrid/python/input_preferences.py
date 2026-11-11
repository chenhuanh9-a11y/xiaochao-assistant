"""Read-only shortcut projection from the shared product settings values."""
from __future__ import annotations

import json
from pathlib import Path

from client_settings_values import ACTION_ORDER, decode_settings
from hotkey_bindings import Chord, parse_chord, validate_unique
from hotkey_keys import _primary_hotkey_to_vk


def read_input_preferences(data_root):
    path = Path(data_root) / "ui-settings.json"
    try:
        try:
            with path.open("rb") as source:
                data = source.read(1_048_577)
        except FileNotFoundError:
            data = None
        if data is not None and len(data) > 1_048_576:
            raise ValueError("设置文件超过 1 MiB")
        raw = {} if data is None else json.loads(data.decode("utf-8-sig"))
        if not isinstance(raw, dict):
            raise ValueError("设置文件必须是 JSON 对象")
        json.dumps(raw, allow_nan=False)
        values, invalid = decode_settings(raw, existing_file=data is not None)
        errors = {key: value for key, value in invalid.items()
                  if key in {"hotkey", "actionHotkeys", "hotkey/actionHotkeys"}}
        if errors:
            raise ValueError("；".join(errors.values()))
        bindings = {"map": parse_chord(values["hotkey"], _primary_hotkey_to_vk),
                    **{action: parse_chord(text, _primary_hotkey_to_vk)
                       for action, text in zip(ACTION_ORDER, values["actionHotkeys"])},
                    "escape": Chord("ESCAPE", 0x1b)}
        validate_unique(bindings)
        # Bare modifiers require the original raw-mouse tap observer; mouse
        # primaries require raw input too. Neither is a keyboard edge shortcut.
        rows = [dict(action=action, name=chord.name, vk=chord.vk,
                     modifiers=list(chord.modifiers),
                     keyboardSupported=chord.vk not in {2, 4, 5, 6, 0x10, 0x11, 0x12})
                for action, chord in bindings.items() if chord.vk]
        return dict(available=True, bindings=rows, error="")
    except (OSError, ValueError, TypeError, UnicodeError, AttributeError) as error:
        return dict(available=False, bindings=[], error=f"快捷键设置读取失败：{error}")
