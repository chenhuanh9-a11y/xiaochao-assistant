"""Batch preparation over the real exported/installed typed package seam."""
from collections import Counter
from io import BytesIO
import gc
from pathlib import Path
import json
import struct
import traceback
import unittest
from unittest.mock import patch
import weakref
import zipfile

import numpy as np
import resource_snapshot_io
import test_structure_identity_resources as fixture
from structure_identity_resources import StructureIdentityResources


class StructureIdentityResourceBatchTests(unittest.TestCase):
    setUpClass = fixture.StructureIdentityResourcesTests.__dict__["setUpClass"]
    setUp = fixture.StructureIdentityResourcesTests.setUp
    view = fixture.StructureIdentityResourcesTests.view
    components = fixture.StructureIdentityResourcesTests.components
    build = fixture.StructureIdentityResourcesTests.build

    def prepared_path(self):
        index = json.loads(self.members["data/base/references/fast-structure/index.json"])
        return index["entries"][0]["preparedResource"]

    def test_full_pool_does_not_read_the_same_index_once_per_layer(self):
        view, bundle, provider = self.components()
        reads = Counter()
        original = view._read_payload

        def counted(package_root, inventory, component_id, relative):
            reads[relative] += 1
            return original(package_root, inventory, component_id, relative)

        with patch.object(view, "_read_payload", side_effect=counted):
            resources = self.build(bundle, provider)
        self.assertTrue(resources.geometry_complete)
        self.assertLessEqual(reads[bundle.fast_structure_source.index_path], 2, reads)
        for info in resources.sources:
            self.assertLessEqual(reads[info.reference_resource.path], 2, reads)
            self.assertLessEqual(reads[info.prepared_resource.path], 2, reads)

    def test_batch_and_original_callable_preserve_all_results_and_read_identities(self):
        _, bundle, provider = self.components()
        original = self.build(bundle, lambda *args: provider(*args))
        batched = self.build(bundle, provider)
        self.assertEqual(batched.index.domain, original.index.domain)
        self.assertEqual(batched.index.entries, original.index.entries)
        self.assertEqual(batched.index.directory_gaps, original.index.directory_gaps)
        self.assertEqual(batched.sources, original.sources)
        self.assertEqual(batched._reads, original._reads)
        self.assertFalse(batched.authorizesIdentity)
        self.assertFalse(batched.lock)

    def test_semantic_optional_damage_is_a_gap_but_keeps_its_read_identity(self):
        def bad_pitch(raw):
            with np.load(BytesIO(raw), allow_pickle=False) as archive:
                arrays = {name: archive[name] for name in archive.files}
            arrays["pitch"] = np.asarray(float("nan"))
            output = BytesIO()
            np.savez_compressed(output, **arrays)
            return output.getvalue()
        view = self.view({self.prepared_path(): bad_pitch})
        _, bundle, provider = self.components(view)
        resources = self.build(bundle, provider)
        row = next(row for row in resources._reads
                   if row.prepared_identity and row.prepared_identity[2].path == self.prepared_path())
        self.assertIsNotNone(row.prepared_identity)
        self.assertFalse(resources.geometry_complete)
        self.assertIn("saved-footprint-preparation-unavailable",
                      [entry.unavailable_reason for entry in resources.index.entries])
        resources.check_current()

    def test_distance_crc_is_consumed_even_when_only_anchors_are_retained(self):
        def bad_distance_crc(raw):
            output = BytesIO()
            with zipfile.ZipFile(BytesIO(raw)) as source, \
                 zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
                for name in source.namelist():
                    target.writestr(name, source.read(name))
            data = bytearray(output.getvalue())
            with zipfile.ZipFile(BytesIO(data)) as archive:
                row = archive.getinfo("distance.npy")
            name_len, extra_len = struct.unpack_from("<HH", data, row.header_offset + 26)
            end = row.header_offset + 30 + name_len + extra_len + row.compress_size
            data[end - 1] ^= 1
            return bytes(data)
        view = self.view({self.prepared_path(): bad_distance_crc})
        _, bundle, provider = self.components(view)
        resources = self.build(bundle, provider)
        self.assertFalse(resources.geometry_complete)
        self.assertIn("saved-footprint-preparation-unavailable",
                      [entry.unavailable_reason for entry in resources.index.entries])

    def test_required_damage_during_decode_rejects_publication(self):
        _, bundle, provider = self.components()
        source, changed = bundle.fast_structure_source, []
        decode = source.decode
        def damage(raw, reference):
            result = decode(raw, reference)
            if not changed:
                reference.path.write_bytes(b"damaged-after-batch")
                changed.append(True)
            return result
        with patch.object(source, "decode", side_effect=damage):
            with self.assertRaisesRegex(ValueError, "file-integrity-mismatch"):
                self.build(bundle, provider)
        self.assertTrue(changed)

    def test_optional_fault_and_required_mutation_do_not_fail_open(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                view, bundle, provider = self.components()
                binding, first = next(iter(bundle.fast_structure_source.entries.items()))
                required = view.path(first[0].path)
                view.path(first[1].path).write_bytes(b"broken-optional")
                read_file, changed = resource_snapshot_io.read_stable_file, []
                def read(path, *args, **kwargs):
                    result = read_file(path, *args, **kwargs)
                    if Path(path) == required and not changed:
                        required.write_bytes(b"broken-required")
                        changed.append(True)
                    return result
                if warm:
                    provider(binding[0], binding[1], binding[3])
                with patch.object(resource_snapshot_io, "read_stable_file", side_effect=read):
                    with self.assertRaisesRegex(ValueError, "file-integrity-mismatch"):
                        self.build(bundle, provider)
                self.assertTrue(changed)

    def test_preflight_context_changes_cannot_enter_payload_fallback(self):
        for change in ("root", "reader-root", "source-index", "selection", "cancel"):
            with self.subTest(change=change):
                selected, stopped, changed = [True], [], []
                def current():
                    if not selected[0]:
                        raise ValueError("selection-withdrawn")
                view = self.view(assert_current=current)
                _, bundle, provider = self.components(view)
                reader = bundle.reader
                check_payload = reader._check_payload
                def check(raw, extension):
                    result = check_payload(raw, extension)
                    if extension == ".npz" and not changed:
                        changed.append(True)
                        if change == "root":
                            bundle.fast_structure_source.root = self.root / "foreign"
                        elif change == "reader-root":
                            reader.root = self.root / "foreign"
                        elif change == "source-index":
                            bundle.fast_structure_source.index_path = "changed-index.json"
                        elif change == "selection":
                            selected[0] = False
                        else:
                            stopped.append(True)
                    return result
                with patch.object(reader, "_check_payload", side_effect=check):
                    with self.assertRaisesRegex(ValueError,
                            "component-root-changed|selection-withdrawn|import-cancelled"):
                        self.build(bundle, provider, cancelled=lambda: bool(stopped))
                self.assertTrue(changed)

    def test_batch_recheck_keeps_first_required_error_and_first_optional_miss(self):
        view, bundle, provider = self.components()
        source = bundle.fast_structure_source
        reference = provider("shared-structure", self.map_id, "1")
        entry = source.entries[(reference.metadata["binding"]["profile"], self.map_id, "variant-01", "1")]
        for optional in (False, True):
            path = view.path(entry[1 if optional else 0].path)
            read_file, attempts = resource_snapshot_io.read_stable_file, []
            def read(target, *args, **kwargs):
                if Path(target) == path:
                    attempts.append(True)
                    if len(attempts) == 1:
                        raise OSError("one-shot failure")
                return read_file(target, *args, **kwargs)
            with self.subTest(optional=optional), \
                 patch.object(resource_snapshot_io, "read_stable_file", side_effect=read):
                if optional:
                    self.assertEqual(source.read_many((reference,)), (None,))
                else:
                    with self.assertRaisesRegex(ValueError, "file-unavailable"):
                        source.read_many((reference,))
            self.assertEqual(len(attempts), 1)

    def test_provider_owner_change_during_decode_rejects_publication(self):
        _, bundle, provider = self.components()
        source, changed = bundle.fast_structure_source, []
        decode = source.decode
        def change(raw, reference):
            result = decode(raw, reference)
            if not changed:
                provider.versions = dict(provider.versions)
                changed.append(True)
            return result
        with patch.object(source, "decode", side_effect=change):
            with self.assertRaisesRegex(ValueError, "resource-owner-changed"):
                self.build(bundle, provider)
        self.assertTrue(changed)

    def first_build_fault(self, kind, *, batched, warm):
        view, bundle, provider = self.components()
        binding = next(key for key in bundle.fast_structure_source.entries if key[3] == "1")
        reference_ref, prepared_ref = bundle.fast_structure_source.entries[binding]
        if warm:
            reference = provider(binding[0], binding[1], binding[3])
            self.assertIsNotNone(bundle.fast_structure_source.acquire_prepared_use(reference).prepared)
        relative = {"declaration": view.component["entrypoint"],
                    "typed": reference_ref.path, "optional": prepared_ref.path}[kind]
        target, attempts = view.path(relative), []
        read_file = resource_snapshot_io.read_stable_file
        def read(path, *args, **kwargs):
            if Path(path) == target:
                attempts.append(True)
                if len(attempts) == 1:
                    raise OSError("first build payload failure")
            return read_file(path, *args, **kwargs)
        with patch.object(resource_snapshot_io, "read_stable_file", side_effect=read):
            try:
                resources = self.build(bundle, provider if batched else lambda *args: provider(*args))
            except ValueError as error:
                self.assertEqual(len(attempts), 1)
                return ("error", getattr(error, "code", str(error)))
        row = next(row for row in resources.index.entries if row.key.floor == "1")
        identity = next((read.prepared_identity is not None for read in resources._reads
                         if read.reference_resource == reference_ref), None)
        # Recovered bytes may be current while the first decode remains a gap.
        resources.check_current()
        return ("result", row.unavailable_reason, identity, resources.index.entries,
                resources.sources, resources.index.directory_gaps)

    def test_first_build_declaration_error_is_not_retried_into_success(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                original = self.first_build_fault("declaration", batched=False, warm=warm)
                self.assertEqual(original, ("error", "resource-package-file-unavailable"))
                self.assertEqual(self.first_build_fault("declaration", batched=True, warm=warm), original)

    def test_first_build_typed_error_keeps_original_cold_and_warm_behavior(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                original = self.first_build_fault("typed", batched=False, warm=warm)
                if warm:
                    self.assertEqual(original, ("error", "resource-package-file-unavailable"))
                else:
                    self.assertEqual(original[:3], ("result", "typed-footprint-reference-unavailable", None))
                self.assertEqual(self.first_build_fault("typed", batched=True, warm=warm), original)

    def test_first_build_optional_error_keeps_gap_after_bytes_recover(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                original = self.first_build_fault("optional", batched=False, warm=warm)
                self.assertEqual(original[:3], ("result", "saved-footprint-preparation-unavailable", True))
                self.assertEqual(self.first_build_fault("optional", batched=True, warm=warm), original)

    def test_initial_compressed_batch_is_released_before_final_full_read(self):
        _, bundle, provider = self.components()
        prepare, check = bundle.fast_structure_source.prepare_batch, StructureIdentityResources.check_current
        batches = []
        def prepared(*args, **kwargs):
            batch = prepare(*args, **kwargs)
            batches.append(weakref.ref(batch))
            return batch
        def recheck(resources, **kwargs):
            self.assertEqual(len(batches), 1)
            self.assertIsNone(batches[0](), "first full compressed pool is still retained")
            return check(resources, **kwargs)
        with patch.object(bundle.fast_structure_source, "prepare_batch", side_effect=prepared), \
             patch.object(StructureIdentityResources, "check_current", recheck):
            self.assertTrue(self.build(bundle, provider).geometry_complete)

    def test_failed_batch_prefix_is_released_before_final_full_read(self):
        view, bundle, provider = self.components()
        view.path(self.prepared_path()).unlink()
        prepare, check = bundle.fast_structure_source.prepare_batch, StructureIdentityResources.check_current
        batches = []
        def prepared(*args, **kwargs):
            batch = prepare(*args, **kwargs)
            batches.append(weakref.ref(batch))
            return batch
        def recheck(resources, **kwargs):
            self.assertEqual(len(batches), 1)
            retained = batches[0]()
            if retained is not None:  # Exception traceback cycles may await GC.
                self.assertEqual(retained.prefix, {})
                self.assertIsNone(retained.error)
            return check(resources, **kwargs)
        with patch.object(bundle.fast_structure_source, "prepare_batch", side_effect=prepared), \
             patch.object(StructureIdentityResources, "check_current", recheck):
            self.assertFalse(self.build(bundle, provider).geometry_complete)

    def combined_build_fault(self, *, batched, warm, persistent_typed):
        view, bundle, provider = self.components()
        rows = {key[3]: (key, entry) for key, entry in bundle.fast_structure_source.entries.items()}
        if warm:
            for floor in ("1", "2"):
                binding = rows[floor][0]
                reference = provider(binding[0], binding[1], binding[3])
                self.assertIsNotNone(bundle.fast_structure_source.acquire_prepared_use(reference).prepared)
        relatives = ([rows[floor][1][0].path for floor in ("1", "2")] if persistent_typed
                     else [rows["1"][1][1].path, rows["2"][1][0].path])
        targets = {view.path(relative) for relative in relatives}
        attempts, read_file = Counter(), resource_snapshot_io.read_stable_file
        def read(path, *args, **kwargs):
            path = Path(path)
            if path in targets:
                attempts[path] += 1
                if persistent_typed or attempts[path] == 1:
                    raise OSError("independent payload failure")
            return read_file(path, *args, **kwargs)
        with patch.object(resource_snapshot_io, "read_stable_file", side_effect=read):
            try:
                resources = self.build(bundle, provider if batched else lambda *args: provider(*args))
            except ValueError as error:
                return ("error", getattr(error, "code", str(error)))
        return ("result", {entry.key.floor: entry.unavailable_reason for entry in resources.index.entries},
                resources.index.entries, resources.sources, resources.index.directory_gaps,
                tuple((read.reference_resource, read.prepared_identity is not None) for read in resources._reads))

    def test_optional_then_other_typed_first_faults_keep_each_consumer_classification(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                original = self.combined_build_fault(batched=False, warm=warm, persistent_typed=False)
                if warm:
                    self.assertEqual(original, ("error", "resource-package-file-unavailable"))
                else:
                    self.assertEqual(original[:2], ("result", {"1": "saved-footprint-preparation-unavailable",
                        "2": "typed-footprint-reference-unavailable", "B1": None}))
                self.assertEqual(self.combined_build_fault(batched=True, warm=warm,
                                                          persistent_typed=False), original)

    def test_two_persistent_typed_faults_keep_each_provider_classification(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                original = self.combined_build_fault(batched=False, warm=warm, persistent_typed=True)
                if warm:
                    self.assertEqual(original, ("error", "resource-package-file-unavailable"))
                else:
                    self.assertEqual(original[:2], ("result", {"1": "typed-footprint-reference-unavailable",
                        "2": "typed-footprint-reference-unavailable", "B1": None}))
                self.assertEqual(self.combined_build_fault(batched=True, warm=warm,
                                                          persistent_typed=True), original)

    def test_no_usable_typed_reference_does_not_consume_prepared_index(self):
        for batched in (False, True):
            with self.subTest(batched=batched):
                view, bundle, provider = self.components()
                source = bundle.fast_structure_source
                targets = {view.path(entry[0].path) for entry in source.entries.values()}
                targets.add(view.path(source.index_path))
                read_file = resource_snapshot_io.read_stable_file
                def read(path, *args, **kwargs):
                    if Path(path) in targets:
                        raise OSError("unavailable typed pool and unused prepared index")
                    return read_file(path, *args, **kwargs)
                with patch.object(resource_snapshot_io, "read_stable_file", side_effect=read):
                    resources = self.build(bundle, provider if batched else lambda *args: provider(*args))
                self.assertEqual(len(resources.index.domain), 3)
                self.assertTrue(all(entry.unavailable_reason == "typed-footprint-reference-unavailable"
                                    for entry in resources.index.entries))
                self.assertEqual(resources._reads, ())

    def test_reader_stages_real_prefix_and_preflights_each_payload_once(self):
        view, bundle, _ = self.components()
        paths = tuple(view.path(entry[0].path) for entry in bundle.fast_structure_source.entries.values())
        staged = []
        with patch.object(bundle.reader, "_check_payload", wraps=bundle.reader._check_payload) as check:
            result = bundle.reader.read_many(paths, expected_root=view.root,
                on_payload=lambda file_ref, raw: staged.append((file_ref, raw)))
        self.assertEqual(check.call_count, len(paths))
        self.assertEqual(tuple(raw for _, raw in staged), result)
        self.assertEqual([file_ref.path for file_ref, _ in staged],
                         [bundle.reader.relative_path(path) for path in paths])
        with patch.object(bundle.reader, "_check_payload", wraps=bundle.reader._check_payload) as check:
            self.assertEqual(bundle.reader.read_many(paths, expected_root=view.root), result)
        self.assertEqual(check.call_count, len(paths))
        staged.clear()
        read_file = resource_snapshot_io.read_stable_file
        def fail_second(path, *args, **kwargs):
            if Path(path) == paths[1]:
                raise OSError("later payload failed")
            return read_file(path, *args, **kwargs)
        with patch.object(resource_snapshot_io, "read_stable_file", side_effect=fail_second):
            with self.assertRaises(ValueError) as raised:
                bundle.reader.read_many(paths, expected_root=view.root,
                    on_payload=lambda file_ref, raw: staged.append((file_ref, raw)))
        self.assertEqual(raised.exception.resource_file_ref.path, bundle.reader.relative_path(paths[1]))
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0][1], result[0])

    def test_staging_callback_errors_and_context_changes_have_no_payload_attribution(self):
        for api in ("view", "reader"):
            for change in ("callback-error", "selection", "cancel", "manifest"):
                with self.subTest(api=api, change=change):
                    selected, stopped, staged = [True], [], []
                    def current():
                        if not selected[0]:
                            raise ValueError("selection-withdrawn")
                    view = self.view(assert_current=current)
                    _, bundle, _ = self.components(view)
                    root = view.root
                    relatives = tuple(entry[0].path for entry in bundle.fast_structure_source.entries.values())
                    manifest = root.parent.parent / "manifest.json"
                    replacement = manifest.with_name("replacement-manifest.json")
                    replacement.write_bytes(manifest.read_bytes())
                    def stage(file_ref, raw):
                        if staged:
                            return
                        staged.append((file_ref, raw))
                        if change == "callback-error":
                            raise ValueError("local-staging-failed")
                        if change == "selection":
                            selected[0] = False
                        elif change == "cancel":
                            stopped.append(True)
                        else:
                            replacement.replace(manifest)
                    owner, paths = ((view, relatives) if api == "view" else
                                    (bundle.reader, tuple(root / relative for relative in relatives)))
                    with self.assertRaisesRegex(ValueError,
                            "local-staging-failed|selection-withdrawn|import-cancelled|manifest-replaced") as raised:
                        owner.read_many(paths, expected_root=root, cancelled=lambda: bool(stopped), on_payload=stage)
                    self.assertFalse(hasattr(raised.exception, "resource_file_ref"))
                    self.assertTrue(staged)

    def test_replayed_optional_error_traceback_releases_consumed_bytes_without_gc(self):
        view, bundle, provider = self.components()
        source = bundle.fast_structure_source
        binding = next(key for key in source.entries if key[3] == "1")
        reference_ref, prepared_ref = source.entries[binding]
        payloads = (view.path(source.index_path).read_bytes(), view.path(reference_ref.path).read_bytes())
        target, attempts = view.path(prepared_ref.path), []
        read_file, prepare = resource_snapshot_io.read_stable_file, source.prepare_batch
        check, errors, original_error = StructureIdentityResources.check_current, [], []
        def fail_once(path, *args, **kwargs):
            if Path(path) == target:
                attempts.append(True)
                if len(attempts) == 1:
                    raise OSError("optional-replay-traceback-probe")
            return read_file(path, *args, **kwargs)
        def prepared(*args, **kwargs):
            batch = prepare(*args, **kwargs)
            error = batch.error
            errors.append(error)
            original_error.append((type(error), error.args, error.resource_file_ref,
                                   type(error.__cause__), str(error.__cause__)))
            return batch
        def recheck(resources, **kwargs):
            self.assertFalse(gc.isenabled())
            self.assertEqual(len(errors), 1)
            error, retained, cycles, seen = errors[0], [], [], set()
            def inspect_value(value, location):
                if id(value) in seen:
                    return
                seen.add(id(value))
                if isinstance(value, bytes):
                    if any(value == expected for expected in payloads):
                        retained.append((location, len(value)))
                elif isinstance(value, dict):
                    for key, item in value.items():
                        inspect_value(item, location + "[" + str(key) + "]")
                elif isinstance(value, (tuple, list)):
                    for index, item in enumerate(value):
                        inspect_value(item, location + "[" + str(index) + "]")
            pending, exceptions = [error], set()
            while pending:
                current = pending.pop()
                if id(current) in exceptions:
                    continue
                exceptions.add(id(current))
                tb = current.__traceback__
                while tb is not None:
                    frame = tb.tb_frame
                    for name, value in frame.f_locals.items():
                        inspect_value(value, frame.f_code.co_name + "." + name)
                    if frame.f_code.co_name == "_read_many":
                        if frame.f_locals.get("error") is current:
                            cycles.append(frame.f_code.co_name)
                    tb = tb.tb_next
                pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
            self.assertEqual(retained, [], "traceback retains consumed resource bytes")
            self.assertEqual(cycles, [], "traceback frame retains its own replayed exception")
            self.assertEqual((type(error), error.args, error.resource_file_ref,
                              type(error.__cause__), str(error.__cause__)), original_error[0])
            formatted = "".join(traceback.format_exception(error))
            self.assertIn("optional-replay-traceback-probe", formatted)
            self.assertIn("_read_many", formatted)
            return check(resources, **kwargs)
        enabled = gc.isenabled()
        gc.disable()
        try:
            with patch.object(resource_snapshot_io, "read_stable_file", side_effect=fail_once), \
                 patch.object(source, "prepare_batch", side_effect=prepared), \
                 patch.object(StructureIdentityResources, "check_current", recheck):
                self.assertFalse(self.build(bundle, provider).geometry_complete)
        finally:
            if enabled:
                gc.enable()
