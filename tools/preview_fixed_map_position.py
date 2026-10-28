"""Offline component preview using the product's reference-position controller.

Supply a real reference image and a separate writable state directory. This
tool starts no game monitor, capture worker, hotkey listener or input injector.
It is not the complete assistant or a test of in-game display authorization.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import queue
import sys
import threading
import time
import traceback
from types import SimpleNamespace


def load_app(resource_root: Path, state_root: Path):
    source = Path(__file__).resolve().parents[1]
    resources, state = resource_root.resolve(), state_root.resolve()
    for name in ('IDENTITYV_NIGHTMARE_MAPS', 'IDENTITYV_AUTHOR_ROUTE_ASSET_ROOT',
                 'IDENTITYV_AUTHOR_STRUCTURE_INDEX', 'IDENTITYV_AUTHOR_STRUCTURE_ROOT',
                 'IDENTITYV_DIFFICULT_MAPS', 'IDENTITYV_LEGACY_ASSETS_ROOT'):
        os.environ.pop(name, None)
    os.environ['IDENTITYV_NIGHTMARE_LIVE_ROOT'] = str(state / 'logs')
    os.environ['STAR_MAP_PACKAGE_INDEX_URL'] = ''
    sys._MEIPASS = str(resources)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source / 'app'))
    import identityv_daily_assistant as app
    app.ROOT, app.LIVE_ROOT = state, state / 'logs'
    return app


def make_controller(app, root, reference_image: Path, state_root: Path, *, monitors=None):
    image_path = reference_image.resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    class ComponentController(app.DailyAssistant):
        # The component supplies exactly one real image instead of pretending
        # that a game map/floor has been recognized by the full resource chain.
        def _display_pixels_for_source(self, path, role, **kwargs):
            if Path(path).resolve() != image_path:
                raise ValueError('Only the explicitly supplied reference is available')
            pixels = app.imread_unicode(image_path, app.cv2.IMREAD_UNCHANGED)
            if pixels is None:
                raise ValueError('Reference image could not be decoded')
            if pixels.ndim == 3 and pixels.shape[2] == 3:
                pixels = app.cv2.cvtColor(pixels, app.cv2.COLOR_BGR2BGRA)
            if pixels.ndim != 3 or pixels.shape[2] != 4 or pixels.dtype != app.np.uint8:
                raise ValueError('Reference image must decode to uint8 BGRA')
            return pixels

        def show_sheet(self):
            return False  # Full game presentation stays disabled in this tool.

    a = ComponentController.__new__(ComponentController)
    a.root, a.closed = root, False
    a.game_mode, a.game_hwnd, a.game_foreground = 'nightmare', None, False
    a.background_enabled = False
    a.gate = SimpleNamespace(selected_id=None, confirmed_id=None)
    item = SimpleNamespace(id='component-preview', name='Reference preview', layers={'1': image_path})
    a.pack = SimpleNamespace(maps=(item,), by_id={item.id: item}, default_entry_floor=1)
    a.author_route_catalog = None
    a.current_floor, a.current_bounds = '1', None
    a.preferred_route_profile_id = 'suju'
    a.settings_path = state_root.resolve() / 'ui-settings.json'
    a.settings = a._load_settings()
    saved = a.settings.get('sheetPosition')
    a.sheet_position = (tuple(int(v) for v in saved) if isinstance(saved, list) and len(saved) == 2
                        and all(isinstance(v, (int, float)) and app.np.isfinite(v) for v in saved) else None)
    a.sheet_scale, a.sheet_opacity = 1.0, .85
    a.sheet_visibility_enabled = a.reference_sheet_visibility_enabled = True
    a.sheet_move_mode, a.sheet_move_generation = False, 0
    a._reference_pointer_mailbox = a.reference_surface = a.sheet_window = None
    a.active_map_cycle_id, a.surface_visibility_generation = None, 0
    a.map_is_open = a.floor_required = False
    a.events, a.urgent_events = queue.Queue(), queue.Queue()
    a.ui_monitors = monitors if monitors is not None else app.enumerate_monitor_contexts()
    a._display_capture_lock = threading.RLock()
    a._display_capture_active, a._display_capture_request = False, None
    # Only fields consumed by the product's existing atomic settings writer.
    a.hotkey_name, a.action_hotkeys = 'MOUSE2', app.default_action_hotkeys(fresh=True)
    a.compact_position, a.interface_opacity = None, .9
    a.experimental_auto_overlay = False
    a.zhan10_coffin_variant_id = 'variant-01'
    a.onboarding_completed = a.round_end_hint_shown = False
    a.active_confirmation = None
    a._log = lambda *args, **kwargs: None
    return a


class PositionPreview:
    def __init__(self, app, controller, *, report=lambda message: None):
        self.app, self.controller, self.report = app, controller, report
        self.pixels = controller._reference_editor_pixels()
        self.surface = app.LayeredOverlay()
        self.surface.visibility_guard = lambda: (not controller.closed
            and not controller.sheet_move_mode and controller._reference_editor_focus_allowed())
        self._last_position = None

    def callbacks(self):
        return {'edit': self.edit, 'finish': self.finish, 'close': self.close}

    def edit(self):
        self.surface.hide()
        result = self.controller.move_sheet()
        self.report(result['message'])
        return result

    def finish(self):
        result = self.controller.finish_reference_sheet_move()
        self._last_position = None
        self.report(result['message'])
        return result

    def poll(self):
        a = self.controller
        if a.closed:
            return
        while not a.urgent_events.empty():
            event = a.urgent_events.get_nowait()
            if event.get('kind') == 'reference-pointer':
                a._consume_reference_pointer_events(event)
        a._check_reference_position_editor()
        if a.sheet_move_mode or not a._reference_editor_focus_allowed():
            self.surface.hide()
            return
        position = a._reference_editor_initial_position(self.pixels.shape[1::-1])
        if position != self._last_position or not self.surface.visible:
            if self.surface.show_bgra(self.pixels, position, opacity=a.sheet_opacity):
                self._last_position = position

    def close(self):
        a = self.controller
        if a.closed:
            return
        a._set_sheet_move_mode(False)
        a.closed = True
        editor = getattr(a, 'reference_editor_surface', None)
        if editor is not None:
            editor.close()
        self.surface.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-root', type=Path, required=True)
    parser.add_argument('--reference-image', type=Path, required=True)
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--label', default='真实地图参考图')
    args = parser.parse_args()
    app = load_app(args.resource_root, args.state_root)
    app.DPI_AWARENESS_MODE = app.enable_per_monitor_dpi_awareness()
    root = app.tk.Tk()
    root.title(f'星图 {app.APP_DISPLAY_VERSION} · 定点地图位置')
    root.geometry('490x230+60+80')
    root.minsize(450, 220)
    status = app.tk.StringVar(root, '真实图片预览；点击“编辑位置”后拖动图片，再点击“完成”。')
    controller = make_controller(app, root, args.reference_image, args.state_root)
    preview = PositionPreview(app, controller, report=status.set)
    callbacks = preview.callbacks()
    app.ttk.Label(root, text='定点地图位置', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w', padx=20, pady=(18, 5))
    app.ttk.Label(root, text=args.label).pack(anchor='w', padx=20)
    app.ttk.Label(root, text='离线组件预览 · 不监听游戏、不发送按键\n切换到其他程序会隐藏预览；点回此窗口再继续。').pack(anchor='w', padx=20, pady=8)
    buttons = app.ttk.Frame(root)
    buttons.pack(anchor='w', padx=20)
    app.ttk.Button(buttons, text='编辑位置', command=callbacks['edit']).pack(side='left', padx=(0, 8))
    app.ttk.Button(buttons, text='完成', command=callbacks['finish']).pack(side='left')
    app.ttk.Label(root, textvariable=status, wraplength=450).pack(anchor='w', padx=20, pady=10)

    next_monitor_poll = 0.0

    def tick():
        nonlocal next_monitor_poll
        if not controller.closed:
            try:
                if time.monotonic() >= next_monitor_poll:
                    controller.ui_monitors = app.enumerate_monitor_contexts()
                    next_monitor_poll = time.monotonic() + .5
                preview.poll()
            except Exception:
                status.set('预览发生错误，请保留日志后关闭。')
                write_error(args.state_root)
                preview.close()
                return
            root.after(25, tick)

    def close():
        preview.close()
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, tick)
    try:
        root.mainloop()
    finally:
        preview.close()


def write_error(state_root):
    path = Path(state_root) / 'logs/preview-error.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(traceback.format_exc(), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        raise SystemExit(1)
