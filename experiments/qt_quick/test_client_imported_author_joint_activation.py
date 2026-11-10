"""Atomic B/C/display dependency updates through the actual activation entry."""
import json
from unittest.mock import patch
import zipfile

import numpy as np

import client_imported_activation as activation
from client_imported_providers import build_imported_engine_factories
from client_route_preferences import prepare_display_layer
from imported_author_display import decode_author_display
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceCatalogError
from resource_transaction import resource_runtime_lease
import star_map_import as files
from test_client_imported_author_selection import _AuthorSelectionFixture, changed_png
from test_client_imported_s1_activation import _inert_component


class ClientImportedAuthorJointActivationTests(_AuthorSelectionFixture):
    def revision(self, version):
        with zipfile.ZipFile(self.source.output) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = version
        members['manifest.json'] = json.dumps(manifest).encode('utf-8')
        path = self.root/(version+'.zip')
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name,raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(path, self.resources)
        return {ref.component_id:ref for ref in saved.component_refs}

    def joint(self, refs, views, **kwargs):
        with resource_runtime_lease(self.resources):
            return activation.activate_context(self.resolver(), self.paths, refs['base'], refs['controls'],
                season='s1', mode='difficult', display_refs=tuple(view.component_ref for view in views), **kwargs)

    def assert_selected(self, refs, author):
        snapshot = self.resolver().snapshot()
        row = snapshot.contexts['s1:difficult']
        self.assertEqual(row['recognitionBase'], refs['base'])
        self.assertEqual(row['controls'], refs['controls'])
        self.assertIn(author.component_ref, snapshot.displays)
        self.assertFalse((self.resources/'transactions/pending-catalog.json').exists())
        return snapshot

    def test_joint_update_retires_broken_old_payloads_and_displays_new_pixels_atomically(self):
        dormant = _inert_component(self.resources, 'display', 'display')
        self.seed(displays=[dormant])
        first, _, _ = self.package(package_id='author-versioned')
        self.activate(first)
        original = self.resolver().snapshot()
        factory = build_imported_engine_factories(self.resolver(), self.paths)
        old_engine = factory.get_engine('difficult', season='s1')
        prepare_display_layer(old_engine, self.source.ids[0], '-4', 'author-fixture')
        next_refs = self.revision('base-v2')
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2',
            dependency=self.wire(next_refs['base']), payloads={'images/route.png':changed_png()})
        third, _, _ = self.package(package_id='author-versioned', resource_version='v3',
            dependency=self.wire(next_refs['base']))
        # Both old packages leave the final closure. Neither their broken
        # bitmap nor their old NPZ is a reason to reject a valid replacement.
        old_reference = self.provider('shared-structure', self.source.ids[0], '-4')
        first.path('images/route.png').write_bytes(b'broken old author')
        old_reference.path.write_bytes(b'broken old base')
        result = self.joint(next_refs, [second])
        self.assertEqual(result['status'], 'catalog-activated')
        selected = self.assert_selected(next_refs, second)
        self.assertEqual(selected.displays, (dormant, second.component_ref))
        self.assertEqual(selected.contexts['s0:nightmare'], original.contexts['s0:nightmare'])
        self.assertEqual(selected.contexts['s1:difficult']['reviewedGroups'],
                         original.contexts['s1:difficult']['reviewedGroups'])
        with self.assertRaises(ResourceCatalogError):
            prepare_display_layer(old_engine, self.source.ids[0], '-4', 'author-fixture')
        engine = factory.get_engine('difficult', season='s1')
        layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        self.assertEqual(engine.qt_native_base.component_ref, next_refs['base'])
        np.testing.assert_array_equal(layer['pixels'], np.full((3,5,4), (93,92,91,94), np.uint8))
        self.assertTrue(engine.qt_rank_ready)
        self.assertFalse(engine.qt_live_ready)
        self.assertFalse(engine.memory_store.root.exists())
        # An author-only change must not be hidden by unchanged B/C refs.
        before = selected.generation
        self.joint(next_refs, [third])
        self.assertNotEqual(self.resolver().snapshot().generation, before)
        before = (self.resources/'catalog.json').read_bytes()
        with patch('imported_author_display.decode_author_display', wraps=decode_author_display) as decoded:
            repeated = self.joint(next_refs, [third])
        self.assertEqual(repeated['status'], 'already-active')
        self.assertEqual(decoded.call_count, 1)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)

    def test_omitted_author_keeps_its_dependency_and_prevents_a_partial_switch(self):
        self.seed()
        first, _, _ = self.package(package_id='author-versioned')
        other, _, _ = self.package(package_id='author-other', profile_id='author-other')
        self.activate(first)
        self.activate(other)
        next_refs = self.revision('base-v2')
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2',
                                   dependency=self.wire(next_refs['base']))
        before = (self.resources/'catalog.json').read_bytes()
        with self.assertRaisesRegex(ResourceCatalogError, 'dependency-not-selected'):
            self.joint(next_refs, [second])
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        selected = self.assert_selected(self.refs, first)
        self.assertEqual(selected.displays, (first.component_ref, other.component_ref))
        # Supplied updates are a subset with unique logical component keys.
        with self.assertRaisesRegex(ResourceCatalogError, 'display-selection-ambiguous'):
            self.joint(next_refs, [second, second])
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)

    def test_second_check_cancel_restores_all_base_controls_and_display_refs(self):
        self.seed()
        first, _, _ = self.package(package_id='author-versioned')
        self.activate(first)
        next_refs = self.revision('base-v2')
        second, _, _ = self.package(package_id='author-versioned', resource_version='v2',
                                   dependency=self.wire(next_refs['base']))
        before = (self.resources/'catalog.json').read_bytes()
        state = dict(calls=0, cancelled=False)

        def decode_then_cancel(*args):
            display = decode_author_display(*args)
            state['calls'] += 1
            if state['calls'] == 2:
                state['cancelled'] = True
            return display

        with patch('imported_author_display.decode_author_display', side_effect=decode_then_cancel):
            with self.assertRaisesRegex(files.ImportRejected, 'import-cancelled'):
                self.joint(next_refs, [second], cancelled=lambda:state['cancelled'])
        self.assertEqual(state['calls'], 2)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assert_selected(self.refs, first)
