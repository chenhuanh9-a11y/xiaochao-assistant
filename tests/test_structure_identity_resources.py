"""Real producer/store/bundle/prepared seams over synthetic typed pixels.

These fixtures test inventory and lifetime, not game recognition accuracy.
Only the duplicate-description unit fixture constructs prepared rows directly.
"""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from client_providers import SharedReferenceProvider
from fast_structure_alignment import prepare
from imported_native_base import decode_native_base
from imported_resource_packages import FileRef
from side_entry_search import write_index
from structure_identity_index import ConstellationA
from structure_identity_resources import (FEATURE_CLASS, build_structure_identity_resources,
                                          footprint_anchors)
import test_export_native_base_package as native_fixture
import test_export_s0_difficult_native_base_package as difficult_fixture
import test_imported_native_base as installed_fixture


def footprint_mask():
    mask = np.zeros((64, 64), np.uint8)
    mask[8:56, 8:56] = 1
    mask[32:56, 32:56] = 0
    return mask


class StructureIdentityResourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the actual native producer fixture, enlarging its typed canvas
        # so the actual contour/preparation producer yields six real corners.
        fixture = native_fixture.NativeBaseExporterTests("runTest")
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        cls.map_id = fixture.mid
        for typed, local, neutral in zip(fixture.rows, fixture.local, fixture.neutral):
            canvas = local["canvas"]
            canvas.update(width=64, height=64, shapeHW=[64, 64])
            path = fixture.source / typed["resourcePath"]
            with np.load(path, allow_pickle=False) as archive:
                metadata = json.loads(str(archive["metadata"].item()))
            metadata.update(canvas=dict(canvas), authorPixelsUsedForStructure=False,
                authorizesMapIdentity=False, authorizesStructureValidation=False,
                roleSemantics=dict(shared_mask="renderer alpha footprint; not semantic walls"),
                sourceGeometry=dict(alphaRule="alpha>0", rendererVerified=False))
            np.savez_compressed(path, metadata=np.asarray(json.dumps(metadata)), shared_mask=footprint_mask())
        fixture.write(native_fixture.exporter.NEUTRAL, dict(schemaVersion=1,
            resourceKind="typed-shared-structure-display", indexPath=native_fixture.exporter.INDEX,
            entries=fixture.neutral))
        coordinates = fixture.read(native_fixture.exporter.COORDINATES)
        for record in coordinates["maps"][fixture.mid]["floors"].values():
            record.update(coordinateWidth=64, coordinateHeight=64)
        fixture.write(native_fixture.exporter.COORDINATES, coordinates)
        side = fixture.read(native_fixture.exporter.ENTRIES + "/side-entries.json")
        side["entries"] = fixture.local
        fixture.write(native_fixture.exporter.ENTRIES + "/side-entries.json", side)
        front = fixture.read(native_fixture.exporter.ENTRIES + "/entries.json")
        front["entries"][0]["canvas"] = dict(fixture.local[0]["canvas"])
        fixture.write(native_fixture.exporter.ENTRIES + "/entries.json", front)
        door = fixture.read(native_fixture.exporter.ENTRIES + "/side-door-anchors.json")["entries"][0]
        search_path = fixture.entry_root / "side-entry-search.npz"
        assert search_path.resolve().is_relative_to(fixture.root.resolve())
        search_path.unlink()
        write_index(search_path, fixture.prepared_entries, {fixture.mid: door})
        fixture.export()
        cls.archive = fixture.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {name: archive.read(name) for name in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="structure-identity-resources-")
        self.addCleanup(temporary.cleanup)
        self.root, self.counter = Path(temporary.name), 0

    view = installed_fixture.ImportedNativeBaseTests.view

    def components(self, view=None):
        view = self.view() if view is None else view
        bundle = decode_native_base(view)
        provider = SharedReferenceProvider(view.root, bundle.resources, bundle.versions,
                                          check_current=bundle.assert_current)
        return view, bundle, provider

    def build(self, bundle, provider, **kwargs):
        # Runtime source assembly cannot fall back to the offline pixel producer.
        with patch("fast_structure_alignment.prepare", side_effect=AssertionError("online preparation")):
            return build_structure_identity_resources(bundle, provider, **kwargs)

    def test_real_exported_footprints_make_full_geometry_index_without_identity_authority(self):
        view, bundle, provider = self.components()
        old_rank = bundle.rank_ready
        resources = self.build(bundle, provider)
        self.assertEqual(resources.feature_class, FEATURE_CLASS)
        self.assertEqual(len(resources.index.domain), 3)
        self.assertTrue(resources.geometry_complete)
        self.assertEqual(bundle.rank_ready, old_rank)
        self.assertEqual(resources.original_rank_ready, old_rank)
        self.assertFalse(resources.authorizesIdentity)
        self.assertFalse(resources.lock)
        self.assertTrue(resources.identity_issues)
        for info, directory in zip(resources.sources, resources.index.entries):
            self.assertTrue(info.geometry_searchable)
            self.assertEqual(info.source_kind, "game-rendered-shared-structure")
            self.assertFalse(info.known_boundary_available)
            self.assertFalse(info.unknown_mask_available)
            self.assertIn(("authorizesStructureValidation", False), info.original_authority)
            self.assertIn("renderer-footprint-equivalence-unverified", info.qualification_issues)
            self.assertIsInstance(info.reference_resource, FileRef)
            self.assertIsInstance(info.prepared_resource, FileRef)
            self.assertEqual(info.reference_resource.component, view.component_ref)
            binding = json.loads(directory.resource_binding)
            self.assertEqual(binding["binding"]["mapId"], self.map_id)
            self.assertEqual(binding["reference"]["path"], info.reference_resource.path)
            self.assertEqual(binding["prepared"]["path"], info.prepared_resource.path)
            self.assertEqual([anchor.feature_id for anchor in directory.anchors], list(range(6)))
        result = resources.index.retrieve(ConstellationA(resources.index.entries[0].anchors, True), epsilon=.1)
        self.assertTrue(result.fingerprint_unique)
        self.assertEqual(len(result.compatible_map_floors), 3)
        self.assertFalse(result.authorizesIdentity)
        resources.check_current()

    def test_declared_missing_layer_stays_in_full_domain_instead_of_using_existing_prepared(self):
        def unavailable(pack):
            pack["maps"][0]["layers"]["2"] = None
            return pack
        view = self.view({native_fixture.exporter.PACK: unavailable})
        _, bundle, provider = self.components(view)
        self.assertFalse(bundle.ready)
        resources = self.build(bundle, provider)
        self.assertEqual(len(resources.index.domain), 3)
        self.assertFalse(resources.geometry_complete)
        missing = next(row for row in resources.index.entries if row.key.floor == "2")
        self.assertEqual(missing.unavailable_reason, "declared-native-layer-unavailable")
        self.assertIsNone(missing.anchors)
        query = next(row.anchors for row in resources.index.entries if row.anchors)
        result = resources.index.retrieve(ConstellationA(query, True), epsilon=.1)
        self.assertTrue(result.proposals)
        self.assertFalse(result.fingerprint_unique)

    def test_same_floor_domain_does_not_depend_on_prepared_availability(self):
        view, bundle, provider = self.components()
        key = next(key for key in bundle.fast_structure_source.entries if key[3] == "1")
        preparation = bundle.fast_structure_source.entries[key][1]
        path = view.path(preparation.path)
        raw = path.read_bytes()
        path.unlink()
        resources = self.build(bundle, provider, floor="1")
        self.assertEqual([(row.map_id, row.floor) for row in resources.index.domain], [(self.map_id, "1")])
        self.assertFalse(resources.geometry_complete)
        self.assertEqual(resources.index.entries[0].unavailable_reason, "saved-footprint-preparation-unavailable")
        resources.check_current()
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "prepared-resource-changed"):
            resources.check_current()

    def test_absent_optional_preparation_index_is_a_gap_without_online_prepare(self):
        def no_preparations(context):
            context["runtimeInterfaces"].pop("fastStructureIndex")
            return context
        view = self.view({"resource-context.json": no_preparations})
        _, bundle, provider = self.components(view)
        resources = self.build(bundle, provider)
        self.assertEqual(len(resources.index.domain), 3)
        self.assertFalse(resources.geometry_complete)
        self.assertTrue(all(row.prepared_resource is None for row in resources.sources))
        self.assertTrue(all(not row.geometry_searchable for row in resources.sources))

    def test_prepared_row_ids_and_shared_physical_centres_preserve_duplicate_descriptions(self):
        prepared = prepare(footprint_mask())
        chains = np.concatenate((prepared.chains, prepared.chains[[0]][:, [2, 1, 0]]))
        rays = np.concatenate((prepared.rays, prepared.rays[[0]][:, [1, 0]]))
        duplicate = replace(prepared, chains=chains, rays=rays)
        before = chains.copy(), rays.copy()
        anchors = footprint_anchors(duplicate)
        self.assertEqual([row.feature_id for row in anchors], list(range(7)))
        self.assertEqual(anchors[0].center_id, anchors[-1].center_id)
        self.assertEqual(len({row.center_id for row in anchors}), 6)
        self.assertEqual(anchors, footprint_anchors(duplicate))
        np.testing.assert_array_equal(chains, before[0])
        np.testing.assert_array_equal(rays, before[1])

    def test_required_payload_changes_are_rejected_after_cached_geometry(self):
        view, bundle, provider = self.components()
        resources = self.build(bundle, provider)
        path = view.path(resources.sources[0].reference_resource.path)
        path.write_bytes(b"changed-required-reference")
        with self.assertRaises(ValueError):
            resources.check_current()

    def test_owning_component_entrypoint_bytes_are_rechecked(self):
        view, bundle, provider = self.components()
        resources = self.build(bundle, provider)
        entrypoint = view.path(view.component["entrypoint"])
        entrypoint.write_bytes(b"changed-runtime-interfaces")
        with self.assertRaisesRegex(ValueError, "file-integrity-mismatch"):
            resources.check_current()

    def test_optional_prepared_payload_changes_invalidate_existing_snapshot(self):
        view, bundle, provider = self.components()
        resources = self.build(bundle, provider)
        view.path(resources.sources[0].prepared_resource.path).write_bytes(b"changed-optional-preparation")
        with self.assertRaisesRegex(ValueError, "prepared-resource-changed"):
            resources.check_current()

    def test_manifest_replacement_invalidates_even_lightweight_lifetime_check(self):
        view, bundle, provider = self.components()
        resources = self.build(bundle, provider)
        manifest = view.root.parent.parent / "manifest.json"
        replacement = manifest.with_suffix(".replacement")
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        with self.assertRaisesRegex(ValueError, "manifest-replaced"):
            resources.assert_current()

    def test_foreign_provider_cannot_supply_same_label_from_another_component(self):
        _, bundle, _ = self.components()
        _, _, foreign_provider = self.components()
        with self.assertRaisesRegex(ValueError, "outside-component"):
            self.build(bundle, foreign_provider)

    def test_invalid_floor_and_cancelled_build_do_not_return_partial_catalogue(self):
        _, bundle, provider = self.components()
        with self.assertRaisesRegex(ValueError, "floor-not-declared"):
            self.build(bundle, provider, floor="not-a-floor")
        with self.assertRaisesRegex(ValueError, "import-cancelled"):
            self.build(bundle, provider, cancelled=lambda: True)


class StructureIdentityLegacyResourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix="identity-legacy-producer-")
        cls.addClassCleanup(temporary.cleanup)
        source = difficult_fixture.S0DifficultSourceFixture(Path(temporary.name))
        source.export()
        cls.archive = source.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {name: archive.read(name) for name in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="identity-legacy-store-")
        self.addCleanup(temporary.cleanup)
        self.root, self.counter = Path(temporary.name), 0

    view = installed_fixture.ImportedNativeBaseTests.view

    def test_full_30_map_pool_retains_two_legacy_gaps_and_original_authority(self):
        view = self.view()
        bundle = decode_native_base(view, season="s0", mode="difficult")
        legacy = bundle.legacy_references
        provider = SharedReferenceProvider(view.root, bundle.resources, bundle.versions,
            legacy_reference_loader=legacy.reference, check_current=bundle.assert_current)
        resources = build_structure_identity_resources(bundle, provider)
        self.assertEqual(len(resources.index.domain), 60)
        self.assertEqual(len({row.map_id for row in resources.index.domain}), 30)
        self.assertFalse(resources.geometry_complete)
        self.assertFalse(bundle.rank_ready)
        self.assertFalse(resources.original_rank_ready)
        rows = [row for row in resources.sources if row.key.map_id == difficult_fixture.LEGACY]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertFalse(row.geometry_searchable)
            self.assertTrue(row.known_boundary_available)
            self.assertTrue(row.unknown_mask_available)
            self.assertEqual(row.source_class, "legacy-clean-working-draft")
            self.assertIn(("authorizesMapIdentity", False), row.original_authority)
            self.assertIn(("authorizesStructureValidation", False), row.original_authority)
        self.assertFalse(resources.authorizesIdentity)
        one_floor = build_structure_identity_resources(bundle, provider, floor="1")
        self.assertEqual(len(one_floor.index.domain), 30)
        self.assertIn(difficult_fixture.LEGACY, {row.map_id for row in one_floor.index.domain})
        self.assertFalse(one_floor.geometry_complete)


if __name__ == "__main__":
    unittest.main()
