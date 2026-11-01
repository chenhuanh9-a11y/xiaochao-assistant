"""Canonical Qt-free entry/reporter; owned Temp files, no native dialog or game."""
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import qt_startup_diagnostics as diagnostics
import packaged_product_identity as identity

SPEC = importlib.util.spec_from_file_location('qt_entry_fixture',
    Path(__file__).parents[1] / 'tools/qt_client_entry.py')
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


class EntryStartupTests(unittest.TestCase):
    def test_import_failure_records_and_notifies_without_qt(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / 'work'
            with patch.dict(sys.modules, {'client': None}), \
                    patch.object(identity, 'baked_identity', return_value={}), \
                    patch.object(diagnostics, '_candidate_directories', return_value=[work]), \
                    patch.object(diagnostics, '_show_native_error') as notify, \
                    patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'stderr', io.StringIO()):
                self.assertEqual(entry.run(), 1)
            self.assertIn('ModuleNotFoundError', (work/diagnostics.LOG_NAME).read_text('utf-8'))
            notify.assert_called_once()

    def test_write_and_stderr_failure_still_notifies(self):
        with patch.dict(sys.modules, {'client': None}), \
                patch.object(identity, 'baked_identity', return_value={}), \
                patch.object(diagnostics, '_persist', side_effect=OSError('fixture denied')), \
                patch.object(sys, 'stderr', SimpleNamespace(write=Mock(side_effect=OSError('closed')))), \
                patch.object(sys, 'frozen', True, create=True), \
                patch.object(diagnostics, '_show_native_error') as notify:
            self.assertEqual(entry.run(), 1)
        notify.assert_called_once()

    def test_client_handled_failure_does_not_notify_twice(self):
        with patch.dict(sys.modules, {'client': SimpleNamespace(main=lambda: 1)}), \
                patch.object(identity, 'baked_identity', return_value={}), \
                patch.object(entry, 'report_startup_failure') as report:
            self.assertEqual(entry.run(), 1)
        report.assert_not_called()


if __name__ == '__main__':
    unittest.main()
