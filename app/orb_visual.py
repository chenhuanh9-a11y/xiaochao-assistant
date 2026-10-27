"""Pure orb artwork. The caller owns state, Canvas geometry, input and lifecycle.

Create on the existing 64-DIP Canvas; keep its transparent background and input
bindings. The main owner still scales coordinates with Canvas.scale. ``scale``
here affects font pixels and line widths only, never coordinates or window size.
Omitted paint values preserve the existing displayed value; in particular a
status update must not reset the pressed fill during a drag.
"""
from __future__ import annotations

from desktop_theme import (FONT, SURFACE, TINT, BUTTON_BG, PRIMARY_HOVER, MUTED,
                           ORB_SHADOW, ERROR_BG)


def build_orb_visual(canvas, *, state="待", mode_text="", status_color=MUTED, scale=1.0):
    ids = {
        "orb_shadow": canvas.create_oval(7, 8, 61, 62, fill=ORB_SHADOW, outline=""),
        "orb_body": canvas.create_oval(3, 3, 59, 59, fill=SURFACE, outline=status_color, width=2),
        "orb_status_dot": canvas.create_oval(46, 46, 54, 54, fill=status_color, outline=SURFACE, width=1),
        "orb_state_badge": canvas.create_oval(5, 5, 19, 19, fill=BUTTON_BG, outline=status_color, width=1),
        "orb_state_text": canvas.create_text(12, 12, text=state),
        "orb_mode_text": canvas.create_text(31, 32, text=mode_text),
    }
    paint_orb(canvas, ids, state=state, mode_text=mode_text,
              status_color=status_color, pressed=False, scale=scale)
    return ids


def paint_orb(canvas, ids, *, state=None, mode_text=None, status_color=None,
              pressed=None, scale=None):
    """Paint caller-decided display values; no state inference or event hooks."""
    canvas.itemconfigure(ids["orb_shadow"], fill=ORB_SHADOW)
    canvas.itemconfigure(ids["orb_status_dot"], outline=SURFACE)
    canvas.itemconfigure(ids["orb_mode_text"], fill=PRIMARY_HOVER)
    if scale is not None:
        px = lambda value: max(1, round(value * scale))
        canvas.itemconfigure(ids["orb_body"], width=px(2))
        canvas.itemconfigure(ids["orb_status_dot"], width=px(1))
        canvas.itemconfigure(ids["orb_state_badge"], width=px(1))
        canvas.itemconfigure(ids["orb_state_text"], font=(FONT, -px(7*96/72), "bold"))
        canvas.itemconfigure(ids["orb_mode_text"], font=(FONT, -px(25*96/72), "bold"))
    if pressed is not None:
        canvas.itemconfigure(ids["orb_body"], fill=TINT if pressed else SURFACE)
    if status_color is not None:
        canvas.itemconfigure(ids["orb_body"], outline=status_color)
        canvas.itemconfigure(ids["orb_status_dot"], fill=status_color)
        canvas.itemconfigure(ids["orb_state_badge"], outline=status_color)
        canvas.itemconfigure(ids["orb_state_text"], fill=status_color)
    if state is not None:
        canvas.itemconfigure(ids["orb_state_badge"], fill=ERROR_BG if state == "错" else BUTTON_BG)
        canvas.itemconfigure(ids["orb_state_text"], text=state)
    if mode_text is not None:
        canvas.itemconfigure(ids["orb_mode_text"], text=mode_text)
