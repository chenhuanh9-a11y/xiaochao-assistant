"""S1 map landmarks: important room names and minecart spots (display only).

Minecart spots are the B1 minecart exit models plus the rooms named 「矿车」
(B2/B3 evacuation points) and 「矿车中转站」 (B1/B2 transfer stations).  They
all belong to the 矿车位置 switch, never to the 重点房间 switch.

Ported from the Android client (``assets/custom-map-landmarks.js`` and
``SeasonMapLandmarks.kt``).  The bundled ``s1_map_landmarks.json`` is generated
by ``tools/maps/build_s1_map_landmarks.py`` in game-native pixels of the S1
structure package and bound to each floor's shared-structure mask digest.

Landmarks never enter recognition, routing, snapping, exports, route packages
or thumbnails.  A floor whose canvas or mask differs from the bound one shows
no landmarks rather than misplaced ones.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import threading

import numpy as np

SCHEMA = "xingtu-s1-map-landmarks-v1"
DATA_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "s1_map_landmarks.json"
SETTING_ROOMS = "mapLandmarkRooms"
SETTING_MINECARTS = "mapLandmarkMinecarts"
SETTING_KEYS = (SETTING_ROOMS, SETTING_MINECARTS)
# Same colours as Android.
CART_FILL = (255, 211, 111, 255)
CART_ORE = (183, 201, 213, 255)
CART_LINE = (38, 56, 71, 255)
TEXT_FILL = (255, 255, 255, 255)
TEXT_OUTLINE = (32, 59, 81, 255)
# Rooms drawn as minecarts (owned by the minecart switch) and their caption.
CART_ROOMS = {"矿车": "矿车", "矿车中转站": "中转站"}
# A minecart exit this close to a minecart room (fraction of the floor's long
# side, about one icon width on screen) is the same spot: draw it once.
CART_MERGE_RATIO = .06


@dataclass(frozen=True)
class LandmarkPreferences:
    rooms: bool = True
    minecarts: bool = True

    @property
    def enabled(self) -> bool:
        return self.rooms or self.minecarts

    @classmethod
    def from_settings(cls, values) -> "LandmarkPreferences":
        values = values or {}
        return cls(rooms=values.get(SETTING_ROOMS, True) is not False,
                   minecarts=values.get(SETTING_MINECARTS, True) is not False)


_LOCK = threading.RLock()
_PREFERENCES = LandmarkPreferences()
_DATA: dict | None = None
_DATA_PATH: Path | None = None
_DATA_ERROR = ""
_DECORATED: "OrderedDict[tuple, np.ndarray]" = OrderedDict()


def preferences() -> LandmarkPreferences:
    with _LOCK:
        return _PREFERENCES


def set_preferences(value: LandmarkPreferences) -> bool:
    """UI-owned saved preference shared by the editor and in-game displays."""
    global _PREFERENCES
    with _LOCK:
        if value == _PREFERENCES:
            return False
        _PREFERENCES = value
        _DECORATED.clear()
        return True


def _valid_document(doc) -> bool:
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA or not isinstance(doc.get("modes"), dict):
        return False
    if not isinstance(doc.get("importantRooms"), dict):
        return False
    for maps in doc["modes"].values():
        for floors in maps.values():
            for row in floors.values():
                width, height = row.get("width"), row.get("height")
                if not (isinstance(width, int) and isinstance(height, int) and 0 < width <= 16384 and 0 < height <= 16384):
                    return False
                if not isinstance(row.get("maskSha256"), str) or len(row["maskSha256"]) != 64:
                    return False
                items = row.get("items")
                if not isinstance(items, list) or len(items) > 256:
                    return False
                for item in items:
                    x, y = item.get("x"), item.get("y")
                    if (item.get("kind") not in ("room", "minecart") or not isinstance(item.get("label"), str)
                            or not item["label"] or len(item["label"]) > 48
                            or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                            or not (0 <= x < width and 0 <= y < height)):
                        return False
    return True


def load_data(path: Path | None = None) -> dict | None:
    """Bundled landmark table, or ``None`` (labels simply do not appear)."""
    global _DATA, _DATA_PATH, _DATA_ERROR
    path = Path(path or DATA_FILE)
    with _LOCK:
        if _DATA_PATH == path:
            return _DATA
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not _valid_document(doc):
                raise ValueError("地图标注格式无效")
            _DATA, _DATA_ERROR = doc, ""
        except (OSError, ValueError) as error:
            _DATA, _DATA_ERROR = None, str(error)
        _DATA_PATH = path
        _DECORATED.clear()
        return _DATA


def data_error() -> str:
    with _LOCK:
        return _DATA_ERROR


def floor_entry(map_id, floor, *, data=None) -> dict | None:
    data = load_data() if data is None else data
    if not data:
        return None
    for maps in data["modes"].values():
        row = maps.get(str(map_id), {}).get(str(floor))
        if row is not None:
            return row
    return None


def bound_entry(map_id, floor, binding, *, data=None) -> dict | None:
    """The floor's landmarks only when the display binding matches.

    ``binding`` is either the PC structure binding (``width``, ``height`` and
    ``maskSha256`` of the native canvas) or an Android route-editor binding
    (``androidLayerMaskSha256``).
    """
    row = floor_entry(map_id, floor, data=data)
    if row is None or not isinstance(binding, dict):
        return None
    android = binding.get("androidLayerMaskSha256")
    if android is not None:
        return row if android == row.get("androidLayerMaskSha256") else None
    try:
        size = (int(binding["width"]), int(binding["height"]))
    except (KeyError, TypeError, ValueError):
        return None
    return row if size == (row["width"], row["height"]) and binding.get("maskSha256") == row["maskSha256"] else None


def important_items(row, floor, *, data=None) -> list[dict]:
    """Minecart spots plus the floor's important rooms (Android ``IMPORTANT_ROOMS``).

    Rooms named in ``CART_ROOMS`` come back as ``kind="minecart"`` with a
    ``caption``; a minecart exit at the same spot as one of them is dropped.
    """
    data = load_data() if data is None else data
    names = set((data or {}).get("importantRooms", {}).get(str(floor), ()))
    cart_rooms = [item for item in row["items"] if item["kind"] == "room" and item["label"] in CART_ROOMS]
    near = CART_MERGE_RATIO * max(row["width"], row["height"])
    out = []
    for item in row["items"]:
        if item["kind"] == "minecart":
            if any(np.hypot(item["x"] - room["x"], item["y"] - room["y"]) <= near for room in cart_rooms):
                continue
            out.append(dict(item))
        elif item["kind"] == "room" and item["label"] in CART_ROOMS:
            out.append(dict(item, kind="minecart", caption=CART_ROOMS[item["label"]]))
        elif item["kind"] == "room" and item["label"] in names:
            out.append(dict(item))
    return out


def select(items, prefs: LandmarkPreferences) -> list[dict]:
    return [item for item in items
            if (item["kind"] == "minecart" and prefs.minecarts) or (item["kind"] == "room" and prefs.rooms)]


def mask_sha256(mask) -> str:
    return hashlib.sha256(np.ascontiguousarray(np.asarray(mask) > 0, dtype=np.uint8).tobytes()).hexdigest()


_FONT_CACHE: dict = {}


def _font(size: int):
    from PIL import ImageFont
    key = int(size)
    font = _FONT_CACHE.get(key)
    if font is not None:
        return font
    for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "/System/Library/Fonts/PingFang.ttc",
                 "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "NotoSansCJK-Bold.ttc", "wqy-microhei.ttc"):
        try:
            font = ImageFont.truetype(name, key)
            break
        except OSError:
            continue
    else:
        font = ImageFont.load_default(size=key)
    _FONT_CACHE[key] = font
    return font


def _draw_cart(image, x, y, r):
    """Android minecart icon, supersampled for anti-aliased edges."""
    from PIL import Image, ImageDraw
    ss = 4
    pad = r * 1.4
    left, top = x - pad, y - pad
    side = int(np.ceil(pad * 2)) + 2
    patch = Image.new("RGBA", (side * ss, side * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(patch)
    line = max(1, round(r / 6 * ss))

    def p(dx, dy):
        return ((x + dx * r - left) * ss, (y + dy * r - top) * ss)

    for fill, shape in ((CART_FILL, [p(-1, -.4), p(1, -.4), p(.72, .5), p(-.72, .5)]),
                        (CART_ORE, [p(-.85, -.45), p(-.45, -1), p(-.05, -.42)]),
                        (CART_ORE, [p(-.15, -.42), p(.35, -1.05), p(.83, -.42)])):
        draw.polygon(shape, fill=fill)
        draw.line(shape + [shape[0]], fill=CART_LINE, width=line, joint="curve")
    for dx in (-.6, .6):
        cx, cy = p(dx, .8)
        w = .23 * r * ss
        draw.ellipse((cx - w, cy - w, cx + w, cy + w), fill=CART_LINE)
    draw.line([p(-1.15, 1.1), p(1.15, 1.1)], fill=CART_LINE, width=line)
    patch = patch.resize((side, side), Image.LANCZOS)
    image.alpha_composite(patch, (int(round(left)), int(round(top))))


def _draw_room(image, label, x, y, size, width, height, route):
    from PIL import Image, ImageDraw
    font = _font(max(6, round(size)))
    lines = [label[i:i + 6] for i in range(0, len(label), 6)] or [label]
    probe = ImageDraw.Draw(image)
    text_width = max(probe.textlength(line, font=font) for line in lines)
    half = min(text_width / 2 + 1, width / 2 - 1)
    x = min(max(x, half + 1), width - half - 1)
    edge = min(size * (len(lines) + 1) / 2, height / 2)
    y = min(max(y - (size * 1.05 if route else 0), edge), height - edge)
    stroke = max(1, round(size * 1.8 / 10.5 / 2))
    squeeze = half * 2 / text_width if text_width > half * 2 else 1.0
    for index, line in enumerate(lines):
        cy = y + (index - (len(lines) - 1) / 2) * size * 1.05
        if squeeze >= 1:
            ImageDraw.Draw(image).text((x, cy), line, font=font, anchor="mm", fill=TEXT_FILL,
                                       stroke_width=stroke, stroke_fill=TEXT_OUTLINE)
            continue
        # Over-wide text is squeezed horizontally, like Android textScaleX.
        w = int(np.ceil(probe.textlength(line, font=font))) + stroke * 2 + 2
        h = int(np.ceil(size * 1.6)) + stroke * 2
        patch = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(patch).text((w / 2, h / 2), line, font=font, anchor="mm", fill=TEXT_FILL,
                                   stroke_width=stroke, stroke_fill=TEXT_OUTLINE)
        patch = patch.resize((max(1, int(w * squeeze)), h), Image.LANCZOS)
        image.alpha_composite(patch, (int(round(x - patch.width / 2)), int(round(cy - h / 2))))


def draw(image, items, *, route=False) -> dict:
    """Draw selected items (already in ``image`` pixels) on a PIL RGBA image.

    Same proportions as Android ``SeasonMapLandmarks.draw``: text 3.5% and
    minecart radius 3% of the long side; with a route both shift upwards so
    the route line underneath stays visible.  A minecart room's caption sits
    just under its icon, in the room-name text size.
    """
    width, height = image.size
    count = dict(rooms=0, minecarts=0)
    if width <= 2 or height <= 2:
        return count
    size = min(max(max(width, height) * .035, 10.0), min(width, height) / 3)
    radius = min(max(max(width, height) * .03, 7.0), min(width, height) / 4)
    for item in items:
        if item["kind"] == "minecart":
            r = radius
            x = min(max(float(item["x"]), r * 1.25), width - r * 1.25)
            y = min(max(float(item["y"]) - (r * 1.8 if route else 0), r * 1.1), height - r * 1.4)
            _draw_cart(image, x, y, r)
            if item.get("caption"):
                _draw_room(image, item["caption"], x, y + r * 1.1 + size * .75, size, width, height, False)
            count["minecarts"] += 1
        elif item["kind"] == "room":
            _draw_room(image, item["label"], float(item["x"]), float(item["y"]), size, width, height, route)
            count["rooms"] += 1
    return count


def decorate_bgra(pixels, map_id, floor, reference_mask, matrix_display_to_reference=None, *,
                  route=False, prefs: LandmarkPreferences | None = None):
    """Return BGRA display pixels with landmarks, or the input unchanged.

    ``matrix_display_to_reference`` maps display pixels into the native
    reference canvas (the existing display contract).  The binding is the
    reference's own canvas shape and clean-mask digest.
    """
    prefs = preferences() if prefs is None else prefs
    if not prefs.enabled or pixels is None or reference_mask is None:
        return pixels
    arr = np.asarray(pixels)
    if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] != 4:
        return pixels
    data = load_data()
    if not data:
        return pixels
    height, width = np.asarray(reference_mask).shape[:2]
    row = floor_entry(map_id, floor, data=data)
    if row is None or (row["width"], row["height"]) != (width, height):
        return pixels
    digest = mask_sha256(reference_mask)
    if digest != row["maskSha256"]:
        return pixels
    items = select(important_items(row, floor, data=data), prefs)
    if not items:
        return pixels
    matrix = np.eye(3) if matrix_display_to_reference is None else np.asarray(matrix_display_to_reference, float)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-9:
        return pixels
    key = (str(map_id), str(floor), digest, prefs, bool(route), arr.shape, matrix.round(9).tobytes(),
           hashlib.blake2b(np.ascontiguousarray(arr).data, digest_size=16).hexdigest())
    with _LOCK:
        cached = _DECORATED.get(key)
        if cached is not None:
            _DECORATED.move_to_end(key)
            return cached
    inverse = np.linalg.inv(matrix)
    placed = []
    for item in items:
        x, y, _ = inverse @ np.array([item["x"], item["y"], 1.0])
        placed.append(dict(item, x=float(x), y=float(y)))
    from PIL import Image
    image = Image.fromarray(np.ascontiguousarray(arr[:, :, [2, 1, 0, 3]]), "RGBA")
    draw(image, placed, route=route)
    out = np.ascontiguousarray(np.asarray(image)[:, :, [2, 1, 0, 3]])
    out.setflags(write=False)
    with _LOCK:
        if prefs == _PREFERENCES:
            _DECORATED[key] = out
            while len(_DECORATED) > 8:
                _DECORATED.popitem(last=False)
    return out


def editor_items(map_id, floor, binding) -> list[dict]:
    """Important rooms and minecart spots for the editor, in editor image pixels.

    ``binding["toImage"]`` maps native pixels to editor image pixels.  Toggles
    are applied by the editor itself so switching them only repaints.
    """
    row = bound_entry(map_id, floor, binding)
    if row is None:
        return []
    matrix = np.asarray(binding.get("toImage", np.eye(3)), float)
    out = []
    for item in important_items(row, floor):
        x, y, z = matrix @ np.array([item["x"], item["y"], 1.0])
        entry = dict(kind=item["kind"], label=item["label"], x=round(float(x / z), 2), y=round(float(y / z), 2))
        if item.get("caption"):
            entry["caption"] = item["caption"]
        out.append(entry)
    return out
