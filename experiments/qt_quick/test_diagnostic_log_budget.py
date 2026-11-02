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

    def test_loss_checkpoint_not_repeated_and_suppression_not_loss(self):
        stream=DiagnosticStream('budget-checkpoint')
        for _ in range(20): stream.suppress(stream.stamp(),'unchanged')
        self.assertEqual(stream.loss()['lostTotal'],0)
        self.assertEqual(stream.loss()['suppressedTotal'],20)
        first=stream.checkpoint()
        self.assertIsNotNone(first)
        self.assertIsNone(stream.checkpoint())
        row=stream.stamp();stream.drop(row,'write-failed')
        checkpoint=stream.checkpoint()
        self.assertEqual(checkpoint['lostTotal'],1)
        self.assertEqual(checkpoint['ranges'][0]['firstSeq'],row['seq'])
        self.assertIsNone(stream.checkpoint())

    def test_qt_failed_batch_accounts_for_unwritten_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            service=problems.ProblemsService(SimpleNamespace(work_root=Path(folder)))
            rows=[service.identity.stamp() for _ in range(3)]
            service._job=SimpleNamespace(rows=rows)
            service._inflight_rows=3
            service._finished(dict(message='injected',completed=1))
            loss=service.identity.loss()
            self.assertEqual(loss['lostTotal'],2)
            self.assertEqual(loss['ranges'][0]['firstSeq'],rows[1]['seq'])
            self.assertEqual(loss['ranges'][0]['lastSeq'],rows[2]['seq'])
            self.assertEqual(service.droppedWrites,2)
            service.shutdown();self.wait(service)

    def test_real_writer_partial_failure_reports_exact_suffix(self):
        with tempfile.TemporaryDirectory() as folder:
            service=problems.ProblemsService(SimpleNamespace(work_root=Path(folder)))
            service._start_next_saved=service._start_next
            service._start_next=lambda:None
            for i in range(3):
                service.record(dict(kind='error',title='fault-'+str(i)))
            original=problems._StrictHandler.emit
            count=[0]
            def injected(handler, record):
                count[0]+=1
                if count[0]==2: raise OSError('injected-write-failure')
                return original(handler,record)
            with patch.object(problems._StrictHandler,'emit',injected):
                service._start_next=service._start_next_saved
                service._start_next();self.wait(service)
            rows=[json.loads(line) for line in service.log_path.read_text().splitlines()]
            self.assertEqual(len(rows),1)
            loss=service.identity.loss()
            self.assertEqual(loss['lostTotal'],2)
            self.assertEqual(loss['ranges'][0]['firstSeq'],rows[0]['seq']+1)
            self.assertEqual(loss['ranges'][0]['lastSeq'],rows[0]['seq']+2)
            service.record(dict(kind='error',title='recovered'))
            service.shutdown();self.wait(service)
            rows=[json.loads(line) for line in service.log_path.read_text().splitlines()]
            self.assertEqual(rows[-1]['diagnosticLoss']['lostTotal'],2)

if __name__=='__main__': unittest.main()
