"""Real resource signal/coordinator/runtime revocation; synthetic capture/IO.

Exercises both source-owned purposes while foreground or background work is
blocked. No game, native window, installed resources or file picker is used.
"""
import threading
import unittest
from unittest.mock import patch

import s1_candidate_purpose as purpose
import test_s1_async_evidence as evidence_fixture
import test_s1_author_settings_chain as chain
import test_s1_formal_round_lock as formal_fixture


class S1ResourceRevocationChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chain.S1AuthorSettingsChainTests.setUpClass()

    def fixture(self, token):
        self.enterContext(patch.object(evidence_fixture.AsyncEvidenceContracts,
            'candidate_purpose', token, create=True))
        if purpose.native_final_round_lock_purpose_is_valid(token):
            # The historical private helper asserts identityLocked is always
            # false; use the formal helper when exercising the formal token.
            self.enterContext(patch.object(evidence_fixture.AsyncEvidenceContracts,
                'show', formal_fixture.FormalRoundLockContracts.show))
        fx = chain.S1AuthorSettingsChainTests('runTest')
        self.addCleanup(fx.doCleanups)
        fx.setUp()
        return fx

    def emit_resource_change(self, fx):
        runtime, coordinator = fx.runtime, fx.coordinator
        invalidate = runtime.invalidate
        calls = []
        def checked(reason, **kwargs):
            self.assertEqual(reason, 'resources-changed')
            self.assertFalse(coordinator.alignedData)
            self.assertFalse(coordinator.referenceData)
            calls.append(reason)
            return invalidate(reason, **kwargs)
        with patch.object(runtime, 'invalidate', side_effect=checked):
            coordinator.resources.resourcesChanged.emit()
        self.assertEqual(calls, ['resources-changed'])

    def assert_revoked(self, fx, tokens):
        fx.app.processEvents()
        self.assertFalse(fx.coordinator.alignedData)
        self.assertFalse(fx.coordinator.referenceData)
        self.assertIsNone(fx.runtime._s1_private_pool)
        self.assertIsNone(fx.runtime._s1_structure_ticket)
        self.assertIsNone(fx.runtime.reference_payload())
        self.assertFalse(fx.runtime.snapshot()['identityLocked'])
        for token in tokens:
            self.assertIsNone(fx.runtime.render_payload(token))
            self.assertFalse(fx.runtime.acknowledge_render_material(token)['success'])

    def test_resource_signal_clears_both_views_before_revoking_runtime_authority(self):
        for token in (purpose._NATIVE_S1_PREVIEW, purpose._NATIVE_S1_PLAYTEST, purpose._NATIVE_S1_PRODUCTION_CANDIDATE):
            with self.subTest(formal=token is purpose._NATIVE_S1_PRODUCTION_CANDIDATE):
                fx = self.fixture(token)
                old = fx.runtime.reference_payload()['token']
                self.assertTrue(fx.coordinator.alignedData)
                self.assertTrue(fx.coordinator.referenceData)
                self.emit_resource_change(fx)
                self.assert_revoked(fx, [old])

    def test_blocked_background_evidence_cannot_restore_pool_or_closed_static_sheet(self):
        for token in (purpose._NATIVE_S1_PREVIEW, purpose._NATIVE_S1_PLAYTEST, purpose._NATIVE_S1_PRODUCTION_CANDIDATE):
            with self.subTest(formal=token is purpose._NATIVE_S1_PRODUCTION_CANDIDATE):
                fx = self.fixture(token)
                # The fixture starts locked. Production correctly stops new
                # identity jobs at lock, so admit this task in a fresh round.
                fx.runtime.dispatch('end_round')
                fx.session.refreshRuntime()
                self.assertIsNone(fx.runtime._s1_private_pool)
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                cancelled_after_boundary = []
                def delayed(frame, ids, floor, *, cancelled, **kwargs):
                    entered.set()
                    if not release.wait(4):
                        raise TimeoutError('background fixture was not released')
                    cancelled_after_boundary.append(cancelled())
                    return fx.fx.report(ids, floor, bad=('a',))
                fx.fx.registry.engine.qt_s1_private_coordinate_prepare = delayed
                shown = fx.fx.show()
                self.assertTrue(entered.wait(4))
                fx.runtime.dispatch('close_map')
                fx.session.refreshRuntime()
                self.assertTrue(fx.coordinator.referenceData)
                pool = fx.runtime._s1_private_pool
                previous = pool.snapshot()
                try:
                    self.emit_resource_change(fx)
                    self.assert_revoked(fx, [shown['token']])
                finally:
                    release.set()
                    fx.fx.idle()
                self.assertEqual(cancelled_after_boundary, [True])
                self.assertEqual(pool.snapshot(), previous)
                self.assert_revoked(fx, [shown['token']])

    def test_blocked_foreground_material_cannot_publish_after_resource_signal(self):
        for token in (purpose._NATIVE_S1_PREVIEW, purpose._NATIVE_S1_PLAYTEST, purpose._NATIVE_S1_PRODUCTION_CANDIDATE):
            with self.subTest(formal=token is purpose._NATIVE_S1_PRODUCTION_CANDIDATE):
                fx = self.fixture(token)
                old = fx.runtime.reference_payload()['token']
                entered, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                original = fx.runtime._display_layer
                def delayed(*args, **kwargs):
                    entered.set()
                    if not release.wait(4):
                        raise TimeoutError('foreground fixture was not released')
                    return original(*args, **kwargs)
                results = []
                with patch.object(fx.runtime, '_display_layer', side_effect=delayed):
                    worker = threading.Thread(target=lambda: results.append(fx.runtime.dispatch('capture_now')))
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(4))
                        self.emit_resource_change(fx)
                        self.assert_revoked(fx, [old])
                    finally:
                        release.set()
                        worker.join(4)
                self.assertFalse(worker.is_alive())
                self.assertFalse(results[0]['success'])
                self.assert_revoked(fx, [old])
