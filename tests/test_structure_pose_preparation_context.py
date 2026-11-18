"""Actual cold preparation proofs, with no timing or game-accuracy claims."""
from copy import copy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4

from imported_resource_catalog import ResourceResolver
import test_structure_pose_inputs as fixture


class StructurePosePreparationContextTests(TestCase):
    setUpClass = fixture.StructurePoseInputTests.__dict__['setUpClass']
    archive = fixture.StructurePoseInputTests.__dict__['archive']
    engine = fixture.StructurePoseInputTests.engine
    prepare = fixture.StructurePoseInputTests.prepare

    def test_static_snapshot_cannot_read_before_successful_batch_is_attached(self):
        engine, mid, _ = self.engine()
        bundle, provider, source = engine.qt_native_base, engine.qt_shared_provider, engine.qt_fast_structure_source
        operation = provider.begin_prepare_operation(bundle, source)
        self.assertIsNone(operation.paths)
        with patch.object(Path, 'stat', side_effect=AssertionError('owner-getter-io')), \
             patch.object(Path, 'resolve', side_effect=AssertionError('owner-getter-io')):
            operation.assert_owned()
            self.assertIs(operation.values['pack'], bundle._values['pack'])
        batch = source.prepare_batch(provider.batch_bindings((('shared-structure', mid, '1'),),
            operation=operation), include_prepared=False)
        with self.assertRaisesRegex(ValueError, 'resource-owner-changed'):
            operation.read_fresh_reference('shared-structure', mid, '1', read_resource=batch.read_resource)
        self.assertIs(provider.begin_read_operation(bundle, batch, preparation=operation), operation)
        reference, fresh = operation.read_fresh_reference('shared-structure', mid, '1',
                                                        read_resource=batch.read_resource)
        self.assertTrue(fresh)
        self.assertIsNotNone(reference)
        batch.release()
        operation.finish()
        self.assertTrue(operation.closed)

    def test_cold_native_preparation_has_only_initial_and_final_context_proofs(self):
        engine, _, _ = self.engine('s0', 'difficult')
        provider, bundle = engine.qt_shared_provider, engine.qt_native_base
        mid = next(key[1] for key in provider.versions)
        owners = provider._operation_owners
        base, controls = owners.base_view, owners.controls_view
        catalog = base.resource_root / 'catalog.json'
        catalog.write_text(json.dumps(dict(schema='xingtu-resource-catalog-v1',
            generation=str(uuid4()), contexts={}, displays=[])), encoding='utf-8')
        snapshot = ResourceResolver(base.resource_root).snapshot()
        current = Mock(wraps=snapshot.assert_current)
        base._assert_current = controls._assert_current = current
        self.assertFalse(provider._cache, 'this must be an actual cold native preparation')
        with patch.object(bundle, '_cached', wraps=bundle._cached) as getters, \
             patch.object(base, '_load', wraps=base._load) as base_load, \
             patch.object(controls, '_load', wraps=controls._load) as controls_load, \
             patch.object(base, '_read_manifest_bytes', wraps=base._read_manifest_bytes) as base_manifest, \
             patch.object(controls, '_read_manifest_bytes', wraps=controls._read_manifest_bytes) as controls_manifest:
            inputs = self.prepare(engine, mid)
        counts = dict(bundleGetters=getters.call_count, baseLoads=base_load.call_count,
            controlsLoads=controls_load.call_count,
            manifestReads=base_manifest.call_count + controls_manifest.call_count,
            catalogProofs=current.call_count)
        print('cold-native-prepare-proofs ' + json.dumps(counts, sort_keys=True))
        self.assertIsNotNone(inputs.prepared(inputs.reference('shared-structure', mid, '1')))
        self.assertEqual(counts['bundleGetters'], 0, counts)
        self.assertEqual(counts['controlsLoads'], 2, counts)
        self.assertLessEqual(counts['manifestReads'], 30, counts)

    def test_model_owner_replacement_after_last_payload_read_rejects_snapshot(self):
        for mutation in ('entry-model', 'entry-reader', 'entry-rows', 'side-model', 'side-reader',
                         'side-rows', 'side-passports', 'interfaces', 'adapters', 'exit-model'):
            with self.subTest(mutation=mutation):
                engine, mid, _ = self.engine()
                source, values = engine.qt_fast_structure_source, engine.qt_native_base._values
                decoded, changed = [], []
                read, decode = source.view._read_payload, source.decode

                def record_decode(*args):
                    value = decode(*args)
                    decoded.append(True)
                    return value

                def replace_after_real_read(*args):
                    raw = read(*args)
                    if decoded and not changed:
                        changed.append(True)
                        if mutation == 'entry-model':
                            values['entry_identity'] = copy(values['entry_identity'])
                        elif mutation == 'entry-reader':
                            values['entry_identity'].read_resource = copy(values['reader'])
                        elif mutation == 'entry-rows':
                            values['entry_identity'].entries = list(values['entry_identity'].entries)
                        elif mutation == 'side-model':
                            values['side_entry_identity'] = copy(values['side_entry_identity'])
                        elif mutation == 'side-reader':
                            values['side_entry_identity'].read_resource = copy(values['reader'])
                        elif mutation == 'side-rows':
                            values['side_entry_identity'].entries = list(values['side_entry_identity'].entries)
                        elif mutation == 'side-passports':
                            values['side_entry_identity'].passports = list(values['side_entry_identity'].passports)
                        elif mutation == 'interfaces':
                            values['interfaces'] = dict(values['interfaces'])
                        elif mutation == 'adapters':
                            values['adapters'] = copy(values['adapters'])
                        else:
                            values['exit_recognizer'] = copy(values['exit_recognizer'])
                    return raw

                with patch.object(source, 'decode', side_effect=record_decode), \
                     patch.object(source.view, '_read_payload', side_effect=replace_after_real_read):
                    with self.assertRaisesRegex(ValueError, 'resource-owner-changed|resource-binding-changed'):
                        self.prepare(engine, mid)
                self.assertTrue(changed)
