"""Real V2 transactions/receipts with synthetic S1 provider contracts.

This tests ownership, mode policy, payload integrity and dependency isolation;
the provider stand-in is not a claim about real S1 geometry or Windows games.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
import generic_author_base as base
import generic_author_package as generic
import managed_base_resources as managed
import star_map_import as importer
from s1_author_displays import InstalledAuthorPackage
from test_generic_author_package import GenericPackageTests, encoded, record


class MixedRetiredAuthorTests(unittest.TestCase):
    def setUp(self):
        self.f = GenericPackageTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root, self.profile = self.f.root, self.f.profile
        self.contracts, self.dependencies = {}, []
        for mode in ('difficult', 'nightmare'):
            prefix = f'_internal/seasons/s1/{mode}/'
            mapping = {row['path']: prefix + row['path'].removeprefix('_internal/') for row in self.f.deps}
            for row in self.f.deps:
                target = self.root / mapping[row['path']]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((self.root / row['path']).read_bytes())
                self.dependencies.append(record(mapping[row['path']], target.read_bytes()))
            contract = copy.deepcopy(self.f.contract)
            contract['referenceResource'] = mapping[contract['referenceResource']]
            contract['dependencies'] = list(mapping.values())
            self.contracts[mode] = contract
        self.dependencies.append(record('_internal/seasons/s1/difficulty2/native-map-manifest.json', b'dormant'))
        providers = dict(base._PROVIDERS)
        def resolve(root, season, mode, mid, floor):
            self.assertEqual(Path(root), self.root)
            self.assertIn(mode, ('difficult', 'nightmare'))
            return copy.deepcopy(self.contracts[mode])
        providers.update({('s1', mode): resolve for mode in ('difficult', 'nightmare', 'difficulty2')})
        self.enterContext(patch.object(base, '_PROVIDERS', providers))
        self.sequence = 0

    def package(self, *, mutate_meta=None, mutate_doc=None, color=(1, 2, 3, 90), corrupt_normal=False):
        payload, layers = {}, []
        for mode in ('difficulty2', 'difficult', 'nightmare'):
            stream = io.BytesIO(); Image.new('RGBA', (9, 8), color).save(stream, format='PNG')
            data = b'corrupt inactive image' if mode == 'difficulty2' and corrupt_normal else stream.getvalue()
            path = generic.image_path(self.profile, 's1', mode, 'map', 'B1')
            contract = copy.deepcopy(self.contracts.get(mode, self.contracts['difficult']))
            if mode == 'difficulty2':
                contract['referenceResource'] = '_internal/seasons/s1/difficulty2/references/old.npz'
            contract.pop('dependencies')
            layer = dict(contract, season='s1', mode=mode, mapId='map', floor='B1', displayPath=path,
                displaySha256=hashlib.sha256(data).hexdigest(), displayBytes=len(data), displayShapeHW=[8, 9],
                referenceSha256=next((r['sha256'] for r in self.dependencies
                    if r['path'] == contract['referenceResource']), 'a' * 64),
                matrixDisplayToReference=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])
            layers.append(layer); payload[path] = data
        doc = dict(schema=generic.BINDINGS_SCHEMA, profileId=self.profile, season='s1', authorName='fixture',
            displayName='Mixed fixture', identityAuthority=False, structureValidationAuthority=False,
            crossFloorProjectionAllowed=False, currentFrameGeometryFitRequired=True, layers=layers)
        if mutate_doc: mutate_doc(doc)
        payload[generic.bindings_path(self.profile)] = encoded(doc)
        metadata = dict(schema=generic.SCHEMA, schemaVersion=1, revision=1, season='s1', packageId=self.profile,
            clientCompatibility={'authorDisplayProtocol': 2}, files=[record(p, b) for p, b in payload.items()],
            requiresFiles=copy.deepcopy(self.dependencies))
        if mutate_meta: mutate_meta(metadata)
        self.sequence += 1
        path = self.root.parent / f'mixed-{self.sequence}.zip'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('map-package.json', encoded(metadata))
            for name, value in payload.items(): archive.writestr('data/' + name, value)
        return path, metadata, payload

    def install(self, archive):
        return importer.install_author_package_live(archive, self.root, application_idle=True, season='s1')

    def test_mixed_import_retains_original_receipt_and_activates_only_supported_layers(self):
        archive, metadata, payload = self.package()
        result = self.install(archive)
        self.assertEqual(len(result['inactiveLayers']), 1)
        receipt_path = self.root / f'work/map-packages/{self.profile}.json'
        before = receipt_path.read_bytes(); receipt = json.loads(before)
        self.assertEqual(receipt['files'], metadata['files'])
        self.assertEqual(receipt['requiresFiles'], metadata['requiresFiles'])
        for path, data in payload.items(): self.assertEqual((self.root / path).read_bytes(), data)
        self.assertFalse((self.root / '_internal/seasons/s1/difficulty2').exists())
        package = InstalledAuthorPackage(self.root / '_internal', self.profile)
        self.assertEqual({r['mode'] for r in package.document['layers']}, {'difficult', 'nightmare'})
        self.assertEqual(package.document['inactiveLayers'][0]['reason'], 'retired-mode')
        self.assertFalse(package.document['inactiveLayers'][0]['bindingVerified'])
        self.assertEqual(importer.verified_installed_package(self.root, self.profile)['status'], 'installed')
        self.assertTrue(self.install(archive)['receiptPreserved'])
        self.assertEqual(receipt_path.read_bytes(), before)

    def test_mixed_payload_upgrade_and_removal_keep_full_ownership(self):
        self.install(self.package()[0])
        changed, metadata, _ = self.package(color=(6, 7, 8, 90))
        self.install(changed)
        inventory, deps, _ = importer._installed_inventory(self.root, self.profile)
        self.assertEqual(len(inventory), 4)
        self.assertEqual(len(deps), len(self.dependencies))
        import author_package_management as manager
        plan = manager.inspect_author_package_removal(self.root, self.profile)
        self.assertEqual(plan['fileCount'], 4)
        owned, active_deps, unrecorded = managed._package_constraints(self.root)
        self.assertEqual(len(owned), 4)
        self.assertFalse(any('/difficulty2/' in row['path'] for _, row in active_deps))
        self.assertFalse(unrecorded)

    def test_dormant_layer_cannot_hide_missing_or_shortened_active_dependency(self):
        archive, _, _ = self.package(mutate_meta=lambda m: m['requiresFiles'].pop(0))
        with self.assertRaises(ValueError): self.install(archive)
        self.assertFalse((self.root / f'work/map-packages/{self.profile}.json').exists())
        active = self.root / self.dependencies[0]['path']; active.unlink()
        with self.assertRaisesRegex(ValueError, 'missing-or-incompatible-dependency'):
            self.install(self.package()[0])

    def test_dormant_bytes_must_still_pass_archive_integrity_and_image_validation(self):
        with self.assertRaises(ValueError): self.install(self.package(corrupt_normal=True)[0])
        self.assertFalse((self.root / f'work/map-packages/{self.profile}.json').exists())

    def test_package_cannot_self_declare_active_layers_disabled(self):
        def mutate(doc):
            doc['inactiveLayers'] = doc['layers']
            doc['layers'][1]['referenceShapeHW'] = [99, 99]
        with self.assertRaisesRegex(ValueError, 'reference-binding-mismatch'):
            self.install(self.package(mutate_doc=mutate)[0])

    def remove(self):
        import map_package_removal as removal
        plan = removal.inspect_package_removal(self.root, self.profile)
        return removal.remove_packages(self.root, self.profile, application_idle=True,
            expected_plan_sha256=plan['planSha256'])

    def test_mixed_removal_removes_all_author_layers_without_touching_active_base(self):
        archive, _, payload = self.package(); self.install(archive)
        self.assertEqual(self.remove()['status'], 'packages-removed')
        self.assertFalse(any((self.root / name).exists() for name in payload))
        for row in generic.effective_dependencies({r['path']: r for r in self.dependencies}, 's1').values():
            self.assertEqual(importer._sha(self.root / row['path']), row['sha256'])
        self.install(archive)
        self.assertTrue(self.install(archive)['receiptPreserved'])

    def test_mixed_interrupted_removal_restores_full_receipt_and_dormant_bytes(self):
        import map_package_removal as removal
        from resource_transaction import resource_write_lock
        archive, _, payload = self.package(); self.install(archive)
        receipt = self.root / f'work/map-packages/{self.profile}.json'; before = receipt.read_bytes()
        original = importer._rollback_published
        def crash(*args):
            original(*args)
            raise KeyboardInterrupt('synthetic process loss')
        with patch.object(importer, '_rollback_published', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            self.remove()
        with resource_write_lock(self.root, wait=True):
            self.assertEqual(removal.recover_pending_removal_locked(self.root)['status'], 'removal-rolled-back')
        self.assertEqual(receipt.read_bytes(), before)
        for name, data in payload.items(): self.assertEqual((self.root / name).read_bytes(), data)
        self.assertTrue(self.install(archive)['receiptPreserved'])


class RootAliasFixtureTests(unittest.TestCase):
    def test_equivalent_temp_roots_keep_original_display_and_receipt_assertions(self):
        # Exercise the original full-byte fixtures through a non-link alias,
        # rather than weakening either the provider root or display assertions.
        cases = (
            (GenericPackageTests, 'test_warm_pixel_cache_rechecks_every_layer_dependency'),
            (MixedRetiredAuthorTests,
             'test_mixed_import_retains_original_receipt_and_activates_only_supported_layers'),
        )
        for case_type, name in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as folder:
                hop = Path(folder) / 'alias-hop'
                hop.mkdir()
                alias = hop / '..'
                self.assertNotEqual(alias, alias.resolve())
                temporary = SimpleNamespace(name=str(alias), cleanup=lambda: None)
                case = case_type(name)
                try:
                    with patch.object(tempfile, 'TemporaryDirectory', return_value=temporary):
                        case.setUp()
                    getattr(case, name)()
                    self.assertEqual(case.root, case.root.resolve())
                    if isinstance(case, MixedRetiredAuthorTests):
                        # An unrelated installation must still fail the exact
                        # resolver assertion; no permissive provider fallback.
                        with self.assertRaises(AssertionError):
                            base.resolve_layer(case.root.parent / 'other-install',
                                's1', 'nightmare', 'map', 'B1')
                finally:
                    case.doCleanups()


if __name__ == '__main__': unittest.main()
