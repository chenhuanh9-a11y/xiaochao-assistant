"""Identity/display separation: real runtime transactions, synthetic IO only.

These deterministic contracts do not measure game accuracy or native timing.
"""
import copy
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from s1_private_experiment import PrivateRoundPool
from s1_structure_selection import StructureCandidateScanner
import test_s1_native_candidate_preview as native


def evidence(ids, floor='-1', bad=(), *, verified=True, reason=None):
    return dict(mode='nightmare', resourceKey='fixture', floor=floor,
        coordinateVerified=verified, evaluatedMapIds=list(ids),
        contradictedMapIds=[mid for mid in ids if mid in bad],
        unknownMapIds=[mid for mid in ids if mid not in bad],
        reason=reason or 'same-coordinate-positive-contradictions-only')


class LockedIdentityContracts(unittest.TestCase):
    start_patch = native.NativeCandidateContracts.start_patch
    show = native.NativeCandidateContracts.show
    current_payload = native.NativeCandidateContracts.current_payload

    def setUp(self):
        native.NativeCandidateContracts.setUp(self)
        self.registry.capability['observedFloors'] = (-1, -2, -3, -4)
        self.registry.invalidate = lambda: None  # Fixture keeps immutable resources.
        self.calls, self.plans, self.events, self.records = [], [], [], []
        self.bad, self.unknown = ('b',), set()
        self.runtime.set_event_callback(self.events.append)
        self.start_patch(patch.object(self.runtime, '_private_record',
            side_effect=lambda context, event, **values: self.records.append(dict(event=event, **values))))
        self.registry.engine.qt_s1_private_coordinate_prepare = (
            lambda frame, ids, floor, **kw: dict(evidence(ids, floor, self.bad),
                mode=self.registry.catalog.mode, resourceKey=self.registry.catalog.resource_key))
        def scan(frame, **kw):
            self.plans.append(tuple(kw.get('planned_map_ids', ())))
            def fit(frame, mid, floor, cancelled, **ignored):
                self.calls.append((mid, floor))
                if mid in self.unknown:
                    return dict(success=False, reason='fixture-current-fit-unknown')
                return copy.deepcopy(self.fit)
            return StructureCandidateScanner(self.registry.catalog, fit).scan(frame, **kw)
        self.registry.engine.qt_s1_structure_scan = scan

    def idle(self):
        thread = self.runtime._s1_private_jobs._thread
        if thread:
            thread.join(4)
        self.assertIsNone(self.runtime._s1_private_jobs._thread)

    def locked(self):
        self.show(); self.idle()
        pool = self.runtime._s1_private_pool
        self.assertEqual(pool.remaining, ('a',))
        self.assertFalse(pool.locked_id)  # One displayed support is not unique.
        self.runtime.dispatch('close_map')
        row = self.show(); self.idle()
        self.assertEqual(pool.locked_id, 'a')
        self.calls.clear(); self.plans.clear()
        return pool, row

    def assert_pending_identity(self, pool, old_row, *, geometry_pending=True):
        self.assertIs(self.runtime._s1_private_pool, pool)
        self.assertEqual(pool.remaining, ('a',))
        self.assertEqual(pool.locked_id, 'a')
        self.assertIsNone(self.current_payload())
        self.assertIsNone(self.runtime.render_payload(old_row['token']))
        self.assertFalse(self.runtime.acknowledge_render_material(old_row['token'])['success'])
        if geometry_pending:
            self.assertFalse(self.runtime.snapshot()['structureEvidence']['currentSupportedMapIds'])
        self.assertFalse(any(row['event'] == 'private-experimental-unlocked' for row in self.events))

    def test_locked_unknown_withdraws_pixels_and_next_frame_relocates_only_locked_id(self):
        pool, row = self.locked(); revision = pool.revision
        self.unknown.add('a')
        result = self.runtime.dispatch('capture_now'); self.idle()
        self.assertFalse(result['success'], result)
        self.assertEqual(self.calls, [('a', '-1')])
        self.assertEqual(self.plans, [('a',)])
        self.assert_pending_identity(pool, row)
        self.assertEqual(pool.revision, revision)
        self.calls.clear(); self.unknown.clear()
        result = self.runtime.dispatch('capture_now'); self.idle()
        self.assertTrue(result['success'], result)
        self.assertEqual(self.calls, [('a', '-1')])
        current = self.current_payload()
        self.assertEqual(current['mapId'], 'a')
        self.assertNotEqual(current['frameId'], row['frameId'])
        self.assertNotEqual(current['token'], row['token'])
        self.assertEqual(pool.locked_id, 'a')

    def test_new_floor_unknown_does_not_reset_identity(self):
        pool, row = self.locked()
        for floor in ('-2', '-3'):
            self.floor = floor; self.unknown.add('a'); self.calls.clear()
            result = self.runtime.dispatch('capture_now'); self.idle()
            self.assertFalse(result['success'], result)
            self.assertEqual(self.calls, [('a', floor)])
            self.assert_pending_identity(pool, row)
            self.unknown.clear(); self.calls.clear()
            result = self.runtime.dispatch('capture_now'); self.idle()
            self.assertTrue(result['success'], result)
            self.assertEqual(self.calls, [('a', floor)])
            self.assertEqual(self.current_payload()['floor'], floor)
            self.assertIs(self.runtime._s1_private_pool, pool)

    def test_active_modes_use_same_locked_unknown_policy(self):
        # Both remaining runtime modes share this ledger. Historical ordinary
        # resources are maintenance-only, never a successful runtime fixture.
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                fx = LockedIdentityContracts(); fx.setUp()
                try:
                    fx.registry.engine.pack.mode = mode
                    fx.registry.catalog = replace(fx.registry.catalog, mode=mode)
                    with patch('s1_structure_selection.StructureCatalog.from_engine', return_value=fx.registry.catalog):
                        self.assertTrue(fx.runtime.dispatch('set_context', dict(season='s1', mode=mode))['success'])
                        pool, row = fx.locked(); fx.unknown.add('a')
                        result = fx.runtime.dispatch('capture_now'); fx.idle()
                        self.assertFalse(result['success'], result)
                        self.assertEqual(fx.calls, [('a', '-1')])
                        fx.assert_pending_identity(pool, row)
                finally:
                    fx.doCleanups()

    def test_unknown_floor_never_uses_locked_old_pixels(self):
        pool, row = self.locked(); self.floor = None
        result = self.runtime.dispatch('capture_now'); self.idle()
        self.assertFalse(result['success'], result)
        self.assertEqual(self.calls, [])
        self.assert_pending_identity(pool, row)

    def test_material_failure_retains_identity_but_no_pixels(self):
        pool, row = self.locked()
        with patch.object(self.runtime, '_display_layer', side_effect=ValueError('fixture unavailable')):
            result = self.runtime.dispatch('capture_now')
        self.idle()
        self.assertFalse(result['success'], result)
        self.assertEqual(self.calls, [('a', '-1')])
        self.assert_pending_identity(pool, row, geometry_pending=False)

    def test_unverified_current_residual_cannot_use_locked_identity_for_display(self):
        pool, row = self.locked()
        self.fit['selectedRouteCurrentFrameResidual']['verified'] = False
        result = self.runtime.dispatch('capture_now'); self.idle()
        self.assertFalse(result['success'], result)
        self.assertEqual(self.calls, [('a', '-1')])
        self.assert_pending_identity(pool, row)

    def test_unlocked_singleton_unknown_can_restore_competition_without_inventing_lock(self):
        self.show(); self.idle()
        pool = self.runtime._s1_private_pool
        self.assertEqual(pool.remaining, ('a',)); self.assertFalse(pool.locked_id)
        self.bad = (); self.unknown.add('a'); self.calls.clear(); self.plans.clear()
        result = self.runtime.dispatch('capture_now'); self.idle()
        self.assertTrue(result['success'], result)
        self.assertEqual(self.calls, [('a', '-1'), ('a', '-1'), ('b', '-1')])
        self.assertEqual(self.plans, [('a',), ('a', 'b')])
        self.assertEqual(self.current_payload()['mapId'], 'b')
        self.assertEqual(pool.remaining, ('a', 'b')); self.assertFalse(pool.locked_id)

    def test_one_current_supported_material_without_eliminations_never_locks(self):
        self.bad = (); self.show(); self.idle()
        pool = self.runtime._s1_private_pool
        self.assertEqual(self.calls, [('a', '-1')])
        self.assertEqual(pool.remaining, ('a', 'b')); self.assertFalse(pool.locked_id)

    def test_force_catalogue_clears_old_pool_and_really_scans_full_catalogue(self):
        old, row = self.locked(); self.bad = (); self.unknown.add('a')
        result = self.runtime.dispatch('capture_now', dict(forceCatalogue=True)); self.idle()
        self.assertTrue(result['success'], result)
        self.assertIsNot(self.runtime._s1_private_pool, old)
        self.assertEqual(self.plans, [('a', 'b')])
        self.assertEqual(self.calls, [('a', '-1'), ('b', '-1')])
        self.assertEqual(self.current_payload()['mapId'], 'b')
        self.assertFalse(self.runtime._s1_private_pool.locked_id)
        self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])

    def test_reselect_clears_pool_and_next_capture_reopens_competition(self):
        old, row = self.locked(); self.bad = (); self.unknown.add('a')
        self.assertTrue(self.runtime.dispatch('reselect_map')['success'])
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])
        result = self.runtime.dispatch('capture_now'); self.idle()
        self.assertTrue(result['success'], result)
        self.assertIsNot(self.runtime._s1_private_pool, old)
        self.assertEqual(self.plans, [('a', 'b')])
        self.assertEqual(self.calls, [('a', '-1'), ('b', '-1')])
        self.assertEqual(self.current_payload()['mapId'], 'b')

    def test_reselection_never_reuses_old_acked_pose_after_subsequent_capture(self):
        for action in ('reselect_map', 'forceCatalogue'):
            with self.subTest(action=action):
                fx = LockedIdentityContracts(); fx.setUp()
                try:
                    old, row = fx.locked()
                    self.assertTrue(fx.runtime.acknowledge_render_material(row['token'])['success'])
                    old_prior = fx.runtime._s1_candidate_pose_hint
                    self.assertIsNotNone(old_prior)
                    prior_calls = []
                    fx.registry.engine.qt_s1_pose_prior = lambda source, floor, scope: prior_calls.append(source)
                    fx.bad = (); fx.unknown.add('a')
                    if action == 'reselect_map':
                        self.assertTrue(fx.runtime.dispatch(action)['success'])
                        result = fx.runtime.dispatch('capture_now')
                    else:
                        result = fx.runtime.dispatch('capture_now', dict(forceCatalogue=True))
                    fx.idle(); self.assertTrue(result['success'], result)
                    self.assertEqual(prior_calls, [])
                    self.assertIsNone(fx.runtime._s1_candidate_preference)
                    self.assertFalse(fx.runtime.acknowledge_render_material(row['token'])['success'])
                    # Reopening without a new ACK must still not revive the old hint.
                    fx.runtime.dispatch('close_map')
                    self.assertTrue(fx.runtime.dispatch('capture_now')['success']); fx.idle()
                    self.assertEqual(prior_calls, [])
                    current = fx.current_payload()
                    self.assertEqual(current['mapId'], 'b')
                    self.assertTrue(fx.runtime.acknowledge_render_material(current['token'])['success'])
                    new_prior = fx.runtime._s1_candidate_pose_hint
                    self.assertIsNot(new_prior, old_prior)
                    fx.runtime.dispatch('close_map')
                    self.assertTrue(fx.runtime.dispatch('capture_now')['success']); fx.idle()
                    self.assertEqual(prior_calls, [new_prior])
                    self.assertEqual(new_prior.map_id, 'b')
                finally:
                    fx.doCleanups()

    def test_late_old_background_cannot_restore_or_replace_new_pool(self):
        old, row = self.locked(); revision = old.revision
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed(frame, ids, floor, **kw):
            if tuple(ids) == ('a',):
                entered.set(); self.assertTrue(release.wait(3))
                return evidence(ids, floor, ('a',))
            return evidence(ids, floor)
        self.registry.engine.qt_s1_private_coordinate_prepare = delayed
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.assertTrue(entered.wait(3))
        self.assertTrue(self.runtime.dispatch('reselect_map')['success'])
        self.unknown.add('a')
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        new = self.runtime._s1_private_pool; token = self.current_payload()['token']
        release.set(); self.idle()
        self.assertIsNot(old, new)
        self.assertEqual((old.locked_id, old.revision), ('a', revision))
        self.assertEqual(new.remaining, ('a', 'b')); self.assertFalse(new.locked_id)
        self.assertEqual(self.current_payload()['token'], token)
        self.assertEqual(self.current_payload()['mapId'], 'b')
        self.assertTrue(any(row['event'] == 'private-evidence-discarded' for row in self.records))

    def test_end_context_settings_resources_and_window_invalidate_old_identity(self):
        for boundary in ('end_round', 'cancel', 'context', 'settings', 'resource', 'window'):
            with self.subTest(boundary=boundary):
                fx = LockedIdentityContracts(); fx.setUp()
                try:
                    old, row = fx.locked(); fx.bad = ()
                    if boundary in ('end_round', 'cancel'):
                        fx.runtime.dispatch(boundary)
                    elif boundary == 'context':
                        # Context switching is rejected until the active round ends.
                        self.assertFalse(fx.runtime.dispatch('set_context', dict(season='s1', mode='difficult'))['success'])
                        self.assertIs(fx.runtime._s1_private_pool, old)
                        fx.runtime.dispatch('end_round')
                        self.assertTrue(fx.runtime.dispatch('set_context', dict(season='s1', mode='difficult'))['success'])
                    elif boundary == 'settings':
                        fx.runtime.configure_settings(dict(mapSelectionMode='auto', idvb0059Enabled=False))
                    elif boundary == 'resource':
                        fx.runtime.invalidate('resource-changed')
                    else:
                        fx.capture.generation += 1
                        self.assertTrue(fx.runtime.dispatch('capture_now')['success'])
                        fx.idle()
                    self.assertIsNot(fx.runtime._s1_private_pool, old)
                    self.assertIsNone(fx.runtime.render_payload(row['token']))
                    self.assertFalse(fx.runtime.acknowledge_render_material(row['token'])['success'])
                finally:
                    fx.doCleanups()


class LockedPoolEvidenceContracts(unittest.TestCase):
    def pool(self):
        pool = PrivateRoundPool('scope', ('a', 'b'))
        pool.observe(evidence(pool.remaining, bad=('b',)))
        self.assertTrue(pool.accept_material('a', coordinate_current=True))
        return pool

    def test_unknown_coordinate_reasons_never_restore_locked_identity(self):
        for reason in ('source-coordinate-unresolved', 'current-common-coordinate-unresolved',
                       'current-common-coordinate-ambiguous', 'coordinate-resources-unavailable'):
            pool = self.pool(); revision = pool.revision
            outcome = pool.observe(evidence(pool.remaining, verified=False, reason=reason))
            self.assertEqual(outcome, 'private-coordinate-unknown')
            self.assertEqual((pool.remaining, pool.locked_id, pool.revision), (('a',), 'a', revision))

    def test_verified_structural_contradiction_restores_competition(self):
        pool = self.pool(); revision = pool.revision
        outcome = pool.observe(evidence(pool.remaining, bad=('a',)))
        self.assertEqual(outcome, 'private-contradiction-recovery')
        self.assertEqual((pool.remaining, pool.locked_id), (('a', 'b'), ''))
        self.assertGreater(pool.revision, revision)

    def test_all_candidate_common_coordinate_conflict_restores_competition(self):
        pool = self.pool()
        report = evidence(pool.remaining, verified=False, reason='coordinate-or-catalogue-conflict')
        report['candidateChecks'] = [dict(mapId='a', explicitContradiction=True)]
        self.assertEqual(pool.observe(report), 'private-contradiction-recovery')
        self.assertEqual((pool.remaining, pool.locked_id), (('a', 'b'), ''))

    def test_unverified_or_wrong_candidate_contradiction_never_unlocks(self):
        for report in (evidence(('a',), bad=('a',), verified=False),
                       evidence(('a', 'b'), bad=('a',)), evidence(('foreign',), bad=('foreign',))):
            pool = self.pool(); revision = pool.revision
            self.assertEqual(pool.observe(report), 'private-coordinate-unknown')
            self.assertEqual((pool.remaining, pool.locked_id, pool.revision), (('a',), 'a', revision))


if __name__ == '__main__':
    unittest.main()
