"""Synthetic S1 author files through real import/readers, not game or GUI timing."""
from collections import Counter
from concurrent.futures import CancelledError
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tests')]
import generic_author_base as base
import generic_author_package as generic
import s1_author_displays as displays
import s1_native_resources as native
import star_map_import as importer
from map_resource_context import BrowseContext
from test_generic_author_package import encoded, record
from test_s1_builtin_resources import fixture as native_fixture


def make_package(root, *, mode='nightmare', image_size=64):
    """60 nightmare / 56 difficult layers, real compressed PNG and typed NPZ."""
    manifest_path, manifest = native_fixture(root / '_internal', mode)
    (root / '_internal/version.json').write_bytes(encoded(dict(displayVersion='1.1.0', numericVersion='1.1.0.1')))
    mode_root = manifest_path.parent
    interfaces = manifest['runtimeInterfaces']
    source = json.loads((mode_root / interfaces['identityPack']).read_text())
    entries, versions, coordinates, layers = [], [], {}, []
    profile = 'author-zhan10-fixture'
    payload = {}
    buf = io.BytesIO()
    Image.new('RGBA', (image_size, image_size), (80, 150, 200, 170)).save(buf, format='PNG')
    image = buf.getvalue()
    prefix = f'_internal/seasons/s1/{mode}'
    dependencies = {f'{prefix}/native-map-manifest.json'}
    for row in source['maps']:
        mid = row['id']
        row['structureContentSha256'] = 'a' * 64
        coordinates[mid] = {'floors': {}}
        for floor, reference in row['layers'].items():
            version = dict(baseSnapshot='fixture', sourceVersion=native.SOURCE_VERSION,
                structureVersionKey=f'{mid}@' + 'a' * 64 + '/' + floor)
            binding = dict(profile='shared-structure', mapId=mid, floor=floor, variant='variant-01')
            canvas = dict(coordinateSpace=f'native:{mid}:{floor}', shapeHW=[image_size, image_size])
            np.savez_compressed(mode_root / reference, metadata=json.dumps(dict(version, binding=binding,
                canvas=canvas, sourceKind='game-rendered-shared-structure')),
                shared_mask=np.ones((image_size, image_size), np.uint8))
            entries.append(dict(binding, **version, resourcePath=reference))
            versions.append(dict(mapId=mid, sourceVersion=native.SOURCE_VERSION,
                sharedLookup=dict(binding, resourcePath=reference), typedReference=reference,
                resourceKey=dict(layoutId=mid, structureRawRecordSha256='a' * 64, floor=floor)))
            coordinates[mid]['floors'][floor] = dict(version, coordinateSpace=canvas['coordinateSpace'],
                coordinateHeight=image_size, coordinateWidth=image_size, referenceResource=reference)
            native_path = f'assets/game-native/{mid}/{floor}.png'
            (mode_root / native_path).write_bytes(image)
            display = generic.image_path(profile, 's1', mode, mid, floor)
            payload[display] = image
            layers.append(dict(version, season='s1', mode=mode, mapId=mid, floor=floor,
                displayPath=display, displaySha256=hashlib.sha256(image).hexdigest(), displayBytes=len(image),
                displayShapeHW=[image_size, image_size], referenceResource=f'{prefix}/{reference}',
                referenceSha256=hashlib.sha256((mode_root / reference).read_bytes()).hexdigest(),
                referenceShapeHW=canvas['shapeHW'], coordinateSpace=canvas['coordinateSpace'],
                nativeImageResource=f'{prefix}/{native_path}', nativeImageSha256=hashlib.sha256(image).hexdigest(),
                matrixDisplayToReference=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]))
            dependencies.update((f'{prefix}/{reference}', f'{prefix}/{native_path}'))
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
        (mode_root / relative).write_bytes(encoded(doc))
        dependencies.add(f'{prefix}/{relative}')
    for row in manifest['files']:
        data = (mode_root / row['path']).read_bytes()
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest_path.write_bytes(encoded(manifest))
    document = dict(schema=generic.BINDINGS_SCHEMA, profileId=profile, season='s1',
        authorName='synthetic 展十', displayName='synthetic S1 展十', identityAuthority=False,
        structureValidationAuthority=False, crossFloorProjectionAllowed=False,
        currentFrameGeometryFitRequired=True, layers=layers)
    payload[generic.bindings_path(profile)] = encoded(document)
    metadata = dict(schema=generic.SCHEMA, schemaVersion=1, revision=1, season='s1', packageId=profile,
        clientCompatibility={'authorDisplayProtocol': 2}, files=[record(p, b) for p, b in payload.items()],
        requiresFiles=[record(p, (root / p).read_bytes()) for p in sorted(dependencies)])
    archive = root / 'synthetic-author.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
        stream.writestr('map-package.json', encoded(metadata))
        for path, data in payload.items():
            stream.writestr('data/' + path, data)
    return archive, profile, mode_root, interfaces, len(layers)


@contextmanager
def count_reads():
    counts = Counter()
    original = Path.open
    def open_path(path, mode='r', *args, **kwargs):
        if 'r' in mode:
            counts[str(path)] += 1
        return original(path, mode, *args, **kwargs)
    with patch.object(Path, 'open', open_path):
        yield counts


class S1AuthorLoadingWorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        cls.archive, cls.profile, cls.mode_root, cls.interfaces, cls.layer_count = make_package(cls.root)
        importer.install_author_package_live(cls.archive, cls.root, application_idle=True, season='s1')

    def context(self):
        return BrowseContext('nightmare', self.root / '_internal', None, None, None, (), {}, season='s1')

    def test_multilayer_package_reads_shared_sources_a_constant_number_of_times(self):
        with count_reads() as reads:
            package = displays.InstalledAuthorPackage(self.root / '_internal', self.profile)
        self.assertEqual(len(package.document['layers']), 60)
        for name in ('identityPack', 'typedIndex', 'versionBindings', 'coordinatePack'):
            path = str(self.mode_root / self.interfaces[name])
            # One base read, one independent final byte verification and the
            # package's two explicit receipt hash checks. Never one per layer.
            self.assertLessEqual(reads[path], 4, (name, reads[path]))

    def test_gallery_uses_the_same_bounded_reader(self):
        with count_reads() as reads:
            context = displays.append_browse_profile(self.context())
        self.assertEqual(context.profiles[0].available_layers, self.layer_count)
        self.assertLessEqual(reads[str(self.mode_root / self.interfaces['coordinatePack'])], 4)

    def test_later_source_corruption_and_same_timestamp_change_fail_closed(self):
        path = self.mode_root / self.interfaces['coordinatePack']
        original, info = path.read_bytes(), path.stat()
        try:
            with self.assertRaisesRegex(ValueError, 'changed during browse'):
                with base.browse_validation_request():
                    base.resolve_layer(self.root, 's1', 'nightmare', sorted(native.MODE_LAYOUTS['nightmare'])[0], '-1')
                    path.write_bytes(original.replace(b'"coordinateWidth":64', b'"coordinateWidth":65'))
                    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
                    base.resolve_layer(self.root, 's1', 'nightmare', sorted(native.MODE_LAYOUTS['nightmare'])[0], '-2')
            with self.assertRaises(ValueError):
                displays.InstalledAuthorPackage(self.root / '_internal', self.profile)
        finally:
            path.write_bytes(original)

    def test_difficult_pack_uses_the_same_bounded_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            archive, profile, mode_root, interfaces, count = make_package(root, mode='difficult')
            importer.install_author_package_live(archive, root, application_idle=True, season='s1')
            with count_reads() as reads:
                package = displays.InstalledAuthorPackage(root / '_internal', profile)
            self.assertEqual(len(package.document['layers']), count)
            self.assertEqual(count, 56)
            for name in ('identityPack', 'typedIndex', 'versionBindings', 'coordinatePack'):
                self.assertLessEqual(reads[str(mode_root / interfaces[name])], 4)

    def test_final_verification_reads_bytes_even_when_identity_is_unchanged(self):
        path = self.mode_root / self.interfaces['coordinatePack']
        original, info = path.read_bytes(), path.stat()
        identity = base._BrowseValidation.identity
        # Simulate a filesystem whose change-time cannot distinguish the edit;
        # the same-length bytes and mtime must still fail the independent hash.
        def coarse_identity(value):
            return (*identity(value)[:-1], 0)
        try:
            with patch.object(base._BrowseValidation, 'identity', staticmethod(coarse_identity)):
                with self.assertRaisesRegex(ValueError, 'changed during browse'):
                    with base.browse_validation_request():
                        base.resolve_layer(self.root, 's1', 'nightmare', sorted(native.MODE_LAYOUTS['nightmare'])[0], '-1')
                        path.write_bytes(original.replace(b'"coordinateWidth":64', b'"coordinateWidth":65'))
                        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        finally:
            path.write_bytes(original)

    def test_cached_declaration_link_replacement_is_rejected(self):
        path = self.mode_root / self.interfaces['coordinatePack']
        original = path.read_bytes()
        alternate = path.with_suffix('.copy')
        alternate.write_bytes(original)
        try:
            with self.assertRaises(ValueError):
                with base.browse_validation_request():
                    base.resolve_layer(self.root, 's1', 'nightmare', sorted(native.MODE_LAYOUTS['nightmare'])[0], '-1')
                    path.unlink()
                    path.symlink_to(alternate)
                    # This layer shares the cached declarations, but publication
                    # still checks the original exact source path, not the alias.
                    base.resolve_layer(self.root, 's1', 'nightmare', sorted(native.MODE_LAYOUTS['nightmare'])[0], '-2')
        finally:
            if path.is_symlink():
                path.unlink()
            path.write_bytes(original)
            alternate.unlink()

    def test_declaration_cache_does_not_authorize_lexical_path_aliases(self):
        relative = self.interfaces['coordinatePack']
        with base.browse_validation_request():
            base._json(self.mode_root, relative)
            with self.assertRaises(ValueError):
                base._json(self.mode_root, 'references/../' + relative)

    def test_wrong_inventory_hash_is_not_hidden_by_shared_source_evidence(self):
        path = self.mode_root / 'native-map-manifest.json'
        original = path.read_bytes()
        try:
            document = json.loads(original)
            next(row for row in document['files'] if row['path'] == self.interfaces['typedIndex'])['sha256'] = '0' * 64
            path.write_bytes(encoded(document))
            with self.assertRaises(ValueError):
                displays.InstalledAuthorPackage(self.root / '_internal', self.profile)
        finally:
            path.write_bytes(original)

    def test_cancellation_during_shared_source_hits_propagates_and_next_read_recovers(self):
        checks = [0]
        def cancelled():
            checks[0] += 1
            return checks[0] > 200
        with self.assertRaises(CancelledError):
            displays.append_browse_profile(self.context(), cancelled=cancelled)
        self.assertIsNone(base._BROWSE_REQUEST.get())
        self.assertEqual(len(displays.InstalledAuthorPackage(self.root / '_internal', self.profile).document['layers']), 60)


if __name__ == '__main__':
    unittest.main()
