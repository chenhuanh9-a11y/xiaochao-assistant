"""PackageView-backed I/O for shared structure references.

The fixture uses a real versioned package manifest and real NPZ bytes. It
checks the resource-reader seam only; no production decoder or game runtime is
substituted.
"""

import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import imported_resource_packages as packages
from shared_structure_reference import SharedStructureResourceStore, load_shared_structure


PACKAGE_ID = "shared-structure-fixture"
VERSION = "v1"
COMPONENT = "structures"
INDEX = "references/shared-structure-index.json"
PRODUCT = "references/map-1.npz"
BINDING = dict(profile="fixture-profile", mapId="map-1", floor="1", variant="variant-01")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _npz_bytes(mask, binding=BINDING):
    metadata = dict(sourceKind="game-rendered-shared-structure", binding=binding,
                    roles=dict(clean="shared_mask", validation="shared_mask"))
    output = io.BytesIO()
    np.savez_compressed(output, shared_mask=mask, metadata=json.dumps(metadata))
    return output.getvalue()


class SharedStructurePackageIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name).resolve()
        self.resource_root = self.storage / "resources"
        self.package_root = self.resource_root / "packages" / PACKAGE_ID / VERSION
        self.component_root = self.package_root / "data" / COMPONENT
        self.component_root.mkdir(parents=True)
        self.mask = np.zeros((7, 9), dtype=np.uint8)
        self.mask[1:5, 2:7] = 1
        self.npz_bytes = _npz_bytes(self.mask)

    def write_package(self, *, resource_path=PRODUCT, undeclared_bytes=None,
                      directory_path=None, assert_current=None, index_encoding="utf-8"):
        index_value = dict(schemaVersion=1, resourceKind="shared-structure",
                           entries=[dict(BINDING, resourcePath=resource_path)])
        index_bytes = _canonical(index_value).decode("utf-8").encode(index_encoding)
        files = {INDEX: index_bytes}
        if resource_path == PRODUCT:
            files[PRODUCT] = self.npz_bytes
        for relative, payload in files.items():
            target = self.component_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        if undeclared_bytes is not None:
            target = self.component_root / resource_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(undeclared_bytes)
        if directory_path is not None:
            target = self.component_root / directory_path
            target.mkdir(parents=True, exist_ok=True)
            (target / "inside.npz").write_bytes(self.npz_bytes)

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
            "files": [{
                "componentId": COMPONENT,
                "path": relative,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            } for relative, payload in sorted(files.items())],
            "provenance": {"source": "package-io-regression"},
            "displayName": "Shared structure fixture",
        }
        manifest_bytes = _canonical(manifest)
        (self.package_root / "manifest.json").write_bytes(manifest_bytes)
        ref = packages.ComponentRef(PACKAGE_ID, VERSION, COMPONENT,
                                    hashlib.sha256(manifest_bytes).hexdigest())
        return packages.PackageView(self.resource_root, ref, assert_current=assert_current)

    @staticmethod
    def package_reader(view, reads):
        component_root = view.root

        def read_resource(path):
            path = Path(path)
            try:
                relative = path.relative_to(component_root).as_posix()
            except ValueError as error:
                raise ValueError("resource path is outside the selected component") from error
            reads.append(relative)
            return view.read(relative)

        return read_resource

    def make_store(self, *, resource_path=PRODUCT, undeclared_bytes=None,
                   directory_path=None, assert_current=None, index_encoding="utf-8"):
        view = self.write_package(resource_path=resource_path,
                                  undeclared_bytes=undeclared_bytes,
                                  directory_path=directory_path,
                                  assert_current=assert_current,
                                  index_encoding=index_encoding)
        reads = []
        reader = self.package_reader(view, reads)
        store = SharedStructureResourceStore(view.path(INDEX), view.root,
                                             read_resource=reader)
        self.assertIsNone(store.error)
        return view, reader, reads, store

    def test_store_reads_index_and_npz_through_package_view_and_matches_file_decode(self):
        view, reader, reads, store = self.make_store()
        self.assertEqual(reads, [INDEX])
        product_path = view.path(PRODUCT)
        direct = load_shared_structure(product_path, expected_binding=BINDING,
                                       resource_root=view.root)
        self.assertIsNotNone(direct)

        reads.clear()
        via_reader = load_shared_structure(product_path, expected_binding=BINDING,
                                            resource_root=view.root,
                                            read_resource=reader)
        self.assertIsNotNone(via_reader)
        self.assertEqual(reads, [PRODUCT])
        np.testing.assert_array_equal(via_reader.clean_mask, direct.clean_mask)
        np.testing.assert_array_equal(via_reader.validation_mask, direct.validation_mask)

        reads.clear()
        loaded = store.load(BINDING["profile"], BINDING["mapId"], BINDING["floor"],
                            variant=BINDING["variant"])
        self.assertIsNotNone(loaded)
        self.assertEqual(reads, [PRODUCT])
        np.testing.assert_array_equal(loaded.clean_mask, direct.clean_mask)
        np.testing.assert_array_equal(loaded.validation_mask, direct.validation_mask)
        self.assertEqual(loaded.metadata["binding"], BINDING)
        self.assertEqual(loaded.path, product_path)
        self.assertEqual(loaded.resource_root, view.root)

    def test_utf16_index_is_rejected_by_default_and_package_reader_paths(self):
        view = self.write_package(index_encoding="utf-16")
        index_path = view.path(INDEX)

        legacy_store = SharedStructureResourceStore(index_path, view.root)
        self.assertIsNotNone(legacy_store.error)
        self.assertEqual(legacy_store.entries, {})

        reads = []
        reader = self.package_reader(view, reads)
        package_store = SharedStructureResourceStore(index_path, view.root,
                                                     read_resource=reader)
        self.assertIsNotNone(package_store.error)
        self.assertEqual(package_store.entries, {})
        self.assertEqual(reads, [INDEX])

    def test_rewritten_npz_is_rejected_without_disk_fallback(self):
        view, _, reads, store = self.make_store()
        rewritten = np.zeros_like(self.mask)
        rewritten[0, 0] = 1
        (view.root / PRODUCT).write_bytes(_npz_bytes(rewritten))
        self.assertIsNotNone(load_shared_structure(view.root / PRODUCT,
            expected_binding=BINDING, resource_root=view.root))

        reads.clear()
        result = store.load(BINDING["profile"], BINDING["mapId"], BINDING["floor"],
                            variant=BINDING["variant"])
        self.assertIsNone(result)
        self.assertEqual(reads, [PRODUCT])

    def test_directory_npz_reference_is_sent_to_package_view_and_rejected(self):
        directory = "references/directory.npz"
        _, _, reads, store = self.make_store(resource_path=directory,
                                             directory_path=directory)
        reads.clear()
        result = store.load(BINDING["profile"], BINDING["mapId"], BINDING["floor"],
                            variant=BINDING["variant"])
        self.assertIsNone(result)
        self.assertEqual(reads, [directory])

    def test_undeclared_valid_npz_is_rejected_without_disk_fallback(self):
        relative = "references/undeclared.npz"
        view, _, reads, store = self.make_store(resource_path=relative,
                                                undeclared_bytes=self.npz_bytes)
        self.assertIsNotNone(load_shared_structure(view.root / relative,
            expected_binding=BINDING, resource_root=view.root))

        reads.clear()
        result = store.load(BINDING["profile"], BINDING["mapId"], BINDING["floor"],
                            variant=BINDING["variant"])
        self.assertIsNone(result)
        self.assertEqual(reads, [relative])

    def test_out_of_bounds_npz_reference_never_reaches_or_falls_back_to_disk(self):
        relative = "../escape.npz"
        view, _, reads, store = self.make_store(resource_path=relative)
        escape = view.root.parent / "escape.npz"
        escape.write_bytes(self.npz_bytes)
        self.assertIsNotNone(load_shared_structure(escape, expected_binding=BINDING,
            resource_root=view.root))

        reads.clear()
        result = store.load(BINDING["profile"], BINDING["mapId"], BINDING["floor"],
                            variant=BINDING["variant"])
        self.assertIsNone(result)
        self.assertEqual(reads, [])

    def test_catalog_switch_during_reader_use_has_no_disk_fallback(self):
        active = {"catalog": "first"}

        def assert_current():
            if active["catalog"] != "first":
                raise ValueError("catalog changed")

        view, _, reads, store = self.make_store(assert_current=assert_current)
        root = view.root
        active["catalog"] = "second"
        self.assertIsNotNone(load_shared_structure(root / PRODUCT,
            expected_binding=BINDING, resource_root=root))

        reads.clear()
        result = store.load(BINDING["profile"], BINDING["mapId"], BINDING["floor"],
                            variant=BINDING["variant"])
        self.assertIsNone(result)
        self.assertEqual(reads, [PRODUCT])


if __name__ == "__main__":
    unittest.main()
