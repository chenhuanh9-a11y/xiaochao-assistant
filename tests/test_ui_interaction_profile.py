"""Headless diagnostics contract tests; no Tk root, window or product constructor."""
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from ui_interaction_profile import InteractionProfile, own_process_metrics


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def add(self, milliseconds):
        self.now += milliseconds * 1_000_000


class ProfileTests(unittest.TestCase):
    def make(self, **options):
        clock = Clock()
        metrics = lambda: {'cpuMs': clock.now/1_000_000, 'workingSetBytes': None}
        profile = InteractionProfile(clock=clock, metrics=metrics, **options)
        self.addCleanup(profile.finish)
        return profile, clock

    def test_nested_and_recursive_exclusive_are_not_aggregate_subtraction(self):
        profile, clock = self.make()

        class Target:
            def outer(self):
                clock.add(2)
                self.child(2)
                clock.add(3)
                return 'result'

            def child(self, depth):
                clock.add(1)
                if depth:
                    self.child(depth-1)

        profile.wrap(Target, 'outer')
        profile.wrap(Target, 'child')
        profile.begin('nested')
        self.assertEqual(Target().outer(), 'result')
        row = profile.end('nested')
        self.assertEqual(row['wallMs'], 8)
        self.assertEqual(row['calls']['outer']['inclusiveMs'], 8)
        self.assertEqual(row['calls']['outer']['exclusiveMs'], 5)
        self.assertEqual(row['calls']['child']['count'], 3)
        self.assertEqual(row['calls']['child']['inclusiveMs'], 6)
        self.assertEqual(row['calls']['child']['exclusiveMs'], 3)
        self.assertEqual(row['processDelta']['cpuMs'], 8)
        self.assertIsNone(row['processDelta']['workingSetBytes'])

    def test_exception_identity_and_reset(self):
        profile, clock = self.make()
        error = ValueError('private argument must not be recorded')

        class Target:
            def fail(self):
                clock.add(7)
                raise error

        profile.wrap(Target, 'fail')
        profile.begin('fail')
        with self.assertRaises(ValueError) as raised:
            Target().fail()
        self.assertIs(raised.exception, error)
        row = profile.end()
        self.assertEqual(row['calls']['fail']['errors'], 1)
        self.assertNotIn(str(error), str(row))
        profile.begin('fresh')
        self.assertEqual(profile.end()['calls'], {})

    def test_key_ring_and_depth_bounds(self):
        profile, clock = self.make(max_keys=2, slow_limit=2, slow_ms=0, max_depth=3)

        class Target:
            def recurse(self, depth):
                clock.add(1)
                if depth:
                    self.recurse(depth-1)

        profile.wrap(Target, 'recurse')
        profile.begin('bounds')
        Target().recurse(8)
        for index in range(10):
            profile._record('fixed-key-' + str(index), 10, 10, False)
        row = profile.end()
        self.assertLessEqual(len(row['calls']), 3)  # max_keys + overflow row
        self.assertEqual(len(row['slowCalls']), 2)
        self.assertEqual(row['depthSkippedCalls'], 6)
        self.assertGreater(row['overflowCalls'], 0)

    def test_restore_static_class_inherited_and_alias(self):
        profile, _ = self.make()

        class Base:
            def method(self):
                return 'base'

        class Target(Base):
            @staticmethod
            def static(value):
                return value

            @classmethod
            def kind(cls):
                return cls.__name__

            alias = Base.method

        originals = dict(vars(Target))
        for name in ('method', 'static', 'kind', 'alias'):
            profile.wrap(Target, name)
        self.assertFalse(profile.wrap(Target, 'method'))
        profile.begin('descriptors')
        self.assertEqual(Target.static(3), 3)
        self.assertEqual(Target.kind(), 'Target')
        self.assertEqual(Target().method(), 'base')
        self.assertEqual(Target().alias(), 'base')
        self.assertEqual(len(profile.end()['calls']), 4)
        self.assertTrue(profile.finish()['restored'])
        self.assertNotIn('method', vars(Target))
        for name in ('static', 'kind', 'alias'):
            self.assertIs(vars(Target)[name], originals[name])
        self.assertTrue(profile.finish()['restored'])

    def test_preserve_later_external_patch_and_reject_double_instrumentation(self):
        profile, _ = self.make()

        class Target:
            def method(self):
                return 1

        profile.wrap(Target, 'method')
        other, _ = self.make()
        with self.assertRaises(RuntimeError):
            other.wrap(Target, 'method')
        external = lambda self: 2
        Target.method = external
        self.assertFalse(profile.finish()['restored'])
        self.assertIs(Target.method, external)

    def test_worker_calls_pass_through_unrecorded_and_idle_caller_has_no_path(self):
        profile, clock = self.make()

        class Target:
            def idle(self):
                return 42

        profile.wrap(Target, 'idle', 'tk.update_idletasks', caller=True)
        profile.begin('thread')
        results = []
        worker = threading.Thread(target=lambda: results.append(Target().idle()))
        worker.start()
        worker.join()
        self.assertEqual(results, [42])
        self.assertEqual(Target().idle(), 42)
        row = profile.end()
        self.assertEqual(len(row['calls']), 1)
        key = next(iter(row['calls']))
        self.assertIn('@test_ui_interaction_profile.py:', key)
        self.assertNotIn(str(Path(__file__).parent), key)
        self.assertEqual(row['calls'][key]['count'], 1)

    def test_install_and_restore_without_creating_ui(self):
        from PIL import ImageTk  # Load dependencies before mocking product imports.

        class Daily:
            def expand_control(self):
                return 'ok'

        daily = SimpleNamespace(DailyAssistant=Daily)
        with patch('ui_interaction_profile.importlib.import_module', return_value=SimpleNamespace()):
            profile = InteractionProfile.install(daily)
        self.addCleanup(profile.finish)
        profile.begin('action')
        self.assertEqual(Daily().expand_control(), 'ok')
        self.assertEqual(profile.end()['calls']['daily.expand_control']['count'], 1)
        self.assertTrue(profile.finish()['restored'])

    def test_stage_validation_and_inactive_pass_through(self):
        profile, _ = self.make()
        with self.assertRaises(RuntimeError):
            profile.end()
        profile.begin('one')
        with self.assertRaises(RuntimeError):
            profile.begin('two')
        with self.assertRaises(ValueError):
            profile.end('two')
        self.assertEqual(profile.end('one')['stage'], 'one')
        profile.finish()
        with self.assertRaises(RuntimeError):
            profile.begin('closed')

    def test_own_process_metrics_types(self):
        before = own_process_metrics()
        after = own_process_metrics()
        self.assertIsInstance(before['cpuMs'], (int, float))
        self.assertGreaterEqual(after['cpuMs'], before['cpuMs'])
        self.assertGreaterEqual(before['pythonThreads'], 1)
        for key in ('workingSetBytes', 'privateBytes', 'handles', 'gdiObjects',
                    'userObjects', 'processThreads'):
            self.assertTrue(before[key] is None or isinstance(before[key], int), key)
            if before[key] is not None:
                self.assertGreaterEqual(before[key], 0, key)
        if sys.platform == 'win32':
            self.assertGreater(before['workingSetBytes'], 0)
            self.assertGreater(before['privateBytes'], 0)


if __name__ == '__main__':
    unittest.main()
