"""Shared presentation tokens; callers retain all state and lifecycle decisions."""

BG = "#edf3fb"
SURFACE = "#ffffff"
CARD = "#ffffff"
TINT = "#f1f6ff"
LINE = "#d6e2f0"
TEXT = "#172b4d"
MUTED = "#52667f"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
ON_PRIMARY = "#ffffff"
BUTTON_BG = "#eaf2ff"
BUTTON_HOVER = "#dbeafe"
BUTTON_TEXT = "#1e40af"
NAV_SELECTED = "#dbeafe"
NAV_TEXT = "#1d4ed8"
INPUT_BG = "#f0f5fc"
IMAGE_BG = "#edf2f8"
SCROLL_THUMB = "#b6c8df"
SCROLL_HOVER = "#8eaace"
GREEN = "#137a4b"
GOLD = "#996600"
WARNING_BG = "#fff4d6"
ERROR = "#b42318"
ERROR_BG = "#fff0ee"
ORB_SHADOW = "#bccfe8"
SHELL_BG = SURFACE
SHELL_HEADER_BG = TINT
SHELL_BORDER = LINE
CONNECTOR_BG = SURFACE
STATUS_PILL_BG = SURFACE
STATUS_PILL_TEXT = TEXT
CARD_RADIUS_DIP = 10
CONTROL_RADIUS_DIP = 8
FONT = "Microsoft YaHei UI"


def status_color(snapshot):
    """Color caller-owned semantic phases for light surfaces; never write facts."""
    phase = snapshot.get("phase")
    if phase == "failed" or snapshot.get("severity") == "error":
        return ERROR
    if phase in {"ready", "recovered"}:
        return GREEN
    if phase == "working":
        return PRIMARY_HOVER
    if phase == "needs-user":
        return GOLD
    return MUTED
