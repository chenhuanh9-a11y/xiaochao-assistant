"""Standalone custom map editor for drawing the built-in default routes.

Runs on macOS or Windows without the game, recognition or overlay: only the
custom map page from the app, with the installed custom map package.

  python tools/draw_custom_routes.py [--maps assets/custom-maps] [--galleries work/custom-map-editor]

Galleries are stored under --galleries. "写入默认图库" writes the selected
gallery to app/custom_default_gallery.json, which the app ships read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import custom_maps  # noqa: E402
from custom_maps import CustomMapLibrary, GalleryStore  # noqa: E402
from custom_map_editor import CustomMapEditor  # noqa: E402
from desktop_main_ui import DesktopMainUI  # noqa: E402
from desktop_rounded import RoundedButton, set_rounded_scale  # noqa: E402
from desktop_theme import BG, BUTTON_BG, BUTTON_TEXT, FONT  # noqa: E402

DEFAULT_FILE = ROOT / "app" / "custom_default_gallery.json"


class EditorHost:
    """The few DesktopMainUI helpers the custom map page uses."""

    _label = DesktopMainUI._label
    _card = DesktopMainUI._card
    _ghost = DesktopMainUI._ghost
    _segmented = DesktopMainUI._segmented

    def __init__(self, window, assistant):
        self.window = window
        self.root = window
        self.assistant = assistant
        self._scale = 1.0
        self._preference_traces = []

    def _px(self, value):
        return round(value * self._scale)

    def _refresh_route_choices(self):
        pass


class EditorAssistant:
    """Gallery and package access without the game runtime."""

    def __init__(self, maps_root: Path, galleries_root: Path):
        self.library = CustomMapLibrary(maps_root)
        self.galleries = GalleryStore(galleries_root / "custom-galleries.json", DEFAULT_FILE)

    def custom_map_library(self):
        return self.library

    def custom_galleries(self):
        return self.galleries

    def custom_map_snapshot(self):
        return dict(available=self.library.available, message=self.library.error,
                    maps=tuple(self.library.maps()) if self.library.available else (),
                    inUse=None, currentMapId=None, currentFloor="1")

    def use_custom_gallery(self, gallery_id):
        return dict(success=False, message="这是独立编辑器，不连接游戏；在星图里选择图库后才会在游戏内使用。")


def write_default(store: GalleryStore, gallery_id: str) -> int:
    payload = store.export(gallery_id)
    payload.update(id=custom_maps.DEFAULT_GALLERY_ID, name="默认路线")
    DEFAULT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    store.default = dict(id=custom_maps.DEFAULT_GALLERY_ID, name="默认路线", maps=payload["maps"])
    store.revision += 1
    return len(payload["maps"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--maps", type=Path, default=ROOT / "assets" / "custom-maps")
    parser.add_argument("--galleries", type=Path, default=ROOT / "work" / "custom-map-editor")
    args = parser.parse_args(argv)
    window = tk.Tk()
    window.title("星图 · 自定义地图编辑器")
    window.configure(bg=BG)
    window.geometry("1180x800")
    window.minsize(900, 620)
    set_rounded_scale(window, 1.0)
    assistant = EditorAssistant(args.maps, args.galleries)
    host = EditorHost(window, assistant)
    editor = CustomMapEditor(window, host)
    editor.pack(fill="both", expand=True)

    def publish_default():
        store = assistant.galleries
        if store.readonly(editor.gallery_id):
            editor._note("请选择一个自己的图库再写入；默认图库本身就是当前内容。", error=True)
            return
        count = write_default(store, editor.gallery_id)
        editor._signature = None
        editor.refresh()
        editor._note(f"已写入默认图库（{count} 张图有路线）：{DEFAULT_FILE.relative_to(ROOT)}。提交这个文件后，新安装包就会内置。")

    button = RoundedButton(editor.use_button.master, text="写入默认图库", command=publish_default, bg=BUTTON_BG,
                           fg=BUTTON_TEXT, highlightthickness=0, font=(FONT, 9, "bold"), padx=14, pady=6,
                           cursor="hand2")
    editor.use_button.pack_forget()
    button.pack(side="right")
    if not assistant.library.available:
        editor.notice.configure(text=f"{assistant.library.error}。请先运行 tools/maps/install_custom_maps.py 安装资源包。")
    editor.refresh()
    window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
