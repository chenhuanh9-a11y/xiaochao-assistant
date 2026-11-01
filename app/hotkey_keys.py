"""Shared legacy primary key names and aliases; no native input or UI imports."""

NAMED_HOTKEYS = {
    "MOUSE2": 0x02,
    "MOUSE3": 0x04,
    "MOUSE4": 0x05,
    "MOUSE5": 0x06,
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "ALT": 0x12,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PRINTSCREEN": 0x2C,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "NUM*": 0x6A,
    "NUM+": 0x6B,
    "NUM_SEPARATOR": 0x6C,
    "NUM-": 0x6D,
    "NUM.": 0x6E,
    "NUM/": 0x6F,
    "NUMLOCK": 0x90,
    "SCROLLLOCK": 0x91,
    "VOLUME_MUTE": 0xAD,
    "VOLUME_DOWN": 0xAE,
    "VOLUME_UP": 0xAF,
    "MEDIA_NEXT": 0xB0,
    "MEDIA_PREV": 0xB1,
    "MEDIA_STOP": 0xB2,
    "MEDIA_PLAY_PAUSE": 0xB3,
}
NAMED_HOTKEYS.update({f"NUMPAD{index}": 0x60 + index for index in range(10)})
HOTKEY_ALIASES = {
    "空格": "SPACE",
    "CAPS": "CAPSLOCK",
    "RETURN": "ENTER",
    "CONTROL": "CTRL",
    "PGUP": "PAGEUP",
    "PGDN": "PAGEDOWN",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PRTSC": "PRINTSCREEN",
    "右键": "MOUSE2",
    "鼠标右键": "MOUSE2",
    "RBUTTON": "MOUSE2",
    "RIGHTBUTTON": "MOUSE2",
    "鼠标中键": "MOUSE3",
    "鼠标侧键1": "MOUSE4",
    "鼠标侧键2": "MOUSE5",
    "XBUTTON1": "MOUSE4",
    "XBUTTON2": "MOUSE5",
}
OEM_HOTKEYS = {
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}
def _primary_hotkey_to_vk(value: str) -> tuple[str, int]:
    raw = value.strip()
    name = HOTKEY_ALIASES.get(raw, HOTKEY_ALIASES.get(raw.upper(), raw.upper()))
    if name in NAMED_HOTKEYS:
        return name, NAMED_HOTKEYS[name]
    if name in OEM_HOTKEYS:
        return name, OEM_HOTKEYS[name]
    if len(name) == 1 and name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        return name, ord(name)
    if name.startswith("F") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return name, 0x70 + int(name[1:]) - 1
    raise ValueError(
        "没识别到这个键。Fn 和滚轮滚动无法单独录入；"
        "其他键请点“重新录入”后再按一下。"
    )

# Explicit Win32 identities for recorded layout-dependent OEM keys. Old symbol
# spellings retain their legacy VK meaning; new captures never guess from text.
OEM_KEY_NAMES = {0xBA: "OEM_1", 0xBB: "OEM_PLUS", 0xBC: "OEM_COMMA",
    0xBD: "OEM_MINUS", 0xBE: "OEM_PERIOD", 0xBF: "OEM_2", 0xC0: "OEM_3",
    0xDB: "OEM_4", 0xDC: "OEM_5", 0xDD: "OEM_6", 0xDE: "OEM_7", 0xE2: "OEM_102"}
NAMED_HOTKEYS.update({name: vk for vk, name in OEM_KEY_NAMES.items()})
# These Win32 identities need not equal Qt's layout-dependent logical key. A
# French ';' can be VK_M, and '&' VK_1; numpad punctuation also varies by layout.
# One native owner avoids a foreground QML shortcut waiting for another symbol.
NATIVE_ONLY_SHORTCUT_KEYS = frozenset((0x02, 0x04, 0x05, 0x06, *OEM_KEY_NAMES,
    *range(0x30, 0x3A), *range(0x41, 0x5B), *range(0x60, 0x70)))


def recorded_native_primary(vk):
    if vk in OEM_KEY_NAMES:
        return OEM_KEY_NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x6F:
        return next((name for name, value in NAMED_HOTKEYS.items() if value == vk), None)
    return None
