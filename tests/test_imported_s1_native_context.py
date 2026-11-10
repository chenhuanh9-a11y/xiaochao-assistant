"""Real exported S1D components and current consumers; no solver/game proof."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

import imported_resource_archive as archives
from imported_native_base import decode_native_base
from imported_resource_packages import PackageView
from imported_s1_controls_profile import S1ControlsProfile
from imported_s1_native_context import ImportedS1NativeContext
from test_export_s1_native_base_package import S1NativeBaseSourceFixture
from test_imported_s1_controls_profile import ProfilePackage, document


class ImportedS1NativeContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_temporary = tempfile.TemporaryDirectory(prefix='imported-s1-context-source-')
        cls.addClassCleanup(cls.source_temporary.cleanup)
        cls.fixture = S1NativeBaseSourceFixture(Path(cls.source_temporary.name))
        cls.fixture.export()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='imported-s1-context-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.current = dict(base=True, controls=True)
        saved = archives.save_archive(self.fixture.output, self.root)
        self.refs = {ref.component_id: ref for ref in saved.component_refs}

        def guard(component):
            def check():
                if not self.current[component]:
                    raise ValueError(component + '-selection-superseded')
            return check

        self.base = PackageView(self.root, self.refs['base'], assert_current=guard('base'))
        self.controls_view = PackageView(self.root, self.refs['controls'], assert_current=guard('controls'))
        self.bundle = decode_native_base(self.base, season='s1', mode='difficult')
        self.controls = S1ControlsProfile(self.controls_view)
        self.context = ImportedS1NativeContext(self.base, self.bundle, self.controls)

    def engine(self):
        from client_providers import SharedReferenceProvider
        native = self.context
        provider = SharedReferenceProvider(native.root, native.resources, native.versions,
            native=native, check_current=native.assert_current)
        return SimpleNamespace(pack=native.pack, qt_season='s1', qt_native_context=native,
            qt_shared_provider=provider, qt_adapters=native.adapters, qt_catalog=native.catalog,
            qt_optional_displays=None, identity_floors=native.pack.floors)

    def test_actual_component_surface_feeds_catalog_and_alignment_reference(self):
        from client_live_runtime import _AlignmentHost
        from s1_structure_selection import StructureCatalog
        native = self.context
        self.assertEqual((native.season, native.mode), ('s1', 'difficult'))
        self.assertEqual(native.root, self.base.root)
        self.assertEqual(native.component_ref, self.base.component_ref)
        for name in ('pack', 'resources', 'adapters', 'versions', 'interfaces', 'reader'):
            self.assertIs(getattr(native, name), getattr(self.bundle, name))
        self.assertIs(native.controls, self.controls)
        self.assertEqual(native.manifest, self.base.manifest)
        self.assertEqual(native.neutral_relative, self.bundle.interfaces['neutralDisplay'])
        self.assertEqual(len(native.pack.by_id), 14)
        self.assertEqual(len(native.resources.entries), 56)
        engine = self.engine()
        catalog = StructureCatalog.from_engine(engine)
        expected_key = hashlib.sha256(json.dumps(self.base.manifest, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(catalog.resource_key, expected_key)
        self.assertEqual(catalog.map_ids, self.fixture.ids)
        self.assertEqual(catalog.floors, self.fixture.floors)
        host = _AlignmentHost(engine)
        self.assertIs(host.author_coordinate_adapters, self.bundle.adapters)
        for floor in self.fixture.floors:
            reference = host._shared_structure_reference('shared-structure', self.fixture.ids[0], floor)
            self.assertIsNotNone(reference)
            self.assertEqual(reference.metadata['binding']['floor'], floor)
            self.assertTrue(native.has_reference_binding(self.fixture.ids[0], floor))
            self.assertTrue(reference.path.resolve().is_relative_to(self.base.root.resolve()))
        self.assertFalse(native.has_reference_binding(self.fixture.ids[0], '-5'))
        self.assertFalse(native.has_reference_binding('missing-map', '-1'))

    def test_shared_fallback_uses_real_provider_without_source_or_author_display(self):
        from client_manual_map import manual_map_thumbnail_source
        from client_route_preferences import prepare_display_layer
        engine = self.engine()
        mid = self.fixture.ids[0]
        reference = engine.qt_shared_provider('shared-structure', mid, '-2')
        self.assertIsNone(self.context.catalog)
        self.assertEqual(self.context.legacy_bindings, {})
        self.assertEqual(self.context.unbound_layers, ())
        self.assertIsNone(self.context.legacy_reference(mid, '-2'))
        self.assertIsNone(manual_map_thumbnail_source(engine.pack, engine.pack.by_id[mid],
            'difficult', self.context))
        rendered = self.context.rendered_display_layer(mid, '-2', reference)
        self.assertIsNone(rendered.layer)
        self.assertEqual(rendered.reason, 's1-native-display-not-installed')
        display = prepare_display_layer(engine, mid, '-2', 'shared-structure')
        self.assertEqual(display['displayKind'], 'shared-structure')
        self.assertEqual(display['routeProfileId'], 'shared-structure')
        self.assertEqual(display['resourceIdentity']['path'], str(reference.path))
        self.assertEqual(display['resourceIdentity']['binding'], reference.metadata['binding'])
        self.assertEqual(display['fallbackReason'], 's1-native-display-not-installed')
        np.testing.assert_array_equal(display['matrixDisplayToReference'], np.eye(3))
        np.testing.assert_array_equal(display['pixels'][..., 3] != 0, reference.clean_mask != 0)

    def test_operator_declared_data_does_not_enable_runtime_automatic_floor_paths(self):
        from client_live_runtime import LiveRuntime
        native = self.context
        floor = native.floor_observation_capability
        candidate = native.structure_candidate_capability
        self.assertEqual(floor['dataFloors'], list(self.fixture.floors))
        self.assertEqual(candidate['dataFloors'], list(self.fixture.floors))
        self.assertTrue(candidate['operatorDeclaredScanReady'])
        self.assertEqual(candidate['operatorDeclaredFloors'], [-1, -2, -3, -4])
        self.assertEqual(floor['sourceReplayFloors'], [])
        self.assertEqual(floor['automaticFloors'], [])
        self.assertEqual(candidate['observedFloors'], [])
        for capability in (floor, candidate):
            self.assertFalse(capability['observedFloorValidated'])
            self.assertFalse(capability['automaticFloorEligible'])
            self.assertFalse(capability['liveValidated'])
            self.assertEqual(capability['validationScope'], 'external-controls-unreviewed')
        self.assertIn('s1-external-controls-floor-observation-unvalidated', native.live_gaps)
        engine = self.engine()
        # Invoke the real capability consumers without starting capture, a
        # session, a solver, or constructing a replacement runtime controller.
        for kind, preview in (('recorded', False), ('live-client-capture', True)):
            consumer = SimpleNamespace(_season='s1', _mode='difficult', _engine=engine,
                _evidence_kind=kind, _native_s1_candidate_preview=preview)
            self.assertFalse(LiveRuntime._s1_candidate_available(consumer, engine=engine))
            self.assertFalse(LiveRuntime._s1_floor_observer_available(consumer, engine=engine))
        # Callers cannot persistently mutate the capability declaration.
        candidate['candidateReady'] = True
        floor['automaticFloors'].append(-1)
        self.assertFalse(native.structure_candidate_capability['candidateReady'])
        self.assertEqual(native.floor_observation_capability['automaticFloors'], [])

    def test_constructor_rejects_other_installation_and_incompatible_controls(self):
        other_root = self.root / 'other-installation'
        archives.save_archive(self.fixture.output, other_root)
        other_base = PackageView(other_root, self.refs['base'])
        self.assertEqual(other_base.component_ref, self.base.component_ref)
        with self.assertRaisesRegex(ValueError, 'base-bundle-mismatch'):
            ImportedS1NativeContext(other_base, self.bundle, self.controls)
        with self.assertRaisesRegex(ValueError, 'base-bundle-mismatch'):
            ImportedS1NativeContext(self.controls_view, self.bundle, self.controls)
        other_controls_package = ProfilePackage(self.root / 'other-controls', document(('nightmare',)))
        other_controls = S1ControlsProfile(other_controls_package.view)
        with self.assertRaisesRegex(ValueError, 'controls-base-floors-mismatch'):
            ImportedS1NativeContext(self.base, self.bundle, other_controls)
        original_floors = self.bundle.pack.floors
        self.bundle.pack.floors = ('-1', '-2', '-3')
        try:
            with self.assertRaisesRegex(ValueError, 'controls-base-floors-mismatch'):
                ImportedS1NativeContext(self.base, self.bundle, self.controls)
        finally:
            self.bundle.pack.floors = original_floors
        with self.assertRaisesRegex(ValueError, 'validated-components-required'):
            ImportedS1NativeContext(self.base, self.bundle, None)

    def test_every_metadata_access_and_provider_cache_hit_checks_both_views(self):
        native = self.context
        engine = self.engine()
        mid = self.fixture.ids[0]
        provider = engine.qt_shared_provider
        reference = provider('shared-structure', mid, '-1')
        self.assertIs(reference, provider('shared-structure', mid, '-1'))
        accesses = [lambda name=name: getattr(native, name) for name in (
            'pack', 'resources', 'adapters', 'versions', 'interfaces', 'reader', 'season', 'mode',
            'root', 'component_ref', 'manifest', 'controls', 'neutral_relative', 'legacy_bindings',
            'unbound_layers', 'catalog', 'live_gaps', 'floor_observation_capability',
            'structure_candidate_capability')]
        accesses += [native.assert_current,
            lambda: native.has_reference_binding(mid, '-1'),
            lambda: native.legacy_reference(mid, '-1'),
            lambda: native.display_reference(mid, '-1'),
            lambda: native.rendered_display_layer(mid, '-1', reference),
            lambda: provider('shared-structure', mid, '-1'),
            lambda: provider.binding_metadata('shared-structure', mid, '-1')]
        for owner in ('base', 'controls'):
            self.current[owner] = False
            try:
                for access in accesses:
                    with self.subTest(owner=owner, access=access):
                        with self.assertRaisesRegex(ValueError, owner + '-selection-superseded'):
                            access()
            finally:
                self.current[owner] = True

    def test_actual_same_byte_manifest_replacement_invalidates_cached_context(self):
        from s1_structure_selection import StructureCatalog
        engine = self.engine()
        engine.qt_shared_provider('shared-structure', self.fixture.ids[0], '-1')
        manifest = self.base.root.parent.parent / 'manifest.json'
        raw = manifest.read_bytes()
        # Preserve the original to prove a new same-byte filesystem object is
        # still a replacement under the owning PackageView's existing checks.
        manifest.rename(manifest.with_name('retained-manifest.json'))
        manifest.write_bytes(raw)
        for access in (lambda: self.context.manifest,
                       lambda: StructureCatalog.from_engine(engine),
                       lambda: engine.qt_shared_provider('shared-structure', self.fixture.ids[0], '-1')):
            with self.assertRaisesRegex(ValueError, 'manifest-replaced'):
                access()


if __name__ == '__main__':
    unittest.main()
