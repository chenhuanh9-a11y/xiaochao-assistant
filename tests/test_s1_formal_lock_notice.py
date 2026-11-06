"""Formal runtime -> queued Qt status/notice delivery, synthetic capture/solver."""
import unittest
from types import MethodType
from unittest.mock import patch
from client_coordinator import ClientCoordinator
import s1_candidate_purpose as purpose
import test_s1_native_candidate_preview as native
import test_s1_structural_conflict_notice as conflict


class FormalLockNotice(unittest.TestCase):
    settle = conflict.StructuralConflictNotice.settle

    def setUp(self):
        self.enterContext(patch.object(native.NativeCandidateContracts, 'candidate_purpose',
            purpose._NATIVE_S1_PRODUCTION_CANDIDATE))
        conflict.StructuralConflictNotice.setUp(self)
        # The conflict-only fixture suppresses reference presentation. Exercise
        # the real reference consumer here as well as the queued lock notice.
        self.coordinator._sync_reference_payload = MethodType(
            ClientCoordinator._sync_reference_payload, self.coordinator)
        self.release.set()
        self.result = lambda ids, floor: conflict.evidence(ids, floor)

    def test_count_is_quiet_and_real_lock_notifies_once(self):
        publish = self.enterContext(patch.object(self.coordinator.images, 'publish',
            wraps=self.coordinator.images.publish))
        def uploads():
            return sum(str(call.args[0]).startswith('reference:') for call in publish.call_args_list)
        self.producer.show(); self.settle()
        initial_uploads = uploads()
        self.assertGreater(initial_uploads, 0)
        self.assertIn('暂用', self.coordinator.referenceData['title'])
        self.assertIn('剩余 1 个候选', self.coordinator._message)
        self.assertFalse([n for n in self.notices if n[0] == '地图已锁定'])
        self.runtime.dispatch('close_map')
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.settle()
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        reference = self.runtime.reference_payload()
        self.assertTrue(reference['identityLocked'])
        self.assertFalse(reference['provisional'])
        self.assertIn('已锁定', self.coordinator.referenceData['title'])
        self.assertNotIn('暂用', self.coordinator.referenceData['title'])
        self.assertEqual(uploads(), initial_uploads)
        notices = [n for n in self.notices if n[0] == '地图已锁定']
        self.assertEqual(len(notices), 1, self.notices)
        self.assertEqual(notices[0][2], 'info')
        for _ in range(2):
            self.runtime.dispatch('close_map')
            self.assertTrue(self.runtime.dispatch('capture_now')['success'])
            self.settle()
            self.assertIn('已锁定', self.coordinator.referenceData['title'])
            self.assertEqual(uploads(), initial_uploads)
        self.assertEqual(len([n for n in self.notices if n[0] == '地图已锁定']), 1)

    def test_obsolete_count_event_cannot_replace_new_round_message(self):
        self.producer.show(); self.settle()
        event = next(e for e in reversed(self.events) if e['event'] == 's1-candidate-pool-changed')
        self.runtime.dispatch('end_round')
        self.coordinator._message = 'new owner'
        self.coordinator._on_runtime_event(event)
        self.assertEqual(self.coordinator._message, 'new owner')
