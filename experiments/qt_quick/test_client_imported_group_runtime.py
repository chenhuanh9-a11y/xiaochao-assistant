"""Production imported S0 group resources through LiveRuntime.

The source ZIP, archive save, activation, engine factory, registry, provider and
reader are real. Frame pixels, the single ranked front candidate, current-floor
observation and alignment fit are controlled test boundaries; this is not game
recognition or acceptance evidence.
"""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtCore import QCoreApplication

import client_imported_activation as activation
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_live_runtime import LiveRuntime, _AlignmentHost
from client_paths import RuntimePaths
from client_session import SessionService
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from resource_transaction import resource_runtime_lease
from s0_nightmare_group_source_fixture import S0NightmareGroupSourceFixture
from test_client_live_runtime import FixtureCapture


def _ranked_front_candidate():
    map_id = "nightmare-04"
    return dict(
        mapId=map_id,
        entryKind="main",
        score=1210.0,
        strongSuggestion=True,
        identityHardGatePassed=True,
        identityEvidence="tilemap-entry-neighborhood-current-frame",
        difficultEntry=dict(
            mapId=map_id,
            entryKind="main",
            profileId="shared-structure",
            verification=dict(success=True, matrixSourceToClient=np.eye(3).tolist()),
        ),
    )


class ImportedGroupRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory(prefix="imported-group-runtime-")
        cls.root = Path(cls._temporary.name).resolve()
        cls.source = S0NightmareGroupSourceFixture(cls.root / "source")
        report = cls.source.export()
        if (report["mapCount"], report["typedLayerCount"], report["reviewedGroupCount"]) != (26, 78, 3):
            raise AssertionError(f"unexpected S0N source fixture report: {report}")

        cls.paths = RuntimePaths(cls.root / "client", False)
        cls.resolver = ResourceResolver(cls.paths.imported_resource_root,
                                        supported_capabilities=SUPPORTED_CAPABILITIES)
        saved = save_archive(cls.source.output, cls.resolver.root)
        refs = {ref.component_id: ref for ref in saved.component_refs}
        with resource_runtime_lease(cls.resolver.root):
            activation.activate_context(cls.resolver, cls.paths, refs["base"], refs["controls"])
        factories = build_imported_engine_factories(cls.resolver, cls.paths)
        cls.engine = factories.get_engine("nightmare")
        cls.registry = cls.engine.qt_logical_group_registry
        if cls.registry is None or cls.registry.file_ref is None:
            raise AssertionError("exported base did not produce its real reviewed-group registry")
        cls.registry_path = (
            cls.resolver.root / "packages" / cls.registry.file_ref.component.package_id
            / cls.registry.file_ref.component.resource_version / "data"
            / cls.registry.file_ref.component.component_id / cls.registry.file_ref.path
        )

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def setUp(self):
        self.capture = FixtureCapture()
        self.fit = dict(
            success=True,
            matrixSourceToClient=np.array([[0.5, 0.0, 40.0], [0.0, 0.5, 60.0], [0.0, 0.0, 1.0]]),
            selectedRouteCurrentFrameResidual=dict(
                verified=True,
                poseVerifiedAgainstCanonicalGeometry=True,
                profileRenderDerivedFromCanonical=True,
                coordinatePackId=self.engine.qt_adapters.canonical_pack_id,
                validationBasis="shared-canonical-geometry-current-frame",
            ),
        )
        self.runtime = LiveRuntime(
            engine_factories={"nightmare": lambda: self.engine},
            capture_provider=self.capture,
            evidence_kind="synthetic",
            clock=lambda: 1.0,
        )
        self.addCleanup(self.runtime.dispatch, "end_round", {})

        self._patches = [
            patch.object(self.engine, "rank", Mock(side_effect=self._rank)),
            patch("s0_vertical_controls.observe_s0_map", side_effect=self._observe_s0_map),
            patch.object(_AlignmentHost, "fit", side_effect=lambda *args, **kwargs: dict(self.fit)),
        ]
        for item in self._patches:
            item.start()
            self.addCleanup(item.stop)
        self.dispatch("set_context", season="s0", mode="nightmare")

    def _rank(self, *args, **kwargs):
        # The only injected map evidence is the front candidate 04. Historical
        # group members are deliberately absent from rank output.
        self.ranked_rows = [_ranked_front_candidate()]
        return self.ranked_rows

    @staticmethod
    def _observe_s0_map(frame, mode, **kwargs):
        return (
            dict(detected=True, mapOpen=True, floor="1", layoutFamily="s0-vertical"),
            dict(selectedFloor=1, season="s0", mode="nightmare", controlAxis="vertical",
                 mapOpen=True, floorSource="synthetic-current-frame"),
        )

    def dispatch(self, action, **payload):
        state = self.runtime.snapshot()
        context = {key: state[key] for key in ("mode", "profileId", "roundId", "frameId")}
        context.update(generation=22, **payload)
        return self.runtime.dispatch(action, context)

    def begin_and_capture(self):
        self.assertTrue(self.dispatch("start_round")["success"])
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        return result

    def choose_member(self, map_id):
        state = self.runtime.snapshot()
        return self.dispatch("select_group_member", mapId=map_id, **{
            key: state[key] for key in ("groupId", "groupGeneration", "cycleId", "floor")
        })

    def test_runtime_locks_members_from_actual_package_registry(self):
        result = self.begin_and_capture()
        state = self.runtime.snapshot()
        self.assertEqual(state["identityPhase"], "GROUP_LOCKED")
        self.assertEqual(state["groupId"], "confirmed-front:nightmare:04-12-19")
        self.assertEqual(set(state["groupMemberIds"]), {
            "nightmare-04", "nightmare-12", "nightmare-19",
        })
        self.assertEqual(self.runtime.render_payload(result["renderToken"])["mapId"], "nightmare-04")
        self.assertEqual(self.runtime._identity_native.resource_key, self.registry.index_digest)
        self.assertEqual(self.engine.rank.call_count, 1)
        self.assertEqual([row["mapId"] for row in self.ranked_rows], ["nightmare-04"])

    def test_damaged_package_registry_blocks_later_manual_member_confirmation(self):
        self.begin_and_capture()
        original = self.registry_path.read_bytes()
        self.registry_path.write_bytes(b"{damaged reviewed group table")
        try:
            result = self.choose_member("nightmare-12")
            self.assertFalse(result["success"], result)
            self.assertEqual(result["reason"], "registered-group-resource-unavailable")
            self.assertNotIn("pendingSiblingToken", result)
            self.assertNotEqual(self.runtime.snapshot()["mapId"], "nightmare-12")
        finally:
            self.registry_path.write_bytes(original)

    def _assert_cancelled_registry_choice_can_retry(self, read_failed):
        self.begin_and_capture()
        cancelled = threading.Event()
        validate = self.registry.validate
        def cancel_after_read(*args, **kwargs):
            validate(*args, **kwargs)
            cancelled.set()
            if read_failed:
                raise ValueError('synthetic-read-failure-after-cancel')
        state = self.runtime.snapshot()
        payload = {key: state[key] for key in (
            'mode', 'profileId', 'roundId', 'frameId', 'groupId',
            'groupGeneration', 'cycleId', 'floor')}
        payload.update(generation=22, mapId='nightmare-12')
        with patch.object(self.registry, 'validate', side_effect=cancel_after_read):
            result = self.runtime.dispatch('select_group_member', payload, cancelled=cancelled.is_set)
        self.assertFalse(result['success'], result)
        self.assertIsNone(self.runtime._group_choice_inflight)
        self.assertFalse(self.runtime.snapshot()['groupMemberSelectionPending'])
        retried = self.choose_member('nightmare-12')
        self.assertTrue(retried['success'], retried)

    def test_cancel_during_registry_read_releases_manual_choice_for_retry(self):
        self._assert_cancelled_registry_choice_can_retry(False)

    def test_cancel_with_registry_read_failure_releases_manual_choice_for_retry(self):
        self._assert_cancelled_registry_choice_can_retry(True)

    def test_pending_member_requires_worker_registry_validation_before_ack(self):
        self.begin_and_capture()
        choice = self.choose_member("nightmare-12")
        self.assertTrue(choice["success"], choice)
        token = choice["pendingSiblingToken"]
        self.assertTrue(self.runtime.sibling_requires_registry_validation(token))

        app = QCoreApplication.instance() or QCoreApplication([])
        session = SessionService(self.runtime)
        receipts, committed, reader_threads = [], [], []
        ui_thread = threading.get_ident()
        validate = self.registry.validate
        def checked_read(*args, **kwargs):
            reader_threads.append(threading.get_ident())
            return validate(*args, **kwargs)
        def acknowledge(actual_token, receipt):
            self.assertEqual(threading.get_ident(), ui_thread)
            receipts.append(receipt)
            if actual_token == token and receipt.get('success'):
                committed.append(self.runtime.acknowledge_sibling(token, True, reason='target-material-ready'))
        session.siblingRegistryValidated.connect(acknowledge)
        try:
            with patch.object(self.registry, 'validate', side_effect=checked_read):
                self.assertTrue(session.dispatchMaintenance('validate_sibling_registry', {'renderToken':token}))
                deadline = time.monotonic() + 8
                while not receipts and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.002)
            self.assertEqual(len(receipts), 1)
            self.assertTrue(receipts[0]['success'], receipts)
            self.assertEqual(receipts[0]['reason'], 'registered-group-resource-verified')
            self.assertTrue(reader_threads)
            self.assertTrue(all(ident != ui_thread for ident in reader_threads))
            self.assertEqual(len(committed), 1)
            self.assertTrue(committed[0]['success'], committed)
            self.assertEqual(self.runtime.snapshot()['mapId'], 'nightmare-12')
        finally:
            session.shutdown()
            app.processEvents()

    def test_registry_damage_after_material_preparation_rejects_ack_without_restoring_old_pixels(self):
        initial = self.begin_and_capture()
        old_token = initial["renderToken"]
        choice = self.choose_member("nightmare-12")
        self.assertTrue(choice["success"], choice)
        token = choice["pendingSiblingToken"]
        self.assertIsNotNone(self.runtime.pending_sibling_payload(token))
        self.assertTrue(self.runtime.sibling_requires_registry_validation(token))

        original = self.registry_path.read_bytes()
        self.registry_path.write_bytes(b"{replaced after material preparation")
        try:
            checked = self.dispatch("validate_sibling_registry", renderToken=token)
            self.assertFalse(checked["success"], checked)
            self.assertEqual(checked["reason"], "registered-group-resource-unavailable")
            committed = self.runtime.acknowledge_sibling(token, True, reason="target-material-ready")
            self.assertFalse(committed["success"], committed)
            self.assertIsNone(self.runtime.render_payload(old_token))
            self.assertNotEqual(self.runtime.snapshot()["mapId"], "nightmare-12")
        finally:
            self.registry_path.write_bytes(original)

    def test_registry_replaced_during_canonical_fit_rejects_new_pose_before_render_authorization(self):
        self.assertTrue(self.dispatch("start_round")["success"])
        original = self.registry_path.read_bytes()

        def fit_then_replace_registry(*args, **kwargs):
            completed_fit = dict(self.fit)
            self.registry_path.write_bytes(b"{registry changed after canonical fit")
            return completed_fit

        fit_hook = Mock(side_effect=fit_then_replace_registry)
        try:
            with patch.object(_AlignmentHost, "fit", fit_hook):
                result = self.dispatch("capture_now")
            self.assertEqual(fit_hook.call_count, 1)
            self.assertFalse(result["success"], result)
            self.assertEqual(result["reason"], "registered-group-resource-unavailable")
            self.assertFalse(result.get("renderToken"))
            self.assertIsNone(self.runtime.render_payload(result.get("renderToken", "")))
            self.assertIsNone(self.runtime._render)
        finally:
            self.registry_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
