"""Synthetic Temp logs only: retained counts, overlap and strict scan budgets."""
import json
from pathlib import Path
import tempfile
import unittest
from client_problem_history import summarize_problem_history
from client_session import _RuntimeWork
import threading


def row(count=1, occurrence='run:1', **extra):
    return dict(event='qt-runtime-problem', kind='error', title='Synthetic failure',
                raw='ValueError: sample', id='1', occurrenceId=occurrence,
                timestampUTC='2026-01-01T00:00:00Z', lastTimestampUTC='2026-01-01T00:00:01Z',
                count=count, **extra)


class HistoryTests(unittest.TestCase):
    def test_fixed_slot_legacy_occurrences_do_not_deduplicate_distinct_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=str(Path(folder).resolve())
            records=[row(2,'events-qt-ring:1',sessionId='a'*32,pid=123,problemGroupId='c'*32),
                     row(3,'events-qt-ring:1',sessionId='b'*32,pid=456,problemGroupId='d'*32)]
            (Path(folder)/'events-qt-ring.jsonl').write_text('\n'.join(map(json.dumps,records)))
            result=summarize_problem_history(folder)['recentProblems']
            self.assertEqual(len(result),2)
            self.assertEqual(sum(r['count'] for r in result),5)

    def test_overlap_checkpoint_copies_current_use_max_then_sum_distinct_occurrences(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            path = Path(folder)
            data = '\n'.join(json.dumps(item) for item in (row(), row(7), row(2, 'run:2'))) + '\n'
            (path/'events-a.jsonl').write_text(data)
            (path/'events-a-previous.jsonl').write_text(data)
            result = summarize_problem_history(path, [row(9)])
            self.assertEqual(result['recentProblems'][0]['count'], 11)
            self.assertFalse(result['coverage']['completeHistory'])

    def test_legacy_missing_repeat_count_is_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            old = row(); old.pop('occurrenceId'); old.pop('count')
            (Path(folder)/'events-old.jsonl').write_text(json.dumps(old)+'\n')
            self.assertEqual(summarize_problem_history(folder, [old])['recentProblems'][0]['count'], 1)

    def test_file_byte_record_and_output_budgets(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            for index in range(4):
                (Path(folder)/f'events-{index}.jsonl').write_text((json.dumps(row())+'\n')*20)
            result = summarize_problem_history(folder, max_files=1, max_bytes=1024, max_records=2)
            coverage = result['coverage']
            self.assertLessEqual(coverage['filesRead'], 1)
            self.assertLessEqual(coverage['bytesRead'], 1024)
            self.assertLessEqual(coverage['recordsExamined'], 2)
            self.assertTrue(coverage['fileLimitReached'])
            self.assertTrue(coverage['byteLimitReached'])

    def test_repeats_do_not_enqueue_per_frame_and_explicit_flush_checkpoints(self):
        from PySide6.QtCore import QCoreApplication
        from types import SimpleNamespace
        from client_problems import ProblemsService
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            service = ProblemsService(SimpleNamespace(work_root=folder))
            service._start_next = lambda: None
            for _ in range(50):
                service.record(row())
            self.assertEqual(len(service._queue), 1)
            self.assertTrue(service.flushSummary())
            self.assertEqual(len(service._queue), 2)
            self.assertEqual(service._queue[-1]['count'], 50)
            self.assertEqual(service._queue[-1]['event'], 'qt-runtime-problem-summary')
            self.assertEqual(service._queue[-1]['occurrenceId'], service.recent[0]['occurrenceId'])
            service.shutdown()

    def test_evicted_repeats_are_checkpointed_before_recent_forgets_them(self):
        from PySide6.QtCore import QCoreApplication
        from types import SimpleNamespace
        from client_problems import ProblemsService, _write_batch
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            service = ProblemsService(SimpleNamespace(work_root=folder))
            service._start_next = lambda: None
            for _ in range(50):
                service.record(row())
            first_occurrence = service.recent[0]['occurrenceId']
            for index in range(32):
                service.record(dict(kind='error', title=f'Independent {index}'))
            self.assertNotIn(first_occurrence, [item['occurrenceId'] for item in service.recent])
            self.assertTrue(service.flushSummary())
            _write_batch(service.log_path, list(service._queue))
            saved = [json.loads(line) for line in service.log_path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(max(item['count'] for item in saved
                                 if item.get('occurrenceId') == first_occurrence), 50)
            # Reappearance puts the same material problem back in the bounded
            # output: original + checkpoint + current must total 50 + 1.
            service._queue.clear()
            service.record(row())
            result = summarize_problem_history(service.log_path.parent, service.recent)
            repeated = next(item for item in result['recentProblems'] if item['title'] == 'Synthetic failure')
            self.assertEqual(repeated['count'], 51)
            service.shutdown()

    def test_evicted_repeat_checkpoint_full_queue_reports_permanent_loss(self):
        from PySide6.QtCore import QCoreApplication
        from types import SimpleNamespace
        from client_problems import ProblemsService, PENDING_LIMIT
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            service = ProblemsService(SimpleNamespace(work_root=folder))
            service._start_next = lambda: None
            for _ in range(50):
                service.record(row())
            for index in range(31):
                service.record(dict(kind='warning', title=f'Independent {index}'))
            for index in range(PENDING_LIMIT - len(service._queue)):
                self.assertTrue(service.recordTrace(dict(role='fixture', stage=f'queued-{index}')))
            before = service.droppedWrites
            self.assertFalse(service.record(dict(kind='warning', title='Evicts repeated')))
            self.assertEqual(service.droppedWrites, before + 2)  # Checkpoint and new original.
            self.assertIn('problem-repeat-count-checkpoint-dropped', service.writeErrorDetails)
            self.assertEqual(len(service._queue) + service._inflight_rows, PENDING_LIMIT)
            self.assertFalse(any(item.get('event') == 'qt-runtime-problem-summary' for item in service._queue))
            service.shutdown()

    def test_already_checkpointed_count_is_not_enqueued_again_on_eviction(self):
        from PySide6.QtCore import QCoreApplication
        from types import SimpleNamespace
        from client_problems import ProblemsService
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            service = ProblemsService(SimpleNamespace(work_root=folder))
            service._start_next = lambda: None
            service.record(row()); service.record(row())
            self.assertTrue(service.flushSummary())
            for index in range(32):
                service.record(dict(kind='warning', title=f'Independent {index}'))
            self.assertTrue(service.flushSummary())
            summaries = [item for item in service._queue if item.get('event') == 'qt-runtime-problem-summary']
            self.assertEqual(len(summaries), 1)
            self.assertFalse(any(key.startswith('_') for item in service._queue for key in item))
            self.assertFalse(any(key.startswith('_') for item in service.recent for key in item))
            service.shutdown()

    def test_diagnostic_snapshot_copies_recent_queue_and_inflight_without_io(self):
        from PySide6.QtCore import QCoreApplication
        from types import SimpleNamespace
        from client_problems import ProblemsService, PENDING_LIMIT, RECENT_LIMIT
        app = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            service = ProblemsService(SimpleNamespace(work_root=folder))
            service._start_next = lambda: None
            for index in range(32):
                service.record(dict(kind='warning', title=f'Independent {index}',
                                    captureDiagnostics={'nested': {'value': index}}))
            original = dict(service._queue.popleft())
            original['occurrenceId'] = 'only-inflight'
            service._job = SimpleNamespace(rows=[original])
            service._inflight_rows = 1
            while len(service._queue) + service._inflight_rows < PENDING_LIMIT:
                service._queue.append(dict(service._recent[0]))
            service._dropped = 3
            snapshot = service.diagnosticSnapshot()
            self.assertEqual(len(service.recent), RECENT_LIMIT)
            self.assertEqual(len(snapshot['recentProblems']), RECENT_LIMIT + PENDING_LIMIT)
            self.assertEqual(snapshot['snapshotLimit'], 96)
            self.assertTrue(snapshot['pendingWrites'])
            self.assertEqual(snapshot['droppedWrites'], 3)
            self.assertIn('only-inflight', [item['occurrenceId'] for item in snapshot['recentProblems']])
            self.assertFalse(any(key.startswith('_') for item in snapshot['recentProblems'] for key in item))
            snapshot['recentProblems'][0]['captureDiagnostics']['nested']['value'] = -1
            self.assertEqual(service.recent[0]['captureDiagnostics']['nested']['value'], 31)
            # Transition rows do not enter the problem snapshot.
            service._job.rows[0] = dict(event='qt-runtime-transition', kind='error', title='not a problem')
            self.assertEqual(len(service.diagnosticSnapshot()['recentProblems']), 95)
            self.assertFalse(service.log_path.exists())
            service._job = None
            service._inflight_rows = 0
            service.shutdown()

    def test_history_consumes_pending_checkpoint_at_snapshot_end_with_hard_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = str(Path(folder).resolve())  # Windows temp may use an 8.3 alias.
            snapshot = [row()] * 95 + [row(50), row(999)]
            result = summarize_problem_history(folder, iter(snapshot), max_files=0)
            self.assertEqual(result['recentProblems'][0]['count'], 50)
            self.assertEqual(result['coverage']['snapshotRecords'], 96)
            self.assertTrue(result['coverage']['snapshotLimitReached'])
            self.assertEqual(result['coverage']['limits']['maxSnapshotRecords'], 96)

    def test_worker_retains_real_type_and_stack(self):
        class Runtime:
            def dispatch(self, *args):
                raise ValueError('synthetic failure')
        work = _RuntimeWork(Runtime(), 'capture_now', {}, {}, threading.Event())
        results = []
        work.signals.done.connect(lambda *values: results.append(values))
        work.run()
        failure = results[0][3]
        self.assertEqual(failure['exceptionType'], 'ValueError')
        self.assertIn('dispatch', failure['traceback'])
        self.assertIn('ValueError: synthetic failure', failure['traceback'])

if __name__ == '__main__':
    unittest.main()
