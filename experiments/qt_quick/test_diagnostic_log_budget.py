"""Real service/writer with a synthetic 60-second producer clock, not game QA."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
import client_problems as problems
from diagnostic_identity import DiagnosticStream

class DiagnosticBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def wait(self, service):
        end=time.monotonic()+10
        while service.pendingWrites and time.monotonic()<end:
            QTest.qWait(5)
            time.sleep(.001)
        self.assertFalse(service.pendingWrites)

    def test_sixty_second_recognition_stream_cannot_rotate_problem_records(self):
        with tempfile.TemporaryDirectory() as folder:
            service=problems.ProblemsService(SimpleNamespace(work_root=Path(folder)))
            try:
                service.record(dict(kind='error',title='retained-problem',raw='important-original'))
                self.wait(service)
                # 3 production observation lanes, 4 Hz for 60 producer seconds.
                for tick in range(240):
                    with patch.object(problems.time,'monotonic',return_value=100+tick*.25):
                        for lane in ('interface','floor','pose'):
                            service.recordTrace(dict(role='runtime',stage='recognition',frame=tick,
                                recognitionDiagnostics=dict(stage=lane,actual=tick,values=list(range(80)))))
                    self.wait(service)
                service.shutdown();self.wait(service)
                rows=[json.loads(line) for line in service.log_path.read_text().splitlines()]
                self.assertTrue(any(row.get('raw')=='important-original' for row in rows))
                self.assertFalse(any('recognitionDiagnostics' in row for row in rows))
                self.assertLess(service.log_path.stat().st_size,10_000)
                diagnostic_paths=list(service.log_path.parent.glob('events-qt-recognition-*.jsonl'))
                self.assertTrue(diagnostic_paths)
                self.assertLessEqual(sum(p.stat().st_size for p in diagnostic_paths),2*problems.LOG_MAX_BYTES)
            finally:
                service.shutdown();self.wait(service)

if __name__=='__main__': unittest.main()
