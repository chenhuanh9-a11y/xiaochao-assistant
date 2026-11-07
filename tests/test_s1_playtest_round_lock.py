"""Packaged playtest/formal dispatch contracts; capture/fit are synthetic IO.

The real scanner, queue, runtime lifecycle and final getters are exercised.
These checks do not establish live-game accuracy or Windows rendering latency.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import s1_candidate_purpose as purpose
import test_s1_locked_identity as fixture


class PlaytestRoundLockContracts(unittest.TestCase):
    start_patch = fixture.LockedIdentityContracts.start_patch
    current_payload = fixture.LockedIdentityContracts.current_payload
    idle = fixture.LockedIdentityContracts.idle
    locked = fixture.LockedIdentityContracts.locked

    def setUp(self):
        import build_qt_client as build
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'installation-profile.json').write_text(json.dumps(dict(
                product=build.PRODUCT, installerInstance=dict(id='s1-playtest', **build.INSTANCES['s1-playtest']))))
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', directory, create=True):
                self.candidate_purpose = purpose.packaged_native_candidate_purpose()
        fixture.LockedIdentityContracts.setUp(self)

    def show(self):
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        row = self.current_payload()
        self.assertIsNotNone(row)
        return row

    def assert_locked(self, pool=None):
        state = self.runtime.snapshot()
        self.assertTrue(state['identityLocked'])
        self.assertEqual(state['durableMapId'], 'a')
        self.assertEqual(state['identityPhase'], 'MEMBER_VERIFIED')
        current = self.current_payload()
        if current is not None:
            self.assertEqual(current['identityPhase'], 'MEMBER_VERIFIED')
            self.assertTrue(current['identityLocked'])
            self.assertEqual(current['durableMapId'], 'a')
            self.assertFalse(current['provisional'])
        if pool is not None:
            self.assertIs(self.runtime._s1_private_pool, pool)
            self.assertEqual((pool.remaining, pool.locked_id), (('a',), 'a'))
        self.assertFalse(any(e['event'] == 'private-experimental-unlocked' for e in self.events))

    def test_packaged_lock_is_public_and_stops_identity_evidence(self):
        pool, row = self.locked()
        self.assert_locked(pool)
        calls = []
        self.registry.engine.qt_s1_private_coordinate_prepare = lambda *a, **k: calls.append(a)
        self.bad = ('a',)
        for floor in ('-1', '-2', '-3', '-4'):
            self.floor = floor
            self.runtime.dispatch('capture_now'); self.idle()
            self.assert_locked(pool)
        self.assertEqual(calls, [])
        self.assertTrue(all(plan == ('a',) for plan in self.plans))
        self.assertTrue(self.runtime.reference_payload()['identityLocked'])

    def test_sparse_new_floor_keeps_lock_and_static_without_old_pose(self):
        pool, row = self.locked()
        self.assertTrue(self.runtime.acknowledge_render_material(row['token'])['success'])
        source = self.runtime._s1_candidate_pose_hint
        prior_calls = []
        self.registry.engine.qt_s1_pose_prior = lambda old, floor, scope: prior_calls.append((old, floor))
        self.unknown.add('a')
        for floor in ('-2', '-3', '-4'):
            self.floor = floor
            result = self.runtime.dispatch('capture_now'); self.idle()
            self.assertFalse(result['success'])
            self.assert_locked(pool)
            self.assertIsNone(self.current_payload())
            self.assertIsNone(self.runtime.render_payload(row['token']))
            self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])
            static = self.runtime.reference_payload()
            self.assertIsNotNone(static)
            self.assertEqual((static['mapId'], static['floor']), ('a', floor))
            self.assertTrue(static['identityLocked'])
            self.assertNotIn('matrix', static)
            self.assertNotIn('liveAuthorization', static)
        self.assertEqual(prior_calls, [(source, '-2'), (source, '-3'), (source, '-4')])
        self.assertEqual(self.plans, [('a',)] * 3)

    def test_scoped_cross_floor_prior_reaches_same_map_scan_without_granting_pose(self):
        from s1_pose_prior import PosePrior
        import numpy as np
        pool, row = self.locked()
        self.assertTrue(self.runtime.acknowledge_render_material(row['token'])['success'])
        source = self.runtime._s1_candidate_pose_hint
        self.registry.engine.qt_s1_pose_prior = lambda old, floor, scope: PosePrior(
            old.mode, old.resource_key, 'fixture-index', old.map_id, old.floor,
            floor, old.frame_id, scope, old.matrix)
        original = self.registry.engine.qt_s1_structure_scan
        hints = []
        def scan(frame, **kwargs):
            hints.append(kwargs.pop('pose_priors', {}))
            return original(frame, **kwargs)
        self.registry.engine.qt_s1_structure_scan = scan
        self.floor = '-3'; self.unknown.add('a')
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.assertEqual(set(hints[0]), {('a', '-3')})
        np.testing.assert_array_equal(hints[0][('a', '-3')], source.matrix)
        self.assert_locked(pool)
        self.assertIsNone(self.current_payload())
        self.assertEqual(self.runtime.reference_payload()['floor'], '-3')

    def test_unknown_floor_keeps_historical_static_not_current_pixels(self):
        pool, row = self.locked()
        self.floor = None
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.assert_locked(pool)
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.reference_payload()['referenceFloorCurrent'])

    def test_reselection_and_force_catalogue_cannot_reopen_locked_round(self):
        pool, row = self.locked()
        for action, payload in [('reselect_map', {}), ('map_reselection', {}),
                ('next_candidate', {}), ('previous_candidate', {}),
                ('select_candidate', {'mapId': 'b'}), ('confirm_route', {'mapId': 'b'}),
                ('capture_now', {'forceCatalogue': True})]:
            with self.subTest(action=action):
                result = self.runtime.dispatch(action, payload)
                self.assertFalse(result['success'], result)
                self.assertEqual(result['reason'], 'identity-locked')
                self.assert_locked(pool)
                self.assertEqual(self.current_payload()['token'], row['token'])
        self.assertEqual(self.plans, [])
        self.assertNotIn('reselect_map', self.runtime.snapshot()['availableCommands'])
        self.assertFalse(self.runtime.snapshot()['manualMapPickAvailable'])
        self.runtime.dispatch('end_round')
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.bad = (); self.unknown.add('a')
        self.show(); self.idle()
        self.assertEqual(self.plans, [('a', 'b')])
        self.assertEqual(self.current_payload()['mapId'], 'b')

    def test_candidate_admitted_before_lock_cannot_clear_new_lock(self):
        self.show(); self.idle()
        entered = threading.Event()
        original = self.runtime._select_s1_provisional
        def select(*args, **kwargs):
            entered.set()
            return original(*args, **kwargs)
        results = []
        with patch.object(self.runtime, '_select_s1_provisional', side_effect=select):
            with self.runtime._work:
                worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('next_candidate')))
                worker.start()
                self.assertTrue(entered.wait(3))
                self.assertTrue(self.runtime.dispatch('capture_now')['success'])
                pool = self.runtime._s1_private_pool
                self.assert_locked(pool)
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]['success'])
        self.assert_locked(pool)

    def test_locked_comparator_conflict_cannot_restore_pool(self):
        pool, row = self.locked(); previous = pool.snapshot()
        report = fixture.evidence(pool.remaining, bad=('a',))
        self.assertEqual(pool.observe(report), 'private-coordinate-unknown')
        report.update(coordinateVerified=False, reason='coordinate-or-catalogue-conflict',
            candidateChecks=[dict(mapId='a', explicitContradiction=True)])
        self.assertEqual(pool.observe(report), 'private-coordinate-unknown')
        self.assertEqual(pool.snapshot(), previous)
        self.assert_locked(pool)

    def test_explicit_floor_and_probe_entries_never_scan_other_map(self):
        pool, row = self.locked()
        for floor in ('-2', '-3', '-4'):
            self.floor = floor
            self.unknown.add('a')
            self.runtime.dispatch('observe_candidate_floor'); self.idle()
            self.assert_locked(pool)
            self.runtime.dispatch('set_floor', {'floor': floor}); self.idle()
            self.assert_locked(pool)
        self.assertTrue(self.plans)
        self.assertTrue(all(plan == ('a',) for plan in self.plans), self.plans)

    def test_settings_and_capture_scope_rebuild_material_not_identity(self):
        pool, row = self.locked()
        self.runtime.configure_settings(dict(mapSelectionMode='auto', idvb0059Enabled=False))
        self.assert_locked()
        self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])
        self.capture.generation += 1
        self.unknown.add('a')
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.idle(); self.assert_locked()
        self.assertIsNot(self.runtime._s1_private_pool, pool)
        self.assertEqual(self.plans, [('a',)])
        self.assertIsNone(self.current_payload())

    def test_prelock_background_cannot_unlock_after_material_commits(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def prepare(frame, ids, floor, **kwargs):
            entered.set(); self.assertTrue(release.wait(3))
            return fixture.evidence(ids, floor, ('a',))
        self.registry.engine.qt_s1_private_coordinate_prepare = prepare
        self.show(); self.assertTrue(entered.wait(3))
        pool = self.runtime._s1_private_pool
        pool.observe(fixture.evidence(pool.remaining, bad=('b',)))
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        self.assertEqual(pool.locked_id, 'a')
        before = pool.snapshot()
        release.set(); self.idle()
        self.assertEqual(pool.snapshot(), before)
        self.assert_locked(pool)
        self.assertTrue(any(r['event'] == 'private-evidence-discarded' for r in self.records))

    def test_resource_replacement_still_ends_unsafe_context(self):
        pool, row = self.locked()
        self.runtime.invalidate('resources-changed')
        state = self.runtime.snapshot()
        self.assertFalse(state['roundActive'])
        self.assertFalse(state['identityLocked'])
        self.assertIsNone(self.runtime.reference_payload())
        self.assertIsNone(self.runtime.render_payload(row['token']))
        self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])

    def test_missing_locked_resource_fails_closed_without_substituting_candidate(self):
        pool, row = self.locked()
        catalog = replace(self.registry.catalog, map_ids=('b',), resource_key='replacement')
        with patch('s1_structure_selection.StructureCatalog.from_engine', return_value=catalog):
            result = self.runtime.dispatch('capture_now')
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'locked-map-resource-unavailable')
        self.assert_locked()
        self.assertEqual(self.plans, [])
        self.assertIsNone(self.current_payload())
        self.assertIsNone(self.runtime.reference_payload())
        from diagnostic_privacy import safe_record
        self.assertEqual(safe_record({'reason': result['reason']})['reason'], result['reason'])


class ProductionRoundLockContracts(PlaytestRoundLockContracts):
    def setUp(self):
        self.candidate_purpose = purpose._NATIVE_S1_PRODUCTION_CANDIDATE
        fixture.LockedIdentityContracts.setUp(self)


class DifficultPlaytestRoundLockContracts(PlaytestRoundLockContracts):
    def setUp(self):
        super().setUp()
        self.registry.engine.pack.mode = 'difficult'
        self.registry.catalog = replace(self.registry.catalog, mode='difficult')
        self.start_patch(patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.registry.catalog))
        self.assertTrue(self.runtime.dispatch('set_context', dict(season='s1', mode='difficult'))['success'])


class DifficultProductionRoundLockContracts(ProductionRoundLockContracts):
    def setUp(self):
        super().setUp()
        self.registry.engine.pack.mode = 'difficult'
        self.registry.catalog = replace(self.registry.catalog, mode='difficult')
        self.start_patch(patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.registry.catalog))
        self.assertTrue(self.runtime.dispatch('set_context', dict(season='s1', mode='difficult'))['success'])



if __name__ == '__main__':
    unittest.main()
