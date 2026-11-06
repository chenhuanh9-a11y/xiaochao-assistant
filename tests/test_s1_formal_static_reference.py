"""Joint production lock/static-sheet contracts with synthetic IO and pixels.

Real runtime ownership and stale-result gates are exercised. This is not game
accuracy, Windows GUI, import-helper or end-to-end latency validation.
"""
import threading
from unittest.mock import patch

import s1_candidate_purpose as purpose
import test_s1_static_reference as static
import test_s1_formal_round_lock as formal


class FormalStaticReferenceContracts(static.StaticReferenceContracts):
    candidate_purpose = purpose._NATIVE_S1_PRODUCTION_CANDIDATE
    show = formal.FormalRoundLockContracts.show

    def setUp(self):
        self.enterContext(patch.object(purpose, '_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED', True))
        super().setUp()

    def assert_reference(self, map_id='a', floor='-1'):
        row = self.runtime.reference_payload()
        self.assertIsNotNone(row)
        self.assertEqual((row['mapId'], row['floor']), (map_id, floor))
        self.assertTrue(row['s1CandidateReference'])
        self.assertTrue(row['referenceAuthorized'])
        self.assertFalse(self.runtime._map_id)
        self.assertFalse(self.runtime._gate.confirmed_id)
        self.assertNotIn('matrix', row)
        self.assertNotIn('liveAuthorization', row)
        return row

    def test_closed_reference_revoked_by_late_positive_exclusion(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def prepare(frame, ids, floor, **kwargs):
            entered.set()
            if not release.wait(4):
                raise TimeoutError('synthetic evidence release')
            return self.report(ids, floor, ('a',))
        self.wire(prepare)
        row = self.show()
        self.assertTrue(entered.wait(4))
        self.runtime.dispatch('close_map')
        self.assert_reference()
        release.set(); self.idle()
        self.assertEqual(self.runtime._s1_private_pool.remaining, ('b',))
        self.assertIsNone(self.runtime.reference_payload())
        self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])


class LockedStaticReferenceContracts(formal.FormalRoundLockContracts):
    def test_locked_failed_new_observation_keeps_identity_not_static_pixels(self):
        pool, old_row = self.locked()
        self.assertIsNotNone(self.runtime.reference_payload())
        self.runtime.dispatch('close_map')
        self.assertIsNotNone(self.runtime.reference_payload())
        self.unknown.add('a')
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.idle()
        self.assertIsNone(self.runtime.reference_payload())
        self.assert_pending_identity(pool, old_row)
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        self.unknown.clear()
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.idle()
        self.assertEqual(self.runtime.reference_payload()['mapId'], 'a')
        self.runtime.dispatch('end_round')
        self.assertIsNone(self.runtime.reference_payload())
        self.assertFalse(self.runtime.snapshot()['identityLocked'])

    def test_resource_replacement_revokes_lock_reference_and_old_material(self):
        pool, old_row = self.locked()
        self.assertIsNotNone(self.runtime.reference_payload())
        self.runtime.invalidate('resources-changed')
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertIsNone(self.runtime.reference_payload())
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.assertFalse(self.runtime.acknowledge_render_material(old_row['token'])['success'])
