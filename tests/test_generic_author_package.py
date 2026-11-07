"""Synthetic V2 packages through production transactions, not geometry/GUI QA."""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import generic_author_package as generic
import generic_author_base as base
import star_map_import as imp


def encoded(doc):
    return json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()


def record(path, data):
    return dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def make_base(root):
    internal = root / '_internal'
    def write(path, doc):
        target = internal / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded(doc))
    write('version.json', dict(displayVersion='1.1.0', numericVersion='1.1.0.1'))
    ref = 'references/entries/map-B1.npz'
    version = dict(structureVersionKey='layout@abc/B1', sourceVersion='v1', baseSnapshot='snapshot')
    binding = dict(profile='shared-structure', mapId='map', floor='B1', variant='variant-01')
    canvas = dict(coordinateSpace='native:layout:B1', shapeHW=[8, 9])
    target = internal / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez(target, metadata=json.dumps(dict(version, binding=binding, canvas=canvas,
        sourceKind='game-rendered-shared-structure')), shared_mask=np.ones((8, 9), np.uint8))
    write('references/nightmare-map-pack.json', dict(mode='nightmare', floors=['B1'], maps=[dict(
        id='map', layers={'B1': ref}, sourceLayout='layout', structureContentSha256='abc', sourceVersion='v1')]))
    write('references/nightmare-shared-structure-index.json', dict(schemaVersion=1, resourceKind='shared-structure',
        structureVersionBindings='references/versions.json', entries=[dict(binding, **version, resourcePath=ref)]))
    write('references/versions.json', dict(schemaVersion=1, resourceKind='native-structure-version-bindings', entries=[dict(
        mapId='map', sourceVersion='v1', sharedLookup=dict(binding, resourcePath=ref), typedReference=ref,
        resourceKey=dict(layoutId='layout', structureRawRecordSha256='abc', floor='B1'))]))
    write('references/nightmare-canonical-coordinate-pack.json', dict(
        schema='identityv-nightmare-canonical-coordinate-pack-v1', coordinateReferenceProfileId='shared-structure',
        policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False, authorizesCrossFloorProjection=False),
        maps={'map': {'floors': {'B1': dict(coordinateSpace=canvas['coordinateSpace'], coordinateHeight=8,
            coordinateWidth=9, referenceResource=ref, structureVersionKey=version['structureVersionKey'], sourceVersion='v1')}}}))


class GenericPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match production provider roots, including Windows temp 8.3 aliases.
        self.root = (Path(self.temp.name) / 'install').resolve()
        self.root.mkdir()
        make_base(self.root)
        self.profile = 'author-aichicu'
        self.sequence = 0
        self.contract = base.resolve_layer(self.root, 's0', 'nightmare', 'map', 'B1')
        self.deps = [record(p, (self.root/p).read_bytes()) for p in self.contract['dependencies']]

    def package(self, profile=None, mutate=None, mutate_meta=None, extra=None, color=(1, 2, 3, 90), fmt='png'):
        profile = profile or self.profile
        buf = io.BytesIO()
        Image.new('RGBA' if fmt == 'png' else 'RGB', (9, 8), color if fmt == 'png' else color[:3]).save(
            buf, format='PNG' if fmt == 'png' else 'JPEG')
        image = buf.getvalue()
        path = generic.image_path(profile, 's0', 'nightmare', 'map', 'B1', fmt)
        layer = {k: v for k, v in self.contract.items() if k != 'dependencies'}
        layer.update(season='s0', mode='nightmare', mapId='map', floor='B1', displayPath=path,
            displaySha256=hashlib.sha256(image).hexdigest(), displayBytes=len(image), displayShapeHW=[8, 9],
            referenceSha256=next(r['sha256'] for r in self.deps if r['path']==layer['referenceResource']),
            matrixDisplayToReference=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        doc = dict(schema=generic.BINDINGS_SCHEMA, profileId=profile, season='s0', authorName='任意新作者',
            displayName='新作者整图', identityAuthority=False, structureValidationAuthority=False,
            crossFloorProjectionAllowed=False, currentFrameGeometryFitRequired=True, layers=[layer])
        if mutate: mutate(doc)
        payload = {path: image, generic.bindings_path(profile): encoded(doc)}
        metadata = dict(schema=generic.SCHEMA, schemaVersion=1, revision=1, season='s0', packageId=profile,
            clientCompatibility={'authorDisplayProtocol': 2}, files=[record(p,b) for p,b in payload.items()],
            requiresFiles=self.deps)
        if mutate_meta: mutate_meta(metadata)
        self.sequence += 1
        archive = self.root.parent / f'package-{self.sequence}.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('map-package.json', encoded(metadata))
            for p, data in payload.items(): z.writestr('data/'+p, data)
            if extra: extra(z)
        return archive

    def inspect(self, archive):
        return imp.inspect_package(archive, self.root)

    def install(self, archive):
        return imp.install_author_package_live(archive, self.root, application_idle=True)

    def test_new_authors_no_code_registration_and_repeat(self):
        for profile in ('author-aichicu', 'author-zhan10', 'author-lianghapi', 'author-unknown-new'):
            archive = self.package(profile)
            result = self.inspect(archive)
            self.assertTrue(imp.supports_live_author_import(result))
            self.assertEqual(result['authorName'], '任意新作者')
            self.install(archive)
            receipt = self.root / f'work/map-packages/{profile}.json'
            first = receipt.read_bytes()
            self.assertTrue(self.install(archive)['receiptPreserved'])
            self.assertEqual(first, receipt.read_bytes())
        self.assertEqual(len(generic.profile_ids(self.root/'_internal')), 4)
        for dep in self.deps: self.assertEqual(imp._sha(self.root/dep['path']), dep['sha256'])

    def test_upgrade_and_author_isolation(self):
        self.install(self.package())
        self.install(self.package('author-other'))
        other = (self.root/'work/map-packages/author-other.json').read_bytes()
        changed = self.package(color=(3, 4, 5, 120))
        self.assertEqual(len(self.inspect(changed)['replaceFiles']), 2)
        self.install(changed)
        self.assertEqual(other, (self.root/'work/map-packages/author-other.json').read_bytes())

    def test_jpeg_supported(self):
        self.install(self.package(fmt='jpeg'))

    def test_updates_cannot_orphan_previous_payload(self):
        self.install(self.package())
        receipt = self.root/f'work/map-packages/{self.profile}.json'
        before = receipt.read_bytes()
        with self.assertRaisesRegex(imp.ImportRejected, 'shrinking-package-requires-remove'):
            self.install(self.package(fmt='jpeg'))
        self.assertEqual(before, receipt.read_bytes())

    def test_bad_bindings_fail_closed(self):
        mutations = [
            lambda d: d.update(identityAuthority=True),
            lambda d: d.update(crossFloorProjectionAllowed=True),
            lambda d: d.update(authorName=''),
            lambda d: d.update(authorName='<img src="image://fixture/author">'),
            lambda d: d.update(displayName='<img src="image://fixture/display">'),
            lambda d: d.update(displayName='<b>formatted</b>'),
            lambda d: d.update(profileId='author-other'),
            lambda d: d.update(season='s2'),
            lambda d: d['layers'].append(copy.deepcopy(d['layers'][0])),
            lambda d: d['layers'][0].update(floor='F9'),
            lambda d: d['layers'][0].update(referenceResource='_internal/references/other.npz'),
            lambda d: d['layers'][0].update(structureVersionKey='fake'),
            lambda d: d['layers'][0].update(referenceShapeHW=[9, 8]),
            lambda d: d['layers'][0].update(displayShapeHW=[9, 8]),
            lambda d: d['layers'][0].update(matrixDisplayToReference=[[1,0,0],[0,0,0],[0,0,1]]),
            lambda d: d['layers'][0].update(matrixDisplayToReference=[[float('nan'),0,0],[0,1,0],[0,0,1]]),
            lambda d: d['layers'][0].update(displayPath='_internal/assets/authors/author-other/s0/nightmare/map/B1.png'),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(imp.ImportRejected):
                self.inspect(self.package(mutate=mutate))
        self.assertFalse((self.root/'work/map-packages/author-aichicu.json').exists())

    def test_missing_extra_or_foreign_dependencies_rejected(self):
        for mutation in (
            lambda m: m.update(requiresFiles=self.deps[:-1]),
            lambda m: m.update(requiresFiles=self.deps+[record('_internal/version.json', (self.root/'_internal/version.json').read_bytes())]),
            lambda m: m.update(schema='unknown'),
            lambda m: m.update(packageId='unknown'),
            lambda m: m.update(clientCompatibility={'authorDisplayProtocol': 99}),
        ):
            with self.subTest(mutation=mutation), self.assertRaises(imp.ImportRejected):
                self.inspect(self.package(mutate_meta=mutation))

    def test_traversal_code_and_symlink_zip_rejected(self):
        for name in ('../outside', 'data/_internal/app.py', 'data/_internal/assets/authors/author-other/x.png'):
            with self.subTest(name=name), self.assertRaises(imp.ImportRejected):
                self.inspect(self.package(extra=lambda z: z.writestr(name, b'payload')))
        def link(z):
            info = zipfile.ZipInfo('data/link.png'); info.create_system=3
            info.external_attr=(stat.S_IFLNK|0o777)<<16; z.writestr(info, b'target')
        with self.assertRaises(imp.ImportRejected): self.inspect(self.package(extra=link))

    def test_base_mutation_after_inspect_rejected_before_commit(self):
        archive = self.package()
        self.inspect(archive)
        (self.root/self.deps[0]['path']).write_bytes(b'changed')
        with self.assertRaises(imp.ImportRejected): self.install(archive)
        self.assertFalse((self.root/'work/map-packages/author-aichicu.json').exists())

    def test_live_cancel_preserves_base_and_no_receipt(self):
        with self.assertRaises(imp.ImportRejected):
            imp.install_author_package_live(self.package(), self.root, application_idle=True, cancelled=lambda:True)
        self.assertFalse((self.root/'work/map-packages/author-aichicu.json').exists())
        for dep in self.deps: self.assertEqual(imp._sha(self.root/dep['path']), dep['sha256'])

    def test_warm_pixel_cache_rechecks_every_layer_dependency(self):
        from s1_author_displays import S1AuthorDisplays
        self.install(self.package())
        native = SimpleNamespace(root=self.root/'_internal', mode='nightmare',
            catalog=SimpleNamespace(profiles={}, _raw={'profiles': []}, default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        runtime = S1AuthorDisplays(self.root/'_internal', native, season='s0')
        image = runtime.binding(self.profile, 'map', 'B1')['path']
        self.assertEqual(runtime.pixels(image, 'aligned-overlay').shape, (8, 9, 4))
        for dep in self.deps:
            path = self.root/dep['path']; original = path.read_bytes()
            try:
                path.write_bytes(b'corrupted')
                with self.subTest(path=dep['path']), self.assertRaises(ValueError):
                    runtime.pixels(image, 'aligned-overlay')
            finally:
                path.write_bytes(original)
        self.assertEqual(runtime.pixels(image, 'aligned-overlay').shape, (8, 9, 4))

    def test_display_rejects_another_installation_root(self):
        from s1_author_displays import S1AuthorDisplays
        self.install(self.package())
        native = SimpleNamespace(root=self.root.parent/'other-install/_internal', mode='nightmare',
            catalog=SimpleNamespace(profiles={}, _raw={'profiles': []}, default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        runtime = S1AuthorDisplays(self.root/'_internal', native, season='s0')
        self.assertIsNone(runtime.binding(self.profile, 'map', 'B1'))
        self.assertIn('s1-author-resource-root-mismatch', runtime.errors.values())


if __name__ == '__main__': unittest.main()
