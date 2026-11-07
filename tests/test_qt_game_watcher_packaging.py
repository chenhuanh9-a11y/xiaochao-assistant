"""Synthetic PE stand-in: staging/identity gates, not real Windows PE validation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_qt_client as build


class GameWatcherPackagingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stage = self.root / 'stage'; self.stage.mkdir()
        self.inputs = self.root / 'inputs.json'
        self.inputs.write_text(json.dumps(dict(sourceRoot=str(build.ROOT), product='星图')))
        self.env = {'XINGTU_QT_BUILD_INPUTS': str(self.inputs)}

    def freeze(self, command, **kwargs):
        self.assertIn('qt-game-watcher.spec', command[-1])
        directory = Path(command[command.index('--distpath') + 1]); directory.mkdir()
        (directory / 'XingtuGameWatcher.exe').write_bytes(b'synthetic executable, not PE')

    def test_onefile_is_verified_before_and_after_copy_without_launch(self):
        with patch.object(build.subprocess, 'run', side_effect=self.freeze) as run, \
                patch.object(build, 'verify_invoker_manifest', return_value=['synthetic-manifest']) as verify:
            report = build.assemble_game_watcher(self.root, self.stage, self.env)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(verify.call_count, 2)
        self.assertFalse(report['launched']); self.assertFalse(report['startupRegistrationChanged'])
        self.assertEqual(report['files']['XingtuGameWatcher.exe']['sha256'], build.sha256(self.stage / 'XingtuGameWatcher.exe'))

    def test_private_profile_is_rejected_before_freeze(self):
        self.inputs.write_text(json.dumps(dict(sourceRoot=str(build.ROOT), product='XingtuQtClientTest')))
        with patch.object(build.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, 'exact production'):
            build.assemble_game_watcher(self.root, self.stage, self.env)
        run.assert_not_called()

    def test_existing_target_is_not_replaced(self):
        target = self.stage / 'XingtuGameWatcher.exe'; target.write_bytes(b'preserve')
        with patch.object(build.subprocess, 'run', side_effect=self.freeze), \
                patch.object(build, 'verify_invoker_manifest', return_value=[]), self.assertRaisesRegex(ValueError, 'replace'):
            build.assemble_game_watcher(self.root, self.stage, self.env)
        self.assertEqual(target.read_bytes(), b'preserve')

    def test_extra_output_and_manifest_failure_stop_staging(self):
        def extra(command, **kwargs):
            self.freeze(command, **kwargs)
            (self.root / 'game-watcher-freeze' / 'unexpected.dll').write_bytes(b'bad')
        with patch.object(build.subprocess, 'run', side_effect=extra), self.assertRaisesRegex(ValueError, 'one exact'):
            build.assemble_game_watcher(self.root, self.stage, self.env)
        self.assertFalse(list(self.stage.iterdir()))


if __name__ == '__main__': unittest.main()
