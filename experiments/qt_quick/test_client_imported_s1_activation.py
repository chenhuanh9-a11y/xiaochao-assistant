"""Real S1 package activation through prospective validation and the default factory."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

import client_imported_activation as imported_activation
from client_imported_providers import (SUPPORTED_CAPABILITIES,
                                       build_imported_engine_factories)
from client_paths import RuntimePaths
from imported_resource_activation import activate_catalog_locked
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceCatalogError, ResourceResolver, SCHEMA
from imported_resource_packages import ComponentRef
from imported_resource_selection import inspect_selection
from resource_transaction import resource_runtime_lease, resource_write_lock
import test_export_s1_native_base_package as source_fixture
from original_qt_resource_fixtures import OriginalS1SourceFixture


def _wire(ref):
    return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)


def _inert_component(root, component_id, component_type):
    payload = b'{}'
    package_id, version = f'fixture-s1-activation-{component_id}', '1'
    manifest = dict(
        schema='xingtu-resource-package', formatVersion=1,
        packageId=package_id, resourceVersion=version,
        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
        components=[dict(componentId=component_id, type=component_type, schemaVersion=1,
                         contexts=[dict(season='s0', mode='nightmare')],
                         entrypoint='entry.json', dependencies={})],
        files=[dict(componentId=component_id, path='entry.json', bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())],
    )
    raw_manifest = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':')).encode('utf-8')
    package = root / 'packages' / package_id / version
    entry = package / 'data' / component_id / 'entry.json'
    entry.parent.mkdir(parents=True)
    entry.write_bytes(payload)
    (package / 'manifest.json').write_bytes(raw_manifest)
    return ComponentRef(package_id, version, component_id, hashlib.sha256(raw_manifest).hexdigest())


class ImportedS1ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s1-activation-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = OriginalS1SourceFixture(Path(temporary.name).resolve())
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s1-activation-active-')
        self.addCleanup(temporary.cleanup)
        self.install_root = Path(temporary.name).resolve()
        self.paths = RuntimePaths(self.install_root, False)
        self.root = Path(self.paths.imported_resource_root).resolve()
        saved = save_archive(self.source.output, self.root)
        self.refs = {ref.component_id: ref for ref in saved.component_refs}
        self.groups = _inert_component(self.root, 'reviewed', 'reviewed-group-registry')
        self.display = _inert_component(self.root, 'display', 'display')
        self.resolver = ResourceResolver(self.root,
            supported_capabilities=SUPPORTED_CAPABILITIES,
            project_registry_refs=(self.groups,))
        self._seed_other_context()

    def _seed_other_context(self):
        document = dict(schema=SCHEMA, generation=str(uuid4()), displays=[_wire(self.display)],
            contexts={
                's0:nightmare': dict(recognitionBase=None, controls=None,
                                     reviewedGroups=_wire(self.groups)),
                's1:difficult': dict(recognitionBase=None, controls=None, reviewedGroups=None),
            })

        def validate(proposed):
            inspect_selection(self.root, proposed,
                supported_capabilities=self.resolver.supported_capabilities,
                project_registry_refs=self.resolver.project_registry_refs)

        with resource_write_lock(self.root):
            return activate_catalog_locked(self.root, document,
                expected_catalog_stamp=self.resolver.snapshot().catalog_stamp,
                validate=validate)

    def activate(self, **kwargs):
        return imported_activation.activate_context(self.resolver, self.paths,
            self.refs['base'], self.refs['controls'], **kwargs)

    def test_s1_activates_real_scanner_without_granting_live_eligibility(self):
        before = self.resolver.snapshot()
        real_build = imported_activation.build_native_engine
        prospective = []

        def record_prospective(*args, **kwargs):
            engine = real_build(*args, **kwargs)
            prospective.append(engine)
            return engine

        with resource_runtime_lease(self.root):
            with patch.object(imported_activation, 'build_native_engine',
                              side_effect=record_prospective), \
                    patch.object(imported_activation.selection, 'inspect_selection',
                                 wraps=inspect_selection) as inspected:
                result = self.activate(season='s1', mode='difficult')

            self.assertEqual(result['status'], 'catalog-activated')
            self.assertEqual(inspected.call_count, 2)
            self.assertEqual(len(prospective), 1)
            self.assertTrue(all(engine.qt_season == 's1' and not engine.qt_live_ready
                                for engine in prospective))
            active = self.resolver.snapshot()
            self.assertEqual(active.contexts['s0:nightmare'], before.contexts['s0:nightmare'])
            self.assertEqual(active.contexts['s0:nightmare']['reviewedGroups'], self.groups)
            self.assertEqual(active.displays, (self.display,))
            self.assertEqual(active.contexts['s1:difficult']['recognitionBase'], self.refs['base'])
            self.assertEqual(active.contexts['s1:difficult']['controls'], self.refs['controls'])
            self.assertIsNone(active.contexts['s1:difficult']['reviewedGroups'])

            factories = build_imported_engine_factories(self.resolver, self.paths)
            engine = factories.get_engine('difficult', season='s1')
            report = factories.capabilities('s1')['difficult']
            self.assertTrue(report['available'], report)
            self.assertFalse(report['liveReady'])
            self.assertFalse(report['liveAuthorization'])
            self.assertEqual(report['mapCount'], 14)
            self.assertEqual(report['inventory']['typedLayers'], 56)
            self.assertFalse(report['inventory']['semanticSideEntries'])
            self.assertTrue(report['candidateObservation']['candidateReady'])
            self.assertTrue(report['floorObservation']['manualMapObservation'])
            self.assertFalse(report['candidateObservation']['liveValidated'])
            # This is registration, not a current-frame eligibility verdict.
            self.assertNotIn('automaticFloorEligible', report['floorObservation'])
            self.assertEqual(report['floorObservation']['automaticFloors'], [-1, -2, -3, -4])
            self.assertEqual(report['floorObservation']['sourceReplayFloors'], [-1, -2, -3, -4])
            self.assertFalse(engine.qt_native_base.semantic_side_entries)
            self.assertFalse(engine.side_entry_identity.side_doors)
            self.assertFalse(engine.side_entry_identity.side_search)
            self.assertTrue(callable(engine.qt_s1_private_coordinate_prepare))
            self.assertTrue(callable(engine.qt_s1_pose_prior))
            self.assertIsNotNone(engine.qt_logical_group_registry)

            with patch.object(engine.entry_identity, 'rank',
                              side_effect=AssertionError('legacy entry rank used')), \
                    patch.object(engine.side_entry_identity, 'rank',
                                 side_effect=AssertionError('semantic side rank used')), \
                    patch.object(engine, 'qt_s1_structure_rank',
                                 wraps=engine.qt_s1_structure_rank) as scanner:
                engine.rank(np.zeros((1200, 1920, 3), np.uint8), floor='-1')
            self.assertEqual(scanner.call_count, 1)
            scan = engine.last_s1_structure_scan
            self.assertEqual(scan['expectedMapIds'], list(self.source.ids))
            self.assertFalse(scan['identityHardGatePassed'])
            self.assertFalse(scan['automaticSelectionAuthorized'])
            self.assertFalse(scan['liveAuthorization'])

            catalog_path = self.root / 'catalog.json'
            original_bytes = catalog_path.read_bytes()
            generation = active.generation
            repeat_validation = []
            with patch.object(imported_activation, 'build_native_engine',
                              side_effect=lambda *args, **kwargs: (
                                  repeat_validation.append(real_build(*args, **kwargs)) or repeat_validation[-1])):
                repeated = self.activate(season='s1', mode='difficult')
            self.assertEqual(repeated['status'], 'already-active')
            self.assertEqual(len(repeat_validation), 1)
            self.assertEqual(catalog_path.read_bytes(), original_bytes)
            self.assertEqual(self.resolver.snapshot().generation, generation)
            self.assertIs(factories.get_engine('difficult', season='s1'), engine)

            with self.assertRaises(ResourceCatalogError) as caught:
                self.activate(season='s1', mode='nightmare')
            # S1 nightmare is implemented, but this S1 difficult package must
            # not be admitted as a different supported context.
            self.assertEqual(caught.exception.code, 'resource-component-context-mismatch')
            self.assertEqual(catalog_path.read_bytes(), original_bytes)


if __name__ == '__main__':
    unittest.main()
