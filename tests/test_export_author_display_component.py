"""New independent author-display ZIP producer over synthetic S1D resources."""
from copy import deepcopy
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
import zipfile
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tools'),
                str(ROOT / 'experiments' / 'qt_quick')]

import export_author_display_component as exporter
import export_native_base_package as native_exporter
import imported_resource_archive as archives
import imported_resource_reader as resource_reader
import map_package_upgrade as publication
import star_map_import as files
from client_providers import SharedReferenceProvider
from imported_author_display import decode_author_display
from imported_author_displays import ImportedAuthorDisplays
from imported_native_base import decode_native_base
from imported_resource_packages import ComponentRef, PackageView
import test_export_s1_native_base_package as base_fixture


def _png_bytes():
    image = Image.new('RGBA', (6, 4), (22, 33, 44, 255))
    image.putpixel((2, 1), (1, 2, 3, 0))
    image.putpixel((3, 1), (5, 6, 7, 127))
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def _jpeg_bytes():
    image = Image.new('RGB', (5, 3), (64, 128, 192))
    stream = BytesIO()
    image.save(stream, format='JPEG', quality=90)
    return stream.getvalue()


def _stack_contains(frame, code):
    while frame is not None:
        if frame.f_code is code:
            return True
        frame = frame.f_back
    return False


class ExportAuthorDisplayComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='author-display-base-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.fixture_root = Path(temporary.name).resolve()
        cls.source = base_fixture.S1NativeBaseSourceFixture(cls.fixture_root / 'base-source')
        # The expensive real native-base producer runs once for this class.
        cls.source.export()
        cls.resource_root = cls.fixture_root / 'installed'
        saved = archives.save_archive(cls.source.output, cls.resource_root)
        cls.base_ref = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        cls.base_view = PackageView(cls.resource_root, cls.base_ref)
        cls.base = decode_native_base(cls.base_view, season='s1', mode='difficult')
        cls.provider = SharedReferenceProvider(cls.base_view.root, cls.base.resources,
            cls.base.versions, check_current=cls.base.assert_current)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='author-display-export-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.resource_root = type(self).resource_root
        self.base_ref = type(self).base_ref
        self.base_view = type(self).base_view
        self.base = type(self).base
        self.provider = type(self).provider
        self.image_root = self.root / 'author-images'
        self.image_root.mkdir()
        (self.image_root / 'route.png').write_bytes(_png_bytes())
        (self.image_root / 'route.jpg').write_bytes(_jpeg_bytes())
        self.registration_path = self.root / 'registration.json'
        self.output = self.root / 'author-display.zip'

    def registration(self, *, layers=None, **extra):
        document = dict(schema=exporter.INPUT_SCHEMA, profileId='author-synthetic',
            authorName='合成测试作者', displayName='合成测试路线',
            layers=deepcopy(layers) if layers is not None else [
                dict(mapId=self.source.ids[0], floor='-4', image='route.png',
                    matrixDisplayToReference=[[.8,.2,1],[.1,1.2,2],[0,0,1]]),
                dict(mapId=self.source.ids[0], floor='-3', image='route.jpg',
                    matrixDisplayToReference=[[1.1,.1,0],[.05,.9,1],[0,0,1]])])
        document.update(extra)
        self.registration_path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        return document

    def export(self, *, registration=None, **kwargs):
        if registration is None:
            self.registration()
        else:
            self.registration_path.write_text(json.dumps(registration, ensure_ascii=False), encoding='utf-8')
        return exporter.export_component(self.resource_root, self.base_ref,
            self.registration_path, self.image_root, self.output,
            package_id='author-synthetic-components', resource_version='v1', **kwargs)

    def test_public_cli_creates_route_suffix_with_real_display_contents(self):
        from resource_package_types import ROUTE_SUFFIX, validate_package_filename
        self.registration()
        output = self.root / ('cli-output' + ROUTE_SUFFIX)
        result = StringIO()
        with redirect_stdout(result):
            exporter.main(['--base-resource-root', str(self.resource_root),
                '--base-package-id', self.base_ref.package_id,
                '--base-resource-version', self.base_ref.resource_version,
                '--base-component-id', self.base_ref.component_id,
                '--base-manifest-sha256', self.base_ref.manifest_sha256,
                '--registration', str(self.registration_path), '--image-root', str(self.image_root),
                '--output', str(output), '--package-id', 'author-cli', '--resource-version', 'cli-v1'])
        self.assertEqual(json.loads(result.getvalue())['layerCount'], 2)
        with zipfile.ZipFile(output) as archive:
            components = json.loads(archive.read('manifest.json'))['components']
        self.assertEqual(validate_package_filename(output, components), ROUTE_SUFFIX)

    def test_actual_zip_public_roundtrip_preserves_png_jpeg_affines_and_runtime_bindings(self):
        result = self.export()
        self.assertEqual(result['componentId'], 'display-s1-difficult')
        self.assertEqual(result['context'], dict(season='s1', mode='difficult'))
        self.assertEqual(result['layerCount'], 2)
        inspected = archives.inspect_archive(self.output)
        self.assertEqual(inspected.status, 'inspected')
        self.assertEqual(inspected.archive_sha256, result['sha256'])
        archive = zipfile.ZipFile(self.output)
        with archive:
            self.assertEqual(set(archive.namelist()), {
                'manifest.json', 'data/display-s1-difficult/display.json',
                f'data/display-s1-difficult/images/{self.source.ids[0]}/-4.png',
                f'data/display-s1-difficult/images/{self.source.ids[0]}/-3.jpg',
            })
            manifest = json.loads(archive.read('manifest.json'))
            component = manifest['components'][0]
            self.assertEqual((component['type'], component['schemaVersion']), ('display', 1))
            self.assertEqual(component['contexts'], [dict(season='s1', mode='difficult')])
            self.assertEqual(component['dependencies']['base'], dict(
                packageId=self.base_ref.package_id, resourceVersion=self.base_ref.resource_version,
                componentId=self.base_ref.component_id, manifestSha256=self.base_ref.manifest_sha256))
            display_doc = json.loads(archive.read('data/display-s1-difficult/display.json'))
            self.assertEqual(display_doc['schema'], exporter.SCHEMA)
            self.assertNotIn('provenance', display_doc)
            self.assertEqual(archive.read(f'data/display-s1-difficult/images/{self.source.ids[0]}/-4.png'),
                             (self.image_root / 'route.png').read_bytes())
            self.assertEqual(archive.read(f'data/display-s1-difficult/images/{self.source.ids[0]}/-3.jpg'),
                             (self.image_root / 'route.jpg').read_bytes())

        saved = archives.save_archive(self.output, self.resource_root)
        display_ref = next(ref for ref in saved.component_refs if ref.component_id == 'display-s1-difficult')
        view = PackageView(self.resource_root, display_ref)
        decoded = decode_author_display(view, self.base, self.provider)
        self.assertEqual(decoded.component_ref, display_ref)
        png_layer = decoded.layer(self.source.ids[0], '-4')
        jpeg_layer = decoded.layer(self.source.ids[0], '-3')
        self.assertEqual(decoded.image_bytes(self.source.ids[0], '-4'), (self.image_root / 'route.png').read_bytes())
        self.assertEqual(decoded.image_bytes(self.source.ids[0], '-3'), (self.image_root / 'route.jpg').read_bytes())
        self.assertEqual(png_layer.matrix_display_to_reference,
            ((.8,.2,1),(.1,1.2,2),(0,0,1)))
        self.assertEqual(jpeg_layer.encoding, 'jpeg')
        self.assertEqual(self.base_view.path(png_layer.reference.path),
            self.provider('shared-structure', self.source.ids[0], '-4').path)
        with Image.open(BytesIO(decoded.image_bytes(self.source.ids[0], '-4'))) as image:
            self.assertEqual(image.getpixel((2, 1)), (1, 2, 3, 0))

        runtime = ImportedAuthorDisplays([view], self.base, self.provider)
        self.assertIn('author-synthetic', runtime.profiles)
        binding = runtime.binding('author-synthetic', self.source.ids[0], '-4')
        self.assertTrue(binding['usable'])
        np.testing.assert_allclose(runtime.adapters.matrix_profile_to_canonical(
            'author-synthetic', self.source.ids[0], '-4'),
            np.asarray(png_layer.matrix_display_to_reference))
        pixels = runtime.pixels(binding['path'], 'full-reference')
        self.assertEqual(tuple(pixels.shape), (4, 6, 4))
        self.assertEqual(tuple(int(value) for value in pixels[1, 2]), (3, 2, 1, 0))

    def test_bad_affine_bad_image_and_escaping_path_leave_no_zip(self):
        for change in ('bad-matrix', 'bad-image', 'bad-path'):
            with self.subTest(change=change):
                if change == 'bad-matrix':
                    layers = [dict(mapId=self.source.ids[0], floor='-4', image='route.png',
                        matrixDisplayToReference=[[1,0,0],[0,0,0],[0,0,1]])]
                    self.registration(layers=layers)
                elif change == 'bad-image':
                    (self.image_root / 'route.png').write_bytes(b'not a png')
                    self.registration(layers=[dict(mapId=self.source.ids[0], floor='-4', image='route.png',
                        matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])])
                else:
                    self.registration(layers=[dict(mapId=self.source.ids[0], floor='-4', image='../outside.png',
                        matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])])
                with self.assertRaises(ValueError):
                    exporter.export_component(self.resource_root, self.base_ref,
                        self.registration_path, self.image_root, self.output,
                        package_id='author-synthetic-components', resource_version='v1')
                self.assertFalse(self.output.exists())
                if change == 'bad-image':
                    (self.image_root / 'route.png').write_bytes(_png_bytes())

    def test_incompatible_base_dependency_and_registration_context_are_rejected(self):
        wrong_ref = ComponentRef(self.base_ref.package_id, self.base_ref.resource_version,
            self.base_ref.component_id, '0' * 64)
        self.registration()
        with self.assertRaises((files.ImportRejected, ValueError)):
            exporter.export_component(self.resource_root, wrong_ref, self.registration_path,
                self.image_root, self.output, package_id='author-synthetic-components', resource_version='v1')
        self.assertFalse(self.output.exists())

    def test_public_decoder_rejects_wrong_dependency_and_component_context(self):
        self.export()
        for change, expected in (('dependency', 'base-dependency-mismatch'),
                                 ('context', 'component-context-invalid')):
            with self.subTest(change=change):
                bad_archive = self.root / f'bad-{change}.zip'
                with zipfile.ZipFile(self.output) as source, zipfile.ZipFile(bad_archive, 'x') as target:
                    for name in source.namelist():
                        raw = source.read(name)
                        if name == 'manifest.json':
                            manifest = json.loads(raw)
                            manifest['packageId'] = f'author-synthetic-bad-{change}'
                            if change == 'dependency':
                                manifest['components'][0]['dependencies']['base']['manifestSha256'] = '0' * 64
                            else:
                                manifest['components'][0]['contexts'] = [dict(season='s0', mode='nightmare')]
                            raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                separators=(',', ':')).encode('utf-8')
                        target.writestr(name, raw)
                inspected = archives.inspect_archive(bad_archive)
                bad_root = self.root / f'bad-resources-{change}'
                installed = archives.save_archive(bad_archive, bad_root,
                    expected_archive_sha256=inspected.archive_sha256)
                display_ref = next(ref for ref in installed.component_refs
                                   if ref.component_id == 'display-s1-difficult')
                view = PackageView(bad_root, display_ref)
                with self.assertRaisesRegex(files.ImportRejected, expected):
                    decode_author_display(view, self.base, self.provider)

        self.registration(season='s0')
        invalid_output = self.root / 'invalid-context.zip'
        with self.assertRaisesRegex(ValueError, 'registration-invalid'):
            exporter.export_component(self.resource_root, self.base_ref, self.registration_path,
                self.image_root, invalid_output, package_id='author-synthetic-components', resource_version='v1')
        self.assertFalse(invalid_output.exists())

    def test_unavailable_typed_reference_is_reported_without_fabrication(self):
        layers = [dict(mapId=self.source.ids[0], floor='-4', image='route.png',
            matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])]
        self.registration(layers=layers)
        with patch.object(exporter, '_runtime_reference_provider', return_value=lambda *_args:None):
            with self.assertRaisesRegex(ValueError, 'typed-reference-unavailable'):
                exporter.export_component(self.resource_root, self.base_ref,
                    self.registration_path, self.image_root, self.output,
                    package_id='author-synthetic-components', resource_version='v1')
        self.assertFalse(self.output.exists())

    def test_component_id_override_and_cli_help_are_explicit(self):
        result = self.export(component_id='custom-display-difficult')
        self.assertEqual(result['componentId'], 'custom-display-difficult')
        help_text = exporter.build_parser().format_help()
        self.assertIn('display-{season}-{mode}', help_text)
        self.assertIn(exporter.INPUT_SCHEMA, help_text)

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b'owned-existing-output')
        self.registration()
        with self.assertRaises(FileExistsError):
            exporter.export_component(self.resource_root, self.base_ref,
                self.registration_path, self.image_root, self.output,
                package_id='author-synthetic-components', resource_version='v1')
        self.assertEqual(self.output.read_bytes(), b'owned-existing-output')

    def test_cancellation_after_final_archive_hash_prevents_publication(self):
        self.registration(layers=[dict(mapId=self.source.ids[0], floor='-4', image='route.png',
            matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])])
        cancelled = {'value': False}
        phase = {'roundtrip_complete': False, 'publication_copy': False}
        real_digest = exporter.hashlib.file_digest
        real_roundtrip = exporter._roundtrip
        real_copy = native_exporter.shutil.copyfileobj

        def finish_roundtrip(*args, **kwargs):
            result = real_roundtrip(*args, **kwargs)
            phase['roundtrip_complete'] = True
            return result

        def hash_then_cancel(handle, algorithm, *args, **kwargs):
            result = real_digest(handle, algorithm, *args, **kwargs)
            if phase['roundtrip_complete']:
                cancelled['value'] = True
            return result

        def record_real_copy(source, target, **kwargs):
            phase['publication_copy'] = True
            return real_copy(source, target, **kwargs)

        with patch.object(exporter, '_roundtrip', side_effect=finish_roundtrip), \
                patch.object(exporter.hashlib, 'file_digest', side_effect=hash_then_cancel), \
                patch.object(native_exporter.shutil, 'copyfileobj', side_effect=record_real_copy):
            with self.assertRaisesRegex(ValueError, 'author-display-export-cancelled'):
                exporter.export_component(self.resource_root, self.base_ref,
                    self.registration_path, self.image_root, self.output,
                    package_id='author-synthetic-components', resource_version='cancel-final-hash',
                    cancelled=lambda: cancelled['value'])
        self.assertTrue(cancelled['value'])
        self.assertFalse(phase['publication_copy'])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.author-display.zip.*.tmp')), [])

    def test_cancellation_during_real_publication_copy_prevents_rename(self):
        self.registration(layers=[dict(mapId=self.source.ids[0], floor='-4', image='route.png',
            matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])])
        cancelled = {'value': False}
        real_copy = native_exporter.shutil.copyfileobj

        def copy_then_cancel(source, target, **kwargs):
            result = real_copy(source, target, **kwargs)
            cancelled['value'] = True
            return result

        with patch.object(native_exporter.shutil, 'copyfileobj', side_effect=copy_then_cancel):
            with self.assertRaisesRegex(ValueError, 'author-display-export-cancelled'):
                exporter.export_component(self.resource_root, self.base_ref,
                    self.registration_path, self.image_root, self.output,
                    package_id='author-synthetic-components', resource_version='cancel-copy',
                    cancelled=lambda: cancelled['value'])
        self.assertTrue(cancelled['value'])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.author-display.zip.*.tmp')), [])

    def test_cancellation_during_actual_owned_move_hash_prevents_rename(self):
        self.registration(layers=[dict(mapId=self.source.ids[0], floor='-4', image='route.png',
            matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])])
        cancelled = {'value': False}
        real_digest = publication.hashlib.file_digest

        def hash_owned_then_cancel(handle, algorithm, *args, **kwargs):
            result = real_digest(handle, algorithm, *args, **kwargs)
            if _stack_contains(inspect.currentframe().f_back, publication._move_owned.__code__):
                cancelled['value'] = True
            return result

        with patch.object(publication.hashlib, 'file_digest', side_effect=hash_owned_then_cancel):
            with self.assertRaisesRegex(ValueError, 'author-display-export-cancelled'):
                exporter.export_component(self.resource_root, self.base_ref,
                    self.registration_path, self.image_root, self.output,
                    package_id='author-synthetic-components', resource_version='cancel-owned-hash',
                    cancelled=lambda: cancelled['value'])
        self.assertTrue(cancelled['value'])
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob('.author-display.zip.*.tmp')), [])

    @unittest.skipUnless(os.name == 'nt', 'exercises the native Windows rename handle')
    def test_cancellation_arriving_after_successful_native_rename_still_reports_success(self):
        import ctypes

        self.registration(layers=[dict(mapId=self.source.ids[0], floor='-4', image='route.png',
            matrixDisplayToReference=[[1,0,0],[0,1,0],[0,0,1]])])
        cancelled = {'value': False}
        real_windll = ctypes.WinDLL

        class WrappedRename:
            def __init__(self, function, after_call):
                self.function = function
                self.after_call = after_call

            @property
            def argtypes(self):
                return self.function.argtypes

            @argtypes.setter
            def argtypes(self, value):
                self.function.argtypes = value

            @property
            def restype(self):
                return self.function.restype

            @restype.setter
            def restype(self, value):
                self.function.restype = value

            def __call__(self, *args):
                result = self.function(*args)
                self.after_call(args, result)
                return result

        class ApiProxy:
            def __init__(self, library):
                self.library = library
                self.output_move = False

            def __getattr__(self, name):
                value = getattr(self.library, name)
                if name == 'CreateFileW':
                    def created(args, result):
                        try:
                            source_name = Path(args[0]).name
                        except (TypeError, ValueError):
                            source_name = ''
                        self.output_move = source_name.startswith(f'.{self.output.name}.')
                    return WrappedRename(value, created)
                if name == 'SetFileInformationByHandle':
                    def renamed(_args, result):
                        if self.output_move and result:
                            cancelled['value'] = True
                    return WrappedRename(value, renamed)
                return value

        def wrapped_windll(*args, **kwargs):
            proxy = ApiProxy(real_windll(*args, **kwargs))
            proxy.output = self.output
            return proxy

        with patch.object(publication.ctypes, 'WinDLL', side_effect=wrapped_windll):
            result = exporter.export_component(self.resource_root, self.base_ref,
                self.registration_path, self.image_root, self.output,
                package_id='author-synthetic-components', resource_version='cancel-after-rename',
                cancelled=lambda: cancelled['value'])
        self.assertTrue(cancelled['value'])
        self.assertEqual(result['output'], str(self.output))
        self.assertEqual(archives.inspect_archive(self.output).status, 'inspected')

    def test_oversize_registration_is_rejected_by_bounded_reader_before_returning_bytes(self):
        from imported_resource_reader import MAX_JSON_BYTES

        self.registration_path.write_bytes(b' ' * (MAX_JSON_BYTES + 1))
        observed = {}
        real_read = exporter.read_stable_file

        def observe_bounded_read(path, limit, **kwargs):
            observed['limit'] = limit
            raw, row = real_read(path, limit, **kwargs)
            observed['returnedBytes'] = len(raw)
            return raw, row

        with patch.object(exporter, 'read_stable_file', side_effect=observe_bounded_read):
            with self.assertRaisesRegex(ValueError, 'registration-invalid-metadata-file'):
                exporter.export_component(self.resource_root, self.base_ref,
                    self.registration_path, self.image_root, self.output,
                    package_id='author-synthetic-components', resource_version='oversize-registration')
        self.assertEqual(observed.get('limit'), MAX_JSON_BYTES)
        self.assertNotIn('returnedBytes', observed)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
