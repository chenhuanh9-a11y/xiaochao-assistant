"""Round identity contracts; synthetic IO does not establish game accuracy."""
from unittest.mock import patch
import test_s1_formal_round_lock as formal
from test_s1_locked_identity import evidence


class ProductionFinalLock(formal.FormalRoundLockContracts):
    def test_verified_and_all_conflict_evidence_cannot_reopen_committed_id(self):
        pool, row = self.locked()
        before = pool.snapshot()
        reports = [evidence(('a',), bad=('a',)),
            dict(evidence(('a',), verified=False, reason='coordinate-or-catalogue-conflict'),
                candidateChecks=[dict(mapId='a', explicitContradiction=True)])]
        for report in reports:
            self.assertEqual(pool.observe(report), 'private-coordinate-unknown')
            self.assertEqual(pool.snapshot(), before)
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        self.assertFalse(pool.recovery_pending)

    def test_locked_floor_changes_skip_ordering_and_background_identity_work(self):
        pool, row = self.locked()
        with patch('s1_stream_priority.candidate_order', side_effect=AssertionError('identity ordering after lock')), \
             patch.object(self.registry.engine, 'qt_s1_private_coordinate_prepare',
                          side_effect=AssertionError('identity evidence after lock')) as observer:
            for floor in ('-2', '-3', '-4', '-1'):
                self.floor = floor
                self.assertTrue(self.runtime.dispatch('capture_now')['success'])
                self.idle()
                self.assertEqual(self.current_payload()['mapId'], 'a')
                self.assertEqual(self.current_payload()['floor'], floor)
            observer.assert_not_called()
        self.assertEqual(self.plans, [('a',)] * 4)
        self.assertEqual(pool.locked_id, 'a')

    def test_locked_bad_fit_withdraws_pixels_without_identity_work(self):
        pool, row = self.locked()
        self.floor = '-3'
        self.unknown.add('a')
        self.bad = ('a',)
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.idle()
        self.assert_pending_identity(pool, row)
        self.assertEqual(self.plans, [('a',)])
        self.assertFalse(pool.recovery_pending)
