"""Revision changes authority, not observation telemetry. Synthetic IO only."""
import copy
import threading
import time
import unittest
from unittest.mock import patch
from s1_private_experiment import PrivateRoundPool
from s1_structure_selection import StructureCandidateScanner
import test_s1_native_candidate_preview as native


def report(ids, bad=(), floor='-1', verified=True):
    return dict(mode='nightmare',resourceKey='fixture',floor=floor,coordinateVerified=verified,
        evaluatedMapIds=list(ids),contradictedMapIds=list(bad),unknownMapIds=[i for i in ids if i not in bad],
        reason='same-coordinate-positive-contradictions-only')


class PoolRevisionContracts(unittest.TestCase):
    def test_no_contradiction_counts_observation_without_authority_revision(self):
        pool=PrivateRoundPool('scope',('a','b'))
        self.assertEqual(pool.observe(report(pool.remaining)),'private-pool-updated')
        self.assertEqual((pool.revision,pool.observation_count),(0,1))
        self.assertEqual(pool.snapshot()['privateObservationCount'],1)
        self.assertEqual(pool.remaining,('a','b'))

    def test_real_prune_and_lock_advance_revision_but_repeat_does_not(self):
        pool=PrivateRoundPool('scope',('a','b','c'))
        pool.observe(report(pool.remaining,('c',)));self.assertEqual(pool.revision,1)
        pool.observe(report(pool.remaining));self.assertEqual(pool.revision,1)
        pool.observe(report(pool.remaining,('b',)));self.assertEqual(pool.revision,2)
        self.assertTrue(pool.accept_material('a',coordinate_current=True));self.assertEqual(pool.revision,3)
        pool.observe(report(pool.remaining));self.assertEqual(pool.revision,3)
        self.assertEqual(pool.locked_id,'a');self.assertEqual(pool.observation_count,4)

    def test_invalid_authority_neither_counts_nor_advances(self):
        for evidence in (report(('a','b'),verified=False),report(('a',)),report(('a','b'),('foreign',))):
            pool=PrivateRoundPool('scope',('a','b'))
            self.assertEqual(pool.observe(evidence),'private-coordinate-unknown')
            self.assertEqual((pool.revision,pool.observation_count),(0,0))
            self.assertEqual(pool.remaining,('a','b'))


class RuntimeRevisionContracts(unittest.TestCase):
    start_patch=native.NativeCandidateContracts.start_patch
    show=native.NativeCandidateContracts.show
    current_payload=native.NativeCandidateContracts.current_payload
    def setUp(self):
        native.NativeCandidateContracts.setUp(self)
        self.registry.capability['observedFloors']=(-1,-2,-3,-4)
        self.events=[];self.records=[];self.runtime.set_event_callback(self.events.append)
        self.entered=threading.Event();self.release=threading.Event();self.committed=threading.Event()
        self.first_entered=threading.Event();self.first_release=threading.Event();self.defer_first=False
        self.prepared=[]
        self.addCleanup(self.release.set)
        self.addCleanup(self.first_release.set)
        def record(context,event,**values):
            self.records.append(dict(event=event,**values))
            if len(self.prepared)==2 and event in ('private-pool-observed','private-evidence-discarded'):
                self.committed.set()
        self.start_patch(patch.object(self.runtime,'_private_record',side_effect=record))
        self.bad=()
        def prepare(frame,ids,floor,*,cancelled,stage=None):
            self.prepared.append(tuple(ids))
            if len(self.prepared)==1 and self.defer_first:
                self.first_entered.set();self.assertTrue(self.first_release.wait(3))
            if len(self.prepared)==2:
                self.entered.set();self.assertTrue(self.release.wait(3))
                return report(ids,self.bad,floor)
            return report(ids,('b',) if 'b' in ids else (),floor)
        self.registry.engine.qt_s1_private_coordinate_prepare=prepare

    def idle(self):
        thread=self.runtime._s1_private_jobs._thread
        if thread:thread.join(3)
        self.assertIsNone(self.runtime._s1_private_jobs._thread)

    def pending_locked_observation(self):
        self.show();self.idle();self.runtime.dispatch('close_map')
        self.show();self.assertTrue(self.entered.wait(3))
        pool=self.runtime._s1_private_pool
        self.assertEqual(pool.remaining,('a',));self.assertEqual(pool.locked_id,'a')
        self.runtime.dispatch('close_map')
        return pool

    def fail_during_background(self):
        calls=[]
        def fit(frame,mid,*args,**kw):
            calls.append(mid)
            if len(calls)==1:
                self.release.set();self.assertTrue(self.committed.wait(3))
            return dict(success=False,reason='fixture-fit-unresolved') if mid=='a' else copy.deepcopy(self.fit)
        self.registry.engine.qt_s1_structure_scan=lambda frame,**kw:StructureCandidateScanner(self.registry.catalog,fit).scan(frame,**kw)
        return calls,self.runtime.dispatch('capture_now')

    def test_noop_background_does_not_unlock_identity_on_current_fit_unknown(self):
        pool=self.pending_locked_observation();revision=pool.revision
        calls,receipt=self.fail_during_background()
        self.assertFalse(receipt['success'],receipt);self.assertEqual(calls,['a'])
        self.assertIsNone(self.current_payload());self.assertEqual(pool.locked_id,'a')
        self.assertEqual(pool.remaining,('a',));self.assertEqual(pool.revision,revision)
        self.assertFalse(any(e['event']=='private-experimental-unlocked' for e in self.events))
        observation=next(r for r in self.records if r.get('snapshotPoolRevision')==revision and r.get('privatePoolBefore')==['a'])
        self.assertEqual(observation['currentPoolRevision'],revision)
        self.assertEqual(observation['privateObservationCount'],2)
        self.idle()

    def test_noop_background_does_not_suppress_unlocked_singleton_recovery(self):
        # Both foreground materials arrive before the first background prune.
        # That prune leaves one ID, but no fresh singleton material has locked it.
        self.defer_first=True
        self.show();self.assertTrue(self.first_entered.wait(3))
        self.runtime.dispatch('close_map');self.show()
        self.first_release.set();self.assertTrue(self.entered.wait(3))
        pool=self.runtime._s1_private_pool;revision=pool.revision
        self.assertEqual(pool.remaining,('a',));self.assertFalse(pool.locked_id)
        self.runtime.dispatch('close_map')
        calls,receipt=self.fail_during_background()
        self.assertTrue(receipt['success'],receipt);self.assertEqual(calls,['a','a','b'])
        self.assertEqual(self.current_payload()['mapId'],'b');self.assertFalse(pool.locked_id)
        self.assertTrue(any(e['event']=='private-experimental-unlocked' and e['reason']=='private-current-fit-unknown' for e in self.events))
        observation=next(r for r in self.records if r.get('snapshotPoolRevision')==revision and r.get('privatePoolBefore')==['a'])
        self.assertEqual(observation['currentPoolRevision'],revision)
        self.assertEqual(observation['privateObservationCount'],2)
        self.idle()

    def test_true_background_change_is_not_overwritten_by_failed_old_scan(self):
        pool=self.pending_locked_observation();revision=pool.revision;self.bad=('a',)
        calls,receipt=self.fail_during_background()
        self.assertFalse(receipt['success']);self.assertEqual(calls,['a'])
        self.assertGreater(pool.revision,revision);self.assertEqual(pool.remaining,('a','b'))
        self.assertFalse(pool.locked_id)
        self.assertFalse(any(e['event']=='private-experimental-unlocked' and e['reason']=='private-current-fit-unknown' for e in self.events))
        self.idle()

    def test_old_round_noop_does_not_commit_to_new_pool(self):
        old=self.pending_locked_observation();old_count=old.observation_count
        self.runtime.dispatch('end_round')
        self.show();new=self.runtime._s1_private_pool;self.assertIsNot(new,old)
        self.release.set();self.idle()
        self.assertEqual(old.observation_count,old_count)
        self.assertEqual(new.observation_count,1)
        self.assertFalse(new.locked_id)
        self.assertTrue(any(r['event']=='private-evidence-discarded' for r in self.records))


if __name__=='__main__':unittest.main()
