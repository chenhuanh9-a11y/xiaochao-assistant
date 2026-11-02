"""Identity and real async writer/export integration; all data is synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from diagnostic_identity import DiagnosticStream, Installation, installation
from async_event_log import AsyncEventLog
from diagnostic_export import export_safe_diagnostic_bundle


class IdentityTests(unittest.TestCase):
    def test_persistent_random_install_reset_and_independent_streams(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            one, two = DiagnosticStream('one', root), DiagnosticStream('two', root)
            a, b, c = one.stamp(), two.stamp(), one.stamp()
            self.assertEqual((a['seq'], b['seq'], c['seq']), (1, 1, 2))
            self.assertEqual(a['sessionId'], b['sessionId'])
            self.assertEqual(a['pid'], os.getpid())
            self.assertEqual(Installation(root).install_id, a['installId'])
            self.assertEqual(set(json.loads((root/'diagnostic-identity.json').read_text())), {'installId'})
            changed = installation(root).reset()
            self.assertNotEqual(a['installId'], changed)
            self.assertEqual(one.stamp()['installId'], changed)
            # A fresh interpreter uses the persisted install ID and a new session.
            script = "from diagnostic_identity import DiagnosticStream; import json,sys; print(json.dumps(DiagnosticStream('one',sys.argv[1]).stamp()))"
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).parents[1] / 'app'))
            child = json.loads(subprocess.check_output([sys.executable, '-c', script, folder], env=env))
            self.assertEqual(child['installId'], changed)
            self.assertNotEqual(child['sessionId'], a['sessionId'])
            self.assertEqual(child['seq'], 1)

    def test_loss_ranges_bounded_and_include_rejected_sequence(self):
        stream = DiagnosticStream('loss-test')
        for i in range(50):
            stream.drop(stream.stamp(), 'capacity' if i % 2 else 'coalesced')
        loss = stream.loss()
        self.assertEqual(loss['droppedTotal'], 50)
        self.assertEqual(len(loss['ranges']), 16)
        self.assertEqual(loss['omittedRangeCount'], 34)
        self.assertTrue(all(row['firstAt'] and row['lastAt'] for row in loss['ranges']))

    def test_real_writer_and_legacy_export_never_invent_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); live = root/'live'; live.mkdir()
            writer = AsyncEventLog(live/'events-real.jsonl', settings_dir=root/'settings')
            writer.emit('recognition', dict(roundId=7, cycleId=3, attemptId='existing-attempt', path='/tmp/private/player/file'))
            self.assertTrue(writer.flush()); writer.close(); writer.thread.join(1)
            (live/'events-legacy.jsonl').write_text('{"time":"2026-11-02T00:00:00Z","event":"assistant-started","processId":999}\n')
            result = export_safe_diagnostic_bundle(root, live, app_version='fixture', build_fingerprint='FIXTURE',
                package_metadata={'status':'fixture'}, export_id='actual-export')
            with zipfile.ZipFile(result['path']) as archive:
                manifest = json.loads(archive.read('diagnostic-manifest.json'))
                self.assertEqual(manifest['exportId'], 'actual-export')
                self.assertEqual(len(manifest['sessions']), 1)
                group = manifest['sessions'][0]
                self.assertEqual(group['recordCount'], 1)
                self.assertEqual(group['firstAt'], group['lastAt'])
                legacy = next(row for row in manifest['logs'] if row['name'] == 'events-legacy.jsonl')
                self.assertIsNone(legacy['sessionId'])
                self.assertEqual(legacy['sessionIdentityStatus'], 'legacy-not-recorded')
                row = json.loads(archive.read('logs/events-real.jsonl'))
                self.assertEqual((row['roundId'],row['cycleId'],row['attemptId']), (7,3,'existing-attempt'))
                self.assertEqual(row['sessionId'],group['sessionId'])
                self.assertNotIn(b'/tmp/private/player', archive.read('logs/events-real.jsonl'))


if __name__ == '__main__': unittest.main()
