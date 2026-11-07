"""Real baked feature-test composition using synthetic capture/material boundaries.

Run the complete shared playtest lifecycle matrix against this package identity,
not a direct capability token. These checks do not operate a game or install.
"""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import build_qt_client as build
import s1_candidate_purpose as purpose
import test_s1_locked_identity as fixture
import test_s1_playtest_round_lock as shared


class FeatureTestRoundLockContracts(shared.PlaytestRoundLockContracts):
    def setUp(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'installation-profile.json').write_text(json.dumps(dict(
                product=build.PRODUCT,
                installerInstance=dict(id='feature-test', **build.INSTANCES['feature-test']))))
            with patch.object(sys, 'frozen', True, create=True), \
                 patch.object(sys, '_MEIPASS', directory, create=True):
                self.candidate_purpose = purpose.packaged_native_candidate_purpose()
        fixture.LockedIdentityContracts.setUp(self)

    def test_feature_identity_has_final_lock_but_not_production_identity(self):
        self.assertTrue(purpose.private_native_candidate_purpose_is_valid(self.candidate_purpose))
        self.assertTrue(purpose.native_final_round_lock_purpose_is_valid(self.candidate_purpose))
        self.assertIsNot(self.candidate_purpose, purpose._NATIVE_S1_PRODUCTION_CANDIDATE)
        self.assertIsNot(self.candidate_purpose, purpose._NATIVE_S1_PREVIEW)

    def test_feature_idle_map_edges_never_start_before_or_after_round(self):
        for opening in (True, False):
            result = self.runtime.dispatch('map', {'openIntent': opening})
            self.assertEqual(result['reason'], 'round-not-started')
            self.assertFalse(self.runtime.snapshot()['roundActive'])
            self.assertFalse(self.runtime.snapshot()['roundPreparing'])
        self.locked()
        self.runtime.dispatch('end_round')
        round_id = self.runtime.snapshot()['roundId']
        for opening in (True, False):
            result = self.runtime.dispatch('map', {'openIntent': opening})
            self.assertEqual(result['reason'], 'round-not-started')
            self.assertEqual(self.runtime.snapshot()['roundId'], round_id)
            self.assertFalse(self.runtime.snapshot()['identityLocked'])

    def test_legacy_auto_experiment_and_other_profiles_are_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory, 'installation-profile.json')
            for name in ('s1-auto-test', 's0-s1-test', 's1-floor-test', 'client'):
                profile.write_text(json.dumps(dict(product=build.PRODUCT,
                    installerInstance=dict(id=name, **build.INSTANCES[name]),
                    features={'finalRoundLock': True})))
                with self.subTest(name=name), patch.object(sys, 'frozen', True, create=True), \
                     patch.object(sys, '_MEIPASS', directory, create=True):
                    value = purpose.packaged_native_candidate_purpose()
                    self.assertFalse(purpose.native_final_round_lock_purpose_is_valid(value))
                    if name == 's1-auto-test':
                        self.assertIs(value, purpose._NATIVE_S1_PREVIEW)
                    else:
                        self.assertIsNone(value)


class DifficultFeatureTestRoundLockContracts(FeatureTestRoundLockContracts):
    def setUp(self):
        from dataclasses import replace
        super().setUp()
        self.registry.engine.pack.mode = 'difficult'
        self.registry.catalog = replace(self.registry.catalog, mode='difficult')
        self.start_patch(patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.registry.catalog))
        self.assertTrue(self.runtime.dispatch('set_context', dict(season='s1', mode='difficult'))['success'])
