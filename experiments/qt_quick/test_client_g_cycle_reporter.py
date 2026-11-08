"""Bounded cross-thread loss reporting uses the existing diagnostic stream."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtGui import QGuiApplication
from client_g_cycle_evidence import EvidenceReporter
from client_problems import ProblemsService
from g_cycle_evidence import GCycleEvidenceRecorder


def metadata(n):
    return dict(identity='fixture', session='fixture', round=1, cycle=n,
                opening=f'{n:032x}', attempt=f'{n+10:032x}', frame='none',
                diagnostic_id=f'{n+20:032x}', outcome='failure', reason='no-algorithm-roi')


class ReporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.problems = ProblemsService(SimpleNamespace(work_root=self.root),
                                        build_fingerprint='TEST0001', source_commit='a'*40)
        self.reporter = EvidenceReporter(self.problems)
        self.warnings = []
        self.reporter.warning.connect(lambda *args: self.warnings.append(args))

    def settle(self):
        deadline = time.monotonic()+5
        while self.reporter.pendingWrites and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertEqual(self.reporter.pendingWrites, 0)

    def tearDown(self):
        self.settle()
        self.problems.shutdown()
        self.settle()
        self.temp.cleanup()

    def rows(self):
        return [json.loads(line) for line in self.problems.log_path.read_text().splitlines()]

    def test_burst_admission_and_async_failure_keep_ids_and_warn(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return None
        recorder = GCycleEvidenceRecorder(self.root/'evidence', enabled=True,
                                           result_callback=self.reporter.publish)
        recorder.start()
        try:
            with patch('g_cycle_evidence._encode', side_effect=slow), \
                    patch.object(recorder._store, 'write', side_effect=OSError('private/path')):
                for n in (1, 2, 3):
                    meta = metadata(n)
                    result = recorder.submit(None, meta)
                    self.reporter.publish(dict(result, metadata=meta), 'admission')
                    if n == 1:
                        self.assertTrue(entered.wait(5))
                self.assertEqual(result['status'], 'queue-full')
                release.set()
                recorder.shutdown(wait=True)
            self.settle()
            rows = [r for r in self.rows() if r.get('stage') == 'g-cycle-evidence']
            admissions = [r for r in rows if r['gCycleEvidence']['evidencePhase'] == 'admission']
            done = [r for r in rows if r['gCycleEvidence']['evidencePhase'] == 'completion']
            self.assertEqual(len(admissions), 3)
            self.assertEqual(len(done), 2)
            self.assertEqual(done[0]['gCycleEvidence']['status'], 'storage-error')
            self.assertEqual(admissions[2]['mapAttempt']['attemptId'], metadata(3)['attempt'])
            self.assertEqual(done[0]['gCycleEvidence']['caseId'], admissions[0]['gCycleEvidence']['caseId'])
            self.assertEqual(len(self.warnings), 1)
            self.assertNotIn('private/path', json.dumps(rows))
        finally:
            release.set()
            recorder.shutdown(wait=True)

    def test_report_queue_is_bounded_and_exposes_dropped_count(self):
        for n in range(1, self.reporter.LIMIT+5):
            self.reporter.publish(dict(status='queued', metadata=metadata(n)), 'admission')
        self.assertEqual(self.reporter._pending, self.reporter.LIMIT)
        self.settle()
        rows = self.rows()
        self.assertEqual(sum(r.get('gCycleEvidence', {}).get('dropped', 0) for r in rows), 4)
        self.assertEqual(len(self.warnings), 1)


if __name__ == '__main__':
    unittest.main()
