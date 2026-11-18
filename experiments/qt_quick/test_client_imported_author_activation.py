"""Prospective display admission uses the actual ZIP, readers and transaction."""
from unittest.mock import patch
from uuid import uuid4

import client_imported_activation as activation
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_paths import RuntimePaths
from client_route_preferences import prepare_display_layer
from imported_resource_activation import activate_catalog_locked
from imported_resource_catalog import ResourceResolver, SCHEMA
from imported_resource_selection import inspect_selection
from resource_transaction import resource_runtime_lease, resource_write_lock
import star_map_import as files
from test_imported_author_display import _AuthorDisplayFixture


class ClientImportedAuthorActivationTests(_AuthorDisplayFixture):
    def resource_root(self):
        self.paths = RuntimePaths(self.root, False)
        return self.paths.imported_resource_root

    def resolver(self):
        return ResourceResolver(self.resources, supported_capabilities=SUPPORTED_CAPABILITIES)

    def seed_before_base_activation(self, views):
        document = dict(schema=SCHEMA, generation=str(uuid4()),
            contexts={'s1:difficult':dict(recognitionBase=None, controls=None, reviewedGroups=None)},
            displays=[self.wire(view.component_ref) for view in views])
        # Deliberately retain metadata-only display selection before a base
        # exists. Activating its context must now prove the actual semantics.
        def inspect(proposed):
            inspect_selection(self.resources, proposed, supported_capabilities=SUPPORTED_CAPABILITIES)
        with resource_write_lock(self.resources):
            activate_catalog_locked(self.resources, document, expected_catalog_stamp=None, validate=inspect)
        return self.resources/'catalog.json'

    def activate_base(self, resolver=None, **kwargs):
        return activation.activate_context(resolver or self.resolver(), self.paths,
            self.refs['base'], self.refs['controls'], season='s1', mode='difficult', **kwargs)

    def test_bad_selected_author_cannot_become_active_with_a_valid_base(self):
        view, _, _ = self.package(lambda document:document.update(identityAuthority=True))
        catalog_path = self.seed_before_base_activation([view])
        before = catalog_path.read_bytes()
        with resource_runtime_lease(self.resources):
            with self.assertRaisesRegex(files.ImportRejected, 'binding-context-mismatch'):
                self.activate_base()
        self.assertEqual(catalog_path.read_bytes(), before)
        self.assertIsNone(self.resolver().snapshot().contexts['s1:difficult']['recognitionBase'])
        self.assertFalse((self.resources/'transactions/pending-catalog.json').exists())

    def test_selected_display_is_decoded_once_with_two_input_checks_and_idempotent_retry(self):
        from imported_author_display import decode_author_display
        view, _, _ = self.package()
        self.seed_before_base_activation([view])
        resolver = self.resolver()
        with resource_runtime_lease(self.resources):
            with patch('imported_author_display.decode_author_display', wraps=decode_author_display) as decoded, \
                    patch.object(activation.selection, 'inspect_selection',
                                 wraps=inspect_selection) as inspected:
                outcome = self.activate_base(resolver)
            self.assertEqual(outcome['status'], 'catalog-activated')
            self.assertEqual(inspected.call_count, 2)
            self.assertEqual(decoded.call_count, 1)
            before = (self.resources/'catalog.json').read_bytes()
            with patch('imported_author_display.decode_author_display', wraps=decode_author_display) as decoded:
                repeated = self.activate_base(resolver)
            self.assertEqual(repeated['status'], 'already-active')
            self.assertEqual(decoded.call_count, 1)
            self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        factory = build_imported_engine_factories(resolver, self.paths)
        engine = factory.get_engine('difficult', season='s1')
        layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        self.assertEqual(layer['displayKind'], 'author-route')
        self.assertTrue(engine.qt_rank_ready)
        self.assertFalse(engine.qt_live_ready)
        self.assertFalse(engine.memory_store.root.exists())

    def test_two_selected_components_cannot_lend_the_same_profile_two_meanings(self):
        first, _, _ = self.package()
        second, _, _ = self.package()
        catalog_path = self.seed_before_base_activation([first, second])
        before = catalog_path.read_bytes()
        with resource_runtime_lease(self.resources):
            with self.assertRaisesRegex(ValueError, 'display-profile-conflict'):
                self.activate_base()
        self.assertEqual(catalog_path.read_bytes(), before)

    def test_cancellation_in_published_input_check_restores_previous_selection(self):
        view, _, _ = self.package()
        catalog_path = self.seed_before_base_activation([view])
        before = catalog_path.read_bytes()
        state = dict(published=False, cancelled=False)

        def publish(root, document, **kwargs):
            def stage(name):
                if name == 'catalog-published':
                    state['published'] = True
            return activate_catalog_locked(root, document, on_stage=stage, **kwargs)

        def inspect_then_cancel(*args, **kwargs):
            inspected = inspect_selection(*args, **kwargs)
            if state['published']:
                state['cancelled'] = True
            return inspected

        with resource_runtime_lease(self.resources):
            with patch.object(activation.publication, 'activate_catalog_locked', side_effect=publish), \
                    patch.object(activation.selection, 'inspect_selection', side_effect=inspect_then_cancel):
                with self.assertRaisesRegex(files.ImportRejected, 'import-cancelled'):
                    self.activate_base(cancelled=lambda:state['cancelled'])
        self.assertTrue(state['published'])
        self.assertTrue(state['cancelled'])
        self.assertEqual(catalog_path.read_bytes(), before)
        self.assertFalse((self.resources/'transactions/pending-catalog.json').exists())
