"""Bounded async evidence/first material seams, not native timing or accuracy."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from s1_private_experiment import LatestEvidenceQueue
from s1_structure_selection import StructureCandidateScanner
import test_s1_native_candidate_preview as native


class QueueContracts(unittest.TestCase):
    def test_shutdown_stops_admission_and_busy_tracks_running_task(self):
        queue=LatestEvidenceQueue();entered=threading.Event();release=threading.Event()
        self.addCleanup(queue.shutdown)
        queue.submit(lambda:(entered.set(),release.wait(3)))
        self.assertTrue(entered.wait(3));thread=queue._thread
        queue.shutdown();self.assertTrue(queue.busy)
        self.assertFalse(queue.submit(lambda:None))
        release.set();thread.join(3);self.assertFalse(queue.busy)

    def test_private_trace_requires_trusted_baked_purpose(self):
        import client_lifecycle_trace as tracing
        from s1_candidate_purpose import _NATIVE_S1_PREVIEW
        import os
        with patch.dict(os.environ,{tracing.ENVIRONMENT_FLAG:'0'}), patch.object(tracing,'LifecycleTrace') as writer:
            self.assertIsNone(tracing.create_lifecycle_trace(SimpleNamespace(),candidate_purpose=object()))
            self.assertIs(tracing.create_lifecycle_trace(SimpleNamespace(),candidate_purpose=_NATIVE_S1_PREVIEW),writer.return_value)
            writer.assert_called_once()

    def test_one_running_latest_only_and_capture_order(self):
        queue=LatestEvidenceQueue();entered=threading.Event();release=threading.Event();seen=[]
        self.addCleanup(queue.shutdown)
        def first():entered.set();release.wait(3);seen.append(1)
        queue.submit(first,sequence=1);self.assertTrue(entered.wait(3))
        queue.submit(lambda:seen.append(2),sequence=2)
        queue.submit(lambda:seen.append(3),sequence=3)
        self.assertFalse(queue.submit(lambda:seen.append(0),sequence=1))
        release.set();queue._thread.join(3)
        self.assertEqual(seen,[1,3]);self.assertIsNone(queue._thread)

    def test_exception_releases_owner_and_next_task_runs(self):
        queue=LatestEvidenceQueue();self.addCleanup(queue.shutdown)
        def fail():raise RuntimeError('fixture')
        queue.submit(fail)
        thread=queue._thread
        if thread:thread.join(3)
        done=threading.Event();queue.submit(done.set);self.assertTrue(done.wait(3))

    def test_real_disk_writer_preserves_origin_and_private_stages(self):
        from client_lifecycle_trace import LifecycleTrace
        with tempfile.TemporaryDirectory() as folder:
            trace=LifecycleTrace(SimpleNamespace(work_root=Path(folder)))
            try:
                trace.record('runtime-event',season='s1',eventName='private-stage',privateStage='gate',
                    privateStageState='cancelled',elapsedMs=12.5,openingId='1'*32,attemptId='2'*32,
                    frameId='old-frame',roundId=7,epoch=2,inputSequence=44,privateFrameDigest='a'*64,
                    privatePoolBefore=['106001','106009'],privateScaleRange=[.4,.6],rawPixels=[1])
                end=time.monotonic()+3
                while trace.pendingWrites and time.monotonic()<end:time.sleep(.02)
                rows=[json.loads(line) for line in trace.log_path.read_text().splitlines()]
                row=rows[-1]
                self.assertEqual(row['season'],'s1');self.assertEqual(row['privateStageState'],'cancelled');self.assertEqual(row['openingId'],'1'*32)
                self.assertEqual(row['privatePoolBefore'],['106001','106009']);self.assertEqual(row['inputSequence'],44)
                self.assertEqual(row['privateScaleRange'],[.4,.6]);self.assertNotIn('rawPixels',row)
            finally:trace.shutdown()


class AsyncEvidenceContracts(unittest.TestCase):
    setUp=native.NativeCandidateContracts.setUp
    start_patch=native.NativeCandidateContracts.start_patch
    show=native.NativeCandidateContracts.show
    current_payload=native.NativeCandidateContracts.current_payload

    def idle(self):
        thread=self.runtime._s1_private_jobs._thread
        if thread:thread.join(4)
        self.assertIsNone(self.runtime._s1_private_jobs._thread)

    def report(self,ids,floor,bad=('b',)):
        return dict(mode='nightmare',resourceKey='fixture',floor=floor,coordinateVerified=True,
            evaluatedMapIds=list(ids),contradictedMapIds=[i for i in ids if i in bad],
            unknownMapIds=[i for i in ids if i not in bad],reason='same-coordinate-positive-contradictions-only')

    def wire(self, prepare=None, fit=None):
        self.calls=[];self.prepared=[];self.events=[]
        def default(frame,ids,floor,*,cancelled,stage=None):
            self.prepared.append((tuple(ids),floor))
            return self.report(ids,floor)
        self.registry.engine.qt_s1_private_coordinate_prepare=prepare or default
        def scan(frame,**kw):
            def solve(f,mid,fl,cancelled,**ignored):
                self.calls.append(mid)
                return fit(f,mid,fl,cancelled) if fit else copy.deepcopy(self.fit)
            return StructureCandidateScanner(self.registry.catalog,solve).scan(frame,**kw)
        self.registry.engine.qt_s1_structure_scan=scan
        self.runtime.set_event_callback(self.events.append)

    def test_first_material_does_not_wait_for_background_and_close_still_accumulates(self):
        entered,release=threading.Event(),threading.Event()
        def prepare(frame,ids,floor,*,cancelled,stage=None):
            entered.set();self.assertTrue(release.wait(3));self.assertFalse(cancelled())
            return self.report(ids,floor)
        self.wire(prepare);first=self.show();self.assertTrue(entered.wait(3))
        self.assertEqual(self.calls,['a'])
        self.assertEqual(self.runtime.snapshot()['structureEvidence']['currentStatus'],'first-material-committed')
        self.runtime.dispatch('close_map');release.set();self.idle()
        self.assertEqual(self.runtime._s1_private_pool.remaining,('a',))
        self.assertIsNone(self.runtime._render)
        self.assertFalse(any(e['event'].startswith('private-pool') for e in self.events))
        self.assertFalse(self.runtime._s1_private_pool.locked_id)
        self.show();self.assertEqual(self.runtime._s1_private_pool.locked_id,'a');self.idle()

    def test_close_before_first_material_still_analyzes_valid_frame(self):
        entered,release=threading.Event(),threading.Event()
        def fit(f,mid,fl,cancelled):entered.set();release.wait(3);return copy.deepcopy(self.fit)
        self.wire(fit=fit);self.runtime.dispatch('start_round');result=[]
        thread=threading.Thread(target=lambda:result.append(self.runtime.dispatch('capture_now')))
        thread.start();self.assertTrue(entered.wait(3));self.runtime.dispatch('close_map');release.set();thread.join(3);self.idle()
        self.assertFalse(result[0]['success']);self.assertEqual(self.runtime._s1_private_pool.remaining,('a',))
        self.assertIsNone(self.runtime._render)

    def test_end_discards_late_result_and_queue_releases(self):
        entered,release=threading.Event(),threading.Event()
        def prepare(frame,ids,floor,*,cancelled,stage=None):
            entered.set();release.wait(3);return self.report(ids,floor)
        self.wire(prepare);self.show();self.assertTrue(entered.wait(3));self.runtime.dispatch('end_round')
        release.set();self.idle();self.assertIsNone(self.runtime._s1_private_pool)
        self.assertIsNone(self.runtime._render)

    def test_queued_new_floor_reads_pool_after_prior_commit(self):
        entered,release=threading.Event(),threading.Event();seen=[]
        def prepare(frame,ids,floor,*,cancelled,stage=None):
            seen.append((tuple(ids),floor))
            if len(seen)==1:entered.set();release.wait(3)
            return self.report(ids,floor)
        self.wire(prepare);self.show();self.assertTrue(entered.wait(3))
        self.runtime.dispatch('close_map');self.floor='-2';self.show()
        release.set();self.idle()
        self.assertEqual(seen,[(('a','b'),'-1'),(('a',),'-2')])

    def test_revision_changed_during_analysis_does_not_restore_pool(self):
        entered,release=threading.Event(),threading.Event()
        def prepare(frame,ids,floor,*,cancelled,stage=None):
            entered.set();release.wait(3);return self.report(ids,floor)
        self.wire(prepare);self.show();self.assertTrue(entered.wait(3))
        with self.runtime._lock:self.runtime._s1_private_pool.restore()
        release.set();self.idle();self.assertEqual(self.runtime._s1_private_pool.remaining,('a','b'))

    def test_binding_change_rejects_historical_result(self):
        entered,release=threading.Event(),threading.Event()
        def prepare(frame,ids,floor,*,cancelled,stage=None):
            entered.set();release.wait(3);return self.report(ids,floor)
        self.wire(prepare);self.show();self.assertTrue(entered.wait(3))
        self.capture.generation+=1
        release.set();self.idle();self.assertEqual(self.runtime._s1_private_pool.remaining,('a','b'))

    def test_explicit_switch_after_abbreviated_scan_gets_full_catalogue(self):
        self.wire(lambda frame,ids,floor,**kw:dict(self.report(ids,floor,()),coordinateVerified=False))
        self.show();self.idle();self.calls.clear()
        result=self.runtime.dispatch('next_candidate');self.idle()
        self.assertTrue(result['success'],result);self.assertEqual(self.current_payload()['mapId'],'b')
        self.assertEqual(self.calls,['a','b'])
