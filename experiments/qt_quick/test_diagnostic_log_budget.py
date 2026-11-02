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
        with tempfile.TemporaryDirectory() as folder, patch.object(problems, 'LOG_MAX_BYTES', 4096):
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
                self.assertTrue(any(row.get('event')=='qt-runtime-problem' for row in rows))
                self.assertFalse(any('recognitionDiagnostics' in row for row in rows))
                self.assertLess(service.log_path.stat().st_size,10_000)
                diagnostic_paths=list(service.log_path.parent.glob('events-qt-recognition-*.jsonl'))
                self.assertEqual(len(diagnostic_paths), 2)  # Exercise actual rotation at a small capacity.
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
            import diagnostic_storage
            original=diagnostic_storage._append
            count=[0]
            def injected(path, record):
                count[0]+=1
                if count[0]==2: raise OSError('injected-write-failure')
                return original(path,record)
            with patch.object(diagnostic_storage,'_append',injected):
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

    def test_readonly_real_file_failure_is_counted_and_recovers(self):
        import os, stat
        with tempfile.TemporaryDirectory() as folder:
            service=problems.ProblemsService(SimpleNamespace(work_root=Path(folder)))
            try:
                service.record(dict(kind='error', title='first')); self.wait(service)
                os.chmod(service.log_path, stat.S_IREAD)
                service.record(dict(kind='error', title='second')); self.wait(service)
                self.assertEqual(service.identity.loss()['lostTotal'],1)
                self.assertTrue(service.writeError)
                os.chmod(service.log_path, stat.S_IREAD | stat.S_IWRITE)
                service.record(dict(kind='error', title='third')); self.wait(service)
                rows=[json.loads(line) for line in service.log_path.read_bytes().splitlines()]
                self.assertEqual(len(rows),2)
                self.assertEqual(rows[-1]['seq']-rows[0]['seq'],2)
            finally:
                if service.log_path.exists(): os.chmod(service.log_path, stat.S_IREAD | stat.S_IWRITE)
                service.shutdown(); self.wait(service)

    def test_qt_sink_and_export_remove_untrusted_nested_text(self):
        import zipfile
        from diagnostic_export import export_safe_diagnostic_bundle
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);service=problems.ProblemsService(SimpleNamespace(work_root=root))
            service.record(dict(kind='error',title='AliceSecret',raw='ValueError: HeroSecret',
                traceback='File "C:/Users/AliceSecret/client_live_runtime.py", line 7, in _full_frame\nValueError: HeroSecret',
                captureDiagnostics=dict(foregroundHwnd=7654321,foregroundPid=7654322,window=dict(role='other',pid=7654323,name='SecretMessenger'))))
            service.recordTrace(dict(role='runtime',stage='capture',reason='capture-error',diag=dict(username='AliceSecret',arbitrary={'actual':'HeroSecret'})))
            service.shutdown();self.wait(service)
            bundle=export_safe_diagnostic_bundle(root,service.log_path.parent,app_version='fixture',
                build_fingerprint='AAAAAAAA',summary=service.diagnosticSnapshot())
            data=service.log_path.read_bytes()
            with zipfile.ZipFile(bundle['path']) as z:
                data+=b''.join(z.read(name) for name in z.namelist() if name.endswith(('.json','.jsonl')))
            for secret in (b'AliceSecret',b'HeroSecret',b'SecretMessenger',b'7654321',b'7654322',b'7654323'):
                self.assertNotIn(secret,data)

    def test_real_sixty_seconds_continuous_problems_and_recognition_retention(self):
        # Real elapsed time and production writers, synthetic inputs only.
        with tempfile.TemporaryDirectory() as folder:
            service=problems.ProblemsService(SimpleNamespace(work_root=Path(folder)))
            started=time.monotonic();attempted=accepted=diagnostics=last_accepted=diagnostic_returns=0
            failures=[];original_write=problems._write_batch
            def measured_write(path,rows):
                try:return original_write(path,rows)
                except Exception as error:
                    chain=[];seen=set()
                    while error is not None and id(error) not in seen:
                        seen.add(id(error))
                        chain.append(dict(type=type(error).__name__,errno=getattr(error,'errno',None),
                            winerror=getattr(error,'winerror',None)))
                        error=error.__cause__ or error.__context__
                    failures.append(chain)
                    raise
            measurement=patch.object(problems,'_write_batch',side_effect=measured_write)
            measurement.start()
            try:
                while time.monotonic()-started<60:
                    tick_start=time.monotonic()
                    for _ in range(8):
                        attempted+=1
                        did_accept=service.record(dict(kind='error',title='synthetic-'+str(attempted),
                            phase='capture',raw='ValueError: synthetic-only',captureDiagnostics=dict(actual=attempted)))
                        accepted+=bool(did_accept)
                        if did_accept:last_accepted=attempted
                    for lane in ('interface','floor','pose'):
                        diagnostics+=1
                        diagnostic_returns+=bool(service.recordTrace(dict(role='runtime',stage='recognition',recognitionDiagnostics=dict(stage=lane,actual=diagnostics))))
                    while time.monotonic()-tick_start<.25:
                        QTest.qWait(5);time.sleep(.002)
                service.shutdown();self.wait(service)
                problem_paths=[service.log_path,service.log_path.with_name(service.log_path.stem+'-previous.jsonl')]
                diag_paths=[service.recognition_path,service.recognition_path.with_name(service.recognition_path.stem+'-previous.jsonl')]
                def retained(paths):
                    return [json.loads(line) for p in paths if p.exists() for line in p.read_bytes().splitlines()]
                problem_rows,diag_rows=retained(problem_paths),retained(diag_paths)
                self.assertTrue(problem_rows);self.assertTrue(diag_rows)
                self.assertTrue(all('recognitionDiagnostics' not in r for r in problem_rows))

                for paths in (problem_paths,diag_paths):
                    self.assertLessEqual(sum(p.stat().st_size for p in paths if p.exists()),2*problems.LOG_MAX_BYTES)
                report=dict(elapsedSeconds=round(time.monotonic()-started,3),problemsAttempted=attempted,
                    problemsAccepted=accepted,problemsRetained=len(problem_rows),problemRetention=len(problem_rows)/accepted,
                    recognitionAttempted=diagnostics,recognitionSuppressed=service.recognition_identity.loss()['suppressedTotal'],
                    recognitionReturnedTrue=diagnostic_returns,recognitionRejected=diagnostics-diagnostic_returns,
                    recognitionEligible=diagnostics-service.recognition_identity.loss()['suppressedTotal'],
                    recognitionAccepted=diagnostic_returns-service.recognition_identity.loss()['suppressedTotal'],
                    recognitionRetained=len(diag_rows),recognitionRetentionOfAttempts=len(diag_rows)/diagnostics,
                    recognitionRetentionOfAccepted=len(diag_rows)/max(1,diagnostic_returns-service.recognition_identity.loss()['suppressedTotal']),
                    problemBytes=sum(p.stat().st_size for p in problem_paths if p.exists()),
                    recognitionBytes=sum(p.stat().st_size for p in diag_paths if p.exists()),
                    loss=service.diagnosticSnapshot()['droppedWrites'],problemLoss=service.identity.loss(),
                    recognitionLoss=service.recognition_identity.loss(),writeFailureTypes=failures,
                    lastAccepted=last_accepted,lastRetained=max(r['captureDiagnostics']['actual'] for r in problem_rows),
                    writeError=bool(service.writeError),writeErrorCodes=__import__('re').findall(r'\[(?:WinError|Errno) \d+\]|diagnostic-[a-z-]+',service.writeErrorDetails))
                print('DIAGNOSTIC_RETENTION '+json.dumps(report),flush=True)
                self.assertEqual(report['lastRetained'],last_accepted,report)
                self.assertEqual(service.identity.loss()['lostTotal'],attempted-accepted,report)
                self.assertEqual(report['recognitionEligible']-report['recognitionRetained'],
                    service.recognition_identity.loss()['lostTotal'],report)
                self.assertEqual(report['recognitionEligible']-report['recognitionAccepted'],
                    report['recognitionRejected'],report)
                if not report['recognitionLoss']['omittedRangeCount']:
                    self.assertEqual(sum(r['count'] for r in report['recognitionLoss']['ranges'] if r['reason']=='queue-capacity'),
                        report['recognitionRejected'],report)
            finally:
                service.shutdown();self.wait(service);measurement.stop()

if __name__=='__main__': unittest.main()
