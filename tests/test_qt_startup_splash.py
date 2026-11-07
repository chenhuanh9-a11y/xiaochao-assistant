"""Lifecycle checks with owned thread and mocked Win32; no real Windows claim."""
import ctypes
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import qt_startup_splash as splash


class SplashTests(unittest.TestCase):
    def test_close_before_show_never_creates_thread(self):
        hint = splash.StartupSplash()
        hint.close()
        with patch.object(splash.os, 'name', 'nt'), patch.object(splash.threading, 'Thread') as thread:
            hint.show()
        thread.assert_not_called()

    def test_repeated_show_and_close_join_owned_thread(self):
        hint = splash.StartupSplash()
        calls = []
        def render():
            calls.append(threading.get_ident())
            hint._ready.set()
            hint._stop.wait(2)
        with patch.object(splash.os, 'name', 'nt'), patch.object(hint, '_native_window', render):
            hint.show()
            hint.show()
            hint.close()
            hint.close()
        self.assertEqual(len(calls), 1)
        self.assertFalse(hint._thread.is_alive())

    def test_native_failure_is_optional(self):
        hint = splash.StartupSplash()
        with patch.object(splash.os, 'name', 'nt'), patch.object(hint, '_native_window', side_effect=OSError('no GUI')):
            hint.show()
            hint.close()
        self.assertTrue(hint._ready.is_set())

    def test_cli_checks_and_probes_remain_window_free(self):
        for args in (['--self-check'], ['--self-ch'], ['--help'], ['--he'], ['-h'],
                     ['--probe-output', 'probe.json'], ['--probe-output=probe.json'],
                     ['--probe-out', 'probe.json'], ['--probe-out=probe.json']):
            with self.subTest(args=args), patch.object(splash, '_splash') as hint:
                splash.show_startup_splash(args)
                hint.show.assert_not_called()
        with patch.object(splash, '_splash') as hint:
            splash.show_startup_splash([])
            hint.show.assert_called_once()
        with patch.object(splash, '_splash') as hint:
            splash.show_startup_splash(['--sandbox', 'fixture'])
            hint.show.assert_called_once()

    def test_native_window_is_nonactivating_and_destroyed_on_close(self):
        hint = splash.StartupSplash()
        user = Mock()
        gdi = Mock()
        gdi.CreateFontW.return_value = 201
        user.CreateWindowExW.side_effect = [101, 102, 103]
        user.GetSystemMetrics.side_effect = [1920, 1080]
        user.GetDpiForWindow.return_value = 96
        user.UpdateWindow.side_effect = lambda _: hint._stop.set()
        with patch.object(ctypes, 'WinDLL', side_effect=[user, gdi], create=True):
            hint._native_window()
        self.assertEqual(user.CreateWindowExW.call_args_list[0].args[0], 0x08000080)
        user.ShowWindow.assert_called_once_with(101, 4)
        user.DestroyWindow.assert_called_once_with(101)
        gdi.DeleteObject.assert_called_once_with(201)
        self.assertEqual(user.SetThreadDpiAwarenessContext.call_count, 2)

    def test_partial_native_creation_still_destroys_window(self):
        hint = splash.StartupSplash()
        user = Mock()
        gdi = Mock()
        gdi.CreateFontW.return_value = 201
        user.CreateWindowExW.side_effect = [101, 0]
        user.GetSystemMetrics.side_effect = [1920, 1080]
        user.GetDpiForWindow.return_value = 96
        with patch.object(ctypes, 'WinDLL', side_effect=[user, gdi], create=True):
            hint._native_window()
        user.ShowWindow.assert_not_called()
        user.DestroyWindow.assert_called_once_with(101)
        gdi.DeleteObject.assert_called_once_with(201)

    def test_native_dpi_scales_bounds_text_and_owned_font_together(self):
        hint = splash.StartupSplash()
        user, gdi = Mock(), Mock()
        user.CreateWindowExW.side_effect = [101, 102, 103]
        user.GetSystemMetrics.side_effect = [1920, 1080]
        user.GetDpiForWindow.return_value = 192
        gdi.CreateFontW.return_value = 201
        user.UpdateWindow.side_effect = lambda _: hint._stop.set()
        with patch.object(ctypes, 'WinDLL', side_effect=[user, gdi], create=True):
            hint._native_window()
        self.assertEqual(user.SetWindowPos.call_args.args[4:6], (880, 260))
        self.assertEqual(user.CreateWindowExW.call_args_list[1].args[4:8], (24, 60, 832, 56))
        self.assertEqual(gdi.CreateFontW.call_args.args[0], -32)
        user.DestroyWindow.assert_called_once_with(101)
        gdi.DeleteObject.assert_called_once_with(201)

    def test_runtime_hook_shows_and_registers_cleanup_without_product_import(self):
        import atexit
        import runpy
        fake = SimpleNamespace(show_startup_splash=Mock(), close_startup_splash=Mock())
        hook = Path(__file__).parents[1] / 'tools/qt_startup_hint_hook.py'
        with patch.dict(sys.modules, {'qt_startup_splash': fake}), \
                patch.object(sys, 'argv', ['fixture', '--sandbox', 'isolated']), \
                patch.object(atexit, 'register') as register:
            runpy.run_path(str(hook))
        fake.show_startup_splash.assert_called_once_with(['--sandbox', 'isolated'])
        register.assert_called_once_with(fake.close_startup_splash)
        spec = (hook.parent / 'qt-client.spec').read_text(encoding='utf8')
        self.assertIn('runtime_hooks=[str(root / "tools" / "qt_startup_hint_hook.py")]', spec)


class EntryLifecycleTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('splash_entry_fixture', Path(__file__).parents[1] / 'tools/qt_client_entry.py')
        self.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.entry)

    def test_hint_precedes_identity_and_closes_before_error(self):
        events = []
        import packaged_product_identity
        def fail(**_):
            events.append('identity')
            raise ValueError('fixture')
        with patch.object(self.entry, 'show_startup_splash', side_effect=lambda _: events.append('show')), \
             patch.object(packaged_product_identity, 'baked_identity', side_effect=fail), \
             patch.object(self.entry, 'close_startup_splash', side_effect=lambda: events.append('close')), \
             patch.object(self.entry, 'report_startup_failure', side_effect=lambda *a, **k: events.append('report')):
            self.assertEqual(self.entry.run(), 1)
        self.assertEqual(events, ['show', 'identity', 'close', 'report', 'close'])

    def test_secondary_exit_and_system_exit_always_close(self):
        import packaged_product_identity
        for main in (Mock(return_value=0), Mock(side_effect=SystemExit(2))):
            with patch.dict(sys.modules, {'client': SimpleNamespace(main=main)}), \
                 patch.object(packaged_product_identity, 'baked_identity'), \
                 patch.object(self.entry, 'show_startup_splash'), \
                 patch.object(self.entry, 'close_startup_splash') as close:
                try:
                    self.entry.run()
                except SystemExit:
                    pass
                close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
