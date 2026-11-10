"""S0 difficult mixed-pool resources through the real producer and decoder."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

import export_native_base_package as exporter
from imported_native_base import decode_native_base
from nightmare_author_coordinate_adapter import CANONICAL_SCHEMA
import star_map_import as files
import test_imported_legacy_references as legacy_fixture
import test_imported_native_base as base_fixture


FLOORS = ('1', '2')
IDS = tuple(sorted([f'difficult-map-{i:02}' for i in range(1, 30)] + ['difficult-map-09-2']))
LEGACY = 'difficult-map-29'
PROFILE = 'shared-structure'
PACK = 'references/difficult-native-identity-pack.json'
SOURCE = 'references/difficult-native-source-manifest.json'
INDEX = 'references/difficult-shared-structure-index.json'
VERSIONS = 'references/difficult-native-version-bindings.json'
NEUTRAL = 'references/difficult-native-structure-display.json'
COORDINATES = 'references/difficult-native-coordinate-pack.json'
ENTRIES = 'recognition/native'
PASSPORTS = legacy_fixture.DIRECTORY + '/entry-passports.json'


def decode_s0_difficult(view):
    return decode_native_base(view, season='s0', mode='difficult')


class S0DifficultSourceFixture:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.sourceRoot = self.root / 'source'
        self.inventory = self.root / 'inventory.json'
        self.output = self.root / 's0-difficult.zip'
        self.payloads = {}
        old = legacy_fixture.LegacyReferenceFixture(self.root / 'legacy')
        self.payloads.update(old.files)
        self.legacy = deepcopy(old.declaration)
        self.legacy['passports'] = PASSPORTS
        typed, versions, neutral, native, identity, main, local = [], [], [], [], [], [], []
        maps, shared, zhan = {}, {}, {}
        profiles = {mid: 'zhan10-speed' if mid == LEGACY else PROFILE for mid in IDS}
        for mid in IDS:
            is_legacy = mid == LEGACY
            raw_hash = hashlib.sha256(mid.encode()).hexdigest()
            native_row = dict(id=mid, sourceLayout='fixture-layout-' + mid,
                sourceVersion='fixture-game-v1', structureContentSha256=raw_hash)
            layers, coordinate_floors, profile_floors = {}, {}, {}
            for floor in FLOORS:
                if is_legacy:
                    canvas = deepcopy(self.legacy['referenceCanvas'][floor])
                    resource = canvas['resourcePath']
                    common = {k: canvas[k] for k in ('sourceVersion', 'structureVersionKey', 'baseSnapshot')}
                    height, width = canvas['shapeHW']
                    matrix = [[1, 0, -2], [0, 1, -1], [0, 0, 1]]
                    coordinate_floors[floor] = dict(referenceResource=resource, coordinateWidth=width,
                        coordinateHeight=height, coordinateSpace=canvas['coordinateSpace'],
                        referenceLayerRole='legacy-clean-binary-only-not-native',
                        matrixCanonicalToLegacyRecognition=[[1, 0, 2], [0, 1, 1], [0, 0, 1]])
                    profile_floors[floor] = dict(matrixProfileToCanonical=matrix,
                        usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
                        evidence=dict(algebraicallyExact=True, authorizesMapIdentity=False, authorizesStructureValidation=False,
                            sourceLayout=None, sourceCommit=canvas['sourceCommit'], staticAcceptance=canvas['staticAcceptance']),
                        **common)
                else:
                    resource = f'references/difficult-native/{mid}-{floor}.npz'
                    canvas = dict(width=8, height=8, shapeHW=[8, 8], floor=floor,
                        coordinateSpace=f'fixture-native:{mid}:{floor}')
                    common = dict(sourceVersion='fixture-game-v1',
                        structureVersionKey=f"{native_row['sourceLayout']}@{raw_hash}/{floor}", baseSnapshot='fixture-base')
                    binding = dict(profile=PROFILE, mapId=mid, floor=floor, variant='variant-01')
                    metadata = dict(sourceKind='game-rendered-shared-structure', canvas=canvas,
                        binding=binding, roles=dict(clean='shared_mask', validation='shared_mask'), **common)
                    buffer = BytesIO()
                    np.savez_compressed(buffer, metadata=np.asarray(json.dumps(metadata)), shared_mask=np.ones((8, 8), np.uint8))
                    self.payloads[resource] = buffer.getvalue()
                    typed.append(dict(binding, resourcePath=resource, **common))
                    key = dict(layoutId=native_row['sourceLayout'], structureRawRecordSha256=raw_hash, floor=floor)
                    versions.append(dict(mapId=mid, floor=floor, resourceKey=key, typedReference=resource,
                        sharedLookup=dict(binding, resourcePath=resource), neutralDisplay=dict(baseResourceKey=key), **common))
                    neutral.append(dict(mapId=mid, floor=floor, binding=binding, canvas=canvas, referenceResource=resource, **common))
                    coordinate_floors[floor] = dict(referenceResource=resource, coordinateWidth=8, coordinateHeight=8, **common)
                    profile_floors[floor] = dict(matrixProfileToCanonical=np.eye(3).tolist(),
                        usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
                        evidence=dict(algebraicallyExact=True), **common)
                layers[floor] = resource
                geometry = dict(mapId=mid, floor=floor, profileId=profiles[mid], variantId='variant-01',
                    canvas=canvas, sourceProduct=resource, structureResource=resource, unit=1., **common)
                if is_legacy:
                    geometry.update(sourceKind='legacy-manually-reconstructed-clean-structure',
                        sourceLayout=None, sourceClass='legacy-clean', staticAcceptance='working-draft-not-batch-accepted',
                        rankStatus='NOT_TESTED', runtimeAuthorPixelsUsed=False)
                    gates = []
                else:
                    geometry.update(sourceKind='game-rendered-shared-structure', knownWallSupportUnavailable=True, authorFeaturesUsed=False)
                    gates = [dict(center=[2., 2.], sourceKind='game-script-static-side-door-spawn-marker',
                        semanticRole='nearby exit marker only; not doorway endpoints or state',
                        doorWidthKnown=False, sourceStateKnown=False, outwardDirectionKnown=False)]
                local.append(dict(geometry, region='fixture-region', gates=gates,
                    featureRole='texture-footprint local-contour proposals; not known walls',
                    turns=[dict(point=p, rays=[[1., 0.], [0., 1.]], next=(i+1) % 3)
                        for i, p in enumerate(([1., 1.], [3., 1.], [3., 3.]))]))
                if floor == '1':
                    item = dict(geometry, material='fixture-room', center=[2., 2.],
                        outline=[[1., 1.], [3., 1.], [3., 3.]],
                        chains=[dict(points=[[1., 1.], [2., 2.], [3., 3.]], rays=[[1., 0.], [0., 1.]])])
                    if not is_legacy:
                        item.update(sourceKind='game-entry-room-proposal', kind='main',
                            proposalOnly=True, authorizesPose=False, authorizesIdentity=False)
                    main.append(item)
            maps[mid], shared[mid], zhan[mid] = dict(floors=coordinate_floors), dict(floors=profile_floors), dict(floors=deepcopy(profile_floors))
            row = dict(id=mid, name=mid, direction='unknown', shape='fixture', previewBox=[0, 0, 8, 8],
                identityProfileId=profiles[mid], routeProfileId='zhan10-speed', layers=layers, oneFlow='assets/author.png')
            if is_legacy:
                row.update(sourceClass='legacy-clean', sourceLayout=None, rankStatus='NOT_TESTED')
            else:
                native.append(native_row)
            identity.append(row)
        self.write(PACK, dict(schemaVersion=1, mode='difficult', packId='fixture-s0-difficult',
            floors=list(FLOORS), defaultEntryFloor='1', maps=identity, identityProfiles=profiles))
        self.write(SOURCE, dict(schemaVersion=1, mode='difficult', floors=list(FLOORS), maps=native,
            identityProfiles={mid: PROFILE for mid in IDS if mid != LEGACY},
            pendingOriginalBindings=[dict(mapId=LEGACY, floor=f, reason='missing reliable game-layout binding') for f in FLOORS]))
        self.write(INDEX, dict(schemaVersion=1, resourceKind='shared-structure', entries=typed, structureVersionBindings=VERSIONS))
        self.write(VERSIONS, dict(schemaVersion=1, resourceKind='native-structure-version-bindings', entries=versions))
        self.write(NEUTRAL, dict(schemaVersion=1, resourceKind='typed-shared-structure-display', indexPath=INDEX, entries=neutral))
        self.write(COORDINATES, dict(schema=CANONICAL_SCHEMA, coordinateReferenceProfileId=PROFILE,
            policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                        authorizesCrossFloorProjection=False),
            referenceProvider=dict(indexPath=INDEX, resourceRoot='.', lookupBindings=[
                {k:r[k] for k in ('profile','mapId','floor','variant')} for r in typed]),
            maps=maps, profiles={PROFILE: dict(maps=shared), 'zhan10-speed': dict(maps=zhan)}, canonicalCrossFloorSearchCandidates={}))
        for name, entries in (('entries.json', main), ('side-entries.json', local)):
            self.write(ENTRIES + '/' + name, dict(schemaVersion=1, mode='difficult', profileId=PROFILE,
                entries=entries, semanticGaps=['fixture-source-authority-unavailable']))
        self.write(PASSPORTS, dict(schemaVersion=1, mode='difficult', profileId='zhan10-speed',
            entries=[dict(mapId=LEGACY, floor='1', sourceEntryId=entry_id, center=[2., 2.],
                sourceCommit='fixture-legacy-commit', structureResource='fixed/map-29-1.npz',
                knownWallLines=[], localInstances=[]) for entry_id in ('main-glyph', 'side-glyph')],
            coverage=[dict(mapId=LEGACY, floor=f, complete=f == '1') for f in FLOORS]))
        self.payloads['assets/author.png'] = b'author-display-not-portable-base'
        self.refresh_manifest()

    def write(self, path, value):
        self.payloads[path] = exporter._encoded(value)

    def refresh_manifest(self):
        interfaces = dict(identityPack=PACK, sourceManifest=SOURCE, typedIndex=INDEX, versionBindings=VERSIONS,
            neutralDisplay=NEUTRAL, coordinatePack=COORDINATES, identityEntries=ENTRIES, legacyClean=self.legacy)
        self.write('difficult-native-manifest.json', dict(runtimeInterfaces=interfaces, files=[
            dict(path=p, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            for p, raw in sorted(self.payloads.items()) if p != 'difficult-native-manifest.json']))
        for relative, raw in self.payloads.items():
            path = self.sourceRoot / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.inventory.write_bytes(exporter._encoded(dict(files={p:dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            for p, raw in self.payloads.items()})))

    def export(self):
        return exporter.export_package(self.sourceRoot, self.output, package_id='fixture-s0-difficult',
            resource_version='v1', source_inventory=self.inventory, season='s0', mode='difficult')


class S0DifficultNativeBaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s0d-producer-fixture-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = S0DifficultSourceFixture(Path(temporary.name))
        cls.receipt = cls.source.export()
        cls.archive = cls.source.output
        with zipfile.ZipFile(cls.archive) as archive:
            cls.members = {p:archive.read(p) for p in archive.namelist()}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s0d-decoder-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root, self.counter = Path(temporary.name), 0

    view = base_fixture.ImportedNativeBaseTests.view
    change = staticmethod(base_fixture.ImportedNativeBaseTests.change)

    def test_mixed_complete_pool_and_source_bytes_remain_distinct(self):
        bundle = decode_s0_difficult(self.view())
        self.assertEqual(set(bundle.pack.by_id), set(IDS))
        self.assertEqual(len(bundle.resources.entries), 58)
        self.assertEqual(len(bundle.legacy_references.bindings), 2)
        self.assertEqual(len(bundle.entry_identity.entries), 30)
        self.assertEqual(len(bundle.side_entry_identity.entries), 60)
        self.assertFalse(bundle.rank_ready)
        self.assertFalse(bundle.semantic_side_entries)
        self.assertFalse(bundle.passport_source_available)
        self.assertEqual(bundle.side_entry_identity.passport_error, 'entry-passports-source-unavailable')
        self.assertEqual([r['floor'] for r in bundle.legacy_passports['entries']], ['1', '1'])
        self.assertIs(bundle.legacy_passports['coverage'][1]['complete'], False)
        self.assertEqual(set(bundle.adapters.profiles['zhan10-speed']['maps']), {LEGACY})
        self.assertEqual(bundle.adapters.matrix_profile_to_canonical(PROFILE, LEGACY, '1')[0,2], -2)
        for mid, floor in [(r['id'], f) for r in bundle.native_source.by_id.values() for f in FLOORS]:
            path = f'references/difficult-native/{mid}-{floor}.npz'
            self.assertEqual(self.members['data/base/' + path], self.source.payloads[path])
        for path in self.source.legacy['referenceCanvas'].values():
            resource = path['resourcePath']
            self.assertEqual(self.members['data/base/' + resource], self.source.payloads[resource])
        for path in (SOURCE, INDEX, NEUTRAL, PASSPORTS, ENTRIES+'/entries.json', ENTRIES+'/side-entries.json'):
            self.assertEqual(self.members['data/base/' + path], self.source.payloads[path])
        self.assertNotIn('data/base/assets/author.png', self.members)
        self.assertNotIn('data/base/'+ENTRIES+'/entry-passports.json', self.members)
        self.assertEqual(self.receipt['controlsTemplates'], 720)
        self.assertEqual(len(self.receipt['controlsLabels']), 12)

    def test_missing_candidate_or_legacy_cannot_shrink_competition(self):
        for path, edit in ((INDEX, lambda d:d['entries'].pop()),
                           ('resource-context.json', lambda d:d['runtimeInterfaces'].update(legacyClean=None))):
            with self.subTest(path=path), self.assertRaises((files.ImportRejected, ValueError)):
                decode_s0_difficult(self.view({path:self.change(edit)}))

    def test_native_proposals_and_static_markers_cannot_gain_authority(self):
        cases = [(ENTRIES+'/entries.json', lambda d:d['entries'][0].update(authorizesIdentity=True)),
                 (ENTRIES+'/side-entries.json', lambda d:d['entries'][0]['gates'][0].update(doorWidthKnown=True)),
                 (ENTRIES+'/side-entries.json', lambda d:d['entries'][0].update(sourceProduct='basename.npz'))]
        for path, edit in cases:
            with self.subTest(path=path), self.assertRaises(files.ImportRejected):
                decode_s0_difficult(self.view({path:self.change(edit)}))

    def test_legacy_draft_and_coordinate_evidence_cannot_be_promoted(self):
        for edit in (lambda d:d['runtimeInterfaces']['legacyClean']['referenceCanvas']['1'].update(staticAcceptance='accepted'),
                     lambda d:d['runtimeInterfaces']['legacyClean']['referenceCanvas']['1'].update(sourceLayout='103001')):
            with self.subTest(edit=edit), self.assertRaises(files.ImportRejected):
                decode_s0_difficult(self.view({'resource-context.json':self.change(edit)}))
        def authorize(d):
            d['profiles'][PROFILE]['maps'][LEGACY]['floors']['1']['evidence']['authorizesMapIdentity'] = True
        with self.assertRaises(files.ImportRejected):
            decode_s0_difficult(self.view({COORDINATES:self.change(authorize)}))

    def test_passports_allow_multiple_glyphs_but_reject_unbound_floor_and_duplicate_entry(self):
        for edit in (lambda d:d['entries'][0].update(floor='3'),
                     lambda d:d['entries'].append(deepcopy(d['entries'][0]))):
            with self.subTest(edit=edit), self.assertRaises(files.ImportRejected):
                decode_s0_difficult(self.view({PASSPORTS:self.change(edit)}))

    def test_data_only_bundle_cannot_enter_default_engine_builder(self):
        from client_imported_providers import build_native_engine, ImportedProviderUnavailable
        from client_paths import RuntimePaths
        from imported_resource_packages import PackageView
        view = self.view()
        controls = PackageView(view.resource_root, replace(view.component_ref, component_id='controls'))
        paths = RuntimePaths(self.root/'program', False)
        with self.assertRaises(ImportedProviderUnavailable) as caught:
            build_native_engine(view, controls, paths, season='s0', mode='difficult')
        self.assertEqual(str(caught.exception), 'native-base-rank-unavailable')
        self.assertEqual(caught.exception.resource_gaps, ('native-base-rank-unavailable',))


if __name__ == '__main__':
    unittest.main()
