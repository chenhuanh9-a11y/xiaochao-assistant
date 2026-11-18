"""Atomic multi-context activation through real S0 producer packages."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

import client_imported_activation as activation
import client_imported_activation_plan as planning
from client_imported_providers import (SUPPORTED_CAPABILITIES,
                                       build_imported_engine_factories)
from client_paths import RuntimePaths
from imported_original_base import decode_original_base
from imported_resource_archive import save_archive
from imported_resource_activation import activate_catalog_locked
from imported_resource_catalog import ResourceCatalogError, ResourceResolver, SCHEMA
from imported_resource_packages import PackageView
from imported_resource_selection import inspect_selection
from resource_transaction import resource_runtime_lease, resource_write_lock
from original_qt_resource_fixtures import (OriginalS0DifficultSourceFixture,
    OriginalS0NightmareSourceFixture)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ImportedPackageActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_temp = tempfile.TemporaryDirectory(prefix='package-activation-producers-')
        cls.addClassCleanup(cls.source_temp.cleanup)
        source_root = Path(cls.source_temp.name).resolve()
        nightmare = OriginalS0NightmareSourceFixture(source_root / 'nightmare')
        cls.nightmare_zip = nightmare.export()['output']
        hard = OriginalS0DifficultSourceFixture(source_root / 'difficult')
        hard.export()
        cls.difficult_zip = hard.output

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='package-activation-runtime-')
        self.addCleanup(temporary.cleanup)
        self.install_root = Path(temporary.name).resolve()
        self.paths = RuntimePaths(self.install_root, False)
        self.root = Path(self.paths.imported_resource_root).resolve()
        nightmare_saved = save_archive(self.nightmare_zip, self.root)
        difficult_saved = save_archive(self.difficult_zip, self.root)
        self.nightmare = {ref.component_id: ref for ref in nightmare_saved.component_refs}
        self.difficult = {ref.component_id: ref for ref in difficult_saved.component_refs}
        self.resolver = ResourceResolver(self.root,
            supported_capabilities=SUPPORTED_CAPABILITIES)
        self.contexts = (
            ('s0', 'nightmare', self.nightmare['base'], self.nightmare['controls']),
            ('s0', 'difficult', self.difficult['base'], self.difficult['controls']),
        )

    def _bad_difficult_revision(self):
        members = {}
        with zipfile.ZipFile(self.difficult_zip) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = 'bad-controls-v2'
        profile_path = 'data/controls/profile.json'
        profile = json.loads(members[profile_path])
        profile['contexts']['s0:difficult']['floors'][0]['floorId'] = 'fixture-wrong-floor'
        members[profile_path] = _encoded(profile)
        for row in manifest['files']:
            raw = members[f"data/{row['componentId']}/{row['path']}"]
            row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = _encoded(manifest)
        bad_path = self.install_root / 'bad-difficult.zip'
        with zipfile.ZipFile(bad_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(bad_path, self.root)
        return {ref.component_id: ref for ref in saved.component_refs}

    def _unknown_display(self):
        payload = b'not a display registration document'
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='fixture-unknown-author', resourceVersion='1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='display', type='display', schemaVersion=1,
                contexts=[dict(season='s0', mode='nightmare')], entrypoint='entry.json',
                dependencies={})],
            files=[dict(componentId='display', path='entry.json', bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest())])
        archive_path = self.install_root / 'unknown-display.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', _encoded(manifest))
            archive.writestr('data/display/entry.json', payload)
        return save_archive(archive_path, self.root).component_refs[0]

    def _select_display_without_decoding_it(self, display_ref):
        document = dict(schema=SCHEMA, generation=str(uuid.uuid4()),
            contexts={key:dict(recognitionBase=None, controls=None, reviewedGroups=None)
                      for key in ('s0:nightmare', 's0:difficult')},
            displays=[dict(packageId=display_ref.package_id,
                resourceVersion=display_ref.resource_version,
                componentId=display_ref.component_id,
                manifestSha256=display_ref.manifest_sha256)])

        def validate(proposed):
            inspect_selection(self.root, proposed,
                supported_capabilities=self.resolver.supported_capabilities,
                project_registry_refs=self.resolver.project_registry_refs)

        with resource_write_lock(self.root):
            return activate_catalog_locked(self.root, document,
                expected_catalog_stamp=None, validate=validate)

    def test_real_s0_packages_publish_two_contexts_once_and_decode_in_factories(self):
        plan = planning.plan_context_activations(self.resolver, self.contexts)
        self.assertEqual(len(plan.contexts), 2)
        self.assertIsNone(plan.catalog_stamp)
        self.assertEqual(plan.rows, ())

        with resource_runtime_lease(self.root):
            with patch.object(activation.publication, 'activate_catalog_locked',
                              wraps=activation.publication.activate_catalog_locked) as publish, \
                    patch.object(activation, 'build_native_engine',
                                 wraps=activation.build_native_engine) as build, \
                    patch.object(activation.selection, 'inspect_selection',
                                 wraps=inspect_selection) as inspected:
                result = activation.activate_contexts(self.resolver, self.paths, self.contexts,
                    expected_catalog_stamp=plan.catalog_stamp)
            publish.assert_called_once()
            self.assertEqual(inspected.call_count, 2)
            self.assertEqual(build.call_count, 2)
            self.assertTrue(all(call.kwargs['require_rank'] is False
                                for call in build.call_args_list))

        self.assertEqual(result['status'], 'catalog-activated')
        self.assertEqual(result['contexts'], ['s0:nightmare', 's0:difficult'])
        active = self.resolver.snapshot()
        for key, refs in (('s0:nightmare', self.nightmare), ('s0:difficult', self.difficult)):
            self.assertEqual(active.contexts[key]['recognitionBase'], refs['base'])
            self.assertEqual(active.contexts[key]['controls'], refs['controls'])
        catalog = json.loads((self.root / 'catalog.json').read_bytes())
        for row in catalog['contexts'].values():
            self.assertEqual(set(row), {'recognitionBase', 'controls', 'reviewedGroups'})

        nightmare_bundle = decode_original_base(PackageView(self.root, self.nightmare['base']),
            season='s0', mode='nightmare')
        difficult_bundle = decode_original_base(PackageView(self.root, self.difficult['base']),
            season='s0', mode='difficult')
        self.assertEqual(len(nightmare_bundle.pack.maps), 26)
        self.assertEqual(len(difficult_bundle.pack.maps), 30)

        factories = build_imported_engine_factories(self.resolver, self.paths)
        nightmare_engine = factories.get_engine('nightmare')
        difficult_engine = factories.get_engine('difficult')
        self.assertEqual(nightmare_engine.qt_season, 's0')
        self.assertEqual(difficult_engine.qt_season, 's0')
        self.assertEqual(nightmare_engine.qt_native_base.pack.by_id.keys(),
                         nightmare_bundle.pack.by_id.keys())
        self.assertEqual(difficult_engine.qt_native_base.pack.by_id.keys(),
                         difficult_bundle.pack.by_id.keys())

    def test_bad_second_context_publishes_neither_context(self):
        bad = self._bad_difficult_revision()
        contexts = (
            self.contexts[0],
            ('s0', 'difficult', bad['base'], bad['controls']),
        )
        with resource_runtime_lease(self.root):
            with self.assertRaisesRegex(ValueError, 'original-controls-floor-context-mismatch'):
                activation.activate_contexts(self.resolver, self.paths, contexts)

        self.assertFalse((self.root / 'catalog.json').exists())
        self.assertFalse((self.root / 'transactions' / 'pending-catalog.json').exists())
        after = self.resolver.snapshot()
        self.assertEqual(after.contexts, {})

    def test_duplicate_contexts_and_stale_catalog_plan_are_rejected(self):
        duplicate = (self.contexts[0], self.contexts[0])
        for call in (
            lambda: planning.plan_context_activations(self.resolver, duplicate),
            lambda: activation.activate_contexts(self.resolver, self.paths, duplicate),
        ):
            with self.assertRaises(ResourceCatalogError) as caught:
                call()
            self.assertEqual(caught.exception.code, 'native-base-context-duplicate')

        plan = planning.plan_context_activations(self.resolver, self.contexts)
        with resource_runtime_lease(self.root):
            activation.activate_contexts(self.resolver, self.paths, self.contexts[:1],
                expected_catalog_stamp=plan.catalog_stamp)
            before = (self.root / 'catalog.json').read_bytes()
            with self.assertRaises(ResourceCatalogError) as caught:
                activation.activate_contexts(self.resolver, self.paths, self.contexts,
                    expected_catalog_stamp=plan.catalog_stamp)
        self.assertEqual(caught.exception.code, 'resource-catalog-superseded')
        self.assertEqual((self.root / 'catalog.json').read_bytes(), before)

    def test_unknown_author_row_is_merged_once_with_one_global_stop_choice(self):
        display = self._unknown_display()
        self._select_display_without_decoding_it(display)
        before = (self.root / 'catalog.json').read_bytes()

        plan = planning.plan_context_activations(self.resolver, self.contexts)

        self.assertEqual(len(plan.contexts), 2)
        self.assertTrue(all(context.catalog_stamp == plan.catalog_stamp
                            for context in plan.contexts))
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual(plan.rows[0], plan.contexts[0].rows[0])
        self.assertEqual(plan.contexts[0].rows, plan.contexts[1].rows)
        self.assertEqual(len(plan.rows[0].choices), 1)
        self.assertIsNone(plan.rows[0].choices[0].display_ref)
        self.assertEqual((self.root / 'catalog.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
