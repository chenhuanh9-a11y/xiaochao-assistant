"""Unit tests for per-map relative route length calculation (app/route_length.py)."""
from __future__ import annotations

import math
from types import SimpleNamespace
import unittest

import numpy as np

from custom_maps import FloorGraph, MapGraph
from route_length import (
    UNITS_PER_LONG_EDGE,
    UNIT_LABEL,
    empty_route_length,
    floor_reference_span,
    format_route_length,
    map_reference_span,
    pixels_to_units,
    polyline_pixel_length,
    raw_points_floor_pixels,
    segment_floor_pixels,
    summarize_route_length,
)


class PolylineAndUnitTests(unittest.TestCase):
    def test_empty_single_and_zero_length_polylines(self):
        self.assertEqual(polyline_pixel_length([]), 0.0)
        self.assertEqual(polyline_pixel_length([(10, 20)]), 0.0)
        self.assertEqual(polyline_pixel_length([(10, 20), (10, 20)]), 0.0)
        self.assertEqual(format_route_length(0.0), f"0 {UNIT_LABEL}")
        self.assertEqual(format_route_length(-5.0), f"0 {UNIT_LABEL}")
        self.assertEqual(format_route_length(float("nan")), f"0 {UNIT_LABEL}")

    def test_polyline_euclidean_sum_tuples_and_dicts(self):
        pts = [(0, 0), (30, 40), (30, 140)]
        self.assertAlmostEqual(polyline_pixel_length(pts), 150.0, places=6)
        dict_pts = [{"x": 0, "y": 0}, {"x": 60, "y": 80}, {"x": 120, "y": 80}]
        self.assertAlmostEqual(polyline_pixel_length(dict_pts), 160.0, places=6)

    def test_unit_conversion_per_map_long_edge(self):
        # 1 格 = long_edge / 100
        self.assertEqual(UNITS_PER_LONG_EDGE, 100.0)
        self.assertAlmostEqual(pixels_to_units(200.0, 2000.0), 10.0, places=6)
        self.assertAlmostEqual(pixels_to_units(2000.0, 2000.0), 100.0, places=6)
        self.assertAlmostEqual(pixels_to_units(500.0, 1000.0), 50.0, places=6)
        self.assertEqual(pixels_to_units(500.0, 0.0), 0.0)
        self.assertEqual(pixels_to_units(-10.0, 1000.0), 0.0)
        self.assertEqual(format_route_length(42.36), "42.4 格")
        self.assertEqual(format_route_length(0.02), "0.1 格")

    def test_floor_and_map_reference_span_uses_shared_max_long_edge(self):
        # CustomMapLibrary style (native_shape present)
        f1 = SimpleNamespace(image_size=(120, 90), native_shape=(180, 240))
        self.assertEqual(floor_reference_span(f1), 240.0)
        # ScopedRouteLibrary style (image_size only)
        f2 = SimpleNamespace(image_size=(1680, 2100))
        f3 = SimpleNamespace(image_size=(1980, 1740))
        self.assertEqual(floor_reference_span(f2), 2100.0)
        self.assertEqual(floor_reference_span(f3), 1980.0)

        class FakeLib:
            floor_ids = ("1", "2")
            def floor(self, mid, fid):
                return f2 if fid == "1" else f3
            def map_floors(self, mid):
                return ("1", "2")

        # Both floors share max(2100, 1980) = 2100 so per-floor units are additive.
        self.assertEqual(map_reference_span(FakeLib(), "m1"), 2100.0)


class CrossFloorAndGraphSummaryTests(unittest.TestCase):
    def _two_floor_graph(self) -> MapGraph:
        # Floor 1: (0,0) -60-> (60,0) -60-> (120,0)
        f1 = FloorGraph(
            points=[[0, 0], [60, 0], [120, 0]],
            rooms=["c", "c", "c"],
            links=[[0, 1, "walk"], [1, 2, "walk"]],
        )
        # Floor 2: (1000,1000) -80-> (1080,1000) -60-> (1080,1060)
        # Intentionally placed far away in XY so any accidental Euclidean jump
        # across floors would be huge and immediately caught!
        f2 = FloorGraph(
            points=[[1000, 1000], [1080, 1000], [1080, 1060]],
            rooms=["c", "c", "c"],
            links=[[0, 1, "walk"], [1, 2, "walk"]],
        )
        # Stair connecting floor 1 node 2 (120, 0) <-> floor 2 node 0 (1000, 1000)
        # with large artificial planner cost 500 that MUST NOT enter route length.
        stairs = [
            dict(
                **{
                    "from": dict(floor="1", x=120, y=0),
                    "to": dict(floor="2", x=1000, y=1000),
                },
                cost=500.0,
                bidirectional=True,
            )
        ]
        return MapGraph({"1": f1, "2": f2}, stairs)

    def test_cross_floor_stair_jump_and_cost_are_excluded(self):
        graph = self._two_floor_graph()
        points = [
            dict(floor="1", x=0.0, y=0.0, kind="via", room=None, color="#63CAFF"),
            dict(floor="2", x=1080.0, y=1000.0, kind="via", room=None, color="#63CAFF"),
            dict(floor="2", x=1080.0, y=1060.0, kind="via", room=None, color="#63CAFF"),
        ]
        segments = graph.plan(points)
        # Planner cost includes stair cost 500, while route length MUST NOT!
        self.assertGreater(segments[0]["cost"], 500.0)

        # Reference long edge = 200 px => 1 格 = 2 px.
        # Floor 1 walked: 0->60->120 = 120 px = 60.0 格
        # Floor 2 walked: 1000->1080 (80 px) + 1000->1060 (60 px) = 140 px = 70.0 格
        # Total = 260 px = 130.0 格 (stair jump 1330 px & stair cost 500 excluded!)
        summary = summarize_route_length(
            graph,
            segments,
            ["1", "2"],
            {"1": "一楼", "2": "二楼"},
            reference_long_edge=200.0,
            points=points,
        )
        self.assertAlmostEqual(summary["totalPixels"], 260.0, places=5)
        self.assertAlmostEqual(summary["totalUnits"], 130.0, places=5)
        self.assertEqual(summary["totalText"], "130.0 格")
        self.assertTrue(summary["hasMultiFloorRoute"])
        self.assertEqual(summary["activeFloorCount"], 2)

        by_floor = {row["id"]: row for row in summary["floors"]}
        self.assertAlmostEqual(by_floor["1"]["units"], 60.0, places=5)
        self.assertEqual(by_floor["1"]["text"], "60.0 格")
        self.assertAlmostEqual(by_floor["2"]["units"], 70.0, places=5)
        self.assertEqual(by_floor["2"]["text"], "70.0 格")

        # Per-segment check: segment 0->1 crosses floors (60 + 40 = 100 格), segment 1->2 stays on floor 2 (30 格)
        self.assertEqual(len(summary["pointSegments"]), 3)
        self.assertTrue(summary["pointSegments"][0]["isStart"])
        self.assertFalse(summary["pointSegments"][0]["hasSegment"])
        seg1 = summary["pointSegments"][1]
        self.assertTrue(seg1["hasSegment"])
        self.assertTrue(seg1["crossFloor"])
        self.assertAlmostEqual(seg1["units"], 100.0, places=5)
        self.assertEqual(seg1["text"], "100.0 格")
        seg2 = summary["pointSegments"][2]
        self.assertTrue(seg2["hasSegment"])
        self.assertFalse(seg2["crossFloor"])
        self.assertAlmostEqual(seg2["units"], 30.0, places=5)
        self.assertEqual(seg2["text"], "30.0 格")

    def test_empty_and_single_point_routes_show_zero(self):
        graph = self._two_floor_graph()
        empty = summarize_route_length(graph, [], ["1", "2"], {"1": "一楼", "2": "二楼"}, 200.0, points=[])
        self.assertEqual(empty["totalUnits"], 0.0)
        self.assertEqual(empty["totalText"], "0 格")
        self.assertEqual([f["text"] for f in empty["floors"]], ["0 格", "0 格"])

        single_pt = [dict(floor="1", x=0.0, y=0.0, kind="via", room=None)]
        single = summarize_route_length(graph, [], ["1", "2"], {"1": "一楼", "2": "二楼"}, 200.0, points=single_pt)
        self.assertEqual(single["totalUnits"], 0.0)
        self.assertEqual(single["totalText"], "0 格")
        self.assertEqual(len(single["pointSegments"]), 1)
        self.assertTrue(single["pointSegments"][0]["isStart"])
        self.assertFalse(single["pointSegments"][0]["hasSegment"])

    def test_new_start_breaks_segment_and_excludes_jump(self):
        graph = self._two_floor_graph()
        points = [
            dict(floor="1", x=0.0, y=0.0, kind="via", room=None),
            dict(floor="1", x=60.0, y=0.0, kind="via", room=None),
            dict(floor="2", x=1000.0, y=1000.0, kind="via", room=None, start=True),
            dict(floor="2", x=1080.0, y=1000.0, kind="via", room=None),
        ]
        segments = graph.plan(points)
        summary = summarize_route_length(graph, segments, ["1", "2"], {"1": "一楼", "2": "二楼"}, 200.0, points=points)
        # 60 px on floor 1 (30 格) + 80 px on floor 2 (40 格) = 70 格
        self.assertAlmostEqual(summary["totalUnits"], 70.0, places=5)
        self.assertTrue(summary["pointSegments"][2]["isStart"])
        self.assertFalse(summary["pointSegments"][2]["hasSegment"])

    def test_imported_structure_polyline_segments_and_cross_floor_gap(self):
        points = [
            dict(floor="1", x=0.0, y=0.0, kind="via", room=None),
            dict(floor="1", x=100.0, y=0.0, kind="via", room=None),
            dict(floor="2", x=50.0, y=50.0, kind="via", room=None),
            dict(floor="2", x=50.0, y=250.0, kind="via", room=None),
        ]
        # ImportedStructureGraph skips cross-floor consecutive pair (idx 1 -> idx 2)
        segments = [
            dict(floor="1", color="#63CAFF", points=[(0.0, 0.0), (100.0, 0.0)], end=1),
            dict(floor="2", color="#63CAFF", points=[(50.0, 50.0), (50.0, 250.0)], end=3),
        ]
        summary = summarize_route_length(
            None, segments, ["1", "2"], {"1": "一楼", "2": "二楼"}, 1000.0, points=points
        )
        # 100 px = 10 格, 200 px = 20 格, total = 30 格
        self.assertAlmostEqual(summary["totalUnits"], 30.0, places=5)
        self.assertEqual(summary["pointSegments"][2]["crossFloor"], True)
        self.assertEqual(summary["pointSegments"][2]["units"], 0.0)
        self.assertEqual(summary["pointSegments"][2]["text"], "0 格")

    def test_raw_points_floor_pixels_excludes_cross_floor_and_new_start(self):
        pts = [
            dict(floor="1", x=0, y=0),
            dict(floor="1", x=30, y=40),   # +50 on floor 1
            dict(floor="2", x=999, y=999), # cross-floor jump -> +0
            dict(floor="2", x=999, y=1099),# +100 on floor 2
            dict(floor="2", x=0, y=0, start=True), # new start -> +0
            dict(floor="2", x=0, y=25),    # +25 on floor 2
        ]
        res = raw_points_floor_pixels(pts, floors=["1", "2"])
        self.assertAlmostEqual(res["1"], 50.0, places=6)
        self.assertAlmostEqual(res["2"], 125.0, places=6)

    def test_plan_error_marks_error_instead_of_zero(self):
        summary = summarize_route_length(
            None, [], ["1", "2"], {"1": "一楼", "2": "二楼"}, 200.0,
            points=[dict(floor="1", x=0, y=0), dict(floor="2", x=10, y=10)],
            error="这两个位置之间没有已确认的通路",
        )
        self.assertTrue(summary["error"])
        self.assertEqual(summary["totalText"], "长度待规划")
        self.assertEqual(empty_route_length(["1"])["totalText"], "0 格")


if __name__ == "__main__":
    unittest.main()
