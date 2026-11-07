"""Synthetic contracts, not game/GUI validation."""
import json
import sys
from pathlib import Path

import numpy as np
import unittest
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import generic_author_base as base


def fixture(root, mode='nightmare'):
    internal = root / '_internal'
    def write(path, doc):
        target = internal / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc))
    ref = 'references/entries/map-B1.npz'
    version = dict(structureVersionKey='layout@abc/B1', sourceVersion='v1', baseSnapshot='snapshot')
    profile = 'zhan10-speed' if mode == 'nightmare' else 'shared-structure'
    binding = dict(profile=profile, mapId='map', floor='B1', variant='variant-01')
    canvas = dict(coordinateSpace='native:layout:B1', shapeHW=[8, 9])
    target = internal / ref
    target.parent.mkdir(parents=True)
    np.savez(target, metadata=json.dumps(dict(version, binding=binding, canvas=canvas,
        sourceKind='game-rendered-shared-structure')), shared_mask=np.ones((8, 9), np.uint8))
    source = dict(mode=mode, maps=[dict(id='map', identityProfileId=profile, layers={'B1':ref}, sourceLayout='layout',
        structureContentSha256='abc', sourceVersion='v1')])
    if mode == 'difficult':
        source['floors'] = ['B1']
        source['maps'][0]['floors'] = {'B1': dict(version, referenceResource=ref)}
        del source['maps'][0]['layers']
    write('references/nightmare-map-pack.json', source)
    write('references/nightmare-shared-structure-index.json', dict(schemaVersion=1, resourceKind='shared-structure',
        structureVersionBindings='references/versions.json', entries=[dict(binding, **version, resourcePath=ref)]))
    write('references/versions.json', dict(schemaVersion=1, resourceKind='native-structure-version-bindings', entries=[dict(
        mapId='map', sourceVersion='v1', sharedLookup=dict(binding, resourcePath=ref), typedReference=ref,
        resourceKey=dict(layoutId='layout', structureRawRecordSha256='abc', floor='B1'))]))
    # Existing S0 coordinate rows need not contain baseSnapshot or index geometry.
    write('references/nightmare-canonical-coordinate-pack.json', dict(
        schema='identityv-nightmare-canonical-coordinate-pack-v1', coordinateReferenceProfileId='shared-structure',
        policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False, authorizesCrossFloorProjection=False),
        maps={'map':{'floors':{'B1':dict(coordinateSpace=canvas['coordinateSpace'], coordinateHeight=8,
            coordinateWidth=9, referenceResource=ref, structureVersionKey=version['structureVersionKey'], sourceVersion='v1')}}}))
    if mode == 'difficult':
        write('difficult-native-manifest.json', dict(runtimeInterfaces=dict(
            sourceManifest='references/nightmare-map-pack.json', typedIndex='references/nightmare-shared-structure-index.json',
            versionBindings='references/versions.json', coordinatePack='references/nightmare-canonical-coordinate-pack.json')))
    return internal


def test_resolve_s0_native_contract_without_index_shape(tmp_path, mode):
    fixture(tmp_path, mode)
    result = base.resolve_layer(tmp_path, 's0', mode, 'map', 'B1')
    assert result['referenceShapeHW'] == [8, 9]
    assert result['baseSnapshot'] == 'snapshot'
    assert result['referenceResource'] == '_internal/references/entries/map-B1.npz'
    assert 'nativeImageResource' not in result
    assert len(result['dependencies']) == (6 if mode == 'difficult' else 5)
    assert all((tmp_path / p).is_file() for p in result['dependencies'])


def test_bad_base_fails_closed(tmp_path, change):
    internal = fixture(tmp_path)
    path = internal / 'references/nightmare-shared-structure-index.json'
    doc = json.loads(path.read_text())
    row = doc['entries'][0]
    if change == 'floor':
        with unittest.TestCase().assertRaises(ValueError): base.resolve_layer(tmp_path, 's0', 'nightmare', 'map', '2')
        return
    if change == 'version': row['baseSnapshot'] = 'other'
    if change == 'path': row['resourcePath'] = '../../escape.npz'
    if change == 'duplicate': doc['entries'].append(dict(row))
    if change == 'missing': (internal / 'references/versions.json').unlink()
    if change == 'shape':
        coord = internal / 'references/nightmare-canonical-coordinate-pack.json'
        value = json.loads(coord.read_text()); value['maps']['map']['floors']['B1']['coordinateWidth'] = 10
        coord.write_text(json.dumps(value))
    path.write_text(json.dumps(doc))
    with unittest.TestCase().assertRaises(ValueError): base.resolve_layer(tmp_path, 's0', 'nightmare', 'map', 'B1')


def test_unknown_season_cannot_register_itself(tmp_path):
    fixture(tmp_path)
    with unittest.TestCase().assertRaises(ValueError):
        base.resolve_layer(tmp_path, 's2', 'nightmare', 'map', 'B1')


def test_explicit_trusted_provider_seam(tmp_path):
    saved = dict(base._PROVIDERS)
    try:
        base.register_provider('s9', 'nightmare', lambda root, *identity: {'trustedProvider': identity})
        assert base.resolve_layer(tmp_path, 's9', 'nightmare', 'map', 'B1')['trustedProvider'][0] == 's9'
        with unittest.TestCase().assertRaises(ValueError): base.register_provider('s9', 'nightmare', lambda *args: {})
    finally:
        base._PROVIDERS.clear(); base._PROVIDERS.update(saved)


def test_symlink_escape_is_rejected(tmp_path):
    internal = fixture(tmp_path)
    target = internal / 'references/versions.json'
    outside = tmp_path / 'outside.json'
    outside.write_bytes(target.read_bytes()); target.unlink(); target.symlink_to(outside)
    with unittest.TestCase().assertRaises(ValueError): base.resolve_layer(tmp_path, 's0', 'nightmare', 'map', 'B1')

class BaseContractTests(unittest.TestCase):
    def test_modes(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                test_resolve_s0_native_contract_without_index_shape(Path(root), mode)

    def test_failures(self):
        for change in ('floor', 'version', 'shape', 'path', 'duplicate', 'missing'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                test_bad_base_fails_closed(Path(root), change)

    def test_other_contracts(self):
        for test in (test_unknown_season_cannot_register_itself, test_explicit_trusted_provider_seam,
                     test_symlink_escape_is_rejected):
            with self.subTest(test=test.__name__), tempfile.TemporaryDirectory() as root:
                test(Path(root))


if __name__ == '__main__':
    unittest.main()
