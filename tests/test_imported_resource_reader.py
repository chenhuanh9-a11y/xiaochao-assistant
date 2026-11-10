"""Actual PackageView -> content preflight -> original typed NPZ decoder."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
from io import BytesIO
import zipfile

import numpy as np
import export_native_base_package as native_base_producer
import imported_native_base as native_base
import imported_resource_archive as archives
import test_shared_structure_package_io as fixture_module
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader
from shared_structure_reference import SharedStructureResourceStore
import star_map_import as files
import test_export_native_base_package as native_base_fixtures


class ComponentResourceReaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.SharedStructurePackageIOTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def add_file(self, raw, relative='metadata.json'):
        view = self.fixture.write_package()
        (view.root / relative).write_bytes(raw)
        manifest_path = self.fixture.package_root / 'manifest.json'
        manifest = json.loads(manifest_path.read_text('utf-8'))
        manifest['files'].append(dict(componentId=fixture_module.COMPONENT, path=relative,
            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        manifest_bytes = fixture_module._canonical(manifest)
        manifest_path.write_bytes(manifest_bytes)
        ref = ComponentRef(fixture_module.PACKAGE_ID, fixture_module.VERSION, fixture_module.COMPONENT,
                           hashlib.sha256(manifest_bytes).hexdigest())
        return PackageView(self.fixture.resource_root, ref), relative

    def short_path_alias(self, path):
        if os.name != 'nt':
            self.skipTest('GetShortPathNameW is Windows-only')
        get_short_path = ctypes.windll.kernel32.GetShortPathNameW
        get_short_path.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        get_short_path.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short_path(str(path), buffer, len(buffer))
        if not length:
            self.skipTest('GetShortPathNameW could not obtain a real alias for this temp path')
        alias = Path(buffer.value)
        if os.path.normcase(str(alias)) == os.path.normcase(str(path)):
            self.skipTest('8.3 aliases are unavailable for this temp path on this volume')
        self.assertTrue(alias.samefile(path))
        return alias

    def test_actual_typed_loader_uses_reader_and_preserves_masks(self):
        view = self.fixture.write_package()
        reader = ComponentResourceReader(view)
        store = SharedStructureResourceStore(view.path(fixture_module.INDEX), view.root, read_resource=reader)
        binding = fixture_module.BINDING
        reference = store.load(binding['profile'], binding['mapId'], binding['floor'])
        self.assertIsNone(store.error)
        self.assertIsNotNone(reference)
        np.testing.assert_array_equal(reference.clean_mask, self.fixture.mask)
        self.assertEqual(reference.path, view.path(fixture_module.PRODUCT))

    def test_invalid_array_header_never_enters_numpy_loader(self):
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('shared_mask.npy', np.lib.format.magic(2, 0) + struct.pack('<I', 2**32 - 1))
        self.fixture.npz_bytes = stream.getvalue()
        view = self.fixture.write_package()
        reader = ComponentResourceReader(view)
        store = SharedStructureResourceStore(view.path(fixture_module.INDEX), view.root, read_resource=reader)
        binding = fixture_module.BINDING
        with patch('numpy.load', side_effect=AssertionError('unbounded allocation entered')):
            self.assertIsNone(store.load(binding['profile'], binding['mapId'], binding['floor']))
        with self.assertRaisesRegex(files.ImportRejected, '^imported-array-header-size$'):
            reader(view.path(fixture_module.PRODUCT))

    def test_component_path_and_replaced_snapshot_never_fall_back(self):
        current = [True]
        def guard():
            if not current[0]:
                raise ValueError('catalog superseded')
        view = self.fixture.write_package(assert_current=guard)
        reader = ComponentResourceReader(view)
        outside = view.root.parent / 'outside.json'
        outside.write_bytes(b'{}')
        with self.assertRaisesRegex(files.ImportRejected, 'path-outside-component'):
            reader(outside)
        reader(view.path(fixture_module.PRODUCT))
        current[0] = False
        with self.assertRaisesRegex(ValueError, 'catalog superseded'):
            reader(view.root / fixture_module.PRODUCT)

    def test_native_base_decoder_accepts_real_short_and_long_component_aliases(self):
        producer = native_base_fixtures.NativeBaseExporterTests(
            'test_real_roundtrip_preserves_full_competition_and_source_bytes')
        producer.setUp()
        self.addCleanup(producer.doCleanups)
        coordinates = producer.read(native_base_producer.COORDINATES)
        for floor in native_base_producer.FLOORS:
            coordinates['maps'][producer.mid]['floors'][floor].update(
                coordinateWidth=8, coordinateHeight=8)
            coordinates['profiles']['shared-structure']['maps'][producer.mid]['floors'][floor]['evidence'] = dict(
                method='same-typed-shared-native-reference-canvas', algebraicallyExact=True)
        producer.write(native_base_producer.COORDINATES, coordinates)
        main = producer.read(native_base_producer.ENTRIES + '/entries.json')
        main['entries'][0].update(center=[2., 2.], kind='main', material='fixture-native-room')
        producer.write(native_base_producer.ENTRIES + '/entries.json', main)
        producer.export()
        resource_root = producer.root / 'roundtrip-resources'
        saved = archives.save_archive(producer.output, resource_root)
        ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')

        # Exercise the same path split as Windows CI: PackageView retains the
        # actual 8.3 alias, while NativeDifficultPack.resolve() asks the reader
        # with the corresponding long path.
        aliased_root = self.short_path_alias(resource_root)
        view = PackageView(aliased_root, ref)
        bundle = native_base.decode_native_base(view)
        self.assertTrue(bundle.ready)
        self.assertEqual(bundle.gaps, ())

    def test_reader_rejects_undeclared_files_and_reparse_aliases(self):
        view = self.fixture.write_package()
        reader = ComponentResourceReader(view)
        undeclared = view.root / 'not-in-inventory.json'
        undeclared.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(files.ImportRejected, 'file-not-declared'):
            reader(undeclared)

        link = self.fixture.storage / 'component-alias'
        try:
            link.symlink_to(view.root, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f'directory symlink creation is unavailable: {error}')
        with self.assertRaisesRegex(files.ImportRejected, 'linked-target'):
            reader(link / fixture_module.PRODUCT)

    def test_json_duplicates_nonfinite_and_complexity_are_rejected(self):
        for raw in (b'{"key":1,"key":2}', b'{"value":NaN}', b'{"value":1e309}'):
            view, relative = self.add_file(raw)
            with self.subTest(raw=raw), self.assertRaises(files.ImportRejected):
                ComponentResourceReader(view).json(relative)
        view, relative = self.add_file(b'{"items":[1,2,3,4]}')
        with patch('imported_resource_reader.MAX_JSON_NODES', 4):
            with self.assertRaisesRegex(files.ImportRejected, 'json-complexity'):
                ComponentResourceReader(view).json(relative)

    def test_json_declared_size_is_rejected_before_reading_large_payload(self):
        view, relative = self.add_file(b'{"key":"value"}')
        reader = ComponentResourceReader(view)
        path = view.path(relative)
        with patch('imported_resource_reader.MAX_JSON_BYTES', 4), patch.object(view, 'read',
                side_effect=AssertionError('oversized JSON payload read')):
            with self.assertRaisesRegex(files.ImportRejected, 'json-size'):
                reader(path)

    def test_uppercase_extensions_do_not_bypass_json_and_npz_preflight(self):
        view, relative = self.add_file(b'{"key":1,"key":2}', 'metadata.JSON')
        with self.assertRaisesRegex(files.ImportRejected, 'json-invalid'):
            ComponentResourceReader(view)(view.path(relative))
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('a.npy', np.lib.format.magic(2, 0) + struct.pack('<I', 2**32 - 1))
        view, relative = self.add_file(stream.getvalue(), 'product.NPZ')
        with self.assertRaisesRegex(files.ImportRejected, 'header-size'):
            ComponentResourceReader(view)(view.path(relative))


if __name__ == '__main__':
    unittest.main()
