"""Real temporary files/decodes; no installed assets, game or GUI timing."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import generic_author_base as base
import s1_author_displays as displays
import generic_author_package as generic
import test_generic_author_package as fixtures
from map_resource_context import BrowseContext


class GalleryValidationRequestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GenericPackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.install(self.fixture.package())
        self.context = BrowseContext('nightmare', self.root / '_internal', None,
                                     None, None, (), {}, season='s0')

    def resolve(self):
        return base.resolve_layer(self.root, 's0', 'nightmare', 'map', 'B1')

    def test_real_receipt_and_binding_resolve_once_without_decoding_all_pixels(self):
        from PIL import Image
        with patch.object(base, '_one', wraps=base._one) as one, \
                patch.object(Image.Image, 'load', autospec=True, side_effect=Image.Image.load) as decode:
            result = displays.append_browse_profile(self.context)
        self.assertEqual(len(result.profiles), 1)
        self.assertEqual(one.call_count, 3)  # one source/index/registry resolution, not two
        self.assertEqual(decode.call_count, 0)
        self.assertIsNone(base._BROWSE_REQUEST.get())

    def test_request_isolation_return_copy_and_thread_isolation(self):
        with patch.object(base, '_one', wraps=base._one) as one:
            for _ in range(2):
                with base.browse_validation_request():
                    self.resolve()['dependencies'].clear()
                    self.assertTrue(self.resolve()['dependencies'])
            self.assertEqual(one.call_count, 6)
        with base.browse_validation_request():
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertIsNone(pool.submit(base._BROWSE_REQUEST.get).result())

    def test_same_size_and_mtime_content_change_fails_final_hash(self):
        path = self.root / '_internal/references/nightmare-map-pack.json'
        with self.assertRaisesRegex(ValueError, 'changed during browse'):
            with base.browse_validation_request():
                self.resolve()
                old = path.stat()
                data = path.read_bytes().replace(b'"v1"', b'"v2"')
                path.write_bytes(data)
                os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
                # Cache hit is provisional; no result escapes before final check.
                self.resolve()
        self.assertIsNone(base._BROWSE_REQUEST.get())

    def test_link_replacement_fails_and_next_request_revalidates(self):
        path = self.root / '_internal/references/versions.json'
        with self.assertRaises(ValueError):
            with base.browse_validation_request():
                self.resolve()
                other = path.with_name('other.json')
                other.write_bytes(path.read_bytes())
                path.unlink()
                path.symlink_to(other)
        with self.assertRaises(ValueError):
            with base.browse_validation_request():
                self.resolve()

    def test_cancel_before_package_and_after_first_image_propagates(self):
        with self.assertRaises(CancelledError):
            displays.append_browse_profile(self.context, cancelled=lambda: True)
        cancelled = [False]
        read = displays.InstalledAuthorPackage._read
        def cancel_on_image(owner, path):
            result = read(owner, path)
            if path.endswith('.png'):
                cancelled[0] = True
            return result
        with patch.object(displays.InstalledAuthorPackage, '_read', cancel_on_image):
            with self.assertRaises(CancelledError):
                displays.append_browse_profile(self.context, cancelled=lambda: cancelled[0])
        self.assertIsNone(base._BROWSE_REQUEST.get())
        self.assertEqual(len(displays.append_browse_profile(self.context).profiles), 1)

    def test_no_cached_author_hash_or_dependency_hash_across_requests(self):
        displays.append_browse_profile(self.context)
        path = next(self.root.glob('_internal/assets/authors/**/*.png'))
        data = bytearray(path.read_bytes()); data[-1] ^= 1
        path.write_bytes(data)
        result = displays.append_browse_profile(self.context)
        self.assertFalse(result.profiles)
        self.assertTrue(any('author-v2-invalid-image' in w for w in result.warnings))

    def test_synthetic_resolution_work_count_and_timing(self):
        samples = {}
        for cached in (False, True):
            timings = []
            counts = []
            for _ in range(3):
                with patch.object(base, '_one', wraps=base._one) as one:
                    started = time.perf_counter()
                    if cached:
                        with base.browse_validation_request():
                            for _ in range(40): self.resolve()
                    else:
                        for _ in range(40): self.resolve()
                    timings.append(round((time.perf_counter() - started) * 1000, 3))
                    counts.append(one.call_count)
            samples[str(cached)] = dict(ms=timings, bindingLookups=counts)
        self.assertEqual(samples['False']['bindingLookups'], [120] * 3)
        self.assertEqual(samples['True']['bindingLookups'], [3] * 3)
        print('SYNTHETIC_REQUEST_BENCHMARK ' + json.dumps(samples))

    def test_s1_all_unique_layers_share_registration_and_preserve_contracts(self):
        import numpy as np
        import s1_native_resources as native
        import test_s1_builtin_resources as builtins
        manifest_path, manifest = builtins.fixture(self.root / '_internal', 'nightmare')
        mode_root = manifest_path.parent
        interfaces = manifest['runtimeInterfaces']
        source_path = mode_root / interfaces['identityPack']
        source = json.loads(source_path.read_text())
        entries, versions, coordinates, identities = [], [], {}, []
        for row in source['maps']:
            mid = row['id']
            row['structureContentSha256'] = 'a' * 64
            coordinates[mid] = {'floors': {}}
            for floor, reference in row['layers'].items():
                version = dict(baseSnapshot='fixture', sourceVersion=native.SOURCE_VERSION,
                    structureVersionKey=f'{mid}@' + 'a' * 64 + '/' + floor)
                binding = dict(profile='shared-structure', mapId=mid, floor=floor, variant='variant-01')
                canvas = dict(coordinateSpace=f'native:{mid}:{floor}', shapeHW=[8, 9])
                np.savez(mode_root / reference, metadata=json.dumps(dict(version, binding=binding,
                    canvas=canvas, sourceKind='game-rendered-shared-structure')), shared_mask=np.ones((8, 9), np.uint8))
                entries.append(dict(binding, **version, resourcePath=reference))
                versions.append(dict(mapId=mid, sourceVersion=native.SOURCE_VERSION,
                    sharedLookup=dict(binding, resourcePath=reference), typedReference=reference,
                    resourceKey=dict(layoutId=mid, structureRawRecordSha256='a' * 64, floor=floor)))
                coordinates[mid]['floors'][floor] = dict(version, coordinateSpace=canvas['coordinateSpace'],
                    coordinateHeight=8, coordinateWidth=9, referenceResource=reference)
                identities.append((mid, floor))
        declarations = {
            interfaces['identityPack']: source,
            interfaces['typedIndex']: dict(schemaVersion=1, resourceKind='shared-structure',
                structureVersionBindings=interfaces['versionBindings'], entries=entries),
            interfaces['versionBindings']: dict(schemaVersion=1,
                resourceKind='native-structure-version-bindings', entries=versions),
            interfaces['coordinatePack']: dict(schema='identityv-nightmare-canonical-coordinate-pack-v1',
                coordinateReferenceProfileId='shared-structure', maps=coordinates,
                policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                            authorizesCrossFloorProjection=False))}
        for relative, doc in declarations.items():
            (mode_root / relative).write_text(json.dumps(doc))
        for row in manifest['files']:
            data = (mode_root / row['path']).read_bytes()
            row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
        results, timings = {}, {}
        for cached in (False, True):
            calls = []
            with patch.object(native, 'registration', wraps=native.registration) as registration:
                started = time.perf_counter()
                def run():
                    for _ in range(2):
                        for mid, floor in identities:
                            calls.append(base.resolve_layer(self.root, 's1', 'nightmare', mid, floor))
                if cached:
                    with base.browse_validation_request(): run()
                else:
                    run()
                timings[str(cached)] = dict(ms=round((time.perf_counter()-started)*1000, 3),
                    registrationCalls=registration.call_count, resolutions=len(calls))
            results[cached] = calls
        self.assertEqual(results[False], results[True])
        self.assertEqual(timings['False']['registrationCalls'], 120)
        self.assertEqual(timings['True']['registrationCalls'], 1)
        print('SYNTHETIC_S1_REQUEST_BENCHMARK ' + json.dumps(timings))


if __name__ == '__main__':
    unittest.main()
