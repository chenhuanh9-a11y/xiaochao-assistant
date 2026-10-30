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

    def test_missing_author_reaches_real_base_renderer_through_factory_binding(self):
        from types import SimpleNamespace
        from client_providers import build_engine_factories
        from client_live_runtime import LiveRuntime
        display=CustomGalleryDisplay(self.service)
        engine=SimpleNamespace(pack=SimpleNamespace(mode="nightmare",maps=[SimpleNamespace(id="synthetic-map")]),
            identity_floors=("1",),qt_catalog=None,
            qt_optional_displays=SimpleNamespace(profiles=set(),binding=lambda *a:None),
            qt_adapters=SimpleNamespace(matrix_profile_to_canonical=lambda *a:np.eye(3)),qt_native_context=None)
        reference=self.reference()
        engine.qt_shared_provider=lambda *a:reference
        factory=build_engine_factories(self.folder/"_internal")
        factory._build=lambda mode:engine
        factory.set_custom_display(display.layer,base_provider=display.base_layer)
        prepared=factory["nightmare"]()
        layer=LiveRuntime._display_layer(prepared,"synthetic-map","1","suju")
        expected=render_game_layer(self.service.base_display_library(),"synthetic-map","1",[])
        self.assertEqual((layer["displayKind"],layer["authorStatus"],layer["preferredProfileId"]),("base-map","missing","suju"))
        np.testing.assert_array_equal(layer["pixels"],expected)
        self.assertIsNone(display.base_layer("synthetic-map","1",reference,"difficult"))
        with self.assertRaisesRegex(ValueError,"custom-display-unavailable"):
            LiveRuntime._display_layer(prepared,"synthetic-map","1","custom:missing-gallery")

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
