"""PackageView IO for metadata-free legacy author structure products."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import imported_resource_packages as packages
from author_structure_resources import AuthorStructureResourceStore
from imported_structure_declarations import (
    validate_legacy_structure_declarations,
    validate_structure_declarations,
)


PACKAGE_ID = "legacy-structure-fixture"
VERSION = "v1"
COMPONENT = "structures"
INDEX = "references/author-structure-index.json"
PRODUCT = "references/map-1.npz"
SHARED = "references/shared-map-1.npz"
KEY = dict(profileId="fixture-profile", mapId="map-1", floor="1", variantId="variant-01")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _legacy_npz(*, shape=(8, 12), changes=None):
    arrays = dict(
        validation_mask=np.ones(shape, dtype=np.uint8),
        clean_mask=np.ones(shape, dtype=np.uint8),
        clean_edge_5px=np.zeros(shape, dtype=np.uint8),
        clean_distance=np.zeros(shape, dtype=np.float16),
        known_boundary=np.zeros(shape, dtype=np.uint8),
        unknown_mask=np.zeros(shape, dtype=np.uint8),
        native_wall_segments=np.asarray([[0, 1, 2, 1, 5, -1]], dtype=np.float64),
    )
    if changes:
        for name, value in changes.items():
            if value is None:
                arrays.pop(name, None)
            else:
                arrays[name] = value
    output = io.BytesIO()
    np.savez_compressed(output, **arrays)
    return output.getvalue()


def _bad_deflate_npz():
    rng = np.random.default_rng(42)
    mask = rng.integers(0, 2, (512, 512), dtype=np.uint8)
    output = io.BytesIO()
    np.savez_compressed(output, validation_mask=mask)
    raw = bytearray(output.getvalue())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
    name_length = int.from_bytes(raw[info.header_offset + 26:info.header_offset + 28], "little")
    extra_length = int.from_bytes(raw[info.header_offset + 28:info.header_offset + 30], "little")
    data_start = info.header_offset + 30 + name_length + extra_length
    if data_start != 69:
        raise AssertionError(f"unexpected NumPy ZIP member data offset: {data_start}")
    damage = bytes.fromhex(
        "7fbaa342004ff2fc51e0f01106ecfe6c08c79d82cb6c98fc3cf03327931a3fd5"
    )
    body_offset = data_start + 25415
    raw[body_offset:body_offset + len(damage)] = damage
    return bytes(raw)


def _bad_typed_deflate_npz():
    raw = _bad_deflate_npz()
    metadata = dict(
        sourceKind="game-rendered-shared-structure",
        binding=dict(profile=KEY["profileId"], mapId=KEY["mapId"],
                     floor=KEY["floor"], variant=KEY["variantId"]),
        roles=dict(clean="validation_mask", validation="validation_mask"),
    )
    metadata_npy = io.BytesIO()
    np.save(metadata_npy, np.asarray(json.dumps(metadata)))
    archive_bytes = io.BytesIO(raw)
    with zipfile.ZipFile(archive_bytes, mode="a") as archive:
        archive.writestr("metadata.npy", metadata_npy.getvalue())
    return archive_bytes.getvalue(), metadata


def _typed_npz():
    metadata = dict(sourceKind="game-rendered-shared-structure",
                    binding=dict(profile=KEY["profileId"], mapId=KEY["mapId"],
                                 floor=KEY["floor"], variant=KEY["variantId"]),
                    roles=dict(clean="shared_mask", validation="shared_mask"))
    output = io.BytesIO()
    np.savez_compressed(output, shared_mask=np.ones((8, 12), np.uint8),
                        metadata=json.dumps(metadata))
    return output.getvalue()


class ImportedLegacyStructureReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name).resolve()
        self.resource_root = self.storage / "resources"
        self.package_root = self.resource_root / "packages" / PACKAGE_ID / VERSION
        self.component_root = self.package_root / "data" / COMPONENT
        self.component_root.mkdir(parents=True)
        self.index_value = dict(schemaVersion=2,
                                validationMaskContract="author_route_structure_mask",
                                entries=[dict(KEY, resourcePath=PRODUCT,
                                              sourceShape=[8, 12])])
        self.files = {INDEX: _canonical(self.index_value), PRODUCT: _legacy_npz()}

    def make_view(self, *, assert_current=None, extra_disk_files=()):
        for relative, raw in self.files.items():
            target = self.component_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        for relative, raw in extra_disk_files:
            target = self.component_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        manifest = {
            "schema": "xingtu-resource-package",
            "formatVersion": 1,
            "packageId": PACKAGE_ID,
            "resourceVersion": VERSION,
            "clientCompatibility": {"resourceProtocol": 1, "requiredCapabilities": []},
            "components": [{
                "componentId": COMPONENT,
                "type": "recognition-base",
                "schemaVersion": 1,
                "contexts": [{"season": "s0", "mode": "nightmare"}],
                "entrypoint": INDEX,
                "dependencies": {},
            }],
            "files": [{"componentId": COMPONENT, "path": relative,
                       "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                      for relative, raw in sorted(self.files.items())],
            "provenance": {"source": "legacy-structure-io-regression"},
            "displayName": "Legacy structure fixture",
        }
        manifest_bytes = _canonical(manifest)
        (self.package_root / "manifest.json").write_bytes(manifest_bytes)
        ref = packages.ComponentRef(PACKAGE_ID, VERSION, COMPONENT,
                                    hashlib.sha256(manifest_bytes).hexdigest())
        return packages.PackageView(self.resource_root, ref, assert_current=assert_current)

    @staticmethod
    def reader(view, reads):
        component_root = view.root

        def read_resource(path):
            path = Path(path)
            relative = path.relative_to(component_root).as_posix()
            reads.append(relative)
            return view.read(relative)

        return read_resource

    def make_store(self, *, check_current=None, extra_disk_files=()):
        view = self.make_view(assert_current=check_current, extra_disk_files=extra_disk_files)
        reads = []
        reader = self.reader(view, reads)
        store = AuthorStructureResourceStore(view.path(INDEX), view.root,
                                             read_resource=reader,
                                             check_current=check_current)
        return view, reader, reads, store

    def test_package_view_reader_loads_metadata_free_product_and_allows_explicit_none_mode(self):
        view, _, reads, store = self.make_store()
        self.assertIsNone(store.error)
        self.assertEqual(reads, [INDEX])
        result = store.load_for_route(np.zeros((8, 12), np.uint8), KEY["profileId"],
                                      KEY["mapId"], KEY["floor"],
                                      variant_id=KEY["variantId"])
        self.assertIsNotNone(result)
        self.assertEqual(reads, [INDEX, PRODUCT])
        self.assertEqual(result.validation_mask.shape, (8, 12))
        self.assertEqual(result.clean_mask.shape, (8, 12))
        self.assertEqual(len(result.native_wall_segments), 1)
        self.assertEqual(result.entry["resourcePath"], PRODUCT)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / PRODUCT).parent.mkdir(parents=True)
            (root / PRODUCT).write_bytes(_legacy_npz())
            (root / "index.json").write_text(_canonical(dict(
                schemaVersion=2,
                validationMaskContract="author_route_structure_mask",
                entries=[dict(KEY, resourcePath=PRODUCT, sourceShape=[8, 12])],
            )).decode("utf-8"), encoding="utf-8")
            trusted = AuthorStructureResourceStore(root / "index.json", root)
            self.assertIsNotNone(trusted.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                                              variant_id=KEY["variantId"], route_shape=(8, 12)))
        self.assertEqual(view.root, self.component_root)

    def test_external_reader_requires_explicit_index_and_resource_root(self):
        with self.assertRaisesRegex(ValueError, "explicit index_path and resource_root"):
            AuthorStructureResourceStore(read_resource=lambda _: b"")

    def test_undeclared_product_is_not_loaded_from_disk(self):
        self.index_value["entries"][0]["resourcePath"] = "references/orphan.npz"
        self.files[INDEX] = _canonical(self.index_value)
        view, _, reads, store = self.make_store(extra_disk_files=[("references/orphan.npz", _legacy_npz())])
        reads.clear()
        result = store.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                            variant_id=KEY["variantId"], route_shape=(8, 12))
        self.assertIsNone(result)
        self.assertEqual(reads, ["references/orphan.npz"])
        self.assertTrue((view.root / "references/orphan.npz").is_file(),
                        "the direct-path fallback would have found the orphan")

    def test_duplicate_external_index_keys_fail_closed(self):
        self.index_value["entries"].append(dict(KEY, resourcePath="references/second.npz",
                                                  sourceShape=[8, 12]))
        self.files[INDEX] = _canonical(self.index_value)
        _, _, _, store = self.make_store()
        self.assertEqual(store.entries, {})
        self.assertEqual(store.error, "duplicate author structure resource entry")

    def test_header_preflight_rejects_oversized_shape_alias_and_malformed_optional_roles_before_load(self):
        oversized = _legacy_npz(shape=(1, 8193))
        self.index_value["entries"][0]["sourceShape"] = [1, 8193]
        self.files[INDEX] = _canonical(self.index_value)
        self.files[PRODUCT] = oversized
        with patch("author_structure_resources.np.load",
                   side_effect=AssertionError("array decoder was entered")):
            view = self.make_view()
            reads = []
            store = AuthorStructureResourceStore(view.path(INDEX), view.root,
                read_resource=self.reader(view, reads))
            self.assertIsNone(store.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                                         variant_id=KEY["variantId"], route_shape=(1, 8193)))

        alias = io.BytesIO()
        np.savez_compressed(alias,
            validation_mask=np.ones((1, 8193), np.uint8),
            **{"validation_mask.npy": np.ones((8, 12), np.uint8)})
        with self.assertRaises(ValueError):
            validate_legacy_structure_declarations(alias.getvalue())

        for changes in (
            {"unknown_mask": None},
            {"clean_edge_5px": None, "clean_distance": None},
        ):
            with self.subTest(fields=tuple(changes)):
                with self.assertRaises(ValueError):
                    validate_legacy_structure_declarations(_legacy_npz(changes=changes))

    def test_external_corrupt_deflate_body_is_a_normal_resource_failure(self):
        raw = _bad_deflate_npz()
        self.index_value["entries"][0]["sourceShape"] = [512, 512]
        self.files[INDEX] = _canonical(self.index_value)
        self.files[PRODUCT] = raw
        self.assertEqual(validate_legacy_structure_declarations(raw), (512, 512))
        _, _, reads, store = self.make_store()
        reads.clear()
        result = store.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                            variant_id=KEY["variantId"], route_shape=(512, 512))
        self.assertIsNone(result)
        self.assertEqual(reads, [PRODUCT])

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / PRODUCT
            target.parent.mkdir(parents=True)
            target.write_bytes(raw)
            (root / "index.json").write_text(_canonical(self.index_value).decode("utf-8"),
                                             encoding="utf-8")
            trusted = AuthorStructureResourceStore(root / "index.json", root)
            with self.assertRaises(zlib.error):
                trusted.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                             variant_id=KEY["variantId"], route_shape=(512, 512))

    def test_stale_generation_rejects_cache_hits_and_late_results(self):
        stale = {"value": False}

        def check_current():
            if stale["value"]:
                raise RuntimeError("resource generation changed")

        _, _, reads, store = self.make_store(check_current=check_current)
        loaded = store.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                            variant_id=KEY["variantId"], route_shape=(8, 12))
        self.assertIsNotNone(loaded)
        self.assertEqual(len(reads), 2)
        stale["value"] = True
        with self.assertRaisesRegex(RuntimeError, "generation changed"):
            store.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                       variant_id=KEY["variantId"], route_shape=(8, 12))
        self.assertEqual(store._cache, {})

        late_stale = {"value": False}
        view = self.make_view(assert_current=lambda: (
            (_ for _ in ()).throw(RuntimeError("late generation change"))
            if late_stale["value"] else None))
        late_reads = []
        actual_reader = self.reader(view, late_reads)

        def late_reader(path):
            raw = actual_reader(path)
            if Path(path).name == Path(PRODUCT).name:
                late_stale["value"] = True
            return raw

        late_store = AuthorStructureResourceStore(view.path(INDEX), view.root,
            read_resource=late_reader, check_current=lambda: (
                (_ for _ in ()).throw(RuntimeError("late generation change"))
                if late_stale["value"] else None))
        with self.assertRaisesRegex(RuntimeError, "late generation change"):
            late_store.load(KEY["profileId"], KEY["mapId"], KEY["floor"],
                            variant_id=KEY["variantId"], route_shape=(8, 12))
        self.assertEqual(late_store._cache, {})

    def test_external_shared_reference_keeps_the_existing_typed_reader_path(self):
        self.files[SHARED] = _typed_npz()
        self.index_value["entries"][0]["sharedResourcePath"] = SHARED
        self.files[INDEX] = _canonical(self.index_value)
        _, _, reads, store = self.make_store()
        reads.clear()
        reference = store.load_shared_reference(KEY["profileId"], KEY["mapId"], KEY["floor"],
                                                variant_id=KEY["variantId"])
        self.assertIsNotNone(reference)
        self.assertEqual(reads, [SHARED])
        np.testing.assert_array_equal(reference.validation_mask, np.ones((8, 12), np.uint8))

        stale = {"value": False}

        def check_current():
            if stale["value"]:
                raise RuntimeError("shared reference generation changed")

        view = self.make_view(assert_current=check_current)
        late_reads = []
        reader = self.reader(view, late_reads)

        def late_reader(path):
            raw = reader(path)
            if Path(path).name == Path(SHARED).name:
                stale["value"] = True
            return raw

        late_store = AuthorStructureResourceStore(view.path(INDEX), view.root,
            read_resource=late_reader, check_current=check_current)
        with self.assertRaisesRegex(RuntimeError, "generation changed"):
            late_store.load_shared_reference(KEY["profileId"], KEY["mapId"], KEY["floor"],
                                             variant_id=KEY["variantId"])
        self.assertEqual(late_reads, [INDEX, SHARED])

    def test_external_typed_shared_reference_normalizes_corrupt_member_body(self):
        raw, metadata = _bad_typed_deflate_npz()
        self.assertEqual(validate_structure_declarations(raw), metadata)
        self.files[SHARED] = raw
        self.index_value["entries"][0]["sharedResourcePath"] = SHARED
        self.files[INDEX] = _canonical(self.index_value)
        _, _, reads, store = self.make_store()
        reads.clear()
        reference = store.load_shared_reference(KEY["profileId"], KEY["mapId"], KEY["floor"],
                                                variant_id=KEY["variantId"])
        self.assertIsNone(reference)
        self.assertEqual(reads, [SHARED])

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / SHARED).parent.mkdir(parents=True)
            (root / SHARED).write_bytes(raw)
            index_value = dict(
                schemaVersion=2,
                validationMaskContract="author_route_structure_mask",
                entries=[dict(KEY, resourcePath=PRODUCT, sharedResourcePath=SHARED,
                              sourceShape=[512, 512])],
            )
            (root / "index.json").write_text(_canonical(index_value).decode("utf-8"),
                                             encoding="utf-8")
            trusted = AuthorStructureResourceStore(root / "index.json", root)
            with self.assertRaises(zlib.error):
                trusted.load_shared_reference(KEY["profileId"], KEY["mapId"], KEY["floor"],
                                              variant_id=KEY["variantId"])


if __name__ == "__main__":
    unittest.main()
