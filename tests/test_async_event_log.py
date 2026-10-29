import ast
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from async_event_log import AsyncEventLog, snapshot
from runtime_timeline import context as runtime_log_context


class WriterTests(unittest.TestCase):
    def test_blocked_sink_bounded_snapshot_priority_and_drain(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release = threading.Event(), threading.Event()
            class SlowWriter(AsyncEventLog):
                def _write(self, row):
                    entered.set()
                    release.wait(3)
                    super()._write(row)
            writer = SlowWriter(Path(folder) / 'events.jsonl', capacity=4)
            writer.emit('first', {})
            self.assertTrue(entered.wait(1))
            data = {'nested': [1]}
            started = time.perf_counter()
            for _ in range(1000):
                writer.emit('map-probe-observed', data)
            writer.emit('capture-error', data)
            self.assertLess(time.perf_counter() - started, .5)
            data['nested'][0] = 999
            self.assertEqual(writer.status()['pending'], 4)
            self.assertFalse(writer.flush(.01))
            release.set()
            self.assertTrue(writer.flush(2))
            writer.close()
            writer.thread.join(1)
            rows = [json.loads(s) for s in writer.path.read_text('utf-8').splitlines()]
            self.assertIn('capture-error', [r['event'] for r in rows])
            self.assertEqual(rows[-1]['nested'], [1])
            self.assertGreater(rows[-1]['diagnosticDroppedTotal'], 0)

    def test_snapshot_does_not_retain_frames_or_call_arbitrary_str(self):
        class Frame:
            def __str__(self):
                raise AssertionError('producer must not stringify objects')
        cyclic = []
        cyclic.append(cyclic)
        value = snapshot({'frame': Frame(), 'cycle': cyclic, 'large': list(range(100000))})
        self.assertEqual(value['frame'], '<Frame>')
        self.assertLess(len(json.dumps(value)), 10000)

    def test_failed_sink_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            class OnceFailing(AsyncEventLog):
                def _write(self, row):
                    if row['event'] == 'bad':
                        raise OSError('injected IO error')
                    super()._write(row)
            writer = OnceFailing(Path(folder) / 'events.jsonl')
            writer.emit('bad', {})
            writer.emit('good', {})
            self.assertTrue(writer.flush(2))
            self.assertEqual(writer.status()['failed'], 1)
            writer.close()

    def test_nonstring_keys_and_snapshot_failure_are_bounded(self):
        value = snapshot(dict.fromkeys(range(100000)))
        self.assertTrue(value['_truncated'])
        with tempfile.TemporaryDirectory() as folder:
            writer = AsyncEventLog(Path(folder) / 'events.jsonl')
            with patch('async_event_log.snapshot', side_effect=RuntimeError('changed size')):
                writer.emit('capture-error', {})
            self.assertTrue(writer.flush(2))
            self.assertEqual(writer.status()['snapshotFailed'], 1)
            self.assertIn('capture-error', writer.path.read_text('utf-8'))
            writer.close()

    def test_writer_start_failure_does_not_break_application(self):
        with patch('async_event_log.threading.Thread.start', side_effect=RuntimeError('injected')):
            writer = AsyncEventLog(Path('unused'))
        writer.emit('startup', {})
        self.assertEqual(writer.status()['startError'], 'RuntimeError')
        self.assertEqual(writer.status()['dropped'], 1)

    def test_production_log_ignores_legacy_lock_and_poll_reschedules(self):
        tree = ast.parse((Path(__file__).parents[1] / 'app/identityv_daily_assistant.py').read_text('utf-8-sig'))
        methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ('_log', '_poll_events')]
        namespace = dict(time=time, runtime_log_context=runtime_log_context)
        exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), '<production>', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as folder:
            writer = AsyncEventLog(Path(folder) / 'events.jsonl')
            subject = SimpleNamespace(_event_log=writer, log_lock=threading.Lock(), closed=False, EVENT_POLL_MS=16)
            subject.log_lock.acquire()
            subject._log = lambda *a, **kw: namespace['_log'](subject, *a, **kw)
            subject._log('test', value=1)
            scheduled = []
            subject.root = SimpleNamespace(after=lambda *a: scheduled.append(a))
            subject._poll_events = lambda: None
            subject._poll_events_once = lambda: (_ for _ in ()).throw(RuntimeError('thread start failed'))
            namespace['_poll_events'](subject)
            self.assertEqual(len(scheduled), 1)
            subject.closed = True
            namespace['_poll_events'](subject)
            self.assertEqual(len(scheduled), 1)
            self.assertTrue(writer.flush(2))
            writer.close()


if __name__ == '__main__':
    unittest.main()
