"""Production UI methods with a controlled clock; no Tk, GUI or FPS claims."""
import ast
from collections import deque
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PIL import Image, ImageDraw


SOURCE = Path(__file__).parents[1] / 'app' / 'desktop_main_ui.py'


class Clock:
    def __init__(self):
        self.now = 0
        self.jobs = {}
        self.serial = 0

    def perf_counter_ns(self):
        return self.now

    def after(self, delay, callback, *args):
        self.serial += 1
        self.jobs[self.serial] = (self.now + delay * 1_000_000, callback, args)
        return self.serial

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, milliseconds):
        self.now += milliseconds * 1_000_000
        due = [(key, value) for key, value in self.jobs.items() if value[0] <= self.now]
        for key, (_, callback, args) in due:
            if key in self.jobs:
                self.jobs.pop(key)
                callback(*args)


def subject(source=SOURCE):
    names = {'_cancel_view_animation', '_animate_view', 'animation_timing_snapshot',
             '_place_nav_mark', '_rise_page', '_poll_home_previews', '_close_home_previews',
             '_apply_icons', '_destroy'}
    methods = [node for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
               if isinstance(node, ast.FunctionDef) and node.name in names]
    clock = Clock()
    photo = Mock(side_effect=lambda image, **kwargs: object())
    tile = Mock(side_effect=lambda icons, name, size: Image.new('RGB', (size, size)))
    namespace = dict(time=clock, deque=deque, tk=SimpleNamespace(TclError=RuntimeError),
                     Image=Image, ImageDraw=ImageDraw, ImageTk=SimpleNamespace(PhotoImage=photo),
                     SURFACE='white', BUTTON_BG='gray', PRIMARY='green', _GLYPHS={},
                     _icon_tile=tile, _load_home_previews=Mock())
    wrapper = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                 str(source), 'exec'), namespace)
    ui = namespace['Subject']()
    ui.root = clock
    ui.window = Mock(winfo_exists=Mock(return_value=True), winfo_viewable=Mock(return_value=True))
    ui._closed = False
    ui._page = 'home'
    ui._nav_buttons = {'home': Mock(winfo_height=Mock(return_value=20),
                                  winfo_y=Mock(return_value=90), winfo_x=Mock(return_value=0))}
    ui._nav_mark = Mock()
    ui.rail = object()
    ui.body = Mock()
    ui._nav_mark_y = 0
    ui._nav_mark_job = ui._page_motion_job = None
    ui._px = lambda value: round(value)
    return ui, clock, namespace


class AnimationScheduling(unittest.TestCase):
    def test_late_nav_callback_finishes_in_one_delivery(self):
        ui, clock, _ = subject()
        ui._place_nav_mark()
        clock.advance(500)
        self.assertEqual(ui._nav_mark_y, 100)
        self.assertIsNone(ui._nav_mark_job)
        self.assertEqual(len(clock.jobs), 0)
        self.assertEqual(ui._nav_mark.place.call_count, 2)

    def test_late_page_callback_reaches_final_layout_without_catchup(self):
        ui, clock, _ = subject()
        ui._rise_page()
        clock.advance(500)
        ui.body.pack_configure.assert_called_with(pady=(0, 0))
        self.assertEqual(ui.body.pack_configure.call_count, 2)
        self.assertIsNone(ui._page_motion_job)

    def test_reverse_nav_ignores_cancelled_callback_and_retargets(self):
        ui, clock, _ = subject()
        ui._place_nav_mark()
        old = clock.jobs[ui._nav_mark_job]
        clock.advance(48)
        current = ui._nav_mark_y
        ui._nav_buttons['home'].winfo_y.return_value = 10
        ui._place_nav_mark()
        self.assertEqual(ui._nav_mark_y, current)
        calls = ui._nav_mark.place.call_count
        old[1](*old[2])  # Even an already-delivered cancellation race is inert.
        self.assertEqual(ui._nav_mark.place.call_count, calls)
        self.assertEqual(len(clock.jobs), 1)
        clock.advance(128)
        self.assertEqual(ui._nav_mark_y, 20)

    def test_rapid_page_retarget_keeps_one_timer_and_final_zero(self):
        ui, clock, _ = subject()
        ui._rise_page()
        clock.advance(32)
        offset = ui._page_motion_offset
        old = clock.jobs[ui._page_motion_job]
        for _ in range(30):
            ui._rise_page()
        self.assertEqual(len(clock.jobs), 1)
        self.assertEqual(ui._page_motion_offset, offset)
        old[1](*old[2])
        self.assertEqual(len(clock.jobs), 1)
        clock.advance(80)
        self.assertEqual(ui._page_motion_offset, 0)

    def test_completed_preview_waits_for_both_motions_and_latest_context(self):
        ui, clock, ns = subject()
        labels = [Mock(winfo_exists=Mock(return_value=True))]
        ui._home_preview_context = 'old'
        ui._home_preview_active = ('old', labels)
        ui._home_preview_future = Future()
        ui._home_preview_future.set_result(['stale'])
        ui._home_preview_pending = ('new', labels, [], (20, 20), 3)
        ui._home_preview_context = 'new'
        new_future = Future()
        ui._home_preview_executor = Mock(submit=Mock(return_value=new_future))
        ui._place_nav_mark()
        ui._rise_page()
        ui._poll_home_previews()
        clock.advance(80)
        ns['ImageTk'].PhotoImage.assert_not_called()
        ui._home_preview_executor.submit.assert_not_called()
        clock.advance(48)
        ui._home_preview_executor.submit.assert_called_once()
        ns['ImageTk'].PhotoImage.assert_not_called()
        new_future.set_result(['latest'])
        ui._poll_home_previews()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 1)
        self.assertEqual(ns['ImageTk'].PhotoImage.call_args.args[0], 'latest')

    def test_finished_current_preview_flushes_at_animation_end(self):
        ui, clock, ns = subject()
        ui._home_preview_context = 3
        ui._home_preview_active = (3, [Mock(winfo_exists=Mock(return_value=True))])
        ui._home_preview_future = Future()
        ui._home_preview_future.set_result(['current'])
        ui._rise_page()
        ns['ImageTk'].PhotoImage.assert_not_called()
        clock.advance(80)
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 1)

    def test_render_failure_releases_only_its_generation_and_preserves_error(self):
        ui, clock, ns = subject()
        ui._home_preview_context = 4
        ui._home_preview_active = (4, [Mock(winfo_exists=Mock(return_value=True))])
        ui._home_preview_future = Future()
        ui._home_preview_future.set_result(['current'])
        error = RuntimeError('widget vanished')
        with self.assertRaisesRegex(RuntimeError, 'widget vanished'):
            ui._animate_view('page_motion', 80, Mock(side_effect=error))
        self.assertFalse(ui._page_motion_animating)
        self.assertIsNone(ui._page_motion_job)
        ui._poll_home_previews()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 1)

        current_render = Mock()
        def redirect_then_fail(progress):
            ui._animate_view('page_motion', 80, current_render)
            raise error
        with self.assertRaisesRegex(RuntimeError, 'widget vanished'):
            ui._animate_view('page_motion', 80, redirect_then_fail)
        self.assertTrue(ui._page_motion_animating)
        self.assertEqual(len(clock.jobs), 1)
        clock.advance(80)
        current_render.assert_called_with(1.0)
        self.assertFalse(ui._page_motion_animating)

    def test_destroy_cancels_callbacks_and_never_uploads_completed_image(self):
        ui, clock, ns = subject()
        ui._home_preview_context = 1
        ui._home_preview_active = (1, [Mock()])
        ui._home_preview_future = Future()
        ui._home_preview_future.set_result(['pixels'])
        ui._support_window = ui._refresh_job = ui._update_dialog = None
        ui._brand_images = Mock()
        ui.brand_icon = Mock()
        ui._close_help_images = Mock()
        ui._scroll_pages = {}
        ui._place_nav_mark()
        ui._rise_page()
        callbacks = list(clock.jobs.values())
        ui._destroy(SimpleNamespace(widget=ui.window))
        before = ui._nav_mark.place.call_count, ui.body.pack_configure.call_count
        for _, callback, args in callbacks:
            callback(*args)
        self.assertEqual((ui._nav_mark.place.call_count, ui.body.pack_configure.call_count), before)
        self.assertFalse(clock.jobs)
        ns['ImageTk'].PhotoImage.assert_not_called()

    def test_diagnostic_ring_is_bounded_copyable_and_records_delayed_interval(self):
        ui, clock, _ = subject()
        for _ in range(150):
            ui._rise_page()
            clock.advance(100)
        rows = ui.animation_timing_snapshot()
        self.assertEqual(len(rows), 240)
        self.assertEqual(rows[-1]['intervalMs'], 100)
        self.assertTrue(rows[-1]['complete'])
        rows[-1]['complete'] = False
        self.assertTrue(ui.animation_timing_snapshot()[-1]['complete'])
        self.assertEqual(len(ui.animation_timing_snapshot(clear=True)), 240)
        self.assertEqual(ui.animation_timing_snapshot(), ())

    def test_missing_nav_geometry_retry_is_cancelled_on_new_request(self):
        ui, clock, _ = subject()
        ui._nav_buttons['home'].winfo_height.return_value = 1
        ui._place_nav_mark()
        old = clock.jobs[ui._nav_mark_job]
        ui._nav_buttons['home'].winfo_height.return_value = 20
        ui._place_nav_mark(animate=False)
        old[1](*old[2])
        self.assertEqual(ui._nav_mark_y, 100)
        self.assertFalse(clock.jobs)

    def test_icon_tiles_and_badges_reuse_photos_but_scale_and_masks_invalidate(self):
        ui, _, ns = subject()
        ui._nav_buttons = {}
        ui._icons = SimpleNamespace(_masks={'map': object()})
        ui._step_badges = [Mock(), Mock()]
        ui._icon_tiles = [SimpleNamespace(_xingtu_icon='map', configure=Mock()),
                          SimpleNamespace(_xingtu_icon='map', configure=Mock())]
        ui._apply_icons()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 2)
        for _ in range(20):
            ui._apply_icons()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 2)
        self.assertIs(ui._icon_tiles[0]._desktop_icon, ui._icon_tiles[1]._desktop_icon)
        ui._px = lambda value: round(value * 1.5)
        ui._apply_icons()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 4)
        ui._icons._masks['map'] = object()
        ui._apply_icons()
        self.assertEqual(ns['ImageTk'].PhotoImage.call_count, 5)
        self.assertEqual(len(ui._icon_tile_photos), 1)


if __name__ == '__main__':
    unittest.main()
