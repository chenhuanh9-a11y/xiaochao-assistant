"""Actual base ZIP/activation/factory/registration; no rank or game acceptance."""
from pathlib import Path
import tempfile
import unittest

from client_engine import registered_logical_family
import client_imported_activation as activation
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_paths import RuntimePaths
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from resource_transaction import resource_runtime_lease
from test_export_s0_difficult_native_base_package import S0DifficultSourceFixture
from s0_nightmare_group_source_fixture import S0NightmareGroupSourceFixture


class ImportedGroupRegistryTests(unittest.TestCase):
    def test_nightmare_base_keeps_original_three_groups_and_rechecks_warm_reference(self):
        with tempfile.TemporaryDirectory(prefix='imported-reviewed-nightmare-') as temporary:
            root = Path(temporary).resolve()
            source = S0NightmareGroupSourceFixture(root / 'source')
            report = source.export()
            self.assertEqual((report['mapCount'], report['typedLayerCount'], report['reviewedGroupCount']), (26,78,3))
            self.assertEqual(report['reviewedGroupGaps'], {})
            paths = RuntimePaths(root / 'client', False)
            resolver = ResourceResolver(paths.imported_resource_root, supported_capabilities=SUPPORTED_CAPABILITIES)
            saved = save_archive(source.output, resolver.root)
            refs = {ref.component_id:ref for ref in saved.component_refs}
            with resource_runtime_lease(resolver.root):
                activation.activate_context(resolver, paths, refs['base'], refs['controls'])
            engine = build_imported_engine_factories(resolver, paths).get_engine('nightmare')
            registry = engine.qt_logical_group_registry
            self.assertEqual(tuple(tuple(group['memberIds']) for group in registry.groups), (
                ('nightmare-04','nightmare-12','nightmare-19'), ('nightmare-03','nightmare-09'),
                ('nightmare-07','nightmare-08','nightmare-20')))
            group = registry.group_for('nightmare-04', floor='1', entrance_kind='front')
            registry.validate(group, engine.qt_shared_provider)
            reference = engine.qt_shared_provider('shared-structure', 'nightmare-12', '1')
            original = reference.path.read_bytes()
            reference.path.write_bytes(b'broken-warm-reference')
            try:
                failed = registered_logical_family(engine, 'nightmare-04', '1', entrance_kind='front')
                self.assertEqual(failed['status'], 'unavailable')
            finally:
                reference.path.write_bytes(original)
            recovered = registered_logical_family(engine, 'nightmare-04', '1', entrance_kind='front')
            self.assertEqual(recovered['status'], 'group')
            self.assertEqual(recovered['candidates'][0]['memberIds'], ['nightmare-04','nightmare-12','nightmare-19'])
            self.assertIsNone(registry.group_for('nightmare-13', floor='1', entrance_kind='front'))
            self.assertIsNone(registry.group_for('nightmare-04', floor='2', entrance_kind='front'))
            self.assertIsNone(registry.group_for('nightmare-04', floor='1', entrance_kind='side'))
            self.assertFalse(engine.memory_store.root.exists())

    def test_difficult_base_supplies_original_five_groups_without_opening_rank_or_live(self):
        with tempfile.TemporaryDirectory(prefix='imported-reviewed-groups-') as temporary:
            root = Path(temporary).resolve()
            source = S0DifficultSourceFixture(root / 'source')
            source.export()
            paths = RuntimePaths(root / 'client', False)
            resolver = ResourceResolver(paths.imported_resource_root, supported_capabilities=SUPPORTED_CAPABILITIES)
            saved = save_archive(source.output, resolver.root)
            refs = {ref.component_id:ref for ref in saved.component_refs}
            with resource_runtime_lease(resolver.root):
                activation.activate_context(resolver, paths, refs['base'], refs['controls'],
                                            season='s0', mode='difficult')
            engine = build_imported_engine_factories(resolver, paths).get_engine('difficult', season='s0')
            registry = engine.qt_logical_group_registry
            self.assertIsNotNone(registry)
            expected = (
                ('difficult-map-08', 'difficult-map-25'),
                ('difficult-map-03', 'difficult-map-10'),
                ('difficult-map-04', 'difficult-map-21'),
                ('difficult-map-20', 'difficult-map-26'),
                ('difficult-map-05', 'difficult-map-09-2'),
            )
            self.assertEqual(tuple(tuple(group['memberIds']) for group in registry.groups), expected)
            family = registered_logical_family(engine, 'difficult-map-05', '1', entrance_kind='front')
            self.assertEqual(family['status'], 'group')
            self.assertEqual(family['candidates'][0]['memberIds'], ['difficult-map-05', 'difficult-map-09-2'])
            self.assertEqual(registered_logical_family(engine, 'difficult-map-05', '2', entrance_kind='front')['status'], 'none')
            self.assertEqual(registered_logical_family(engine, 'difficult-map-05', '1', entrance_kind='side')['status'], 'none')
            self.assertFalse(engine.qt_rank_ready)
            self.assertFalse(engine.qt_live_ready)
            self.assertFalse(engine.memory_store.root.exists())


if __name__ == '__main__':
    unittest.main()
