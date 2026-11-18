"""Actual S1 producer/catalog/provider/Host consumption of prepared resources.

The reference and frame are explicit synthetic inputs. A black frame remains
unsupported; consuming a prepared file is not a positive or real-game fit.
"""
from pathlib import Path
from io import BytesIO
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

import client_imported_activation as activation
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_live_runtime import _AlignmentHost
from client_paths import RuntimePaths
import fast_structure_alignment as fast
import resource_snapshot_io
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_resource_packages import PackageView
from imported_native_base import decode_native_base
from imported_fast_structure import decode_prepared
from resource_transaction import resource_runtime_lease
import star_map_import as files
import test_export_s1_native_base_package as source_fixture


class ImportedFastStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='imported-fast-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source_root = Path(temporary.name)
        cls.sources = {}
        for mode in ('difficult', 'nightmare'):
            source = source_fixture.S1NativeBaseSourceFixture(cls.source_root/mode,
                                                              mode=mode, include_fast=True)
            source.export()
            cls.sources[mode] = source

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='imported-fast-active-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.root = Path(temporary.name).resolve()
        self.archive_counter = 0
        self.resolver = ResourceResolver(self.paths.imported_resource_root,
                                          supported_capabilities=SUPPORTED_CAPABILITIES)

    def save(self, mode, mutate=None):
        source = self.sources[mode]
        archive = source.output
        if mutate is not None:
            self.archive_counter += 1
            with zipfile.ZipFile(archive) as incoming:
                members = {name:incoming.read(name) for name in incoming.namelist()}
            mutate(members)
            manifest = json.loads(members['manifest.json'])
            manifest['resourceVersion'] = f'mutated-{self.archive_counter}'
            manifest['files'] = [row for row in manifest['files']
                                if f"data/{row['componentId']}/{row['path']}" in members]
            for row in manifest['files']:
                raw = members[f"data/{row['componentId']}/{row['path']}"]
                row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            members['manifest.json'] = json.dumps(manifest).encode('utf-8')
            archive = self.root / f'input-{self.archive_counter}.zip'
            with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as outgoing:
                for name, raw in members.items():
                    outgoing.writestr(name, raw)
        saved = save_archive(archive, self.resolver.root)
        refs = {ref.component_id:ref for ref in saved.component_refs}
        return source, refs

    def activate(self, mode, refs, **kwargs):
        with resource_runtime_lease(self.resolver.root):
            activation.activate_context(self.resolver, self.paths, refs['base'], refs['controls'],
                                        season='s1', mode=mode, **kwargs)

    def install(self, mode, mutate=None):
        source, refs = self.save(mode, mutate)
        self.activate(mode, refs)
        return source, refs

    @staticmethod
    def without_fast(members):
        context = json.loads(members['data/base/resource-context.json'])
        context['runtimeInterfaces'].pop('fastStructureIndex')
        members['data/base/resource-context.json'] = json.dumps(context).encode('utf-8')
        for path in tuple(members):
            if path.startswith('data/base/references/fast-structure/'):
                members.pop(path)

    @staticmethod
    def rewrite_array(raw, **changes):
        with np.load(BytesIO(raw), allow_pickle=False) as incoming:
            arrays = {name:incoming[name] for name in incoming.files}
        arrays.update(changes)
        output = BytesIO()
        np.savez_compressed(output, **arrays)
        return output.getvalue()

    def test_both_modes_b3_host_reads_declared_prepared_and_still_rejects_empty_observation(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                source, refs = self.save(mode)
                with patch.object(fast, 'prepare', wraps=fast.prepare) as derived:
                    self.activate(mode, refs)
                self.assertEqual(derived.call_count, len(source.ids) * 4 * 2)
                with patch.object(fast, 'prepare', side_effect=AssertionError('online preprocessing')):
                    engine = build_imported_engine_factories(self.resolver, self.paths).get_engine(mode, season='s1')
                    host = _AlignmentHost(engine, collect_fit_diagnostics=True)
                    prepared_source = engine.qt_fast_structure_source
                    prepared_path = prepared_source.view.root / source.prepared_resources[source.ids[0], '-3']
                    with patch.object(prepared_source, 'acquire_prepared_use',
                                      wraps=prepared_source.acquire_prepared_use) as acquired, \
                         patch.object(resource_snapshot_io, 'read_stable_file',
                                      wraps=resource_snapshot_io.read_stable_file) as read:
                        result = host.fit(np.zeros((720,1280,3),np.uint8), source.ids[0], '-3', lambda:False)
                self.assertFalse(result['success'])
                self.assertEqual(result['reason'], 'initial-structure-query-unavailable')
                # One acquisition plus an actual return-boundary byte check;
                # the latter must not acquire or decode the arrays again.
                self.assertEqual(acquired.call_count, 1)
                self.assertEqual(sum(Path(call.args[0]).samefile(prepared_path)
                                     for call in read.call_args_list), 3)
                events = [event for event in host.events if event.get('event') == 'experimental-fast-alignment']
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['reason'], 'fast-observation-unavailable')
                self.assertEqual(acquired.call_args.args[0].metadata['binding']['floor'], '-3')
                self.assertFalse(engine.qt_live_ready)
                self.assertFalse(engine.memory_store.root.exists())

    def test_warm_cache_checks_both_payloads_cancellation_and_catalog(self):
        fixture, _ = self.install('difficult')
        engine = build_imported_engine_factories(self.resolver, self.paths).get_engine('difficult', season='s1')
        source = engine.qt_fast_structure_source
        reference = engine.qt_shared_provider('shared-structure', fixture.ids[0], '-3')
        first = fast.load_prepared(reference, source=source)
        self.assertIsNotNone(first)
        self.assertFalse(first.distance.flags.writeable)
        with patch.object(source, 'decode', side_effect=AssertionError('warm cache decoded')):
            self.assertIs(fast.load_prepared(reference, source=source), first)
            path = source.view.root / fixture.prepared_resources[fixture.ids[0], '-3']
            raw = path.read_bytes()
            path.write_bytes(b'broken-prepared')
            try:
                self.assertIsNone(fast.load_prepared(reference, source=source))
            finally:
                path.write_bytes(raw)
            self.assertIs(fast.load_prepared(reference, source=source), first)
            raw = reference.path.read_bytes()
            reference.path.write_bytes(b'broken-reference')
            try:
                with self.assertRaises(files.ImportRejected):
                    fast.load_prepared(reference, source=source)
            finally:
                reference.path.write_bytes(raw)
            with self.assertRaisesRegex(files.ImportRejected, 'import-cancelled'):
                fast.load_prepared(reference, source=source, cancelled=lambda:True)
            catalog = self.resolver.root / 'catalog.json'
            catalog.write_bytes(catalog.read_bytes() + b' ')
            with self.assertRaises(ValueError):
                fast.load_prepared(reference, source=source)

    def test_legacy_package_keeps_normal_solver_without_bare_path_fallback(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                fixture, _ = self.install(mode, self.without_fast)
                with patch.object(fast, 'prepare', side_effect=AssertionError('online preprocessing')), \
                        patch.object(fast, 'prepared_path_for', side_effect=AssertionError('bare path read')):
                    engine = build_imported_engine_factories(self.resolver, self.paths).get_engine(mode, season='s1')
                    self.assertTrue(engine.qt_rank_ready)
                    self.assertTrue(engine.qt_native_base.ready)
                    self.assertEqual(engine.qt_fast_structure_source.gaps, ('fast-prepared-not-provided',))
                    host = _AlignmentHost(engine, collect_fit_diagnostics=True)
                    result = host.fit(np.zeros((720,1280,3),np.uint8), fixture.ids[0], '-3', lambda:False)
                self.assertEqual(result['reason'], 'initial-structure-query-unavailable')
                self.assertFalse(engine.qt_live_ready)
                self.assertFalse(engine.memory_store.root.exists())
                reference = engine.qt_shared_provider('shared-structure', fixture.ids[0], '-3')
                raw = reference.path.read_bytes()
                reference.path.write_bytes(b'broken-legacy-reference')
                try:
                    with self.assertRaises(files.ImportRejected):
                        fast.load_prepared(reference, source=engine.qt_fast_structure_source)
                finally:
                    reference.path.write_bytes(raw)

    def test_full_derivation_rejects_finite_wrong_arrays_before_publication(self):
        fixture = self.sources['difficult']
        relative = fixture.prepared_resources[fixture.ids[0], '-3']
        def wrong(members):
            path = 'data/base/' + relative
            members[path] = self.rewrite_array(members[path], distance=np.ones((8,8),np.uint16))
        _, refs = self.save('difficult', wrong)
        with self.assertRaisesRegex(files.ImportRejected, 'imported-fast-structure-derived-mismatch'):
            self.activate('difficult', refs)
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        self.assertFalse(self.paths.memory_root('difficult', 's1').exists())

    def test_role_decoder_and_manifest_replacement_cannot_reuse_cached_arrays(self):
        fixture, refs = self.install('difficult')
        engine = build_imported_engine_factories(self.resolver, self.paths).get_engine('difficult', season='s1')
        reference = engine.qt_shared_provider('shared-structure', fixture.ids[0], '-3')
        source = engine.qt_fast_structure_source
        raw, _ = source.read(reference)
        changes = (
            dict(distance=np.ones((8,8),np.float32)),
            dict(chains=np.zeros((0,2,2),np.float32)),
            dict(rays=np.zeros((1,2,2),np.float32)),
            dict(shape=np.asarray([9,8],np.int64)),
            dict(pitch=np.asarray(float('nan'))),
            dict(sourceMetadata=np.asarray('{}')),
            dict(extra=np.asarray(1)),
        )
        for changed in changes:
            with self.subTest(field=next(iter(changed))):
                with self.assertRaises(files.ImportRejected):
                    decode_prepared(self.rewrite_array(raw, **changed), reference)
        self.assertIsNotNone(fast.load_prepared(reference, source=source))
        manifest = source.view.root.parent.parent / 'manifest.json'
        replacement = manifest.with_name('replacement.json')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        with self.assertRaises(files.ImportRejected):
            fast.load_prepared(reference, source=source)

    def test_cancellation_after_cache_publication_discards_only_new_entry(self):
        fixture, _ = self.install('difficult')
        engine = build_imported_engine_factories(self.resolver, self.paths).get_engine('difficult', season='s1')
        reference = engine.qt_shared_provider('shared-structure', fixture.ids[0], '-3')
        source = engine.qt_fast_structure_source
        cancelled = [False]
        trim = fast.trim_prepared_cache
        def cancel_after_publish(*args, **kwargs):
            trim(*args, **kwargs)
            cancelled[0] = True
        with patch.object(fast, 'trim_prepared_cache', side_effect=cancel_after_publish):
            with self.assertRaisesRegex(files.ImportRejected, 'import-cancelled'):
                fast.load_prepared(reference, source=source, cancelled=lambda:cancelled[0])
        with fast._prepared_lock:
            self.assertTrue(all(entry[0] is not reference for entry in fast._prepared.values()))
        self.assertIsNotNone(fast.load_prepared(reference, source=source))

    def test_declared_index_requires_exact_complete_owned_bindings(self):
        def change_index(change):
            def mutate(members):
                path = 'data/base/references/fast-structure/index.json'
                value = json.loads(members[path])
                change(value['entries'])
                members[path] = json.dumps(value).encode('utf-8')
            return mutate
        changes = (
            lambda rows: rows.pop(),
            lambda rows: rows.__setitem__(1, dict(rows[0])),
            lambda rows: rows[0].__setitem__('referenceResource', rows[1]['referenceResource']),
            lambda rows: rows[0].__setitem__('preparedResource', 'references/not-owned.npz'),
        )
        for number, change in enumerate(changes):
            with self.subTest(change=number):
                _, refs = self.save('difficult', change_index(change))
                view = PackageView(self.resolver.root, refs['base'])
                with self.assertRaises(files.ImportRejected):
                    decode_native_base(view, season='s1', mode='difficult')


if __name__ == '__main__':
    unittest.main()
