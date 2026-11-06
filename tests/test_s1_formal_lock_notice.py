"""Formal runtime -> queued Qt status/notice delivery, synthetic capture/solver."""
import unittest
from unittest.mock import patch
import s1_candidate_purpose as purpose
import test_s1_native_candidate_preview as native
import test_s1_structural_conflict_notice as conflict


class FormalLockNotice(unittest.TestCase):
    settle = conflict.StructuralConflictNotice.settle

    def setUp(self):
        self.enterContext(patch.object(native.NativeCandidateContracts, 'candidate_purpose',
            purpose._NATIVE_S1_PRODUCTION_CANDIDATE))
        conflict.StructuralConflictNotice.setUp(self)
        self.release.set()
        self.result = lambda ids, floor: conflict.evidence(ids, floor)

    def test_count_is_quiet_and_real_lock_notifies_once(self):
        self.producer.show(); self.settle()
        self.assertIn('剩余 1 个候选', self.coordinator._message)
        self.assertFalse([n for n in self.notices if n[0] == '地图已锁定'])
        self.runtime.dispatch('close_map')
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.settle()
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        notices = [n for n in self.notices if n[0] == '地图已锁定']
        self.assertEqual(len(notices), 1, self.notices)
        self.assertEqual(notices[0][2], 'info')
        for _ in range(2):
            self.runtime.dispatch('close_map')
            self.assertTrue(self.runtime.dispatch('capture_now')['success'])
            self.settle()
        self.assertEqual(len([n for n in self.notices if n[0] == '地图已锁定']), 1)

    def test_obsolete_count_event_cannot_replace_new_round_message(self):
        self.producer.show(); self.settle()
        event = next(e for e in reversed(self.events) if e['event'] == 's1-candidate-pool-changed')
        self.runtime.dispatch('end_round')
        self.coordinator._message = 'new owner'
        self.coordinator._on_runtime_event(event)
        self.assertEqual(self.coordinator._message, 'new owner')
