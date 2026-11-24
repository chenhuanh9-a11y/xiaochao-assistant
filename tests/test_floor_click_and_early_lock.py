"""Targeted positive and negative tests for S0/S1 mouse floor clicks and early locking.

Uses synthetic frames/fixtures only; does not claim real-game acceptance.
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

from floor_observation_policy import cursor_in_floor_column, game_floor_control_hit
from nightmare_candidate import map_color_mask
from s0_test_fixtures import synthetic_read
from s0_vertical_controls import bind_control_geometry
from s1_common_coordinate_filter import CommonCoordinateFilter
from s1_floor_observation import ORDER, bind_s1_control_geometry
from test_s1_common_wall_evidence import image as wall_image, room as wall_room


def s1_read(selected=-1, mode="nightmare"):
    points = [[0.86, 0.18 + i * 0.08] for i in range(4)]
    evidence = [
        dict(floor=floor, negative=True, digitAspect=0.35 if i == 0 else 0.5,
             polarity="dark" if floor == selected else "light",
             highlightFill=0.85 if floor == selected else 0.05,
             signBounds=[1630, int(p[1] * 1080), 10, 2],
             digitBounds=[1644, int(p[1] * 1080) - 8, 8, 16])
        for i, (floor, p) in enumerate(zip(ORDER, points))
    ]
    return dict(
        season="s1", mode=mode, reason="s1-floor-observed", selectedFloor=selected,
        observedFloorValidated=True, automaticFloorEligible=True,
        controlAxis="vertical", observedOrder=list(ORDER), mapOpen=True,
        centerPoints=points, centers=points, labelEvidence=evidence,
        controlBounds=[1600, 150, 1700, 500],
    )


class MouseFloorClickFixTests(unittest.TestCase):
    def test_s1_bind_control_geometry_enables_hits_and_fails_closed(self):
        bounds = (100, 200, 2020, 1280)
        for mode in ("nightmare", "difficult"):
            bound = bind_s1_control_geometry(s1_read(-2, mode), mode, (1920, 1080), map_open=True)
            self.assertTrue(bound["controlGeometryVerified"])
            self.assertEqual(bound["layout"], "localized-floor-contract")
            self.assertEqual(bound["controlFrameSize"], [1920, 1080])
            self.assertEqual(len(bound["controlRects"]), 4)
            for floor, (cx, cy) in zip(ORDER, bound["centerPoints"]):
                hit = game_floor_control_hit((100 + cx * 1920, 200 + cy * 1080), bounds, bound)
                self.assertEqual(hit, floor)
            # Frame size mismatch (>1px) must fail closed.
            self.assertIsNone(game_floor_control_hit(
                (100 + 0.86 * 1920, 200 + 0.18 * 1080), (100, 200, 2025, 1280), bound))
            # Clicking in the gap between -1 and -2 buttons is not a hit,
            # but IS inside the floor column so it must not initiate a drag.
            gap_y = 200 + 0.22 * 1080
            gap_x = 100 + 0.86 * 1920
            self.assertIsNone(game_floor_control_hit((gap_x, gap_y), bounds, bound))
            self.assertTrue(cursor_in_floor_column((gap_x, gap_y), bounds, bound))
            self.assertFalse(cursor_in_floor_column((100 + 0.5 * 1920, 200 + 0.5 * 1080), bounds, bound))

        closed = bind_s1_control_geometry(s1_read(-1), "nightmare", (1920, 1080), map_open=False)
        self.assertFalse(closed["controlGeometryVerified"])
        self.assertNotIn("controlRects", closed)
        unvalidated = bind_s1_control_geometry(
            dict(s1_read(-1), observedFloorValidated=False), "nightmare", (1920, 1080), map_open=True)
        self.assertFalse(unvalidated["controlGeometryVerified"])

    def test_s0_widened_hitbox_catches_button_edges_without_overlap(self):
        read = synthetic_read("nightmare", 1)
        bound = bind_control_geometry(read, "nightmare", (1280, 720), map_open=True)
        self.assertTrue(bound["controlGeometryVerified"])
        w = read["tileBounds"][2]
        cx, cy = read["centerPoints"][0]  # floor 2
        bounds = (100, 200, 1380, 920)
        # Edge click at cx + 0.45 * w previously missed (old limit was 0.40 * w).
        self.assertEqual(game_floor_control_hit((100 + (cx + 0.45 * w) * 1280, 200 + cy * 720), bounds, bound), 2)
        rects = [row["rect"] for row in bound["controlRects"]]
        for upper, lower in zip(rects, rects[1:]):
            self.assertLess(upper[3], lower[1])

    def test_pointer_press_allows_gettickcount_quantization_slack_only(self):
        from client_native_keys import PassThroughKeyboardSource
        source = PassThroughKeyboardSource()
        source.update_pointer_target(42)
        source.set_pointer_context_provider(lambda: dict(observationCreatedAt=100.0))
        # 15ms behind observationCreatedAt (within 25ms GetTickCount64 slack) is accepted.
        ok = source._pointer_event(500, 500, 42, created_at=99.985, phase="down")
        self.assertIsNotNone(ok)
        source.cancel_pointer("reset")
        # 40ms behind observationCreatedAt is genuinely stale and rejected.
        stale = source._pointer_event(500, 500, 42, created_at=99.960, phase="down")
        self.assertIsNone(stale)


class EarlyLockFixTests(unittest.TestCase):
    def _compare(self, frame, masks, *, include_difference=True):
        observer = CommonCoordinateFilter.__new__(CommonCoordinateFilter)
        observer.mode, observer.resource_key = "nightmare", "fixture"
        observer.entries = {mid: {} for mid in masks}
        fields = []
        edge_masks = []
        for mask in masks.values():
            edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) > 0
            edge_masks.append(edge.astype(np.uint8))
            fields.append(cv2.distanceTransform((~edge).astype(np.uint8), cv2.DIST_L2, 3))
        data = dict(ids=tuple(masks), prepared=object(), shape=frame.shape[:2],
                    origin=np.zeros(2), fields=fields)
        if include_difference:
            union_m = np.maximum.reduce(edge_masks)
            common_m = np.minimum.reduce(edge_masks)
            data["difference"] = cv2.dilate((union_m != common_m).astype(np.uint8), np.ones((31, 31), np.uint8))
        observer._pool = lambda *args: data
        observer._domains_from_common = lambda *args: [(np.array([[1.0, 0.0, 0.0]]), np.eye(3), {})]
        context = dict(query=map_color_mask(frame, live=True),
                       observationBounds=(0, 0, frame.shape[1], frame.shape[0]))
        return observer.evaluate(frame, [context], tuple(masks), "-1")

    def test_s1_straight_1d_wall_triggers_contradiction_while_icons_do_not(self):
        # Two maps identical except a single straight horizontal partition wall (1D, min(spread) < 12).
        base = wall_room()
        with_bar = base.copy()
        with_bar[105:111, 100:180] = 0  # straight horizontal cutout spanning 80px horizontally, ~6px vertically
        report = self._compare(wall_image(with_bar), {"wrong": base, "matching": with_bar})
        self.assertTrue(report["coordinateVerified"], report)
        self.assertEqual(report["contradictedMapIds"], ["wrong"])
        self.assertEqual(report["unknownMapIds"], ["matching"])

        # Small separated icon strokes still do not contradict any map.
        frame = wall_image(base)
        for x, y in ((145, 105), (164, 105), (145, 124), (164, 124)):
            frame[y:y + 7, x:x + 5] = 230
        icon_report = self._compare(frame, {"a": base, "b": base.copy()}, include_difference=False)
        self.assertTrue(icon_report["coordinateVerified"], icon_report)
        self.assertEqual(icon_report["contradictedMapIds"], [])

    def test_s0_main_entry_verifies_up_to_six_competitors_and_locks_with_four_neighbors(self):
        from difficult_entry_identity import MapEntryGeometryIdentity

        def build_identity(map_count):
            inst = MapEntryGeometryIdentity.__new__(MapEntryGeometryIdentity)
            inst.profile_id = "native"
            inst.profile_for_map = lambda mid: "native"
            inst.error = None
            inst.entries = []
            # Build 8 non-periodic synthetic query-compatible chains (3 main + 5 neighbor).
            centers = np.array([
                [10.0, 10.0], [32.0, 14.0], [51.0, 29.0],
                [77.0, 12.0], [95.0, 48.0], [118.0, 21.0], [141.0, 67.0], [163.0, 35.0],
            ], dtype=float)
            chains = np.stack([centers - [10.0, 0.0], centers, centers + [0.0, 10.0]], axis=1)
            rays = np.tile(np.array([[[1.0, 0.0], [0.0, 1.0]]]), (len(centers), 1, 1))
            main = np.array([True, True, True, False, False, False, False, False])
            for idx in range(map_count):
                item = dict(mapId=f"map-{idx}", floor="1", unit=20.0, center=[20.0, 0.0],
                            material="main", sourceProduct="shared")
                inst.entries.append((item, chains.copy(), rays.copy(), main.copy()))
            inst.geometry = lambda frame: dict(query=np.ones((200, 200), np.uint8))
            return inst, centers, rays

        frame = np.zeros((200, 200, 3), np.uint8)
        # 5 competitors (<= 6): verifies all 5 and locks the unique geometry winner with mainChains=3, neighborChains=5, score=8.
        inst5, centers, rays = build_identity(5)
        verified_ids = []
        def verify5(f, row, **kw):
            verified_ids.append(row["mapId"])
            return dict(success=row["mapId"] == "map-2", reason="verified" if row["mapId"] == "map-2" else "mismatch")
        inst5.verify = verify5
        nodes = [dict(point=p, rays=r, lengths=[20.0, 20.0]) for p, r in zip(centers, rays)]
        with patch("difficult_entry_identity.entry_corners", return_value=nodes):
            rows, diag = inst5.rank(frame, "1", budget_seconds=2.0)
        self.assertEqual(len(verified_ids), 5)
        self.assertEqual(diag["reason"], "entry-identity-verified")
        winners = [r for r in rows if r["identityVerified"]]
        self.assertEqual([w["mapId"] for w in winners], ["map-2"])

        # 7 competitors (> 6): stays unverified.
        inst7, _, _ = build_identity(7)
        inst7.verify = Mock(side_effect=AssertionError("should not verify > 6 competitors"))
        with patch("difficult_entry_identity.entry_corners", return_value=nodes):
            rows7, diag7 = inst7.rank(frame, "1", budget_seconds=2.0)
        self.assertFalse(any(r["identityVerified"] for r in rows7))
        self.assertEqual(diag7["reason"], "entry-neighborhood-candidates")

    def test_s1_live_runtime_mouse_floor_click_and_singleton_lock_confirm(self):
        from test_s1_formal_round_lock import FormalRoundLockContracts
        suite = FormalRoundLockContracts("runTest")
        suite.setUp()
        self.addCleanup(suite.doCleanups)
        # Replace _observe to supply verified S1 click geometry matching suite.floor.
        def observe_with_geometry(engine, frame):
            selected = int(suite.floor)
            read = bind_s1_control_geometry(s1_read(selected, "nightmare"), "nightmare",
                (frame.shape[1], frame.shape[0]), map_open=True)
            obs = dict(detected=True, mapOpen=True, floor=suite.floor, layoutFamily="s1-vertical", controlAxis="vertical")
            return obs, suite.floor, read

        suite.runtime._observe = observe_with_geometry
        suite.runtime._monotonic = lambda: 50.0
        first = suite.show()
        suite.idle()
        # Background narrowed pool to ('a',), still unlocked before confirm.
        self.assertEqual(suite.runtime._s1_private_pool.remaining, ("a",))
        self.assertFalse(suite.runtime.snapshot()["identityLocked"])
        # Same-floor observe_candidate_floor confirms singleton and locks immediately!
        confirm = suite.runtime.dispatch("observe_candidate_floor")
        self.assertTrue(confirm["success"], confirm)
        self.assertTrue(suite.runtime.snapshot()["identityLocked"])
        self.assertEqual(suite.runtime.snapshot()["durableMapId"], "a")

        # Now click floor -2 on the S1 column!
        ctx = suite.runtime.game_floor_click_context()
        self.assertIsNotNone(ctx)
        # Button -2 is at normalized (0.86, 0.26) inside clientBounds (100, 200, 1380, 920) -> wait: frame is 1280x720!
        left, top, right, bottom = ctx["clientBounds"]
        x = left + 0.86 * (right - left)
        y = top + 0.26 * (bottom - top)
        clicked = suite.runtime.observe_game_floor_click(x, y, ctx)
        self.assertTrue(clicked["success"], clicked)
        self.assertEqual(clicked["clickedFloor"], -2)
        self.assertTrue(suite.runtime.has_pending_floor_intent())
        # Probe while game highlight is still -1 defers cleanly without clearing intent.
        deferred = suite.runtime.dispatch("observe_candidate_floor")
        self.assertFalse(deferred["success"])
        self.assertEqual(deferred["reason"], "floor-policy-deferred")
        self.assertTrue(suite.runtime.has_pending_floor_intent())
        # Game switches highlight to -2 -> probe switches & renders -2 immediately!
        suite.floor = "-2"
        switched = suite.runtime.dispatch("observe_candidate_floor")
        self.assertTrue(switched["success"], switched)
        self.assertFalse(suite.runtime.has_pending_floor_intent())
        self.assertEqual(suite.runtime.snapshot()["floor"], "-2")
        self.assertEqual(suite.current_payload()["floor"], "-2")


if __name__ == "__main__":
    unittest.main()
