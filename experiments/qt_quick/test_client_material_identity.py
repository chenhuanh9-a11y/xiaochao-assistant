"""Material reuse must not become reuse of a live frame grant."""
import unittest

import numpy as np

from client_coordinator import LiveImages, _aligned_material_scope


class MaterialIdentityTests(unittest.TestCase):
    def setUp(self):
        self.images = LiveImages()
        self.pixels = np.full((30, 40, 4), 255, np.uint8)
        self.payload = dict(roundId=1, mode="nightmare", profileId="base",
                            mapId="a", floor="1", resourceIdentity=["r", 1],
                            routeWidth=40, routeHeight=30)

    def publish(self, token, pixels=None, **changes):
        return self.images.publish(token, self.pixels if pixels is None else pixels,
            material_scope=_aligned_material_scope(dict(self.payload, **changes)))

    def test_new_frame_tokens_with_equal_pixels_keep_one_material(self):
        first = self.publish("frame-1")
        for index in range(2, 50):
            self.assertEqual(self.publish(f"frame-{index}", self.pixels.copy()), first)
        self.assertEqual(len(self.images._images), 1)

    def test_changed_pixels_or_display_scope_cannot_reuse_material(self):
        first = self.publish("frame-1")
        changed = self.pixels.copy()
        changed[10, 10, 0] = 0
        self.assertNotEqual(self.publish("frame-2", changed), first)
        for index, change in enumerate((dict(floor="2"), dict(mapId="b"),
                dict(profileId="other"), dict(resourceIdentity=["r", 2]),
                dict(roundId=2), dict(mode="difficult"))):
            before = self.publish(f"before-{index}")
            self.assertNotEqual(self.publish(f"after-{index}", **change), before)
        self.assertLessEqual(len(self.images._images), 2)

    def test_clear_revokes_material_and_accepted_staging_becomes_reusable(self):
        self.publish("source")
        target = self.publish("pending:target", mapId="b")
        self.images.finish_staging("target", True)
        self.assertEqual(self.publish("new-target-frame", mapId="b"), target)
        self.images.clear_aligned()
        self.assertNotEqual(self.publish("after-clear", mapId="b"), target)
        self.assertEqual(len(self.images._images), 1)

    def test_rejected_staging_does_not_replace_source_material(self):
        source = self.publish("source")
        self.publish("pending:target", mapId="b")
        self.images.finish_staging("target", False)
        self.assertEqual(self.publish("source-new-frame"), source)


if __name__ == "__main__":
    unittest.main()
