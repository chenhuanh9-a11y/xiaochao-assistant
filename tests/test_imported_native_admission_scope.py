"""Actual native admission retains file proofs without repeating declarations.

Source pixels are synthetic. Producer ZIPs, PackageView, every content decoder,
the native model constructors and the final file reads are real.
"""
from collections import Counter
from collections.abc import Mapping
from copy import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np

import imported_difficult_native_base as difficult
import imported_native_base as native
from imported_resource_catalog import CatalogSnapshot, SCHEMA as CATALOG_SCHEMA
import imported_resource_packages as packages
import imported_resource_reader as resource_reader
from imported_resource_reader import ComponentResourceReader
import imported_structure_declarations as declarations
import star_map_import as files
import test_export_s0_difficult_native_base_package as difficult_fixtures
import test_export_s1_native_base_package as s1_fixtures
import test_imported_native_base as fixture_module


class ImportedNativeAdmissionScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_module.ImportedNativeBaseTests.setUpClass.__func__(cls)

    def setUp(self):
        fixture_module.ImportedNativeBaseTests.setUp(self)

    view = fixture_module.ImportedNativeBaseTests.view

    @classmethod
    def difficult_archive(cls):
        if not hasattr(cls, '_difficult_source'):
            temporary = tempfile.TemporaryDirectory(prefix='admission-s0-difficult-')
            cls.addClassCleanup(temporary.cleanup)
            cls._difficult_source = difficult_fixtures.S0DifficultSourceFixture(Path(temporary.name))
            cls._difficult_source.export()
        return cls._difficult_source.output

    def difficult_view(self):
        original_archive, self.archive = self.archive, self.difficult_archive()
        try:
            return self.view()
        finally:
            self.archive = original_archive

    def trace(self, decode, view):
        payloads, admission_boundary = [], []
        read = packages.PackageView._read_payload
        construct = native.NativeBaseBundle

        def actual_read(package_root, inventory, component, relative):
            payloads.append((component, relative))
            return read(package_root, inventory, component, relative)

        def actual_construct(*args, **kwargs):
            admission_boundary.append(len(payloads))
            return construct(*args, **kwargs)

        with patch.object(packages.PackageView, '_read_payload', staticmethod(actual_read)), \
                patch.object(native, 'NativeBaseBundle', actual_construct), \
                patch.object(difficult, 'NativeBaseBundle', actual_construct):
            bundle = decode(view)
        return bundle, payloads, admission_boundary[0]

    @staticmethod
    def normalize(value, root):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, Path):
            return value.relative_to(root).as_posix()
        if isinstance(value, Mapping):
            return {key: ImportedNativeAdmissionScopeTests.normalize(item, root) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [ImportedNativeAdmissionScopeTests.normalize(item, root) for item in value]
        return value

    def model(self, bundle):
        values = bundle._values
        main, local, fast = (values[name] for name in
                             ('entry_identity', 'side_entry_identity', 'fast_structure_source'))
        products = {}
        for key in values['resources'].entries:
            product = values['resources'].load(key[0], key[1], key[3], variant=key[2])
            self.assertIsNotNone(product, key)
            products[key] = {name: getattr(product, name) for name in
                            ('clean_mask', 'validation_mask', 'metadata', 'path', 'known_boundary',
                             'unknown_mask', 'native_wall_segments')}
        result = {name: values[name] for name in
                  ('season', 'mode', 'interfaces', 'versions', 'gaps', 'legacy_passports',
                   'semantic_side_entries', 'passport_source_available', 'rank_ready')}
        result.update(pack=values['pack'].raw, typed_entries=values['resources'].entries,
            coordinates=values['adapters']._raw, main=main.entries, local=local.entries,
            passports=local.passports, coverage=local.entry_coverage, side_doors=local.side_doors,
            side_search=local.side_search, products=products, fast_entries=dict(fast.entries),
            errors=(main.error, local.passport_error, local.side_door_error, local.side_search_error))
        # ComponentRef is immutable and FileRef contains only declarations;
        # both remain directly comparable between the two installed trees.
        return self.normalize(result, values['reader'].root.resolve())

    def assert_live_readers(self, bundle, view):
        values = bundle._values
        reader = values['reader']
        self.assertIs(type(reader), ComponentResourceReader)
        self.assertIs(reader.view, view)
        for owner, field in ((values['resources'], 'read_resource'),
                (values['adapters'], '_read_resource'), (values['entry_identity'], 'read_resource'),
                (values['side_entry_identity'], 'read_resource')):
            self.assertIs(getattr(owner, field), reader)
        if values['exit_recognizer'] is not None:
            self.assertIs(values['exit_recognizer'].read_resource, reader)
        fast = values['fast_structure_source']
        self.assertIs(fast.reader, reader)
        self.assertIs(fast.view, view)
        legacy = values['legacy_references']
        if legacy is not None:
            self.assertIs(legacy._view, view)
            self.assertIs(legacy._reader, reader)
            self.assertIs(legacy._store.read_resource, reader)

    def test_actual_decoder_proves_declarations_at_admission_boundaries(self):
        view = self.view()
        manifest_reads = []
        payload_reads = []
        read = packages.resource_snapshot_io.read_stable_file
        construct = native.NativeBaseBundle
        admission_manifest_reads = []

        def actual_read(path, *args, **kwargs):
            result = read(path, *args, **kwargs)
            (manifest_reads if Path(path).name == 'manifest.json' else payload_reads).append(Path(path))
            return result

        def actual_construct(*args, **kwargs):
            admission_manifest_reads.append(len(manifest_reads))
            return construct(*args, **kwargs)

        with patch.object(packages.resource_snapshot_io, 'read_stable_file', side_effect=actual_read), \
                patch.object(native, 'NativeBaseBundle', actual_construct):
            bundle = native.decode_native_base(view)
        self.assertEqual(len(bundle.resources.entries), 3)
        self.assertTrue(payload_reads)
        # The bundle and fast source deliberately retain their original live
        # proofs. Only the preceding, unpublished construction is scoped.
        self.assertEqual(admission_manifest_reads, [3])

    def test_actual_payload_order_matches_live_path_then_rereads_consumed_files(self):
        live = lambda view: native._decode_component(view, view, 's0', 'nightmare')
        expected, original_payloads, _ = self.trace(live, self.view())
        view = self.view()
        result, actual_payloads, _ = self.trace(native.decode_native_base, view)
        self.assertEqual(actual_payloads[:len(original_payloads)], original_payloads)
        self.assertEqual(actual_payloads[len(original_payloads):],
                         list(dict.fromkeys(original_payloads)))
        self.assertEqual(self.model(result), self.model(expected))
        self.assert_live_readers(result, view)

    def test_typed_products_reuse_only_the_duplicate_generic_preflight(self):
        verify = native._verify_typed_products
        load = native.SharedStructureResourceStore.load
        preflight = resource_reader.validate_npz_bytes
        observations = []

        def verify_with_count(view, reader, resources, versions):
            by_raw = {view.path(row['resourcePath']).read_bytes(): row['resourcePath']
                      for row in resources.entries.values()}
            counts = Counter()

            def actual_preflight(raw):
                counts[by_raw[raw]] += 1
                return preflight(raw)

            with patch.object(resource_reader, 'validate_npz_bytes', actual_preflight), \
                    patch.object(declarations, 'validate_npz_bytes', actual_preflight):
                verify(view, reader, resources, versions)
            observations.append(counts)

        def load_with_full_reader(store, *args, **kwargs):
            kwargs.pop('read_resource', None)
            return load(store, *args, **kwargs)

        with patch.object(native, '_verify_typed_products', verify_with_count):
            with patch.object(native.SharedStructureResourceStore, 'load', load_with_full_reader):
                expected, original_payloads, _ = self.trace(native.decode_native_base, self.view())
            view = self.view()
            result, actual_payloads, _ = self.trace(native.decode_native_base, view)
        relatives = {row['resourcePath'] for row in result.resources.entries.values()}
        self.assertEqual(observations, [Counter({relative: 4 for relative in relatives}),
                                        Counter({relative: 3 for relative in relatives})])
        self.assertEqual(actual_payloads, original_payloads)
        self.assertEqual(self.model(result), self.model(expected))
        self.assert_live_readers(result, view)

    def test_second_typed_read_still_rejects_actual_file_and_owner_changes(self):
        validate = native.validate_structure_declarations
        read = packages.PackageView._read_payload
        for damage in ('permission', 'bytes', 'missing', 'directory', 'manifest', 'owner', 'link'):
            with self.subTest(damage=damage):
                view = self.view()
                index = json.loads(view.read(fixture_module.producer.INDEX))
                relative = index['entries'][0]['resourcePath']
                path = view.path(relative)
                raw = path.read_bytes()
                changed = []
                if damage == 'link':
                    target = self.root / 'outside-typed-product.npz'
                    target.write_bytes(raw)
                    probe = self.root / 'typed-link-probe'
                    try:
                        probe.symlink_to(target)
                    except (OSError, NotImplementedError) as error:
                        self.skipTest(f'file symlink creation is unavailable: {error}')
                    probe.unlink()

                def change_after_first_declarations(raw, **kwargs):
                    result = validate(raw, **kwargs)
                    if not changed:
                        changed.append(relative)
                        if damage == 'bytes':
                            path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                        elif damage in ('missing', 'directory', 'link'):
                            path.unlink()
                            if damage == 'directory':
                                path.mkdir()
                            elif damage == 'link':
                                path.symlink_to(target)
                        elif damage == 'manifest':
                            manifest = view.root.parent.parent / 'manifest.json'
                            manifest.write_bytes(manifest.read_bytes() + b' ')
                        elif damage == 'owner':
                            view.component_ref = replace(view.component_ref, component_id='controls')
                    return result

                def actual_read(package_root, inventory, component, selected):
                    if damage == 'permission' and changed and selected == relative:
                        raise PermissionError('second typed read is unavailable')
                    return read(package_root, inventory, component, selected)

                reason = ('manifest-changed' if damage == 'manifest'
                          else '^imported-native-base-typed-product-invalid$')
                with patch.object(native, 'validate_structure_declarations', change_after_first_declarations), \
                        patch.object(packages.PackageView, '_read_payload', staticmethod(actual_read)):
                    with self.assertRaisesRegex(files.ImportRejected, reason):
                        native.decode_native_base(view)
                self.assertEqual(changed, [relative])

    def test_typed_preflight_callback_rejects_a_different_actual_resource(self):
        view = self.view()
        index = json.loads(view.read(fixture_module.producer.INDEX))
        paths = [view.path(row['resourcePath']) for row in index['entries']]
        load = native.SharedStructureResourceStore.load
        read = packages.PackageView._read_payload
        payloads, checked = [], []

        def actual_read(package_root, inventory, component, relative):
            payloads.append(relative)
            return read(package_root, inventory, component, relative)

        def load_with_wrong_request(store, *args, **kwargs):
            callback = kwargs.get('read_resource')
            if callback is not None:
                current = store.entries[args[0], args[1], kwargs['variant'], args[2]]['resourcePath']
                other = next(path for path in paths if path != view.root / current)
                before = list(payloads)
                with self.assertRaises(files.ImportRejected):
                    callback(other)
                self.assertEqual(payloads, before)
                checked.append(current)
            return load(store, *args, **kwargs)

        with patch.object(native.SharedStructureResourceStore, 'load', load_with_wrong_request), \
                patch.object(packages.PackageView, '_read_payload', staticmethod(actual_read)):
            bundle = native.decode_native_base(view)
        self.assertEqual(checked, [row['resourcePath'] for row in bundle.resources.entries.values()])
        self.assert_live_readers(bundle, view)

    def at_replay(self, view, change, *, mode='nightmare'):
        """Mutate real state after the first final reread, not a mocked proof."""
        read = packages.resource_snapshot_io.read_stable_file
        entry = view.component['entrypoint']
        context_reads, returned = [], []

        def actual_read(path, *args, **kwargs):
            result = read(path, *args, **kwargs)
            if Path(path).name == Path(entry).name:
                context_reads.append(Path(path))
                if len(context_reads) == 2:
                    change()
            return result

        with patch.object(packages.resource_snapshot_io, 'read_stable_file', side_effect=actual_read):
            returned.append(native.decode_native_base(view, mode=mode))
        return returned

    def test_final_real_reread_rejects_consumed_bytes_missing_and_nonregular_files(self):
        for damage, reason in (('bytes', 'file-integrity-mismatch'),
                               ('missing', 'file-unavailable'), ('directory', 'non-regular-package-file')):
            with self.subTest(damage=damage):
                view = self.view()
                context = view.path(view.component['entrypoint'])
                raw = context.read_bytes()
                construct = native.NativeBaseBundle
                published = []

                def actual_construct(*args, **kwargs):
                    result = construct(*args, **kwargs)
                    if damage == 'bytes':
                        context.write_bytes(raw[:-1] + b' ')
                    else:
                        context.unlink()
                        if damage == 'directory':
                            context.mkdir()
                    return result

                with patch.object(native, 'NativeBaseBundle', actual_construct):
                    with self.assertRaisesRegex(files.ImportRejected, reason) as caught:
                        published.append(native.decode_native_base(view))
                self.assertEqual(caught.exception.resource_file_ref, packages.FileRef(view.component_ref, context.name))
                self.assertEqual(published, [])

    def test_final_real_reread_rejects_a_consumed_file_replaced_by_a_real_link(self):
        view = self.view()
        context = view.path(view.component['entrypoint'])
        target = self.root / 'outside-context.json'
        target.write_bytes(context.read_bytes())
        probe = self.root / 'link-probe'
        try:
            probe.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'file symlink creation is unavailable: {error}')
        probe.unlink()
        construct = native.NativeBaseBundle

        def actual_construct(*args, **kwargs):
            result = construct(*args, **kwargs)
            context.unlink()
            context.symlink_to(target)
            return result

        with patch.object(native, 'NativeBaseBundle', actual_construct):
            with self.assertRaisesRegex(files.ImportRejected, 'linked-target'):
                native.decode_native_base(view)

    def test_final_reread_covers_actual_live_fast_index_and_reviewed_groups(self):
        for consumed in ('fast', 'groups'):
            with self.subTest(consumed=consumed):
                view = self.view()
                construct = native.NativeBaseBundle
                successful_reads, before = [], []
                read = packages.resource_snapshot_io.read_stable_file

                def actual_read(path, *args, **kwargs):
                    result = read(path, *args, **kwargs)
                    successful_reads.append(Path(path))
                    return result

                def actual_construct(*args, **kwargs):
                    result = construct(*args, **kwargs)
                    values = result._values
                    relative = (values['fast_structure_source'].index_path if consumed == 'fast'
                                else values['interfaces']['reviewedFrontGroups'])
                    path = values['reader'].root / relative
                    before.append((path, successful_reads.count(path)))
                    self.assertGreater(before[-1][1], 0)
                    raw = path.read_bytes()
                    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
                    return result

                with patch.object(native, 'NativeBaseBundle', actual_construct), \
                        patch.object(packages.resource_snapshot_io, 'read_stable_file', side_effect=actual_read):
                    with self.assertRaisesRegex(files.ImportRejected, 'file-integrity-mismatch'):
                        native.decode_native_base(view)
                self.assertGreater(successful_reads.count(before[0][0]), before[0][1])

    def test_final_reread_covers_actual_live_legacy_payloads(self):
        view = self.difficult_view()
        construct = difficult.NativeBaseBundle
        successful_reads, before = [], []
        read = packages.resource_snapshot_io.read_stable_file

        def actual_read(path, *args, **kwargs):
            result = read(path, *args, **kwargs)
            successful_reads.append(Path(path))
            return result

        def actual_construct(*args, **kwargs):
            result = construct(*args, **kwargs)
            values = result._values
            legacy = values['legacy_references']
            relative = next(iter(legacy._canvases.values()))['resourcePath']
            path = values['reader'].root / relative
            before.append((path, successful_reads.count(path)))
            self.assertGreater(before[-1][1], 0)
            raw = path.read_bytes()
            path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
            return result

        with patch.object(difficult, 'NativeBaseBundle', actual_construct), \
                patch.object(packages.resource_snapshot_io, 'read_stable_file', side_effect=actual_read):
            with self.assertRaisesRegex(files.ImportRejected, 'file-integrity-mismatch'):
                native.decode_native_base(view, season='s0', mode='difficult')
        self.assertGreater(successful_reads.count(before[0][0]), before[0][1])

    def test_unconsumed_prepared_payload_is_not_read_or_added_to_the_final_proof(self):
        view = self.view()
        construct = native.NativeBaseBundle
        prepared = []
        reads = []
        read = packages.PackageView._read_payload

        def actual_read(package_root, inventory, component, relative):
            self.assertNotIn(relative, prepared)
            reads.append(relative)
            return read(package_root, inventory, component, relative)

        def actual_construct(*args, **kwargs):
            result = construct(*args, **kwargs)
            values = result._values
            reference, preparation = next(iter(values['fast_structure_source'].entries.values()))
            self.assertNotEqual(reference.path, preparation.path)
            prepared.append(preparation.path)
            (values['reader'].root / preparation.path).unlink()
            return result

        with patch.object(native, 'NativeBaseBundle', actual_construct), \
                patch.object(packages.PackageView, '_read_payload', staticmethod(actual_read)):
            bundle = native.decode_native_base(view)
        self.assertTrue(bundle.ready)
        self.assertEqual(len(prepared), 1)
        self.assertNotIn(prepared[0], reads)

    def test_final_legacy_ownership_includes_actual_store_reading_fields(self):
        for field in ('read_resource', 'check_current', 'resource_root', 'index_path', 'entries'):
            with self.subTest(field=field):
                view = self.difficult_view()
                construct = difficult.NativeBaseBundle
                constructed = []

                def actual_construct(*args, **kwargs):
                    result = construct(*args, **kwargs)
                    constructed.append(result)
                    return result

                def change():
                    legacy = constructed[0]._values['legacy_references']
                    replacement = {'read_resource': copy(legacy._reader), 'check_current': lambda: None,
                        'resource_root': self.root / 'foreign-root', 'index_path': self.root / 'foreign-index.json',
                        'entries': {key: dict(row, resourcePath='missing.npz')
                                    for key, row in legacy._store.entries.items()}}[field]
                    setattr(legacy._store, field, replacement)

                with patch.object(difficult, 'NativeBaseBundle', actual_construct):
                    with self.assertRaisesRegex(files.ImportRejected, 'component-root-changed'):
                        self.at_replay(view, change, mode='difficult')

    def test_final_real_manifest_proof_rejects_replacement_and_byte_change_during_replay(self):
        for damage, reason in (('replacement', 'manifest-replaced'), ('bytes', 'manifest-changed')):
            with self.subTest(damage=damage):
                view = self.view()
                manifest = view.root.parent.parent / 'manifest.json'

                def change():
                    if damage == 'replacement':
                        retained = manifest.with_suffix('.retained')
                        manifest.rename(retained)
                        manifest.write_bytes(retained.read_bytes())
                    else:
                        manifest.write_bytes(manifest.read_bytes() + b' ')

                with self.assertRaisesRegex(files.ImportRejected, reason):
                    self.at_replay(view, change)

    def selected_view(self):
        view = self.view()
        ref = view.component_ref
        wire = lambda component: dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
            componentId=component, manifestSha256=ref.manifest_sha256)
        catalog = dict(schema=CATALOG_SCHEMA, generation=str(uuid.uuid4()), displays=[], contexts={
            's0:nightmare': dict(recognitionBase=wire('base'), controls=wire('controls'), reviewedGroups=None)})
        path = view.resource_root / 'catalog.json'
        path.write_text(json.dumps(catalog), encoding='utf-8')
        snapshot = CatalogSnapshot(view.resource_root,
            supported_capabilities=view.manifest['clientCompatibility']['requiredCapabilities'])
        return snapshot.resolve_context('s0', 'nightmare').base, path, catalog

    def test_final_catalog_proof_rejects_superseded_selection_and_pending_recovery(self):
        for damage, code in (('superseded', 'resource-catalog-superseded'),
                             ('pending', 'resource-catalog-recovery-required')):
            with self.subTest(damage=damage):
                view, path, catalog = self.selected_view()

                def change():
                    if damage == 'superseded':
                        catalog['generation'] = str(uuid.uuid4())
                        path.write_text(json.dumps(catalog), encoding='utf-8')
                    else:
                        pending = path.parent / 'transactions/pending-catalog.json'
                        pending.parent.mkdir()
                        pending.write_bytes(b'{}')

                with self.assertRaisesRegex(files.ImportRejected, '^imported-native-base-invalid$') as caught:
                    self.at_replay(view, change)
                self.assertEqual(caught.exception.__cause__.code, code)

    def test_final_ownership_proof_rejects_root_reference_guard_and_live_callback_replacement(self):
        for changed in ('resource_root', 'reference', 'guard', 'reader_root', 'bundle_reader', 'bundle_reference',
                        'resources', 'adapters', 'main', 'local', 'exit', 'fast', 'groups_view'):
            with self.subTest(changed=changed):
                view, other = self.view(), self.view()
                construct = native.NativeBaseBundle
                constructed = []

                def actual_construct(*args, **kwargs):
                    result = construct(*args, **kwargs)
                    constructed.append(result)
                    return result

                def change():
                    values = constructed[0]._values
                    if changed == 'resource_root':
                        view.resource_root = other.resource_root
                    elif changed == 'reference':
                        view.component_ref = replace(view.component_ref, component_id='controls')
                    elif changed == 'guard':
                        view._assert_current = lambda: None
                    elif changed == 'reader_root':
                        values['reader'].root = other.root
                    elif changed == 'bundle_reader':
                        values['reader'] = copy(values['reader'])
                    elif changed == 'bundle_reference':
                        values['component_ref'] = replace(view.component_ref, component_id='controls')
                    elif changed == 'fast':
                        values['fast_structure_source'].reader = copy(values['reader'])
                    elif changed == 'groups_view':
                        values['reviewed_front_groups']._view = other
                    else:
                        owner, field = {
                            'resources': (values['resources'], 'read_resource'),
                            'adapters': (values['adapters'], '_read_resource'),
                            'main': (values['entry_identity'], 'read_resource'),
                            'local': (values['side_entry_identity'], 'read_resource'),
                            'exit': (values['exit_recognizer'], 'read_resource'),
                        }[changed]
                        setattr(owner, field, copy(values['reader']))

                with patch.object(native, 'NativeBaseBundle', actual_construct):
                    with self.assertRaisesRegex(files.ImportRejected, 'component-(root|reference)-changed'):
                        self.at_replay(view, change)

    def test_first_semantic_error_precedes_missing_root_and_exit_proof_errors(self):
        def invalid_component(manifest):
            next(row for row in manifest['components'] if row['componentId'] == 'base')['type'] = 'controls-profile'

        view = self.view(manifest_change=invalid_component)
        view.root.rename(view.root.with_name('retained-base'))
        with patch.object(packages._AdmissionReadScope, '_finish',
                          side_effect=AssertionError('exit proof masked first error')) as finish:
            with self.assertRaisesRegex(files.ImportRejected, '^imported-native-base-component-type-invalid$'):
                native.decode_native_base(view)
        finish.assert_not_called()

    def test_final_ownership_includes_actual_consumer_sources_and_store_aliases(self):
        for changed in ('resources_root', 'resources_index', 'resources_entries', 'main_root', 'local_root',
                        'main_structures', 'local_structures', 'main_provider', 'local_provider',
                        'fast_index', 'fast_entries'):
            with self.subTest(changed=changed):
                view = self.view()
                construct = native.NativeBaseBundle
                constructed = []

                def actual_construct(*args, **kwargs):
                    result = construct(*args, **kwargs)
                    constructed.append(result)
                    return result

                def change():
                    values = constructed[0]._values
                    resources = values['resources']
                    main, local = values['entry_identity'], values['side_entry_identity']
                    fast = values['fast_structure_source']
                    owner, field, replacement = {
                        'resources_root': (resources, 'resource_root', self.root / 'foreign-root'),
                        'resources_index': (resources, 'index_path', self.root / 'foreign-index.json'),
                        'resources_entries': (resources, 'entries', dict(resources.entries)),
                        'main_root': (main, 'root', self.root / 'foreign-main'),
                        'local_root': (local, 'root', self.root / 'foreign-local'),
                        'main_structures': (main, 'structures', copy(resources)),
                        'local_structures': (local, 'structures', copy(resources)),
                        'main_provider': (main, 'shared_reference_provider', lambda *args: None),
                        'local_provider': (local, 'shared_reference_provider', lambda *args: None),
                        'fast_index': (fast, 'index_path', 'foreign-index.json'),
                        'fast_entries': (fast, 'entries', dict(fast.entries)),
                    }[changed]
                    setattr(owner, field, replacement)

                with patch.object(native, 'NativeBaseBundle', actual_construct):
                    with self.assertRaisesRegex(files.ImportRejected, 'component-root-changed'):
                        self.at_replay(view, change)

    def test_published_consumers_retain_live_integrity_reads(self):
        view = self.view()
        bundle = native.decode_native_base(view)
        self.assert_live_readers(bundle, view)
        values = bundle._values
        row = next(iter(values['resources'].entries.values()))
        path = view.path(row['resourcePath'])
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        for reader in (values['reader'], values['resources'].read_resource,
                values['adapters']._read_resource, values['entry_identity'].read_resource,
                values['side_entry_identity'].read_resource, values['exit_recognizer'].read_resource):
            with self.subTest(reader=reader):
                with self.assertRaisesRegex(files.ImportRejected, 'file-integrity-mismatch'):
                    reader(path)

    def test_four_modes_match_actual_live_models_and_keep_all_persistent_readers_live(self):
        cases = [('s0', 'nightmare', self.archive, (1, 3, 1, 3), (True, True, True)),
                 ('s0', 'difficult', self.difficult_archive(), (30, 58, 30, 60), (False, False, False))]
        for season, mode in (('s1', 'difficult'), ('s1', 'nightmare')):
            temporary = tempfile.TemporaryDirectory(prefix=f'admission-{season}-{mode}-')
            self.addCleanup(temporary.cleanup)
            source = (difficult_fixtures.S0DifficultSourceFixture(Path(temporary.name)) if season == 's0'
                      else s1_fixtures.S1NativeBaseSourceFixture(Path(temporary.name), mode=mode))
            source.export()
            counts = ((30, 58, 30, 60) if season == 's0' else
                      (len(source.ids), len(source.ids) * 4, len(source.ids), len(source.ids) * 4))
            roles = (False, False, False) if season == 's0' else (True, False, True)
            cases.append((season, mode, source.output, counts, roles))

        for season, mode, archive, counts, roles in cases:
            with self.subTest(season=season, mode=mode):
                original_archive, self.archive = self.archive, archive
                try:
                    live_view, view = self.view(), self.view()
                finally:
                    self.archive = original_archive
                live_decode = (difficult._decode_s0_difficult if season == 's0' and mode == 'difficult' else
                               lambda checked, live: native._decode_component(checked, live, season, mode))
                expected, original_payloads, _ = self.trace(lambda selected: live_decode(selected, selected), live_view)
                bundle, actual_payloads, _ = self.trace(
                    lambda selected: native.decode_native_base(selected, season=season, mode=mode), view)
                self.assertEqual(actual_payloads[:len(original_payloads)], original_payloads)
                self.assertEqual(actual_payloads[len(original_payloads):], list(dict.fromkeys(original_payloads)))
                self.assert_live_readers(bundle, view)
                self.assertEqual(self.model(bundle), self.model(expected))
                self.assertEqual((len(bundle.pack.maps), len(bundle.resources.entries),
                    len(bundle.entry_identity.entries), len(bundle.side_entry_identity.entries)), counts)
                self.assertEqual((bundle.rank_ready, bundle.semantic_side_entries,
                                  bundle.passport_source_available), roles)


if __name__ == '__main__':
    unittest.main()
