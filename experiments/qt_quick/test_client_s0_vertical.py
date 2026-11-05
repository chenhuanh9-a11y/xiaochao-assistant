"""Synthetic lifecycle only; real pixel replay is an external input script."""
import copy
import unittest
from unittest.mock import Mock,patch
import test_client_live_runtime as fixtures
from test_s0_vertical_controls import synthetic_read


class VerticalRuntimeContracts(unittest.TestCase):
    setUp=fixtures.LiveRuntimeContracts.setUp
    dispatch=fixtures.LiveRuntimeContracts.dispatch

    def prepare(self,mode="nightmare"):
        self.now=10.;self.runtime._monotonic=lambda:self.now
        self.capture.generation=5
        self.read=synthetic_read(mode)
        p=patch("floor_control_region.locate_vertical_floor_controls",side_effect=lambda *a,**k:copy.deepcopy(self.read))
        p.start();self.addCleanup(p.stop)
        self.engine=fixtures.fixture_engine(mode)
        self.engine.qt_map_ui.observe=Mock(side_effect=AssertionError("legacy template must not own new layout"))
        self.runtime._factories[mode]=lambda:self.engine
        self.assertTrue(self.dispatch("set_game_mode",mode=mode)["success"])
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertTrue(self.dispatch("capture_now")["success"])
        return self.runtime.game_floor_click_context()

    def click(self,floor,context):
        x,y=self.read["centerPoints"][self.read["observedOrder"].index(floor)]
        return self.runtime.observe_game_floor_click(100+x*1280,200+y*720,context)

    def test_click_revokes_and_waits_for_current_matching_highlight(self):
        for mode in ["difficult","nightmare"]:
            with self.subTest(mode=mode):
                if self.runtime.snapshot()["roundActive"]:self.dispatch("end_round")
                context=self.prepare(mode)
                accepted=self.click(2,context)
                self.assertTrue(accepted["success"],accepted)
                self.assertEqual(self.runtime.snapshot()["floor"],"2")
                self.assertTrue(self.runtime.snapshot()["floorRequired"])
                self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
                self.assertFalse(self.dispatch("refresh_map")["success"])
                self.read=synthetic_read(mode,2)
                result=self.dispatch("refresh_map")
                self.assertTrue(result["success"],result)
                self.assertEqual(self.runtime.render_payload(result["renderToken"])["floor"],"2")
                self.assertIsNone(self.runtime._game_floor_intent)

    def test_map_canvas_gate_cannot_be_replaced_by_a_readable_selector(self):
        self.prepare("difficult")
        with patch("map_interface_observation.detect_map_interface",return_value=dict(detected=False)):
            observation,floor,read=self.runtime._observe(self.engine,self.capture.frame)
        self.assertFalse(observation["mapOpen"])
        self.assertIsNone(floor);self.assertIsNone(read["selectedFloor"])
        self.assertFalse(read["controlGeometryVerified"])

    def test_basement_click_keeps_semantic_b1_identity(self):
        context=self.prepare("nightmare")
        accepted=self.click(-1,context)
        self.assertTrue(accepted["success"],accepted)
        self.assertEqual(accepted["clickedFloorId"],"B1")
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(self.dispatch("refresh_map")["success"])
        self.read=synthetic_read("nightmare",-1)
        current=self.dispatch("refresh_map")
        self.assertTrue(current["success"],current)
        self.assertEqual(self.runtime.render_payload(current["renderToken"])["floor"],"B1")

    def test_changed_capture_rejects_current_looking_control(self):
        context=self.prepare()
        self.capture.is_current=lambda *args,**kwargs:False
        epoch=self.runtime._epoch
        self.assertEqual(self.click(2,context)["reason"],"capture-context-changed")
        self.assertEqual(self.runtime._epoch,epoch)

    def test_vertical_precedes_conflicting_legacy_positive(self):
        self.prepare()
        with patch("map_interface_observation.detect_selected_floor",side_effect=AssertionError("legacy conflict")):
            observation,floor,read=self.runtime._observe(self.engine,self.capture.frame)
        self.assertEqual(floor,"1")
        self.assertEqual(read["layout"],"localized-s0-vertical")

    def test_expired_stale_and_closed_contexts_do_not_mutate(self):
        context=self.prepare()
        self.now+=2.001
        self.assertEqual(self.click(2,context)["reason"],"floor-click-expired")
        context=self.runtime.game_floor_click_context()
        self.runtime.invalidate("foreground-lost")
        self.assertFalse(self.click(2,context)["success"])
        self.dispatch("end_round")
        self.assertFalse(self.click(2,context)["success"])

    def test_s1_guard_precedes_any_vertical_sensor(self):
        self.runtime._season="s1"
        with patch("floor_control_region.locate_vertical_floor_controls",side_effect=AssertionError("S1 sensor called")):
            o,f,r=self.runtime._observe(self.engine,self.capture.frame)
        self.assertIsNone(f);self.assertIsNone(o["mapOpen"])
        self.assertEqual(r["reason"],"s1-floor-control-detector-unvalidated")

    def test_horizontal_region_cannot_override_vertical_geometry(self):
        self.prepare()
        from floor_control_region import apply_floor_region
        self.runtime._settings["floorControlMode"]="region"
        self.runtime._settings["floorControlRegion"]=dict(rect=[.7,0,.2,.1],clientSize=[1280,720])
        with patch("floor_control_region.read_floor_in_region",return_value=dict(selectedFloor=2)):
            _,floor,read=self.runtime._observe(self.engine,self.capture.frame)
        self.assertIsNone(floor);self.assertFalse(read["controlGeometryVerified"])


if __name__=="__main__":unittest.main()

