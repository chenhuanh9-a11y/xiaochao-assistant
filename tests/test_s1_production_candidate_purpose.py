"""INACTIVE production proposal. Synthetic profile/capture/solver tests only.

Positive cases patch the source rollout constant inside the test. This is not
final native resource, package build, install or game validation evidence.
"""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import build_qt_client as build
import packaged_product_identity as identity
import s1_candidate_purpose as purpose
from client_live_runtime import LiveRuntime
from test_client_live_runtime import fixture_engine
from test_s1_manual_floor_runtime import CurrentFixtureCapture
import test_s1_native_candidate_preview as native

ROOT = Path(__file__).resolve().parents[1]
ROLLOUT = '_PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED'


def profile_for(instance='production'):
    return dict(product=build.INSTANCES[instance]['product'],
                installerInstance=dict(id=instance, **build.INSTANCES[instance]))


class BakedProductionPurposeContracts(unittest.TestCase):
    def setUp(self):
        # Negative cases intentionally start disabled, independently of the release policy.
        self.enterContext(patch.object(purpose, ROLLOUT, False))
        temp = tempfile.TemporaryDirectory(prefix='s1-production-purpose-fixture-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.profile_path = self.root / 'installation-profile.json'
        self.write_profile(profile_for())
        for item in (patch.object(sys, 'frozen', True, create=True),
                     patch.object(sys, '_MEIPASS', str(self.root), create=True)):
            item.start(); self.addCleanup(item.stop)

    def write_profile(self, profile):
        self.profile_path.write_text(json.dumps(profile), encoding='utf8')

    def test_disabled_source_policy_does_not_grant_production_purpose(self):
        self.assertIs(getattr(purpose, ROLLOUT), False)
        self.assertEqual(identity.baked_identity(require_qt=True), 'qt-production')
        self.assertIsNone(purpose.production_native_candidate_purpose())
        self.assertIsNone(purpose.packaged_native_candidate_purpose())
        self.assertFalse(purpose.native_candidate_purpose_is_valid(purpose._NATIVE_S1_PRODUCTION_CANDIDATE))

    def test_exact_production_identity_mints_distinct_narrow_token_only_in_fixture(self):
        with patch.object(purpose, ROLLOUT, True):
            token = purpose.packaged_native_candidate_purpose()
            self.assertIs(token, purpose._NATIVE_S1_PRODUCTION_CANDIDATE)
            self.assertTrue(purpose.native_candidate_purpose_is_valid(token))
            self.assertFalse(purpose.private_native_candidate_purpose_is_valid(token))
            self.assertIsNone(purpose.private_native_candidate_purpose())
        self.assertFalse(purpose.native_candidate_purpose_is_valid(token))

    def test_forged_profile_features_and_environment_cannot_enable_rollout(self):
        profile = profile_for()
        profile.update(s1CandidatePurpose='production', productionS1Enabled=True,
                       liveReady=True, liveValidated=True, candidateReady=True)
        profile['installerInstance']['productionS1Enabled'] = True
        self.write_profile(profile)
        with patch.dict(os.environ, dict(XINGTU_S1_PRODUCTION='1',
                XINGTU_LIFECYCLE_TRACE='1', PRODUCTION_S1_CANDIDATE_ROLLOUT_ENABLED='1')):
            self.assertIsNone(purpose.packaged_native_candidate_purpose())
        for value in ('true', 1, {'enabled': True}):
            with self.subTest(value=value), patch.object(purpose, ROLLOUT, value):
                self.assertIsNone(purpose.production_native_candidate_purpose())

    def test_private_and_formal_profiles_cannot_be_mixed_or_renamed(self):
        mismatches = []
        for key, value in (('id', 's1-auto-test'), ('product', build.PRODUCT),
                           ('appId', build.INSTANCES['s1-auto-test']['appId']),
                           ('appId', 'D9FE6D76-C31A-47AF-9281-B4614C245E92')):
            profile = profile_for(); profile['installerInstance'][key] = value
            mismatches.append(profile)
        profile = profile_for(); profile['product'] = build.PRODUCT
        mismatches.append(profile)
        profile = profile_for(); profile['installerInstance']['id'] = 's1-playtest'
        mismatches.append(profile)
        with patch.object(purpose, ROLLOUT, True):
            for profile in mismatches:
                with self.subTest(profile=profile):
                    self.write_profile(profile)
                    with self.assertRaises(identity.InstallationProfileError):
                        purpose.packaged_native_candidate_purpose()

    def test_exact_private_identity_remains_private_even_if_formal_fixture_enabled(self):
        with patch.object(purpose, ROLLOUT, True):
            for name in ('s0-s1-test', 's1-floor-test', 's1-auto-test'):
                self.write_profile(profile_for(name))
                self.assertIsNone(purpose.production_native_candidate_purpose())
                token = purpose.packaged_native_candidate_purpose()
                self.assertIs(token, purpose._NATIVE_S1_PREVIEW if name == 's1-auto-test' else None)

    def test_source_run_cannot_use_adjacent_baked_profile_as_a_grant(self):
        with patch.object(sys, 'frozen', False), patch.object(purpose, ROLLOUT, True):
            self.assertIsNone(purpose.packaged_native_candidate_purpose())

    def test_missing_malformed_or_linked_baked_profile_fails_closed(self):
        with patch.object(purpose, ROLLOUT, True):
            self.profile_path.unlink()
            with self.assertRaises(identity.InstallationProfileError):
                purpose.packaged_native_candidate_purpose()
            self.profile_path.write_text('{')
            with self.assertRaises(identity.InstallationProfileError):
                purpose.packaged_native_candidate_purpose()
            self.profile_path.unlink()
            target = self.root / 'adjacent-profile.json'
            target.write_text(json.dumps(profile_for()))
            self.profile_path.symlink_to(target)
            with self.assertRaises(identity.InstallationProfileError):
                purpose.packaged_native_candidate_purpose()

    def test_qt_composition_root_uses_explicit_packaged_policy(self):
        module = ast.parse((ROOT / 'experiments/qt_quick/client.py').read_text('utf8'))
        policy_imports = [node for node in ast.walk(module)
            if isinstance(node, ast.ImportFrom) and node.module == 's1_candidate_purpose']
        self.assertEqual([alias.name for node in policy_imports for alias in node.names],
                         ['packaged_native_candidate_purpose'])
        assignment = next(node for node in ast.walk(module) if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'candidate_purpose' for target in node.targets))
        self.assertIsInstance(assignment.value, ast.Call)
        self.assertEqual(assignment.value.func.id, 'packaged_native_candidate_purpose')
        # Actual Qt-free frozen entry/profile check, with a stub client.main.
        # Thus this does not constitute packaged execution or GUI evidence.
        import qt_client_entry as entry
        with patch.dict(sys.modules, {'client': SimpleNamespace(main=purpose.packaged_native_candidate_purpose)}), \
                patch.object(entry, 'report_startup_failure') as failure:
            self.assertIsNone(entry.run())
            with patch.object(purpose, ROLLOUT, True):
                self.assertIs(entry.run(), purpose._NATIVE_S1_PRODUCTION_CANDIDATE)
        failure.assert_not_called()

    def test_production_purpose_never_implicitly_starts_diagnostic_writer(self):
        import client_lifecycle_trace as tracing
        with patch.dict(os.environ, {}, clear=True), patch.object(purpose, ROLLOUT, True), \
                patch.object(tracing, 'LifecycleTrace') as writer:
            self.assertIsNone(tracing.create_lifecycle_trace(SimpleNamespace(),
                candidate_purpose=purpose.packaged_native_candidate_purpose()))
            writer.assert_not_called()
            tracing.create_lifecycle_trace(SimpleNamespace(), candidate_purpose=purpose._NATIVE_S1_PREVIEW)
            writer.assert_called_once()
            writer.reset_mock()
            with patch.dict(os.environ, {tracing.ENVIRONMENT_FLAG: '1'}):
                tracing.create_lifecycle_trace(SimpleNamespace(), candidate_purpose=None)
            writer.assert_called_once()

    def test_production_builder_still_blocks_unreviewed_season_projection(self):
        with self.assertRaisesRegex(ValueError, 'does not support season resources|inventory/version differs'):
            build.production_file_projection(self.root,
                {'seasons/s1/nightmare/native-map-manifest.json': {}})


class ProductionCandidateRuntimeContracts(native.NativeCandidateContracts):
    def setUp(self):
        # Real composition, synthetic baked-profile bytes, test-only rollout.
        self.profile_fixture = BakedProductionPurposeContracts()
        self.profile_fixture.setUp(); self.addCleanup(self.profile_fixture.doCleanups)
        enabled = patch.object(purpose, ROLLOUT, True)
        enabled.start(); self.addCleanup(enabled.stop)
        self.candidate_purpose = purpose.packaged_native_candidate_purpose()
        self.assertIs(self.candidate_purpose, purpose._NATIVE_S1_PRODUCTION_CANDIDATE)
        super().setUp()

    def test_missing_resource_capability_does_not_start_round(self):
        self.registry.capability.clear()
        self.assertNotIn('start_round', self.runtime.snapshot()['availableCommands'])
        result = self.runtime.dispatch('start_round', dict(candidateReady=True, liveReady=True))
        self.assertFalse(result['success'], result)
        self.assertIsNone(self.current_payload())

    def test_missing_resource_provider_does_not_start_round(self):
        with patch.object(self.registry, 'capabilities', return_value={mode: dict(available=False,
                prepared=False, liveReady=False, reason='missing-resource-fixture')
                for mode in ('nightmare', 'difficult', 'difficulty2')}):
            self.assertNotIn('start_round', self.runtime.snapshot()['availableCommands'])
            self.assertFalse(self.runtime.dispatch('start_round')['success'])
            self.assertIsNone(self.current_payload())

    def test_production_candidate_does_not_run_private_elimination_or_lock_identity(self):
        prepare = self.registry.engine.qt_s1_private_coordinate_prepare = Mock(
            side_effect=AssertionError('production must not call private observation'))
        first = self.show()
        self.assertFalse(self.runtime._native_s1_private_experiment)
        self.assertFalse(self.runtime.snapshot()['privateExperiment']['privateExperimental'])
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertFalse(self.runtime._s1_private_jobs.busy)
        self.assertTrue(self.runtime.acknowledge_render_material(first['token'])['success'])
        self.runtime.dispatch('close_map')
        self.assertTrue(self.runtime.dispatch('capture_now')['success'])
        prepare.assert_not_called()
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.assertFalse(self.registry.capability['liveValidated'])

    def test_current_capture_gate_still_rejects_stale_frame(self):
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        with patch.object(self.capture, 'is_current', return_value=False):
            result = self.runtime.dispatch('capture_now')
        self.assertFalse(result['success'], result)
        self.assertIsNone(self.current_payload())

    def test_current_geometry_gate_still_rejects_unverified_fit(self):
        self.fit['selectedRouteCurrentFrameResidual']['verified'] = False
        self.fit['selectedRouteCurrentFrameResidual']['poseVerifiedAgainstCanonicalGeometry'] = False
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        self.runtime.dispatch('capture_now')
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_material_ack_retains_preference_only_and_old_token_cannot_win(self):
        first = self.show()
        self.assertIsNone(self.runtime._s1_candidate_preference)
        with patch.object(self.capture, 'is_current', return_value=False):
            self.assertFalse(self.runtime.acknowledge_render_material(first['token'])['success'])
        self.assertIsNone(self.runtime._s1_candidate_preference)
        ack = self.runtime.acknowledge_render_material(first['token'])
        self.assertTrue(ack['success']); self.assertFalse(ack['needsWorker'])
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.runtime.dispatch('next_candidate')
        second = self.current_payload()
        self.assertNotEqual(first['token'], second['token'])
        self.assertFalse(self.runtime.acknowledge_render_material(first['token'])['success'])
        self.runtime.dispatch('end_round')
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime.acknowledge_render_material(second['token'])['success'])

    def test_s0_capabilities_are_unchanged_for_each_shared_mode(self):
        runtimes = []
        for token in (None, self.candidate_purpose):
            runtime = LiveRuntime(engine_factories={mode: lambda mode=mode: fixture_engine(mode)
                for mode in ('nightmare', 'difficult')}, capture_provider=CurrentFixtureCapture(),
                evidence_kind='live-client-capture', s1_candidate_purpose=token)
            runtime.configure_settings(dict(mapSelectionMode='auto'))
            self.addCleanup(runtime.shutdown); runtimes.append(runtime)
        for mode in ('nightmare', 'difficult'):
            states = []
            for runtime in runtimes:
                self.assertTrue(runtime.dispatch('set_context', dict(season='s0', mode=mode))['success'])
                state = runtime.snapshot()
                self.assertFalse(state['candidateStructureAvailable'])
                self.assertFalse(state['candidateProvisionalDisplayEnabled'])
                self.assertFalse(state['privateExperiment']['privateExperimental'])
                states.append({key: state[key] for key in ('availableCommands', 'liveEngineAvailable',
                    'automaticIdentityAvailable', 'savedFrameCapabilities', 'candidateSupportedFloors')})
            self.assertEqual(states[0], states[1])


if __name__ == '__main__':
    unittest.main()
