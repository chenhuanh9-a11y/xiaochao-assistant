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
                        _mode_map_interface=lambda f: {}, _mode_selected_floor=lambda f: {},
                        _log=lambda *a, **k: None, _queue_latest_map_probe=lambda e: None)
    return s, lambda **kw: ns['_begin_map_probe'](s, 1, **kw)


class ProbeWorkerTests(unittest.TestCase):
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
