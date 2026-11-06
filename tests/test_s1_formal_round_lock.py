"""Formal token runs real runtime ledger; capture/solver evidence is synthetic."""
from unittest.mock import patch
import s1_candidate_purpose as purpose
from test_s1_locked_identity import LockedIdentityContracts


class FormalRoundLockContracts(LockedIdentityContracts):
    candidate_purpose = purpose._NATIVE_S1_PRODUCTION_CANDIDATE

    def setUp(self):
        self.enterContext(patch.object(purpose, '_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED', True))
        super().setUp()

    def show(self):
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        payload = self.current_payload()
        self.assertIsNotNone(payload)
        self.assertTrue(payload['liveAuthorization'])
        return payload

    def test_unlocked_singleton_unknown_can_restore_competition_without_inventing_lock(self):
        # Formal rollout deliberately retains positive historical exclusions.
        self.test_unconfirmed_singleton_unknown_preserves_accumulated_evidence()

    def test_public_identity_and_single_lock_transition(self):
        pool, row = self.locked()
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(self.runtime.snapshot()['durableMapId'], 'a')
        self.assertFalse(self.runtime._native_s1_private_experiment)
        for floor in ('-2', '-3', '-4'):
            self.floor = floor
            self.assertTrue(self.runtime.dispatch('capture_now')['success'])
            self.idle()
        self.assertEqual(len([e for e in self.events if e['event'] == 'private-experimental-locked']), 1)
        self.assertTrue(all(plan == ('a',) for plan in self.plans))

    def test_unconfirmed_singleton_unknown_preserves_accumulated_evidence(self):
        self.show(); self.idle()
        pool = self.runtime._s1_private_pool
        self.assertEqual(pool.remaining, ('a',))
        self.bad = ()
        self.unknown.add('a')
        self.floor = '-2'
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.idle()
        self.assertEqual(pool.remaining, ('a',))
        self.assertFalse(pool.locked_id)

    def test_pool_progress_emits_current_count_without_claiming_lock(self):
        self.show(); self.idle()
        events = [e for e in self.events if e['event'] == 's1-candidate-pool-changed']
        self.assertEqual([len(e['privatePoolMapIds']) for e in events], [2, 1])
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(self.runtime.snapshot()['s1RemainingCandidateCount'], 1)
        self.assertTrue(self.runtime.private_experiment_event_is_current(events[-1]))
        self.runtime.dispatch('end_round')
        self.assertFalse(self.runtime.private_experiment_event_is_current(events[-1]))

    def test_formal_round_diagnostic_retains_unresolved_reason_without_private_trace(self):
        from diagnostic_privacy import safe_record
        self.show(); self.idle()
        rows = [row['recognitionDiagnostics'] for row in self.events
            if row['event'] == 'recognition-diagnostic' and
                row['recognitionDiagnostics'].get('stage') == 's1-round-evidence']
        self.assertTrue(rows)
        row = safe_record(rows[-1])
        self.assertEqual(row['remainingCount'], 1)
        self.assertEqual(row['contradictedCount'], 1)
        self.assertEqual(row['privateCoordinateReason'], 'same-coordinate-positive-contradictions-only')
        self.assertFalse(row['privateExperimentalLocked'])
        self.assertFalse(self.runtime._native_s1_private_experiment)

    def test_three_candidates_accumulate_only_positive_cross_floor_evidence(self):
        from s1_private_experiment import PrivateRoundPool
        from test_s1_locked_identity import evidence
        pool = PrivateRoundPool('round', ('a', 'b', 'c'))
        pool.observe(evidence(pool.remaining, '-1', ('b',)))
        self.assertEqual(pool.remaining, ('a', 'c'))
        pool.observe(evidence(pool.remaining, '-2', ('a',), verified=False))
        self.assertEqual(pool.remaining, ('a', 'c'))
        pool.observe(evidence(pool.remaining, '-3', ('c',)))
        self.assertEqual(pool.remaining, ('a',))
        self.assertFalse(pool.accept_material('a', coordinate_current=False))
        self.assertTrue(pool.accept_material('a', coordinate_current=True))
        self.assertFalse(pool.accept_material('a', coordinate_current=True))

    def test_background_exclusion_revokes_already_painted_candidate(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        from test_s1_locked_identity import evidence
        def prepare(frame, ids, floor, **kwargs):
            entered.set()
            if not release.wait(4):
                raise TimeoutError('fixture')
            return dict(evidence(ids, floor, ('a',)),
                mode=self.registry.catalog.mode, resourceKey=self.registry.catalog.resource_key)
        self.registry.engine.qt_s1_private_coordinate_prepare = prepare
        row = self.show()
        self.assertEqual(row['mapId'], 'a')
        self.assertTrue(entered.wait(4))
        release.set(); self.idle()
        self.assertEqual(self.runtime._s1_private_pool.remaining, ('b',))
        self.assertIsNone(self.runtime.render_payload(row['token']))
        self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.assertTrue(any(e['event'] == 'render-revoked' and e.get('reason') == 's1-candidate-excluded'
            for e in self.events))
