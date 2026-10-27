"""Custom route storage, routing and rendering on a synthetic structure."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_routes import (CustomRouteStore, FloorRoute, RoutePathError, Walkable,
                           render_layer, route_polyline)  # noqa: E402


def _u_shape():
    """Two vertical corridors joined at the bottom; the gap between is void."""
    mask = np.zeros((600, 600), np.uint8)
    mask[60:540, 60:180] = 1
    mask[60:540, 420:540] = 1
    mask[420:540, 60:540] = 1
    return mask


class CustomRouteTests(unittest.TestCase):
    def test_path_stays_on_footprint(self):
        mask = _u_shape()
        walkable = Walkable(mask)
        start, goal = walkable.snap((120, 100)), walkable.snap((480, 100))
        path = walkable.path(start, goal)
        self.assertIsNotNone(path)
        dense = np.zeros_like(mask)
        import cv2
        cv2.polylines(dense, [np.array(path, np.int32)], False, 1, 1)
        self.assertFalse((dense & (mask == 0)).any(), "route crosses void")
        self.assertGreater(max(y for _x, y in path), 420, "route must go round the bottom")

    def test_snap_rejects_far_points(self):
        walkable = Walkable(_u_shape())
        self.assertIsNone(walkable.snap((300, 200), reach=3))
        self.assertIsNotNone(walkable.snap((300, 200), reach=20))

    def test_straight_segments_when_snap_off(self):
        route = FloorRoute(((120, 100), (480, 100)), snap=False)
        self.assertEqual(route_polyline(route, Walkable(_u_shape())), [[(120, 100), (480, 100)]])

    def test_snapped_disconnected_route_raises_instead_of_drawing_a_line(self):
        mask = np.zeros((100, 100), np.uint8)
        mask[10:40, 10:40] = mask[60:90, 60:90] = 1
        route = FloorRoute(((25, 25), (75, 75)))
        with self.assertRaises(RoutePathError) as raised:
            route_polyline(route, Walkable(mask))
        self.assertIsInstance(raised.exception, ValueError)
        self.assertEqual(raised.exception.reason, "no_path")
        self.assertEqual(raised.exception.segment_index, 0)
        with self.assertRaises(RoutePathError):
            render_layer(mask, route, Walkable(mask))
        self.assertEqual(route_polyline(FloorRoute(route.points, snap=False), Walkable(mask)),
                         [[(25, 25), (75, 75)]])

    def test_missing_footprint_is_an_explicit_failure_only_for_snapped_points(self):
        with self.assertRaises(RoutePathError) as raised:
            route_polyline(FloorRoute(((15, 15),)), None)
        self.assertEqual(raised.exception.reason, "missing_footprint")
        self.assertEqual(route_polyline(FloorRoute(), None), [])
        self.assertEqual(route_polyline(FloorRoute(((1, 2), (3, 4)), snap=False), None),
                         [[(1, 2), (3, 4)]])

    def test_endpoints_are_not_clamped_into_the_footprint(self):
        mask = np.ones((60, 60), np.uint8)
        mask[0:10, 0:10] = 0
        walkable = Walkable(mask)
        for point in ((-1, 15), (60, 15), (15, -1), (15, 60), (5, 5)):
            with self.subTest(point=point):
                self.assertIsNone(walkable.path(point, (25, 25)))
                with self.assertRaises(RoutePathError) as raised:
                    route_polyline(FloorRoute(((25, 25), point)), walkable)
                self.assertEqual(raised.exception.reason, "invalid_endpoint")
                self.assertEqual(raised.exception.point_index, 1)
        self.assertIsNone(walkable.snap((-1, 15)))
        self.assertIsNone(walkable.snap((60, 15)))

    def test_diagonal_cells_do_not_connect_across_void_corners(self):
        mask = np.zeros((30, 30), np.uint8)
        mask[0:10, 0:10] = mask[10:20, 10:20] = 1
        walkable = Walkable(mask)
        self.assertIsNone(walkable.path((5, 5), (15, 15)))
        with self.assertRaises(RoutePathError):
            route_polyline(FloorRoute(((5, 5), (15, 15))), walkable)

    def test_same_cell_hole_is_not_bridged(self):
        mask = np.ones((20, 20), np.uint8)
        mask[0:10, 4:6] = 0  # The cell still meets the coarse open-cell threshold.
        walkable = Walkable(mask)
        self.assertIsNone(walkable.path((2, 5), (8, 5)))
        self.assertIsNone(walkable.path((4, 5), (15, 15)))
        snapped = walkable.snap((2, 5))
        self.assertIsNotNone(snapped)
        self.assertTrue(walkable.contains(snapped), "snap must not return a centre in a sub-cell hole")

    def test_thin_bend_does_not_get_simplified_across_void(self):
        mask = np.zeros((100, 100), np.uint8)
        mask[10:90, 10:20] = 1
        mask[80:90, 10:90] = 1
        walkable = Walkable(mask)
        path = walkable.path((15, 15), (85, 85))
        self.assertIsNotNone(path)
        import cv2
        dense = np.zeros_like(mask)
        cv2.polylines(dense, [np.array(path, np.int32)], False, 1, 1)
        self.assertFalse((dense & (mask == 0)).any())

    def test_render_matches_canvas(self):
        mask = _u_shape()
        route = FloorRoute(((120, 100), (480, 100)), color="#e03150")
        image = render_layer(mask, route, Walkable(mask))
        self.assertEqual(image.shape, (*mask.shape, 4))
        self.assertTrue((image[mask == 0][:, 3] == 0).sum() > 0)
        red = (image[:, :, 2] > 200) & (image[:, :, 1] < 80) & (image[:, :, 3] == 255)
        self.assertGreater(int(red.sum()), 500)

    def test_store_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom-routes.json"
            store = CustomRouteStore(path)
            store.set_route("nightmare-01", "1", FloorRoute(((1, 2), (3, 4)), "#12a37f", False))
            store.set_use_in_game(True)
            loaded = CustomRouteStore(path)
            self.assertTrue(loaded.use_in_game)
            self.assertEqual(loaded.route("nightmare-01", "1"), FloorRoute(((1, 2), (3, 4)), "#12a37f", False))
            self.assertEqual(loaded.floors_with_routes("nightmare-01"), {"1"})
            loaded.set_route("nightmare-01", "1", FloorRoute())
            self.assertEqual(CustomRouteStore(path).count(), 0)

    def test_store_ignores_bad_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom-routes.json"
            path.write_text('{"schema": "xingtu-custom-routes-v1", "maps": {"a": {"9": {}, "1": '
                            '{"points": [[1, "x"], [2, 3]], "color": "red"}}}}', encoding="utf-8")
            store = CustomRouteStore(path)
            self.assertEqual(store.route("a", "1"), FloorRoute(((2, 3),)))
            self.assertEqual(store.floors_with_routes("a"), {"1"})

    def test_empty_floor_preferences_survive_reload_without_counting_as_a_route(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom-routes.json"
            store = CustomRouteStore(path)
            preferences = FloorRoute(color="#12a37f", snap=False)
            self.assertTrue(store.set_route("a", "2", preferences))
            loaded = CustomRouteStore(path)
            self.assertEqual(loaded.route("a", "2"), preferences)
            self.assertEqual(loaded.count(), 0)
            self.assertEqual(loaded.floors_with_routes("a"), set())
            first = loaded.route("a", "2")
            loaded.set_route("a", "2", FloorRoute(((15, 15),), first.color, first.snap))
            self.assertEqual(loaded.count(), 1)
            self.assertEqual(loaded.floors_with_routes("a"), {"2"})
            loaded.set_route("a", "2", preferences)  # Clear points, retaining preferences.
            self.assertEqual(CustomRouteStore(path).route("a", "2"), preferences)
            loaded.set_route("a", "2", FloorRoute())  # Explicit preference reset.
            reset = CustomRouteStore(path)
            self.assertEqual(reset.route("a", "2"), FloorRoute())
            self.assertEqual(reset.count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
