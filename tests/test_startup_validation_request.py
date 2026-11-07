"""Real temporary package bytes; lightweight engine shell is not GUI/game QA."""
from concurrent.futures import CancelledError
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / name) for name in ('app', 'experiments/qt_quick')]
import test_generic_author_package as fixtures
import generic_author_base as base
import s1_author_displays as displays
from client_providers import EngineFactories, ProviderUnavailable
from client_engine import EngineAdapter

class StartupValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GenericPackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.install(self.fixture.package())

    def package(self):
        return displays.InstalledAuthorPackage(self.root / '_internal', self.fixture.profile)

    def test_startup_receipt_and_bindings_resolve_once_without_decoding_all_pixels(self):
        from PIL import Image
        with patch.object(base, '_one', wraps=base._one) as one, patch.object(
                Image.Image, 'load', autospec=True, side_effect=Image.Image.load) as decode:
            self.package()
        self.assertEqual(one.call_count, 3)
        self.assertEqual(decode.call_count, 0)
        self.assertIsNone(base._BROWSE_REQUEST.get())

    def test_next_request_revalidates_dependency_bytes_even_same_stat(self):
        self.package()
        path = self.root / '_internal/references/versions.json'
        old = path.stat()
        path.write_bytes(path.read_bytes().replace(b'"v1"', b'"v2"'))
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaises(ValueError):
            self.package()

    def test_change_during_validation_is_rejected_before_publication(self):
        original = displays.InstalledAuthorPackage._read
        path = self.root / '_internal/references/versions.json'
        def read(package, relative):
            data = original(package, relative)
            if relative.endswith('.png'):
                old = path.stat()
                path.write_bytes(path.read_bytes().replace(b'"v1"', b'"v2"'))
                os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
            return data
        with patch.object(displays.InstalledAuthorPackage, '_read', read), self.assertRaises(ValueError):
            self.package()

    def test_outer_cancel_scope_is_preserved(self):
        with self.assertRaises(CancelledError):
            with base.browse_validation_request(lambda: True):
                self.package()
        self.assertIsNone(base._BROWSE_REQUEST.get())

    def registry(self):
        factories = EngineFactories(self.root / '_internal')
        calls = []
        def build(mode, *, season='s0'):
            calls.append(mode)
            native = SimpleNamespace(root=self.root / '_internal', mode=mode,
                catalog=None, adapters=SimpleNamespace(profiles={}))
            optional = displays.S1AuthorDisplays(self.root / '_internal', native, season=season)
            self.assertFalse(optional.errors)
            return SimpleNamespace(pack=SimpleNamespace(maps=[]), identity_floors=('B1',))
        factories._build = build
        return factories, calls

    def test_applied_mode_only_then_switch_and_invalidate(self):
        registry, calls = self.registry()
        adapter = EngineAdapter(engine_factories=registry)
        adapter.dispatch('set_game_mode', dict(mode='difficult'))
        self.assertTrue(adapter.dispatch('inspect_engine_resources')['success'])
        self.assertEqual(calls, ['difficult'])
        self.assertFalse(registry.capabilities()['nightmare']['prepared'])
        adapter.dispatch('set_game_mode', dict(mode='nightmare'))
        self.assertTrue(adapter.dispatch('inspect_engine_resources')['success'])
        self.assertEqual(calls, ['difficult', 'nightmare'])
        adapter.dispatch('inspect_engine_resources')
        self.assertEqual(calls, ['difficult', 'nightmare'])
        registry.invalidate()
        adapter.dispatch('inspect_engine_resources')
        self.assertEqual(calls, ['difficult', 'nightmare', 'nightmare'])
        self.assertFalse(registry.capabilities()['difficult']['prepared'])

    def test_full_self_check_still_builds_all_and_cancel_stays_pending(self):
        registry, calls = self.registry()
        cancel = threading.Event(); cancel.set()
        report = registry.prepare_context('difficult', cancel)
        self.assertEqual(calls, [])
        self.assertFalse(report['difficult']['prepared'])
        registry.prepare()
        self.assertEqual(calls, ['nightmare', 'difficult'])
        self.assertTrue(all(r['prepared'] for r in registry.capabilities().values()))

    def test_s1_selected_registration_only_and_later_mode_switch(self):
        from test_s1_builtin_resources import fixture
        for mode in ('nightmare', 'difficult', 'difficulty2'):
            fixture(self.root / '_internal', mode)
        registry = EngineFactories(self.root / '_internal')
        calls = []
        def build(mode, *, season="s0"):
            calls.append((season, mode))
            return SimpleNamespace(pack=SimpleNamespace(maps=[]), identity_floors=('-1',))
        # Native registration is real; product decoding/engine is a fixture.
        registry._build = build
        adapter = EngineAdapter(engine_factories=registry)
        adapter.dispatch('set_context', dict(season='s1', mode='nightmare'))
        self.assertTrue(adapter.dispatch('inspect_engine_resources')['success'])
        self.assertEqual(calls, [('s1', 'nightmare')])
        self.assertFalse(registry.capabilities('s1')['difficult']['prepared'])
        adapter.dispatch('set_context', dict(season='s1', mode='difficult'))
        adapter.dispatch('inspect_engine_resources')
        self.assertEqual(calls, [('s1', 'nightmare'), ('s1', 'difficult')])
        # Damage the declared context before a later cached lookup. The
        # existing registration-before-cache contract must remain enforced.
        (self.root / '_internal/seasons/s1/difficult/native-map-manifest.json').write_text('{}')
        with self.assertRaises(ProviderUnavailable):
            registry.get_engine('difficult', 's1')

    def test_invalidated_late_build_cannot_publish(self):
        registry, calls = self.registry()
        original = registry._build
        def build(*args, **kwargs):
            engine = original(*args, **kwargs)
            registry.invalidate()
            return engine
        registry._build = build
        report = registry.prepare_context('difficult')
        self.assertFalse(report['difficult']['prepared'])
        self.assertFalse(registry._engines)

if __name__ == '__main__': unittest.main()
