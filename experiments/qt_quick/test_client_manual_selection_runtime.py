"""Actual Session worker queue + shared pose core with synthetic frame/fit seams."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from client_live_runtime import LiveRuntime, _AlignmentHost
from client_session import SessionService
from manual_selection_model import manual_selection_scope
import test_client_live_runtime as fixtures
from test_client_session import until


class ManualRuntimeTests(unittest.TestCase):
    def setup_runtime(self, mode):
        fx = fixtures.LiveRuntimeContracts(); fx.setUp()
        self.addCleanup(fx.doCleanups)
        engine = fixtures.fixture_engine(mode)
        engine.pack.default_entry_floor = "1"
        second = SimpleNamespace(id="second-map")
        engine.pack.maps.append(second); engine.pack.by_id[second.id] = second
        engine.rank = Mock(side_effect=AssertionError("manual mode must not rank identity"))
        runtime = LiveRuntime(engine_factories={mode: lambda: engine}, capture_provider=fx.capture,
                              evidence_kind="synthetic", clock=lambda: 1.)
        runtime.dispatch("set_context", dict(mode=mode))
        runtime.configure_settings(dict(mapSelectionMode="manual"))
        session = SessionService(runtime)
        self.addCleanup(session.shutdown)
        self.addCleanup(lambda: runtime.dispatch("end_round"))
        def drive(action, **payload):
            session.dispatchWithPayload(action, payload)
            until(lambda: not session.busy)
            receipt = session.snapshot["lastReceipt"]
            self.assertEqual(receipt.get("action"), action, session.snapshot)
            return receipt
        def choose(map_id):
            return drive("select_manual_map", mapId=map_id, **manual_selection_scope(session.snapshot).payload())
        return fx, engine, runtime, session, drive, choose

    def test_both_modes_preselect_before_capture_atomic_material_commit_and_floor_capture_continue(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                fx, engine, runtime, session, drive, choose = self.setup_runtime(mode)
                workers = []
                original = runtime.dispatch
                runtime.dispatch = lambda *a, **k: (workers.append(threading.get_ident()), original(*a, **k))[1]
                self.assertTrue(drive("start_round")["success"])
                self.assertTrue(session.snapshot["roundPrepared"])
                request = choose("second-map")
                self.assertTrue(request["success"], request)
                self.assertEqual(fx.capture.count, 0)
                self.assertEqual(session.snapshot["mapId"], "")
                result = drive("capture_now")
                self.assertTrue(result["success"], result)
                token = result["pendingSiblingToken"]
                self.assertEqual(session.snapshot["mapId"], "")
                self.assertIsNone(runtime.reference_payload())
                self.assertIsNone(runtime.render_payload(token))
                pending = runtime.pending_sibling_payload(token)
                self.assertEqual((pending["mapId"], pending["floor"]), ("second-map", "1"))
                self.assertFalse(pending["liveAuthorization"])
                self.assertTrue(runtime.acknowledge_sibling(token, True)["success"])
                session.refreshRuntime()
                self.assertEqual(session.snapshot["mapId"], "second-map")
                self.assertEqual(session.snapshot["manualSelectedMapId"], "second-map")
                for material in (runtime.render_payload(token), runtime.reference_payload()):
                    self.assertEqual((material["mapId"], material["floor"]), ("second-map", "1"))
                    self.assertEqual(material["identitySource"], "manual-map-selection")
                engine.rank.assert_not_called()
                drive("close_map")
                self.assertFalse(session.snapshot["mapOpen"])
                self.assertIsNone(runtime.render_payload(token))
                self.assertEqual(runtime.reference_payload()["mapId"], "second-map")
                if mode == "difficult":
                    engine.qt_map_ui.observe = lambda *a: dict(mapOpen=True, floor="2")
                with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=2)):
                    reopened = drive("capture_now")
                self.assertTrue(reopened["success"], reopened)
                self.assertEqual(runtime.reference_payload()["floor"], "2")
                self.assertEqual(runtime.render_payload(reopened["renderToken"])["floor"], "2")
                engine.rank.assert_not_called()
                self.assertEqual(len(set(workers)), 1)
                self.assertNotEqual(workers[0], threading.get_ident())
                drive("end_round")
                self.assertEqual(session.snapshot["manualSelectedMapId"], "")
                self.assertEqual(session.snapshot["desiredManualMapId"], "")
                self.assertEqual(session.snapshot["mapSelectionMode"], "manual")
                self.assertFalse(runtime.acknowledge_sibling(token, True)["success"])

    def test_both_modes_wait_for_pick_failure_replacement_and_late_ack_are_safe(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                fx, engine, runtime, session, drive, choose = self.setup_runtime(mode)
                drive("start_round")
                waiting = drive("capture_now")
                self.assertEqual(waiting["reason"], "manual-map-selection-required")
                self.assertTrue(session.snapshot["mapOpen"])
                self.assertEqual(session.snapshot["floor"], "1")
                engine.rank.assert_not_called()
                good = deepcopy(fx.fit)
                fx.fit = dict(success=False, reason="initial-structure-ambiguous")
                failed = choose("second-map")
                self.assertTrue(failed["success"]); self.assertFalse(failed["choiceApplied"])
                self.assertIsNone(runtime.reference_payload())
                self.assertFalse(session.snapshot["displayAuthorized"])
                self.assertEqual(session.snapshot["mapId"], "")
                fx.fit = good
                staged = drive("refresh_map")["pendingSiblingToken"]
                count = fx.capture.count
                self.assertTrue(drive("refresh_map")["coalesced"])
                self.assertEqual(fx.capture.count, count)
                replaced = choose("fixture-map")["pendingSiblingToken"]
                self.assertFalse(runtime.acknowledge_sibling(staged, True)["success"])
                self.assertTrue(runtime.acknowledge_sibling(replaced, True)["success"])
                session.refreshRuntime()
                self.assertEqual(session.snapshot["mapId"], "fixture-map")
                next_token = choose("second-map")["pendingSiblingToken"]
                self.assertEqual(session.snapshot["mapId"], "fixture-map")
                self.assertIsNone(runtime.reference_payload())
                self.assertIsNone(runtime.render_payload(replaced))
                drive("close_map")
                self.assertFalse(runtime.acknowledge_sibling(next_token, True)["success"])
                self.assertEqual(runtime.snapshot()["mapId"], "fixture-map")
                fresh = drive("capture_now")["pendingSiblingToken"]
                drive("end_round")
                self.assertFalse(runtime.acknowledge_sibling(fresh, True)["success"])
                self.assertIsNone(runtime.reference_payload())

    def test_default_auto_and_difficult_f2_policy_unchanged_and_manual_explicit_pick_allowed(self):
        fx, engine, runtime, session, drive, choose = self.setup_runtime("difficult")
        self.assertTrue(drive("set_map_selection_mode", selectionMode="auto")["success"])
        engine.qt_map_ui.observe = lambda *a: dict(mapOpen=True, floor="2")
        drive("start_round")
        result = drive("capture_now")
        self.assertEqual(result["reason"], "difficult-f2-entry-recognition-disabled")
        engine.rank.assert_not_called()
        drive("set_map_selection_mode", selectionMode="manual")
        result = choose("second-map")
        token = result["pendingSiblingToken"]
        self.assertTrue(runtime.acknowledge_sibling(token, True)["success"])
        self.assertEqual(runtime.reference_payload()["floor"], "2")
        engine.rank.assert_not_called()
        fresh = LiveRuntime(engine_factories={})
        self.assertEqual(fresh.snapshot()["mapSelectionMode"], "auto")

    def test_both_modes_manual_pick_after_drag_retires_old_offset_before_fitting_new_map(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                fx, engine, runtime, session, drive, choose = self.setup_runtime(mode)
                drive("start_round"); drive("capture_now")
                first = choose("fixture-map")["pendingSiblingToken"]
                self.assertTrue(runtime.acknowledge_sibling(first, True)["success"])
                session.refreshRuntime()
                context = runtime.game_floor_click_context()
                for phase, sequence, total, delta in (
                        ("down", 1, [0., 0.], [0., 0.]),
                        ("move", 2, [13., -7.], [13., -7.]),
                        ("up", 3, [13., -7.], [0., 0.])):
                    moved = runtime.observe_game_drag(dict(phase=phase, gestureId="manual-pick-drag",
                        pointerSequence=sequence, context=context, createdAt=1., totalDelta=total, delta=delta))
                    self.assertTrue(moved["success"], moved)
                self.assertTrue(runtime._transient_cycle_current())
                session.refreshRuntime()
                def fit_new_map(*args):
                    self.assertEqual(runtime.snapshot()["mapId"], "fixture-map")
                    self.assertIsNone(runtime._transient_cycle)
                    return deepcopy(fx.fit)
                with patch.object(_AlignmentHost, "fit", side_effect=fit_new_map) as fit, \
                        patch.object(runtime, "_try_transient_floor_projection",
                                     side_effect=AssertionError("new selection must not project the old drag")):
                    result = choose("second-map")
                self.assertTrue(result["success"], result)
                fit.assert_called_once()
                token = result["pendingSiblingToken"]
                self.assertEqual(session.snapshot["mapId"], "fixture-map")
                self.assertIsNone(runtime.reference_payload())
                self.assertIsNone(runtime.render_payload(first))
                pending = runtime.pending_sibling_payload(token)
                self.assertEqual((pending["mapId"], pending["floor"]), ("second-map", "1"))
                self.assertEqual(pending["matrix"], fx.fit["matrixSourceToClient"].tolist())
                self.assertTrue(runtime.acknowledge_sibling(token, True)["success"])
                session.refreshRuntime()
                self.assertEqual(session.snapshot["mapId"], "second-map")
                self.assertEqual(runtime.reference_payload()["mapId"], "second-map")
                self.assertFalse(runtime._transient_cycle_current())
                engine.rank.assert_not_called()

    def test_actual_queue_cancels_delayed_selection_before_end_and_settings_refresh_keeps_new_preference(self):
        fx, engine, runtime, session, drive, choose = self.setup_runtime("nightmare")
        runtime.configure_settings(dict(mapSelectionMode="auto", sheetOpacity=.5))
        session.refreshRuntime()
        self.assertTrue(drive("set_map_selection_mode", selectionMode="manual")["success"])
        # An unrelated settings save still carries the previous persisted mode.
        runtime.configure_settings(dict(mapSelectionMode="auto", sheetOpacity=.7))
        self.assertEqual(runtime.snapshot()["mapSelectionMode"], "manual")
        runtime.configure_settings(dict(mapSelectionMode="manual", sheetOpacity=.7))
        drive("start_round"); drive("capture_now")
        entered, release = threading.Event(), threading.Event()
        def delayed_fit(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test release was not delivered")
            raise RuntimeError("late solver error")
        request = dict(manual_selection_scope(session.snapshot).payload(), mapId="second-map")
        with patch.object(_AlignmentHost, "fit", side_effect=delayed_fit):
            session.dispatchWithPayload("select_manual_map", request)
            until(entered.is_set)
            session.dispatch("end_round")
            release.set()
            until(lambda: not session.busy and not session._jobs)
        self.assertEqual(session.snapshot["lastReceipt"]["action"], "end_round")
        self.assertFalse(session.snapshot["roundActive"])
        self.assertEqual(session.snapshot["manualSelectedMapId"], "")
        self.assertEqual(session.snapshot["desiredManualMapId"], "")
        self.assertEqual(session.snapshot["mapSelectionMode"], "manual")
        self.assertGreaterEqual(session.stale_results, 1)
        self.assertIsNone(runtime.reference_payload())
        self.assertFalse(runtime.snapshot()["displayAuthorized"])


if __name__ == "__main__":
    unittest.main()
