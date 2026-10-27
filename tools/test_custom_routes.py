"""Custom route storage, routing and rendering on a synthetic structure."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from custom_routes import CustomRouteStore, FloorRoute, Walkable, render_layer, route_polyline  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
