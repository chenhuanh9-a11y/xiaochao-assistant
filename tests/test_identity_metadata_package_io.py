"""Identity and coordinate JSON reads through an immutable PackageView."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from imported_resource_packages import ComponentRef, PackageView
from difficult_native_identity import NativeDifficultPack
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters


def raw_json(value):
    return json.dumps(value).encode("utf-8")


class IdentityMetadataPackageIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store_root = Path(self.temp.name).resolve()
        self.version_number = 0

    @staticmethod
    def documents():
        identity = {
            "schemaVersion": 1,
            "mode": "difficult",
            "season": "s0",
            "packId": "fixture-difficult-v1",
            "floors": ["1"],
            "maps": [{
                "id": "difficult-map1",
                "name": "Fixture map",
                "direction": "N",
                "shape": "fixture",
                "identityProfileId": "shared-structure",
                "routeProfileId": "fixture-route",
                "layers": {"1": None},
            }],
        }
        coordinates = {
            "schema": "identityv-nightmare-canonical-coordinate-pack-v1",
            "policy": {
                "requiresCurrentFrameGeometryFit": True,
                "authorizesMapIdentity": False,
                "authorizesCrossFloorProjection": False,
            },
            "canonicalPackId": "fixture-canonical-v1",
            "coordinateReferenceProfileId": "fixture-reference",
            "profiles": {
                "fixture-profile": {
                    "maps": {
                        "fixture-map": {
                            "floors": {
                                "1": {
                                    "usableForCurrentFrameRendering": True,
                                    "crossFloorProjectionAllowed": False,
                                    "evidence": {"algebraicallyExact": True},
                                    "matrixProfileToCanonical": [
                                        [1, 0, 3], [0, 1, 4], [0, 0, 1],
                                    ],
                                },
                            },
                        },
                    },
                },
            },
        }
        return {"identity.json": raw_json(identity), "coordinates.json": raw_json(coordinates)}

    @staticmethod
    def _expired_snapshot():
        raise RuntimeError("snapshot expired")

    def prepare(self, *, omitted=(), make_directory=None, expire=False, overrides=None):
        self.version_number += 1
        package_root = self.store_root / "packages" / "fixture" / f"v{self.version_number}"
        self.data_root = package_root / "data" / "base"
        self.data_root.mkdir(parents=True)
        payloads = self.documents()
        payloads.update(overrides or {})
        for relative, data in payloads.items():
            (self.data_root / relative).write_bytes(data)
        manifest = {
            "schema": "xingtu-resource-package",
            "formatVersion": 1,
            "packageId": "fixture",
            "resourceVersion": f"v{self.version_number}",
            "clientCompatibility": {"resourceProtocol": 1, "requiredCapabilities": []},
            "components": [{
                "componentId": "base",
                "type": "recognition-base",
                "schemaVersion": 1,
                "contexts": [{"season": "s0", "mode": "difficult"}],
                "entrypoint": "identity.json",
                "dependencies": {},
            }],
            "files": [
                {"componentId": "base", "path": relative, "bytes": len(data),
                 "sha256": hashlib.sha256(data).hexdigest()}
                for relative, data in payloads.items() if relative not in omitted
            ],
        }
        manifest_bytes = raw_json(manifest)
        (package_root / "manifest.json").write_bytes(manifest_bytes)
        self.view = PackageView(
            self.store_root,
            ComponentRef("fixture", f"v{self.version_number}", "base",
                         hashlib.sha256(manifest_bytes).hexdigest()),
            assert_current=self._expired_snapshot if expire else None,
        )
        if make_directory is not None:
            target = self.data_root / make_directory
            target.unlink()
            target.mkdir()
        self.reads = []

    def read_resource(self, path):
        relative = Path(path).relative_to(self.data_root).as_posix()
        self.reads.append(relative)
        return self.view.read(relative)

    def construct(self, which, *, packaged):
        reader = self.read_resource if packaged else None
        if which == "identity":
            return NativeDifficultPack(
                self.data_root, "identity.json", expected_mode="difficult",
                expected_season="s0", read_resource=reader,
            )
        if which == "coordinates":
            return AuthorCoordinateAdapters(
                self.data_root / "coordinates.json", read_resource=reader,
            )
        raise AssertionError(which)

    def test_package_and_path_constructors_have_same_validated_results(self):
        self.prepare()
        identity_path = self.construct("identity", packaged=False)
        coordinates_path = self.construct("coordinates", packaged=False)
        identity_package = self.construct("identity", packaged=True)
        coordinates_package = self.construct("coordinates", packaged=True)

        self.assertEqual(identity_package.raw, identity_path.raw)
        self.assertEqual(identity_package.manifest_path, identity_path.manifest_path)
        self.assertEqual(identity_package.source_root, identity_path.source_root)
        self.assertEqual(identity_package.pack_id, identity_path.pack_id)
        self.assertEqual(identity_package.display_name, identity_path.display_name)
        self.assertEqual(identity_package.floors, identity_path.floors)
        self.assertEqual(identity_package.default_entry_floor, identity_path.default_entry_floor)
        self.assertEqual(identity_package.identity_profiles, identity_path.identity_profiles)
        self.assertEqual(identity_package.route_profiles, identity_path.route_profiles)
        self.assertEqual(identity_package.maps, identity_path.maps)
        self.assertIsNone(identity_package.maps[0].one_flow)
        self.assertIsNone(identity_package.maps[0].layers["1"])

        self.assertEqual(coordinates_package.path, coordinates_path.path)
        self.assertEqual(coordinates_package.schema, coordinates_path.schema)
        self.assertEqual(coordinates_package.policy, coordinates_path.policy)
        self.assertEqual(coordinates_package.canonical_pack_id, coordinates_path.canonical_pack_id)
        self.assertEqual(coordinates_package.coordinate_reference_profile_id,
                         coordinates_path.coordinate_reference_profile_id)
        np.testing.assert_array_equal(
            coordinates_package.matrix_profile_to_canonical("fixture-profile", "fixture-map", "1"),
            coordinates_path.matrix_profile_to_canonical("fixture-profile", "fixture-map", "1"),
        )
        self.assertIsNone(coordinates_package.matrix_profile_to_canonical("missing", "fixture-map", "1"))
        self.assertIsNone(coordinates_path.matrix_profile_to_canonical("missing", "fixture-map", "1"))
        self.assertEqual(self.reads, ["identity.json", "coordinates.json"])

    def _assert_package_read_fails_closed(self, which, failure):
        relative = "identity.json" if which == "identity" else "coordinates.json"
        if failure == "undeclared":
            self.prepare(omitted=(relative,))
            self.assertTrue((self.data_root / relative).is_file())
            # The existing on-disk document is usable as a direct-path control.
            self.construct(which, packaged=False)
            self.reads.clear()
            expected = ("entrypoint-not-in-inventory" if which == "identity"
                        else "file-not-declared")
        elif failure == "changed-bytes":
            self.prepare()
            path = self.data_root / relative
            if which == "identity":
                changed = json.loads(path.read_text(encoding="utf-8"))
                changed["packId"] = "unverified-disk-fallback"
            else:
                changed = json.loads(path.read_text(encoding="utf-8"))
                changed["profiles"]["fixture-profile"]["maps"]["fixture-map"]["floors"]["1"][
                    "matrixProfileToCanonical"][0][2] = 99
            path.write_bytes(raw_json(changed))
            # Direct-path parsing still succeeds, but the package inventory no longer matches.
            self.construct(which, packaged=False)
            self.reads.clear()
            expected = "integrity-mismatch"
        elif failure == "directory":
            self.prepare(make_directory=relative)
            expected = "non-regular-package-file"
        elif failure == "invalid-utf8":
            self.prepare(overrides={relative: b"\xffunparseable-utf8"})
            expected = "utf-8"
        elif failure == "expired-snapshot":
            self.prepare(expire=True)
            expected = "snapshot expired"
        else:
            raise AssertionError(failure)

        with self.assertRaisesRegex((ValueError, RuntimeError), expected):
            self.construct(which, packaged=True)
        self.assertEqual(self.reads, [relative])

    def test_missing_or_unverified_package_reads_never_fall_back_to_disk(self):
        for which in ("identity", "coordinates"):
            for failure in ("undeclared", "changed-bytes", "invalid-utf8", "directory",
                            "expired-snapshot"):
                with self.subTest(entry=which, failure=failure):
                    self._assert_package_read_fails_closed(which, failure)


if __name__ == "__main__":
    unittest.main()
