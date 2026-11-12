"""Real package preparation and RAM query seams; no game or GUI claims."""
from contextlib import ExitStack
from copy import copy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import struct
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import zlib

import numpy as np

from client_imported_providers import load_native_resources
from client_providers import SharedReferenceProvider
from imported_native_base import NativeBaseBundle
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from map_identity_engine import MapIdentityEngine
from structure_pose_inputs import (
    PoseIndexSource, PoseInputsUnavailable, StructurePoseInputCache, prepare_structure_pose_inputs,
)
import test_export_native_base_package as native_fixture


def _grid_body_fault(kind):
    """Legal NPY declarations with a failure only when the array is decoded."""
    if kind == "structured":
        stream = BytesIO()
        np.savez(stream, grid_chains=np.zeros((1000, 3, 2), dtype=[("x", "f8"), ("y", "f8")]),
                 grid_rays=np.ones((1000, 2, 2)))
        return stream.getvalue()
    stream = BytesIO()
    np.save(stream, np.ones((1000, 3, 2)))
    full = stream.getvalue()
    rays = BytesIO()
    np.save(rays, np.ones((1000, 2, 2)))
    if kind == "truncated":
        body, compression = full[:-16], zipfile.ZIP_STORED
        crc = zlib.crc32(body)
    elif kind == "deflate":
        compressor = zlib.compressobj(level=0, wbits=-15)
        body = compressor.compress(full[:16384]) + compressor.flush(zlib.Z_SYNC_FLUSH) + b"\x07"
        compression, crc = zipfile.ZIP_DEFLATED, zlib.crc32(full)
    else:
        raise ValueError(kind)
    target = BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("grid_chains.npy", body)
        archive.writestr("grid_rays.npy", rays.getvalue())
    raw = bytearray(target.getvalue())
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        offset = archive.getinfo("grid_chains.npy").header_offset
    struct.pack_into("<H", raw, offset + 8, compression)
    struct.pack_into("<I", raw, offset + 14, crc)
    struct.pack_into("<I", raw, offset + 22, len(full))
    end = raw.rfind(b"PK\x05\x06")
    position = struct.unpack_from("<I", raw, end + 16)[0]
    while position < end:
        name, extra, comment = struct.unpack_from("<3H", raw, position + 28)
        if raw[position + 46:position + 46 + name] == b"grid_chains.npy":
            struct.pack_into("<H", raw, position + 10, compression)
            struct.pack_into("<I", raw, position + 16, crc)
            struct.pack_into("<I", raw, position + 24, len(full))
            break
        position += 46 + name + extra + comment
    return bytes(raw)


class StructurePoseInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = native_fixture.NativeBaseExporterTests("runTest")
        source.setUp()
        cls.addClassCleanup(source.doCleanups)
        for row in source.rows:
            path = source.source / row["resourcePath"]
            with np.load(path, allow_pickle=False) as archive:
                data = {name: archive[name] for name in archive.files}
            metadata = json.loads(str(data["metadata"].item()))
            metadata["sourceGeometry"] = dict(sourceCatalogSha256="c" * 64,
                                               rendererMetadataSha256="d" * 64)
            data["metadata"] = np.asarray(json.dumps(metadata))
            data["grid_chains"] = np.asarray([[[1., 0.], [0., 0.], [0., 1.]]] * 6)
            data["grid_rays"] = np.asarray([[[1., 0.], [0., 1.]]] * 6)
            np.savez_compressed(path, **data)
        source.export()
        cls.archives = {("s0", "nightmare"): (source.output.read_bytes(), source.mid)}

    @classmethod
    def archive(cls, season, mode):
        key = (season, mode)
        if key not in cls.archives:
            temporary = tempfile.TemporaryDirectory(prefix="pose-input-source-")
            cls.addClassCleanup(temporary.cleanup)
            if season == "s1":
                from test_export_s1_native_base_package import S1NativeBaseSourceFixture
                source = S1NativeBaseSourceFixture(Path(temporary.name), mode=mode)
                mid = source.ids[0]
                # A real synthetic S1 source with the existing coordinate-index
                # provenance fields, including matching saved fast metadata.
                for binding, relative in source.resources.items():
                    path = source.prefixRoot / relative
                    with np.load(path, allow_pickle=False) as archive:
                        data = {name: archive[name] for name in archive.files}
                    metadata = json.loads(str(data["metadata"].item()))
                    metadata["sourceGeometry"] = dict(sourceCatalogSha256="c" * 64,
                                                       rendererMetadataSha256="d" * 64)
                    data["metadata"] = np.asarray(json.dumps(metadata))
                    np.savez_compressed(path, **data)
                    prepared_path = source.prefixRoot / source.prepared_resources[binding]
                    with np.load(prepared_path, allow_pickle=False) as archive:
                        data = {name: archive[name] for name in archive.files}
                    data["sourceMetadata"] = np.asarray(json.dumps(metadata))
                    np.savez_compressed(prepared_path, **data)
                source.refresh_after_source_edit()
            else:
                from test_export_s0_difficult_native_base_package import S0DifficultSourceFixture, LEGACY
                source = S0DifficultSourceFixture(Path(temporary.name))
                mid = LEGACY
            source.export()
            cls.archives[key] = (source.output.read_bytes(), mid)
        return cls.archives[key]

    def engine(self, season="s0", mode="nightmare", *, map_id=None, extra_files=None):
        raw, default_mid = self.archive(season, mode)
        temporary = tempfile.TemporaryDirectory(prefix="pose-input-view-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        archive = root / "base.zip"
        archive.write_bytes(raw)
        current = {"base": True, "controls": True}
        saved = save_archive(archive, root / "resources")
        refs = saved.component_refs
        if extra_files:
            package = root / "resources" / "packages" / refs[0].package_id / refs[0].resource_version
            manifest_path = package / "manifest.json"
            manifest = json.loads(manifest_path.read_bytes())
            for relative, payload in extra_files.items():
                target = package / "data" / "base" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                manifest["files"].append(dict(componentId="base", path=relative,
                    bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
            manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            manifest_path.write_bytes(manifest_raw)
            refs = tuple(replace(ref, manifest_sha256=hashlib.sha256(manifest_raw).hexdigest()) for ref in refs)
        def guard(component):
            def check():
                if not current[component]:
                    raise ValueError(component + "-selection-superseded")
            return check
        views = {ref.component_id: PackageView(root / "resources", ref,
                    assert_current=guard(ref.component_id)) for ref in refs}
        bundle, controls, native, provider, check = load_native_resources(
            views["base"], views["controls"], season=season, mode=mode, require_rank=False)
        engine = SimpleNamespace(pack=bundle.pack, qt_native_base=bundle,
            qt_fast_structure_source=bundle.fast_structure_source, qt_shared_provider=provider,
            qt_adapters=bundle.adapters, qt_assert_resource_current=check)
        engine.pose_index_candidates = MethodType(MapIdentityEngine.pose_index_candidates, engine)
        return engine, str(map_id or default_mid), current

    def prepare(self, engine, mid, floor="1", **kwargs):
        return prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4, **kwargs)

    def test_real_inputs_are_immutable_and_queries_need_no_live_resource_objects(self):
        engine, mid, _ = self.engine()
        inputs = self.prepare(engine, mid, recovery_floors=("2",), pose_index_enabled=True)
        self.assertEqual(inputs.floors, ("1", "2"))
        self.assertEqual(inputs.binding.component_ref, engine.qt_native_base.component_ref)
        self.assertEqual(inputs.binding.resource_revision, 4)
        self.assertGreater(inputs.preparation_seconds, 0)
        reference = inputs.reference("shared-structure", mid, "1")
        prepared = inputs.prepared(reference)
        self.assertIsNotNone(prepared)
        for array in (reference.clean_mask, reference.validation_mask, prepared.distance,
                      prepared.chains, prepared.rays,
                      inputs.adapters.matrix_profile_to_canonical("shared-structure", mid, "1")):
            with self.assertRaises(ValueError):
                array.setflags(write=True)
        with self.assertRaises(TypeError):
            reference.metadata["binding"]["mapId"] = "changed"
        frame = np.zeros((128, 128, 3), np.uint8)
        from fast_structure_alignment import propose
        from nightmare_candidate import selected_route_current_frame_residual
        from single_map_structure import current_geometry
        from entry_passport_evidence import PassportEntryEvidence
        from side_entry_evidence import SideEntryEvidence
        with ExitStack() as stack:
            for owner, name in ((ComponentResourceReader, "__call__"),
                                (SharedReferenceProvider, "__call__"),
                                (NativeBaseBundle, "assert_current"),
                                (Path, "resolve"), (Path, "stat"), (Path, "read_bytes"), (Path, "read_text")):
                stack.enter_context(patch.object(owner, name, side_effect=AssertionError("query resource IO")))
            stack.enter_context(patch.object(engine.qt_fast_structure_source, "read",
                                            side_effect=AssertionError("query source IO")))
            stack.enter_context(patch.object(engine, "qt_assert_resource_current",
                                            side_effect=AssertionError("query native guard")))
            geometry = inputs.new_geometry_engine()
            self.assertFalse(hasattr(geometry, "qt_assert_resource_current"))
            self.assertFalse(hasattr(geometry, "qt_fast_structure_source"))
            self.assertIs(geometry.qt_shared_provider("shared-structure", mid, "1"), reference)
            self.assertEqual(geometry.identity_profile_for_map(mid), inputs.pack.identity_profiles[mid])
            self.assertFalse(geometry.entry_identity.rank(frame, "1", map_id=mid)[0])
            geometry.side_entry_identity.rank(frame, "1", map_id=mid, budget_seconds=.02)
            geometry.side_entry_identity.rank_entry(frame, "2", budget_seconds=.02)
            geometry.side_entry_identity.verify(frame, dict(mapId=mid, floor="1",
                matrixSourceToClient=np.eye(3)), context=None)
            geometry.side_entry_identity.refine_candidate(frame, dict(mapId=mid, floor="1",
                matrixSourceToClient=np.eye(3)))
            rows, context, diagnostics = propose(frame, reference, prepared)
            self.assertIsInstance(rows, list)
            selected_route_current_frame_residual(frame, None, np.eye(3),
                reference_override=reference.validation_mask, shared_reference=reference)
            self.assertEqual(geometry.pose_index_candidates(frame, mid, "1")[1]["reason"],
                             "pose-index-unavailable")
        self.assertIsNot(inputs.new_geometry_engine().entry_identity, geometry.entry_identity)

    def test_later_disk_damage_keeps_ram_but_new_preparation_rejects_it(self):
        engine, mid, _ = self.engine()
        inputs = self.prepare(engine, mid)
        reference = inputs.reference("shared-structure", mid, "1")
        pixels = reference.clean_mask.copy()
        reference.path.write_bytes(b"damaged after preparation")
        np.testing.assert_array_equal(reference.clean_mask, pixels)
        self.assertIsNotNone(inputs.prepared(reference))
        with self.assertRaises(ValueError):
            self.prepare(engine, mid)

    def test_preparation_rejects_byte_manifest_reader_root_and_selection_changes(self):
        for mutation in ("bytes", "manifest", "reader", "source-root", "base-selection", "controls-selection"):
            with self.subTest(mutation=mutation):
                engine, mid, current = self.engine()
                source = engine.qt_fast_structure_source
                decode = source.decode
                changed = []
                def change(raw, reference):
                    value = decode(raw, reference)
                    if changed:
                        return value
                    changed.append(True)
                    if mutation == "bytes":
                        reference.path.write_bytes(b"changed during preparation")
                    elif mutation == "manifest":
                        manifest = source.view.root.parent.parent / "manifest.json"
                        replacement = manifest.with_name("replacement.json")
                        replacement.write_bytes(manifest.read_bytes())
                        replacement.replace(manifest)
                    elif mutation == "reader":
                        other = copy(source.reader)
                        other.root = source.root / "different-root"
                        source.reader = other
                    elif mutation == "source-root":
                        source.root = source.root / "different-root"
                    else:
                        current[mutation.split("-")[0]] = False
                    return value
                with patch.object(source, "decode", side_effect=change):
                    with self.assertRaises(ValueError):
                        self.prepare(engine, mid)
                self.assertTrue(changed)

    def test_independent_reader_view_retains_its_own_selection_check(self):
        engine, mid, _ = self.engine()
        source = engine.qt_fast_structure_source
        selected = [True]
        def guard():
            if not selected[0]:
                raise ValueError("independent-reader-superseded")
        source.reader = ComponentResourceReader(PackageView(source.view.resource_root,
            source.view.component_ref, assert_current=guard))
        self.assertIsNotNone(self.prepare(engine, mid).reference("shared-structure", mid, "1"))
        decode = source.decode
        def revoke(raw, reference):
            value = decode(raw, reference)
            selected[0] = False
            return value
        with patch.object(source, "decode", side_effect=revoke):
            with self.assertRaisesRegex(ValueError, "independent-reader-superseded"):
                self.prepare(engine, mid)

    def test_optional_preparation_missing_preserves_reference_and_capability(self):
        engine, mid, _ = self.engine()
        source = engine.qt_fast_structure_source
        binding = next(key for key in source.entries if key[1] == mid and key[3] == "1")
        (source.root / source.entries[binding][1].path).write_bytes(b"missing optional arrays")
        inputs = self.prepare(engine, mid)
        self.assertIsNotNone(inputs.reference("shared-structure", mid, "1"))
        self.assertIsNone(inputs.prepared(inputs.reference("shared-structure", mid, "1")))
        self.assertIn("fast-prepared-unavailable:1", inputs.gaps)
        self.assertTrue(inputs.capabilities["rankReady"])

    def test_prepared_scope_is_explicit_and_complete_catalog_is_retained(self):
        engine, mid, _ = self.engine()
        inputs = self.prepare(engine, mid)
        self.assertEqual(set(inputs.pack.by_id), set(engine.pack.by_id))
        inputs.require_scope(mid, "1")
        for query in ((mid, "2"), ("unknown", "1")):
            with self.assertRaises(PoseInputsUnavailable):
                inputs.require_scope(*query)
            with self.assertRaises(PoseInputsUnavailable):
                inputs.new_geometry_engine().qt_shared_provider("shared-structure", *query)
        with self.assertRaises(PoseInputsUnavailable):
            self.prepare(engine, "unknown")

    def test_cache_is_bounded_memory_only_and_revisions_do_not_alias(self):
        engine, mid, _ = self.engine()
        one, two = self.prepare(engine, mid), self.prepare(engine, mid, "2")
        cache = StructurePoseInputCache(max_entries=1, max_bytes=one.retained_bytes + two.retained_bytes)
        self.assertTrue(cache.put(engine, one))
        with patch.object(engine, "qt_assert_resource_current", side_effect=AssertionError("cache IO")):
            self.assertIs(cache.get(engine, mid, "1", resource_revision=4), one)
            self.assertIsNone(cache.get(engine, mid, "1", resource_revision=5))
        self.assertTrue(cache.put(engine, two))
        self.assertIsNone(cache.get(engine, mid, "1", resource_revision=4))
        self.assertLessEqual(cache.retained_bytes, cache.max_bytes)
        self.assertIsNotNone(one.reference("shared-structure", mid, "1"))
        cache.invalidate()
        self.assertEqual(cache.retained_bytes, 0)
        self.assertFalse(StructurePoseInputCache(max_bytes=1).put(engine, one))

    def test_unknown_pose_index_callable_is_not_smuggled_into_math(self):
        engine, mid, _ = self.engine()
        engine.pose_index_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("IO provider"))
        inputs = self.prepare(engine, mid)
        with self.assertRaisesRegex(PoseInputsUnavailable, "not-prepared"):
            inputs.new_geometry_engine().pose_index_candidates(None, mid, "1")
        with self.assertRaisesRegex(PoseInputsUnavailable, "prepare-adapter-required"):
            self.prepare(engine, mid, pose_index_enabled=True)

    def test_declared_grid_index_uses_original_query_with_prepared_arrays_only(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        engine, mid, _ = self.engine()
        reference = engine.qt_shared_provider("shared-structure", mid, "1")
        root = engine.qt_native_base.reader.root
        relative = reference.path.relative_to(root).as_posix()
        engine.grid_locator = DifficultGridLocator(root, {(mid, "1"): dict(
            gridResource=relative, sourceShape=[8, 8], gridUnitPixels=12)})
        engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
        inputs = self.prepare(engine, mid, pose_index_enabled=True)
        with patch.object(ComponentResourceReader, "__call__", side_effect=AssertionError("grid IO")), \
             patch.object(Path, "stat", side_effect=AssertionError("grid stat")), \
             patch.object(Path, "resolve", side_effect=AssertionError("grid resolve")), \
             patch.object(np, "load", side_effect=AssertionError("grid lazy decode")):
            rows, diagnostic = inputs.new_geometry_engine().pose_index_candidates(
                np.zeros((128, 128, 3), np.uint8), mid, "1")
        self.assertEqual(rows, [])
        self.assertEqual(diagnostic["reason"], "grid-query-unavailable")
        for value in inputs._grid.load(mid, "1"):
            with self.assertRaises(ValueError):
                value.setflags(write=True)

    def test_coordinate_prior_is_separate_verified_ram_data(self):
        from s1_pose_prior import AcknowledgedPose, CoordinatePriorIndex
        engine, mid, _ = self.engine("s1", "difficult")
        path = engine.qt_native_base.reader.root.parent.parent.parent / "prior.json"
        entries = {str(-floor): dict(sourceCatalogSha256="c" * 64,
            sourceRendererMetadataSha256="d" * 64, shapeHW=[8, 8],
            imageToEntranceXZ=[[1, 0, 10 * floor], [0, -1, 20 * floor], [0, 0, 1]])
            for floor in (1, 2)}
        reference = engine.qt_shared_provider("shared-structure", mid, "-1")
        raw = json.dumps(dict(schema="s1-common-entrance-coordinate-index-v1",
            sourceVersion=reference.metadata["sourceVersion"], maps={"difficult": {mid: entries}})).encode()
        path.write_bytes(raw)
        declaration = PoseIndexSource(path, hashlib.sha256(raw).hexdigest(), "difficult", "fixture-resource")
        inputs = self.prepare(engine, mid, "-1", recovery_floors=("-2",), pose_index_source=declaration)
        index = CoordinatePriorIndex(path, expected_sha256=declaration.expected_sha256,
            mode=declaration.mode, resource_key=declaration.resource_key,
            provider=lambda mid, floor: inputs.reference("shared-structure", mid, floor))
        source = AcknowledgedPose("difficult", "fixture-resource", mid, "-1", "round", "frame",
                                 tuple(map(tuple, np.eye(3))), True, True)
        expected = index.make_hint(source, target_floor="-2", current_owner_scope="round")
        self.assertIsNotNone(expected)
        path.write_bytes(b"damaged after preparation")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("prior IO")):
            actual = inputs.coordinate_prior.make_hint(source, target_floor="-2", current_owner_scope="round")
        self.assertEqual(actual, expected)
        self.assertFalse(inputs.pose_index_enabled)
        with self.assertRaisesRegex(ValueError, "digest-mismatch"):
            self.prepare(engine, mid, "-1", recovery_floors=("-2",), pose_index_source=declaration)

    def test_optional_grid_payload_failure_keeps_original_unavailable_query(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        engine, mid, _ = self.engine()
        root = engine.qt_native_base.reader.root
        optional = engine.qt_shared_provider("shared-structure", mid, "2").path
        engine.grid_locator = DifficultGridLocator(root, {(mid, "1"): dict(
            gridResource=optional.relative_to(root).as_posix(), sourceShape=[8, 8], gridUnitPixels=12)})
        engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
        optional.write_bytes(b"unavailable optional grid payload")
        inputs = self.prepare(engine, mid, pose_index_enabled=True)
        self.assertIsNotNone(inputs.reference("shared-structure", mid, "1"))
        self.assertIn("pose-index-grid-unavailable:1", inputs.gaps)
        rows, diagnostic = inputs.new_geometry_engine().pose_index_candidates(
            np.zeros((128, 128, 3), np.uint8), mid, "1")
        self.assertEqual(rows, [])
        self.assertEqual(diagnostic["reason"], "grid-resource-unavailable")

    def test_optional_grid_bad_header_with_valid_manifest_keeps_initial_fallback(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        from structure_pose_solver import PreparedStructurePoseSolver
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("grid_chains.npy", np.lib.format.magic(2, 0) + struct.pack("<I", 2**32 - 1))
        engine, mid, _ = self.engine(extra_files={"optional-grid.npz": stream.getvalue()})
        reader = engine.qt_native_base.reader
        optional = reader.root / "optional-grid.npz"
        # This is a genuine semantic-preflight error, not altered bytes against
        # an old manifest. The single-file public reader does not tag its origin.
        with self.assertRaisesRegex(ValueError, "imported-array-header-size") as rejected:
            reader(optional)
        self.assertIsNone(getattr(rejected.exception, "resource_file_ref", None))
        engine.grid_locator = DifficultGridLocator(reader.root, {(mid, "1"): dict(
            gridResource="optional-grid.npz", sourceShape=[8, 8], gridUnitPixels=12)})
        engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
        inputs = self.prepare(engine, mid, pose_index_enabled=True)
        self.assertIn("pose-index-grid-unavailable:1", inputs.gaps)
        frame = np.zeros((128, 128, 3), np.uint8)
        fit = PreparedStructurePoseSolver(inputs, settings={"poseIndexCandidatesEnabled": True}).fit(
            frame, mid, "1", lambda: False)
        self.assertFalse(fit["success"])
        self.assertEqual(fit["diagnostics"]["poseIndexProvider"]["reason"], "grid-resource-unavailable")

    def test_optional_grid_bad_crc_with_valid_manifest_keeps_initial_fallback(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        from imported_array_archive import validate_npz_bytes
        from structure_pose_solver import PreparedStructurePoseSolver
        stream = BytesIO()
        np.savez(stream, grid_chains=np.ones((1000, 3, 2)), grid_rays=np.ones((1000, 2, 2)))
        raw = bytearray(stream.getvalue())
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            info = archive.getinfo("grid_chains.npy")
        name_length, extra_length = struct.unpack_from("<HH", raw, info.header_offset + 26)
        body = info.header_offset + 30 + name_length + extra_length
        raw[body + info.file_size - 8] ^= 1
        malformed = bytes(raw)
        validate_npz_bytes(malformed)  # Header preflight deliberately cannot prove body CRC.
        engine, mid, _ = self.engine(extra_files={"optional-grid.npz": malformed})
        reader = engine.qt_native_base.reader
        optional = reader.root / "optional-grid.npz"
        self.assertEqual(reader.read_many((optional,), expected_root=reader.root), (malformed,))
        engine.grid_locator = DifficultGridLocator(reader.root, {(mid, "1"): dict(
            gridResource="optional-grid.npz", sourceShape=[8, 8], gridUnitPixels=12)})
        self.assertIsNone(engine.grid_locator.load(mid, "1"))
        engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
        inputs = self.prepare(engine, mid, pose_index_enabled=True)
        self.assertIn("pose-index-grid-unavailable:1", inputs.gaps)
        fit = PreparedStructurePoseSolver(inputs, settings={"poseIndexCandidatesEnabled": True}).fit(
            np.zeros((128, 128, 3), np.uint8), mid, "1", lambda: False)
        self.assertFalse(fit["success"])
        self.assertEqual(fit["diagnostics"]["poseIndexProvider"]["reason"], "grid-resource-unavailable")

    def test_optional_grid_array_body_faults_stay_unavailable_after_real_preflight(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        from imported_array_archive import validate_npz_bytes
        from structure_pose_solver import PreparedStructurePoseSolver
        for kind, error in (("truncated", ValueError), ("deflate", zlib.error), ("structured", TypeError)):
            with self.subTest(kind=kind):
                malformed = _grid_body_fault(kind)
                validate_npz_bytes(malformed)
                with self.assertRaises(error):
                    with np.load(BytesIO(malformed), allow_pickle=False) as archive:
                        np.asarray(archive["grid_chains"], float)
                engine, mid, _ = self.engine(extra_files={"optional-grid.npz": malformed})
                reader = engine.qt_native_base.reader
                optional = reader.root / "optional-grid.npz"
                self.assertEqual(reader.read_many((optional,), expected_root=reader.root), (malformed,))
                engine.grid_locator = DifficultGridLocator(reader.root, {(mid, "1"): dict(
                    gridResource="optional-grid.npz", sourceShape=[8, 8], gridUnitPixels=12)})
                engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
                inputs = self.prepare(engine, mid, pose_index_enabled=True)
                self.assertIn("pose-index-grid-unavailable:1", inputs.gaps)
                fit = PreparedStructurePoseSolver(inputs, settings={"poseIndexCandidatesEnabled": True}).fit(
                    np.zeros((128, 128, 3), np.uint8), mid, "1", lambda: False)
                self.assertFalse(fit["success"])
                self.assertEqual(fit["diagnostics"]["poseIndexProvider"]["reason"], "grid-resource-unavailable")

    def test_optional_grid_decoder_does_not_swallow_memory_or_process_exit(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        stream = BytesIO()
        np.savez(stream, grid_chains=np.ones((1000, 3, 2)), grid_rays=np.ones((1000, 2, 2)))
        valid = stream.getvalue()
        for error in (MemoryError, KeyboardInterrupt, SystemExit):
            with self.subTest(error=error.__name__):
                engine, mid, _ = self.engine(extra_files={"optional-grid.npz": valid})
                reader = engine.qt_native_base.reader
                engine.grid_locator = DifficultGridLocator(reader.root, {(mid, "1"): dict(
                    gridResource="optional-grid.npz", sourceShape=[8, 8], gridUnitPixels=12)})
                engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
                load = np.load
                def stop_optional(source, *args, **kwargs):
                    if isinstance(source, BytesIO) and source.getvalue() == valid:
                        raise error("controlled decoder interruption")
                    return load(source, *args, **kwargs)
                with patch.object(np, "load", side_effect=stop_optional):
                    with self.assertRaises(error):
                        self.prepare(engine, mid, pose_index_enabled=True)

    def test_optional_grid_semantic_failure_does_not_hide_context_change(self):
        from difficult_grid import DifficultGridLocator
        from difficult_mode import DifficultCandidateEngine
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("grid_chains.npy", np.lib.format.magic(2, 0) + struct.pack("<I", 2**32 - 1))
        malformed = stream.getvalue()
        for mutation, expected in (("manifest", "manifest-replaced"),
                                   ("source-root", "component-root-changed"),
                                   ("reader-root", "resource-binding-changed"),
                                   ("base", "base-selection-superseded"),
                                   ("controls", "controls-selection-superseded")):
            with self.subTest(mutation=mutation):
                engine, mid, current = self.engine(extra_files={"optional-grid.npz": malformed})
                source = engine.qt_fast_structure_source
                reader = engine.qt_native_base.reader
                engine.grid_locator = DifficultGridLocator(reader.root, {(mid, "1"): dict(
                    gridResource="optional-grid.npz", sourceShape=[8, 8], gridUnitPixels=12)})
                engine.pose_index_candidates = MethodType(DifficultCandidateEngine.pose_index_candidates, engine)
                preflight, changed = reader._check_payload, []
                def change(raw, extension):
                    if raw == malformed and not changed:
                        changed.append(True)
                        if mutation == "manifest":
                            manifest = source.view.root.parent.parent / "manifest.json"
                            replacement = manifest.with_name("replacement.json")
                            replacement.write_bytes(manifest.read_bytes())
                            replacement.replace(manifest)
                        elif mutation == "source-root":
                            source.root = source.root / "changed-root"
                        elif mutation == "reader-root":
                            reader.root = reader.root / "changed-reader-root"
                        else:
                            current[mutation] = False
                    return preflight(raw, extension)
                with patch.object(reader, "_check_payload", side_effect=change):
                    with self.assertRaisesRegex(ValueError, expected):
                        self.prepare(engine, mid, pose_index_enabled=True)
                self.assertTrue(changed)

    def test_four_mode_capabilities_and_legacy_source_authority_are_preserved(self):
        for season, mode in (("s0", "nightmare"), ("s0", "difficult"),
                             ("s1", "difficult"), ("s1", "nightmare")):
            with self.subTest(season=season, mode=mode):
                engine, mid, _ = self.engine(season, mode)
                floor = "-3" if season == "s1" else "1"
                inputs = self.prepare(engine, mid, floor, pose_index_enabled=True)
                self.assertEqual((inputs.binding.season, inputs.binding.mode), (season, mode))
                self.assertEqual(inputs.capabilities["rankReady"], engine.qt_native_base.rank_ready)
                self.assertEqual(inputs.capabilities["semanticSideEntries"],
                                 engine.qt_native_base.semantic_side_entries)
                reference = inputs.reference("shared-structure", mid, floor)
                self.assertIsNotNone(reference)
                if (season, mode) == ("s0", "difficult"):
                    self.assertEqual(reference.metadata["sourceKind"], "legacy-shared-binary")
                    self.assertIs(reference.metadata["authorizesMapIdentity"], False)
                    self.assertIs(reference.metadata["authorizesStructureValidation"], False)
                    self.assertIsNone(inputs.prepared(reference))
                if season == "s1":
                    self.assertFalse(inputs.capabilities["semanticSideEntries"])


if __name__ == "__main__":
    unittest.main()
