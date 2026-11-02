"""Local synthetic files only; all publisher entry calls are intercepted."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import copy
import subprocess
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import preview_windows_notice as preview


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'version.json').write_text(json.dumps(dict(schema='identityv-app-version-v1', version='1.0.1', displayVersion='1.0.1', numericVersion='1.0.0.5')), encoding='utf-8')
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

    def qt_record(self):
        return dict(schema='xingtu-qt-setup-build-v1',
                    source=dict(commit='a'*40, tree='b'*40, clean=True),
                    version=json.loads((self.root/'version.json').read_text()),
                    installerInstance=dict(id='production', appId='D04394A0-DACE-4FF9-874D-3DCB43EE53D1',
                                           product='星图', installFolder='XiaochaoAssistant', versionSuffix='', runtimeDataProfile='production',
                                           defaultDirName=r'{localappdata}\Programs\XiaochaoAssistant',
                                           usePreviousAppDir=True, installerPrivilege='lowest'),
                    product='星图', action='build-setup-only', buildCompleted=True,
                    output=str(self.root),
                    installer=dict(fileName=self.installer.name, bytes=self.installer.stat().st_size,
                                   sha256=hashlib.sha256(self.installer.read_bytes()).hexdigest()))

    def qt_setup(self):
        setup = self.root/'setup'
        setup.mkdir()
        self.installer = self.installer.rename(setup/self.installer.name)
        return self.qt_record()

    def run_qt(self, record, expected='a'*40):
        self.record_path.write_text(json.dumps(record), encoding='utf-8')
        args = ['--installer', str(self.installer), '--build-record', str(self.record_path)]
        if expected is not None:
            args += ['--source-commit', expected]
        with mock.patch.object(preview.publisher, 'main', return_value=0) as call, \
                mock.patch.object(preview, '_source_metadata', return_value=('b'*40, json.loads((self.root/'version.json').read_text()))), \
                mock.patch('sys.stdout', io.StringIO()), mock.patch('sys.stderr', io.StringIO()):
            code = preview.main(args)
        self.assertEqual(call.call_count, 1 if code == 0 else 0)
        return code

    def test_qt_completed_production_record_allows_preview(self):
        record = self.qt_setup()
        self.assertEqual(self.run_qt(record), 0)
        self.assertEqual(json.loads(self.record_path.read_text()), record)

    def test_qt_source_is_frozen_commit_not_tool_head(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git('init')
        git('add', 'version.json')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '-m', 'synthetic source')
        source = git('rev-parse', 'HEAD')
        tree = git('rev-parse', 'HEAD^{tree}')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            'commit', '--allow-empty', '-m', 'later release tool revision')
        self.assertNotEqual(source, git('rev-parse', 'HEAD'))
        record = self.qt_setup()
        record['source'].update(commit=source, tree=tree)
        self.record_path.write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(preview.validate_build_record(self.installer, self.record_path, source), record)
        record['source']['tree'] = 'c'*40
        self.record_path.write_text(json.dumps(record), encoding='utf-8')
        with self.assertRaises(preview.publisher.PublishError):
            preview.validate_build_record(self.installer, self.record_path, source)
        with self.assertRaises(preview.publisher.PublishError):
            preview.validate_build_record(self.installer, self.record_path, 'c'*40)

    def test_qt_rejects_mismatches_before_publisher(self):
        record = self.qt_setup()
        for path, value in [
            (('source', 'commit'), 'c'*40), (('source', 'tree'), 'c'*40),
            (('source', 'clean'), False), (('source', 'clean'), 1),
            (('version', 'displayVersion'), '1.0'), (('version', 'numericVersion'), '1.0.0.4'),
            (('version', 'version'), '1.0'), (('version', 'schema'), 'other-schema'),
            (('buildCompleted',), False), (('buildCompleted',), 1),
            (('action',), 'plan-only'), (('installerInstance', 'id'), 'client'),
            (('installerInstance', 'appId'), 'test'), (('installerInstance', 'runtimeDataProfile'), 'isolated'),
            (('installerInstance', 'installFolder'), 'OtherInstallation'),
            (('installer', 'bytes'), True), (('installer', 'bytes'), 1),
            (('installer', 'sha256'), '0'*64), (('installer', 'fileName'), 'other.exe'),
            (('output',), str(self.root/'other')), (('product',), 'XingtuQtClientTest'),
        ]:
            with self.subTest(path=path, value=value):
                changed = copy.deepcopy(record)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                self.assertEqual(self.run_qt(changed), 2)
        for expected in (None, 'short', 'c'*40):
            with self.subTest(expected=expected):
                self.assertEqual(self.run_qt(record, expected), 2)


if __name__=='__main__':
    unittest.main()
