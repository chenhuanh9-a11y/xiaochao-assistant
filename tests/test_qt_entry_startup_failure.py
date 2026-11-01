"""Qt-free windowed startup boundary with owned Temp files and mocked notification."""
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('qt_entry_fixture',
    Path(__file__).parents[1] / 'tools/qt_client_entry.py')
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


class EntryStartupTests(unittest.TestCase):
    def test_native_notification_is_mocked_without_loading_qt(self):
        user32=Mock()
        with patch.object(entry.os,'name','nt'), \
             patch('ctypes.windll',SimpleNamespace(user32=user32),create=True):
            entry.show_startup_failure('fixture startup failure')
        user32.MessageBoxW.assert_called_once_with(None,'fixture startup failure','星图启动提示',0x10)

    def test_import_failure_records_and_notifies_without_qt(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(sys.modules, {'client':None}), \
                 patch.object(sys,'executable',str(Path(folder)/'fixture.exe')), \
                 patch.object(sys,'stderr',io.StringIO()), \
                 patch.object(entry,'show_startup_failure',create=True) as notify:
                self.assertEqual(entry.run(),1)
            record=Path(folder)/'work/qt-client-startup-error.txt'
            self.assertIn('ModuleNotFoundError',record.read_text('utf-8'))
            notify.assert_called_once()

    def test_write_and_stderr_failure_still_notifies(self):
        with patch.dict(sys.modules, {'client':None}), \
             patch.object(Path,'mkdir',side_effect=OSError('fixture denied')), \
             patch.object(sys,'stderr',SimpleNamespace(write=Mock(side_effect=OSError('closed')))), \
             patch.object(entry,'show_startup_failure',create=True) as notify:
            self.assertEqual(entry.run(),1)
        notify.assert_called_once()

    def test_client_handled_failure_does_not_notify_twice(self):
        with patch.dict(sys.modules, {'client':SimpleNamespace(main=lambda:1)}), \
             patch.object(entry,'show_startup_failure',create=True) as notify:
            self.assertEqual(entry.run(),1)
        notify.assert_not_called()


if __name__=='__main__':
    unittest.main()

