"""Real package inventories/closure; no algorithm or GUI qualification."""
import hashlib
import unittest
import uuid

from imported_resource_catalog import ResourceCatalogError, SCHEMA
from imported_resource_selection import inspect_selection
import test_imported_resource_catalog as fixtures


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ImportedCatalogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def document(self, base=None, controls=None, display=None, groups=None):
        return dict(schema=SCHEMA, generation=uuid.uuid4().hex,
                    contexts={'s0:nightmare': self.fixture.context(base, controls, groups)},
                    displays=[display] if display else [])

    def with_dependency(self, ref, dependency):
        path = self.root / 'packages' / ref['packageId'] / ref['resourceVersion'] / 'manifest.json'
        import json
        value = json.loads(path.read_bytes())
        value['components'][0]['dependencies'] = {'base': dependency}
        raw = fixtures.encoded(value)
        path.write_bytes(raw)
        return dict(ref, manifestSha256=hashlib.sha256(raw).hexdigest())

    def test_empty_selection_does_not_create_resource_root(self):
        result = inspect_selection(self.root, self.document())
        self.assertFalse(result.views)
        self.assertFalse(self.root.exists())

    def test_full_inventory_and_exact_base_dependency_are_checked(self):
        base = self.fixture.component('base', 'recognition-base')
        controls = self.fixture.component('controls', 'controls-profile')
        display = self.fixture.component('display', 'display')
        display = self.with_dependency(display, base)
        result = inspect_selection(self.root, self.document(base, controls, display))
        self.assertEqual(len(result.views), 3)
        self.assertFalse((self.root / 'catalog.json').exists())
        self.assertEqual(next(view for ref, view in result.views.items()
                              if ref.component_id == 'display').read('entry.json'), b'{}')

    def test_display_cannot_silently_reselect_old_base(self):
        old = self.fixture.component('old', 'recognition-base')
        current = self.fixture.component('current', 'recognition-base')
        display = self.with_dependency(self.fixture.component('display', 'display'), old)
        with self.assertRaisesRegex(ResourceCatalogError, 'dependency-not-selected'):
            inspect_selection(self.root, self.document(current, display=display))
        self.assertFalse((self.root / 'catalog.json').exists())

    def test_shared_components_do_not_require_activating_all_supported_contexts(self):
        import json
        def shared(ref):
            path = self.root / 'packages' / ref['packageId'] / '1/manifest.json'
            value = json.loads(path.read_bytes())
            value['components'][0]['contexts'].append(dict(season='s0', mode='difficult'))
            raw = fixtures.encoded(value)
            path.write_bytes(raw)
            return dict(ref, manifestSha256=hashlib.sha256(raw).hexdigest())
        base = shared(self.fixture.component('base', 'recognition-base'))
        controls = self.with_dependency(shared(self.fixture.component('controls', 'controls-profile')), base)
        value = self.document(base, controls)
        result = inspect_selection(self.root, value)
        self.assertEqual(set(result.contexts), {'s0:nightmare'})
        value['contexts']['s0:difficult'] = self.fixture.context(base, controls)
        self.assertEqual(len(inspect_selection(self.root, value).views), 2)

    def test_damaged_payload_and_unknown_files_prevent_admission(self):
        base = self.fixture.component('base', 'recognition-base')
        entry = self.root / 'packages/fixture-base/1/data/base/entry.json'
        entry.write_bytes(b'[]')  # Same size, different digest.
        with self.assertRaisesRegex(ValueError, 'integrity-mismatch'):
            inspect_selection(self.root, self.document(base))
        entry.write_bytes(b'{}')
        entry.with_name('unknown.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'undeclared-file'):
            inspect_selection(self.root, self.document(base))

    def test_unqualified_registry_cannot_enter_proposed_catalog(self):
        registry = self.fixture.component('registry', 'reviewed-group-registry', provenance={'reviewed': True})
        with self.assertRaisesRegex(ResourceCatalogError, 'registry-untrusted'):
            inspect_selection(self.root, self.document(groups=registry))

    def test_required_program_capability_and_role_are_enforced(self):
        base = self.fixture.component('base', 'recognition-base', capabilities=('structure-v1',))
        with self.assertRaisesRegex(ResourceCatalogError, 'capability-unsupported'):
            inspect_selection(self.root, self.document(base))
        self.assertEqual(len(inspect_selection(self.root, self.document(base),
                                              supported_capabilities=('structure-v1',)).views), 1)
        with self.assertRaisesRegex(ResourceCatalogError, 'context-mismatch'):
            inspect_selection(self.root, self.document(controls=base),
                              supported_capabilities=('structure-v1',))

    def test_cancelled_inventory_does_not_write_activation(self):
        base = self.fixture.component('base', 'recognition-base')
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            inspect_selection(self.root, self.document(base), cancelled=lambda: True)
        self.assertFalse((self.root / 'catalog.json').exists())


if __name__ == '__main__':
    unittest.main()
