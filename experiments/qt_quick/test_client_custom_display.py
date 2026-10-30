"""Custom display ABI and binding checks on synthetic art/road resources only."""
import unittest
from unittest.mock import patch

import numpy as np

from client_custom_display import CustomGalleryDisplay, custom_profile_id
from custom_maps import render_game_layer
from shared_structure_reference import SharedStructureReference
import test_client_custom_gallery as gallery_fixture


class CustomDisplayTests(unittest.TestCase):
    setUp = gallery_fixture.CustomGalleryTests.setUp
    cleanup = gallery_fixture.CustomGalleryTests.cleanup
    call = gallery_fixture.CustomGalleryTests.call

    def reference(self, *, floor="1", mask=None):
        mask = np.ones((200, 200), np.uint8) if mask is None else mask
        return SharedStructureReference(mask, mask.copy(),
            dict(binding=dict(profile="shared-structure", mapId="synthetic-map", floor=floor),
                 structureVersionKey="synthetic-v1"), self.folder / "synthetic-reference.npz")

    def test_native_display_reuses_renderer_bgra_and_exact_reference(self):
        self.call("createGallery", "显示路线")
        self.call("addPoint", 20., 20.)
        self.call("addPoint", 60., 30.)
        gid = self.service.snapshot["selectedGallery"]
        profile = custom_profile_id(gid)
        display = CustomGalleryDisplay(self.service)
        request = display.use_request(gid, "nightmare")
        self.assertTrue(request["success"])
        self.assertFalse(request["applied"])
        self.assertFalse(self.service.snapshot["inUse"])
        reference = self.reference()
        binding_before = dict(reference.metadata["binding"])
        layer = display.layer(profile, "synthetic-map", "1", reference, "nightmare")
        original = render_game_layer(self.service.display_snapshot(gid)["library"], "synthetic-map", "1",
                                     self.service.display_snapshot(gid, "synthetic-map")["points"])
        self.assertTrue(np.array_equal(layer["pixels"], original))
        self.assertEqual(layer["pixelFormat"], "BGRA")
        self.assertEqual(layer["pixels"][150, 150].tolist(), [253, 242, 234, 255])
        self.assertEqual(layer["pixels"][:, :, [2, 1, 0, 3]][150, 150].tolist(), [234, 242, 253, 255])
        self.assertFalse(layer["pixels"].flags.writeable)
        self.assertTrue(np.array_equal(layer["matrixDisplayToReference"], np.eye(3)))
        self.assertEqual(reference.metadata["binding"], binding_before)
        self.assertIsNone(display.layer(profile, "synthetic-map", "1", reference, "difficult"))
        self.assertIsNone(display.layer(profile, "synthetic-map", "2", reference, "nightmare"))
        self.assertIsNone(display.layer(profile, "synthetic-map", "1", self.reference(mask=np.zeros((200, 200), np.uint8)), "nightmare"))

    def test_uncommitted_or_changed_routes_never_become_current_display(self):
        self.call("createGallery", "路线")
        gid = self.service.snapshot["selectedGallery"]
        profile = custom_profile_id(gid)
        display = CustomGalleryDisplay(self.service)
        first = display.layer(profile, "synthetic-map", "1", self.reference(), "nightmare")
        self.call("addPoint", 20., 20.)
        second = display.layer(profile, "synthetic-map", "1", self.reference(), "nightmare")
        self.assertNotEqual(first["resourceIdentity"]["routeSha256"], second["resourceIdentity"]["routeSha256"])
        self.assertFalse(display.eligibility(gid, "difficult")["allowed"])
        self.assertEqual(display.active_gallery(profile, "nightmare"), gid)
        real = render_game_layer
        def changed(library, mid, floor, points):
            pixels = real(library, mid, floor, points)
            self.service.path.write_text("external change", encoding="utf-8")
            return pixels
        display.invalidate()
        with patch("client_custom_display.render_game_layer", side_effect=changed):
            self.assertIsNone(display.layer(profile, "synthetic-map", "1", self.reference(), "nightmare"))
        self.assertFalse(display.eligibility(gid, "nightmare")["allowed"])


if __name__ == "__main__":
    unittest.main()
