"""Synthetic author images and real display policy; no solver/capture or grants."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from client_paths import APP_ROOT, WORK_ROOT
import sys
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from author_route_catalog import AuthorRouteCatalog
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters, CANONICAL_SCHEMA
from shared_structure_reference import SharedStructureReference
from client_route_reprojection import prepare_route_reprojection


class RouteReprojectionTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-route-reprojection-", dir=WORK_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profiles = []
        for profile, color in (("suju", 40), ("zhan10-speed", 80), ("nightingale", 120)):
            path = self.root / (profile + ".png")
            cv2.imencode(".png", np.full((20, 24, 4), (color, 30, 60, 255), np.uint8))[1].tofile(str(path))
            profiles.append(dict(id=profile, displayName=profile, authorName="fixture", routeFamilyName="fixture",
                runtimeEnabled=True, settingsSelectable=True, alignedOverlayEligible=True,
                maps={"fixture-map": [dict(id="variant-01", displayName="fixture", layers={"1": path.name},
                    identityStatus="display-only", displayPolicy="requires-current-shared-structure-fit")]}))
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps(dict(schemaVersion=1, profiles=profiles)), encoding="utf8")
        row = lambda scale: dict(usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
            matrixProfileToCanonical=np.diag([scale, scale, 1.]).tolist(), evidence=dict(algebraicallyExact=True))
        coordinates = self.root / "coordinates.json"
        coordinates.write_text(json.dumps(dict(schema=CANONICAL_SCHEMA, canonicalPackId="fixture-pack",
            coordinateReferenceProfileId="shared-structure",
            referenceCanvas=dict(shapeHW=[20,24], coordinateSpace="fixture-pixels"),
            policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False, authorizesCrossFloorProjection=False),
            profiles={profile: dict(maps={"fixture-map": dict(floors={"1": row(scale)})})
                      for profile, scale in (("shared-structure",2), ("suju",3), ("zhan10-speed",4), ("nightingale",5))})), encoding="utf8")
        reference = SharedStructureReference(np.ones((20,24), np.uint8), np.ones((20,24), np.uint8),
            dict(binding=dict(profile="fixture", mapId="fixture-map", floor="1", variant="variant-01"),
                 canvas=dict(shapeHW=[20,24], coordinateSpace="fixture-pixels"), structureVersionKey="fixture-version"), Path("fixture.npz"))
        self.engine = SimpleNamespace(pack=SimpleNamespace(mode="nightmare"),
            qt_catalog=AuthorRouteCatalog(catalog, asset_base=self.root),
            qt_adapters=AuthorCoordinateAdapters(coordinates), qt_shared_provider=lambda *args: reference,
            qt_optional_displays=SimpleNamespace(profiles=set(), binding=lambda *args: None), qt_native_context=None,
            rank=Mock(side_effect=AssertionError("reprojection ranked")),
            fit=Mock(side_effect=AssertionError("reprojection fitted")),
            capture=Mock(side_effect=AssertionError("reprojection captured")))
        self.engine.root_pose = dict(mapId="fixture-map", floor="1", generation=7,
            identityPhase="MEMBER_VERIFIED", authorization="existing-grant", matrix=np.eye(3))

    def test_cross_author_reprojection_uses_nonidentity_shared_basis_and_keeps_root(self):
        canonical = np.array([[.8, .1, 31], [-.2, .9, 47], [.0001, -.0002, 1.]])
        original = canonical.copy()
        root = deepcopy(self.engine.root_pose)
        raw = deepcopy(self.engine.qt_adapters._raw)
        a = prepare_route_reprojection(self.engine, "fixture-map", "1", "suju", canonical)
        # Original policy requires explicit optional-author registration.
        self.engine.qt_optional_displays = SimpleNamespace(profiles={"zhan10-speed"},
            binding=lambda profile, *args: dict(path=self.root / "zhan10-speed.png", usable=True,
                optionalPixels=False) if profile == "zhan10-speed" else None)
        b = prepare_route_reprojection(self.engine, "fixture-map", "1", "zhan10-speed", canonical)
        self.assertEqual(a["layer"]["effectiveProfileId"], "suju")
        self.assertEqual(b["layer"]["effectiveProfileId"], "zhan10-speed")
        np.testing.assert_allclose(a["matrixSourceToClient"], canonical @ np.diag([3.,3.,1.]))
        np.testing.assert_allclose(b["matrixSourceToClient"], canonical @ np.diag([4.,4.,1.]))
        self.assertFalse(np.array_equal(a["layer"]["pixels"], b["layer"]["pixels"]))
        self.assertFalse(np.allclose(a["matrixSourceToClient"], canonical @ a["layer"]["matrixDisplayToReference"]))
        np.testing.assert_array_equal(canonical, original)
        np.testing.assert_array_equal(self.engine.root_pose["matrix"], root["matrix"])
        self.assertEqual({k:v for k,v in self.engine.root_pose.items() if k != "matrix"},
                         {k:v for k,v in root.items() if k != "matrix"})
        self.assertEqual(self.engine.qt_adapters._raw, raw)
        for method in (self.engine.rank, self.engine.fit, self.engine.capture): method.assert_not_called()
        self.assertNotIn("liveAuthorization", a)
        self.assertNotIn("authorization", a)

    def test_missing_author_reuses_original_base_fallback_and_bridge(self):
        (self.root / "suju.png").unlink()
        pixels = np.full((20,24,4), 19, np.uint8)
        bridge = np.array([[1.1, 0, 2], [0, 1.2, 3], [0, 0, 1.]])
        layer = dict(pixels=pixels, matrixDisplayToReference=bridge, resourceIdentity={"source":"base-fixture"},
            displayKind="base-map", displayLabel="base", routeProfileId="shared-structure")
        self.engine.qt_base_display = Mock(return_value=layer)
        canonical = np.array([[1.,0,15],[0,1.,22],[0,0,1.]])
        declared_missing = prepare_route_reprojection(self.engine, "fixture-map", 1, "suju", canonical)
        self.assertEqual(declared_missing["layer"]["displayKind"], "shared-structure")
        self.assertEqual(declared_missing["layer"]["authorStatus"], "load-error")
        self.engine.qt_base_display.assert_not_called()
        # A genuinely absent author declaration follows the existing base path.
        catalog_path = self.root / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf8"))
        catalog["profiles"] = [p for p in catalog["profiles"] if p["id"] != "suju"]
        catalog_path.write_text(json.dumps(catalog), encoding="utf8")
        self.engine.qt_catalog = AuthorRouteCatalog(catalog_path, asset_base=self.root)
        result = prepare_route_reprojection(self.engine, "fixture-map", 1, "suju", canonical)
        self.engine.qt_base_display.assert_called_once()
        self.assertEqual(result["layer"]["displayKind"], "base-map")
        self.assertTrue(result["layer"]["fallback"])
        self.assertEqual(result["layer"]["authorStatus"], "missing")
        np.testing.assert_allclose(result["matrixSourceToClient"], canonical @ np.diag([2.,2.,1.]) @ bridge)
        self.assertFalse(np.shares_memory(result["layer"]["pixels"], pixels))
        self.assertFalse(np.shares_memory(result["layer"]["matrixDisplayToReference"], bridge))

    def test_ref_only_prepares_no_pose_or_grant_and_invalid_matrix_does_not_load(self):
        result = prepare_route_reprojection(self.engine, "fixture-map", "1", "suju")
        self.assertEqual(set(result), {"layer"})
        for key in ("liveAuthorization", "referenceAuthorized", "authorization", "grant"):
            self.assertNotIn(key, result["layer"])
        with patch("client_route_reprojection.prepare_display_layer") as prepare:
            with self.assertRaisesRegex(ValueError, "canonical-client-matrix-invalid"):
                prepare_route_reprojection(self.engine, "fixture-map", "1", "suju", np.full((3,3), np.nan))
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
