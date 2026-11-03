import ast
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from datetime import datetime, timezone
import unittest
from unittest.mock import patch


def subject():
    import os
    path = Path(os.environ.get('PROBE_TEST_SOURCE', str(Path(__file__).parents[1] / 'app/identityv_daily_assistant.py')))
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_begin_map_probe')
    ns = dict(threading=threading, time=time, datetime=datetime, timezone=timezone,
              probable_map_interface=lambda e: True, verified_map_hotkey_frame=lambda e, f: True)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(path), 'exec'), ns)
    s = SimpleNamespace(capture_lock=threading.Lock(), map_probe_worker_lock=threading.Lock(),
                        capture_generation=1, _runtime_trace_context=lambda: {},
                        _capture_source_frame=lambda h: (bytearray(1024*1024), None),
                        _mode_map_interface=lambda f, **kw: {}, _mode_selected_floor=lambda f, **kw: {},
                        _log=lambda *a, **k: None, _queue_latest_map_probe=lambda e: None)
    return s, lambda **kw: ns['_begin_map_probe'](s, 1, **kw)


class ProbeWorkerTests(unittest.TestCase):
    def test_capture_mode_switch_keeps_dispatched_decoder_and_mode_together(self):
        s, begin = subject()
        entered, release, published = threading.Event(), threading.Event(), threading.Event()
        s.game_mode = 'difficult'
        observer = s.difficult_map_ui = object()
        calls, events = [], []
        def capture(_hwnd):
            entered.set()
            release.wait(3)
            return b'frame', (0, 0, 1, 1)
        def read(frame, **context):
            calls.append(context)
            return dict(selectedFloor=2)
        def publish(event):
            events.append(event)
            published.set()
        s._capture_source_frame = capture
        s._mode_selected_floor = read
        s._mode_map_interface = read
        s._queue_latest_map_probe = publish
        try:
            self.assertTrue(begin())
            self.assertTrue(entered.wait(2))
            s.game_mode, s.difficult_map_ui = 'nightmare', object()
        finally:
            release.set()
        self.assertTrue(published.wait(2))
        self.assertEqual(calls, [dict(mode='difficult', observer=observer)] * 2)
        self.assertEqual(events[0]['floorReadMode'], 'difficult')
        self.assertEqual(events[0]['floorRead'], dict(selectedFloor=2))

    def test_mismatched_probe_mode_is_rejected_before_any_state_consumer(self):
        path = Path(__file__).parents[1] / 'app/identityv_daily_assistant.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_handle_map_probe_event')
        ns = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(path), 'exec'), ns)
        # No controller attributes or policy substitutes: reaching a consumer
        # would fail, so this proves the mismatched event is dropped at entry.
        ns['_handle_map_probe_event'](SimpleNamespace(game_mode='nightmare'),
            dict(floorReadMode='difficult', floorRead=dict(selectedFloor=2), open=True))

    def test_blocked_log_and_publish_keep_single_physical_worker(self):
        for endpoint in ('_log', '_queue_latest_map_probe'):
            with self.subTest(endpoint=endpoint):
                s, begin = subject()
                entered, release = threading.Event(), threading.Event()
                def block(*a, **k):
                    entered.set()
                    release.wait(3)
                setattr(s, endpoint, block)
                try:
                    self.assertTrue(begin())
                    self.assertTrue(entered.wait(2))
                    self.assertFalse(s.capture_lock.locked())
                    started = sum(begin() for _ in range(20))
                    self.assertEqual(started, 0)
                finally:
                    release.set()
                deadline = time.monotonic() + 2
                while s.map_probe_worker_lock.locked() and time.monotonic() < deadline:
                    time.sleep(.001)
                self.assertFalse(s.map_probe_worker_lock.locked())

    def test_start_failure_and_stale_generation_release_both_lanes(self):
        s, begin = subject()
        self.assertFalse(begin(expected_probe_generation=42))
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('injected start failure')):
            self.assertFalse(begin())
        self.assertFalse(s.capture_lock.locked())
        self.assertFalse(s.map_probe_worker_lock.locked())
        self.assertTrue(begin())


if __name__ == '__main__': unittest.main()
