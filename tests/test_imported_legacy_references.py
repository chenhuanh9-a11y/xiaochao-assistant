"""Legacy reference metadata stays a working draft under actual PackageViews."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from imported_legacy_references import ImportedLegacyReferences
from imported_resource_packages import ComponentRef, PackageView
from imported_resource_reader import ComponentResourceReader
import test_imported_legacy_structure_reader as legacy_io


MID = 'difficult-map-29'
PROFILE = 'zhan10-speed'
DIRECTORY = 'recognition/legacy-clean-map29'
INDEX = DIRECTORY + '/index.json'


class LegacyReferenceFixture:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.package = self.root / 'packages' / 'legacy-reference-fixture' / 'v1'
        self.component = self.package / 'data' / 'base'
        self.files, entries, canvases = {}, [], {}
        for floor, shape in (('1', (8, 12)), ('2', (6, 10))):
            relative = f'fixed/map-29-{floor}.npz'
            path = DIRECTORY + '/' + relative
            raw = legacy_io._legacy_npz(shape=shape)
            digest = hashlib.sha256(raw).hexdigest()
            self.files[path] = raw
            entries.append(dict(profileId=PROFILE, mapId=MID, floor=floor, variantId='variant-01',
                resourcePath=relative, sourceShape=list(shape), sourceCommit='fixture-legacy-commit'))
            canvases[floor] = dict(shapeHW=list(shape), sourceLayout=None,
                sourceClass='legacy-clean-working-draft', staticAcceptance='working-draft-not-batch-accepted',
                coordinateSpace=f'fixture-legacy-cropped:{floor}', sourceCommit='fixture-legacy-commit',
                resourcePath=path, resourceSHA256=digest, structureVersionKey=f'fixture-legacy-version:{floor}',
                sourceVersion='fixture-legacy-v1', baseSnapshot='fixture-legacy-base')
        self.files[INDEX] = json.dumps(dict(schemaVersion=2,
            validationMaskContract='author_route_structure_mask', entries=entries)).encode('utf-8')
        self.declaration = dict(index=INDEX, resourceRoot=DIRECTORY, mapIds=[MID],
            profileId=PROFILE, referenceCanvas=canvases)
        for path, raw in self.files.items():
            target = self.component / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='legacy-reference-fixture', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='base', type='recognition-base', schemaVersion=1,
                contexts=[dict(season='s0', mode='difficult')], entrypoint=INDEX, dependencies={})],
            files=[dict(componentId='base', path=path, bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest()) for path, raw in self.files.items()])
        self.manifest_raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.manifest_path = self.package / 'manifest.json'
        self.manifest_path.write_bytes(self.manifest_raw)
        ref = ComponentRef('legacy-reference-fixture', 'v1', 'base',
                           hashlib.sha256(self.manifest_raw).hexdigest())
        self.view = PackageView(self.root, ref)

    def references(self, declaration=None, **kwargs):
        return ImportedLegacyReferences(self.view, self.declaration if declaration is None else declaration,
            expected_maps={MID}, floors=('1', '2'), **kwargs)


class ImportedLegacyReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='legacy-reference-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = LegacyReferenceFixture(self.root / 'first')

    def test_actual_reference_uses_legacy_masks_and_never_grants_native_authority(self):
        refs = self.fixture.references()
        self.assertEqual(set(refs.bindings), {(PROFILE, MID, 'variant-01', floor) for floor in ('1', '2')})
        for floor, shape in (('1', (8, 12)), ('2', (6, 10))):
            with self.subTest(floor=floor):
                reference = refs.reference(MID, floor)
                self.assertEqual(reference.clean_mask.shape, shape)
                self.assertEqual(reference.metadata['sourceKind'], 'legacy-shared-binary')
                self.assertEqual(reference.metadata['sourceClass'], 'legacy-clean-working-draft')
                self.assertIsNone(reference.metadata['sourceLayout'])
                self.assertEqual(reference.metadata['staticAcceptance'], 'working-draft-not-batch-accepted')
                self.assertFalse(reference.metadata['authorizesMapIdentity'])
                self.assertFalse(reference.metadata['authorizesStructureValidation'])
        self.assertIsNone(refs.reference('other-map', '1'))
        self.assertIsNone(refs.reference(MID, '-1'))

    def test_native_or_accepted_declarations_are_rejected_instead_of_normalized(self):
        for field, value in (('sourceLayout', '103029'), ('sourceClass', 'game-rendered-shared-structure'),
                             ('staticAcceptance', 'accepted')):
            declaration = deepcopy(self.fixture.declaration)
            declaration['referenceCanvas']['1'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'legacy-canvas-invalid'):
                self.fixture.references(declaration)

    def test_missing_competitor_file_or_declared_floor_does_not_shrink_inventory(self):
        declaration = deepcopy(self.fixture.declaration)
        declaration['referenceCanvas'].pop('2')
        with self.assertRaisesRegex(ValueError, 'legacy-floor-inventory-invalid'):
            self.fixture.references(declaration)
        (self.fixture.component / self.fixture.declaration['referenceCanvas']['2']['resourcePath']).unlink()
        with self.assertRaises((ValueError, OSError)):
            self.fixture.references()

    def test_declared_digest_and_reader_installation_bind_the_actual_files(self):
        declaration = deepcopy(self.fixture.declaration)
        declaration['referenceCanvas']['1']['resourceSHA256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'legacy-product-digest-mismatch'):
            self.fixture.references(declaration)
        other = LegacyReferenceFixture(self.root / 'second')
        self.assertEqual(other.view.component_ref, self.fixture.view.component_ref)
        with self.assertRaisesRegex(ValueError, 'legacy-reader-view-mismatch'):
            self.fixture.references(reader=ComponentResourceReader(other.view))

    def test_bindings_are_copies_and_manifest_replacement_rejects_metadata_and_cache(self):
        refs = self.fixture.references()
        bindings = refs.bindings
        bindings[PROFILE, MID, 'variant-01', '1']['shapeHW'][0] = 100
        self.assertEqual(refs.reference(MID, '1').clean_mask.shape, (8, 12))
        replacement = self.fixture.package / 'replacement.json'
        replacement.write_bytes(self.fixture.manifest_raw)
        replacement.replace(self.fixture.manifest_path)
        for access in (lambda: refs.bindings, lambda: refs.reference(MID, '1')):
            with self.subTest(access=access), self.assertRaisesRegex(ValueError, 'manifest-replaced'):
                access()
            self.assertFalse(refs._store._cache)


if __name__ == '__main__':
    unittest.main()
