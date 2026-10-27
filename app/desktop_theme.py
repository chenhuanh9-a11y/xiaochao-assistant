"""Shared presentation tokens; callers retain all state and lifecycle decisions.

Xingtu palette: a light star-chart canvas, white cards and one action blue,
with a night-navy identity (sidebar, home hero) and a single star-gold accent.
"""

BG = "#f3f5fa"
SURFACE = "#ffffff"
CARD = "#ffffff"
TINT = "#eef2fb"
LINE = "#e3e8f1"
TEXT = "#142035"
MUTED = "#5c677d"
SUBTLE = "#8a95a8"
PRIMARY = "#2f66eb"
PRIMARY_HOVER = "#1f50cc"
ON_PRIMARY = "#ffffff"
BUTTON_BG = "#e9effd"
BUTTON_HOVER = "#dce6fc"
BUTTON_TEXT = "#1f50cc"
NAV_SELECTED = "#e9effd"
NAV_TEXT = "#1f50cc"
INPUT_BG = "#f5f7fb"
IMAGE_BG = "#eef1f7"
SCROLL_THUMB = "#c5cedd"
SCROLL_HOVER = "#98a4b8"
GREEN = "#17775a"
SUCCESS_BG = "#e3f4ec"
GOLD = "#9a5b0f"
WARNING_BG = "#fcf0dc"
ERROR = "#b8364a"
ERROR_BG = "#fbe7ea"
ORB_SHADOW = "#c3d2ec"
SHELL_BG = SURFACE
SHELL_HEADER_BG = TINT
SHELL_BORDER = LINE
CONNECTOR_BG = SURFACE
STATUS_PILL_BG = SURFACE
STATUS_PILL_TEXT = TEXT

# Night identity: desktop sidebar and the home hero card.
NIGHT = "#13214a"
NIGHT_RING = "#1d2f63"
NIGHT_TEXT = "#f1f5fd"
NIGHT_MUTED = "#aebddf"
NIGHT_ACCENT = "#9db8ff"
NIGHT_HOVER = "#22356b"
STAR_GOLD = "#f2b544"
SIDEBAR = "#101a35"
SIDEBAR_HOVER = "#1a2748"
SIDEBAR_SELECTED = "#253863"
SIDEBAR_TEXT = "#f1f5fd"
SIDEBAR_MUTED = "#9aa8c7"
SIDEBAR_LINE = "#223154"
SIDEBAR_SUCCESS = "#7fd8b2"

CARD_RADIUS_DIP = 12
CONTROL_RADIUS_DIP = 10
PANEL_RADIUS_DIP = 18
SHADOW_DIP = 10
SHADOW_COLOR = "#142035"
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
