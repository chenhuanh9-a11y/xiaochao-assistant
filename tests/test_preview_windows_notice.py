"""Local synthetic files only; all publisher entry calls are intercepted."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import preview_windows_notice as preview


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'version.json').write_text(json.dumps(dict(displayVersion='1.0.1', numericVersion='1.0.0.5')), encoding='utf-8')
        self.installer = self.root/'星图-1.0.1-Setup.exe'
        self.installer.write_bytes(b'local synthetic fixture; never sent to production')
        self.record = dict(schema='xingtu-windows-build-record-v1', sourceCommit='a'*40,
                           displayVersion='1.0.1', numericVersion='1.0.0.5',
                           installer=dict(fileName=self.installer.name,size=self.installer.stat().st_size,
                                          sha256=hashlib.sha256(self.installer.read_bytes()).hexdigest()))
        self.record_path = self.root/'build-record.json'
        self.record_path.write_text(json.dumps(self.record),encoding='utf-8')
        guard = mock.patch.object(preview, 'ROOT', self.root)
        guard.start()
        self.addCleanup(guard.stop)

    def test_valid_record_allows_only_default_preview(self):
        notes=self.root/'notes.txt'
        with mock.patch.object(preview.publisher,'main',return_value=0) as call, mock.patch('sys.stdout',io.StringIO()):
            code=preview.main(['--installer',str(self.installer),'--build-record',str(self.record_path),'--notes',str(notes)])
        self.assertEqual(code,0)
        call.assert_called_once_with(['--installer',str(self.installer),'--notes',str(notes)])

    def test_old_record_cannot_validate_a_renamed_installer(self):
        self.record['displayVersion']='1.0'
        self.record['numericVersion']='1.0.0.4'
        self.record_path.write_text(json.dumps(self.record),encoding='utf-8')
        with mock.patch.object(preview.publisher,'main') as call, mock.patch('sys.stderr',io.StringIO()):
            self.assertEqual(preview.main(['--installer',str(self.installer),'--build-record',str(self.record_path)]),2)
        call.assert_not_called()

    def test_changed_installer_or_missing_record_stops_before_online_read(self):
        self.installer.write_bytes(b'changed bytes')
        for record in (self.record_path,self.root/'missing.json'):
            with self.subTest(record=record.name), mock.patch.object(preview.publisher,'main') as call, mock.patch('sys.stderr',io.StringIO()):
                self.assertEqual(preview.main(['--installer',str(self.installer),'--build-record',str(record)]),2)
                call.assert_not_called()

    def test_publish_flags_are_not_supported(self):
        with mock.patch.object(preview.publisher,'main') as call, mock.patch('sys.stderr',io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                preview.main(['--installer',str(self.installer),'--build-record',str(self.record_path),'--publish','--yes'])
        self.assertEqual(error.exception.code,2)
        call.assert_not_called()


if __name__=='__main__':
    unittest.main()
