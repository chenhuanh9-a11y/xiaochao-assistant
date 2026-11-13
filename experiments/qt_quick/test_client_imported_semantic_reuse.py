"""Public activation regressions for bounded semantic result reuse.

The original-layout fixture is synthetic resource data. These cases prove
admission, immutable-input checks and catalog behavior, not game recognition.
"""
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Barrier, Event, Lock, get_ident
from time import monotonic
import unittest
from unittest.mock import patch
import zipfile

import cv2
import numpy as np

import client_imported_activation as imported_activation
from client_imported_activation import SemanticQualifications
from client_imported_providers import SUPPORTED_CAPABILITIES
from client_paths import RuntimePaths
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_resource_packages import PackageView
from original_qt_resource_fixtures import (OriginalS0DifficultSourceFixture,
    OriginalS0NightmareSourceFixture)
from resource_transaction import resource_runtime_lease
from imported_original_base import decode_original_base


def _wire(ref):
    return dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                componentId=ref.component_id, manifestSha256=ref.manifest_sha256)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ImportedSemanticReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_temp = tempfile.TemporaryDirectory(prefix='original-s0n-semantic-reuse-')
        cls.addClassCleanup(source_temp.cleanup)
        cls.source = OriginalS0NightmareSourceFixture(Path(source_temp.name))
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.install_root = Path(temporary.name).resolve()
        self.paths = RuntimePaths(self.install_root, False)
        self.root = Path(self.paths.imported_resource_root).resolve()
        saved = save_archive(self.source.output, self.root)
        self.refs = {ref.component_id: ref for ref in saved.component_refs}
        self.resolver = ResourceResolver(
            self.root, supported_capabilities=SUPPORTED_CAPABILITIES)
        self._runtime_lease = resource_runtime_lease(self.root)
        self.addCleanup(self._runtime_lease.close)
        self.qualifications = SemanticQualifications()
        self.author_pixels = {}

    def activate(self, refs=None, **kwargs):
        kwargs.setdefault('qualifications', self.qualifications)
        chosen = refs or self.refs
        return imported_activation.activate_context(
            self.resolver, self.paths, chosen['base'], chosen['controls'], **kwargs)

    def author_component(self, suffix, *, profile_id, bgra):
        """Create and save a real, inventory-bound author display package."""
        from client_providers import SharedReferenceProvider
        from imported_author_display import SCHEMA as DISPLAY_SCHEMA

        base_view = PackageView(self.root, self.refs['base'])
        base = decode_original_base(base_view, season='s0', mode='nightmare')
        provider = SharedReferenceProvider(base_view.root, base.resources, base.versions,
                                           check_current=base.assert_current)
        map_id, floor = next(iter(base.pack.by_id)), base.pack.floors[0]
        reference = provider('shared-structure', map_id, floor)
        row = dict(mapId=map_id, floor=floor, displayPath='route.png', displayShapeHW=[3, 5],
            matrixDisplayToReference=np.eye(3).tolist(), referenceShapeHW=list(reference.shape),
            referenceResource=reference.path.relative_to(base_view.root).as_posix(),
            coordinateSpace=reference.metadata['canvas']['coordinateSpace'],
            **{key: reference.metadata[key] for key in
               ('structureVersionKey', 'sourceVersion', 'baseSnapshot')})
        document = dict(schema=DISPLAY_SCHEMA, profileId=profile_id,
            authorName=f'fixture-{suffix}', displayName=f'fixture-{suffix}',
            season='s0', mode='nightmare', baseDependency='base', layers=[row],
            identityAuthority=False, structureValidationAuthority=False,
            crossFloorProjectionAllowed=False, currentFrameGeometryFitRequired=True)
        pixels = np.full((3, 5, 4), bgra, dtype=np.uint8)
        ok, encoded_image = cv2.imencode('.png', pixels)
        self.assertTrue(ok)
        members = {'entry.json': _encoded(document), 'route.png': encoded_image.tobytes()}
        package_id = f'fixture-semantic-author-{suffix}'
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId=package_id, resourceVersion='1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='display', type='display', schemaVersion=1,
                contexts=[dict(season='s0', mode='nightmare')], entrypoint='entry.json',
                dependencies={'base': _wire(self.refs['base'])})],
            files=[dict(componentId='display', path=name, bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest()) for name, raw in members.items()])
        archive_path = self.install_root / f'{package_id}.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', _encoded(manifest))
            for name, raw in members.items():
                archive.writestr(f'data/display/{name}', raw)
        ref = save_archive(archive_path, self.root).component_refs[0]
        self.author_pixels[ref] = pixels
        return ref

    def observe_author_decodes(self, observed):
        import imported_author_display
        actual_decode = imported_author_display.decode_author_display

        def record(*args, **kwargs):
            display = actual_decode(*args, **kwargs)
            layer = display.layers[0]
            observed.append((display.component_ref,
                             display.image_bytes(layer.map_id, layer.floor)))
            return display

        return patch.object(imported_author_display, 'decode_author_display', side_effect=record)

    def save_revision(self, version, *, wrong_control_floor=False):
        """Re-export real fixture bytes with a self-consistent new package ref."""
        members = {}
        with zipfile.ZipFile(self.source.output) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = version
        if wrong_control_floor:
            profile_path = 'data/controls/profile.json'
            profile = json.loads(members[profile_path])
            profile['contexts']['s0:nightmare']['floors'][0]['floorId'] = 'fixture-wrong-floor'
            members[profile_path] = _encoded(profile)
        for row in manifest['files']:
            raw = members[f"data/{row['componentId']}/{row['path']}"]
            row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        manifest_bytes = _encoded(manifest)
        members['manifest.json'] = manifest_bytes
        archive_path = self.install_root / f'{version}.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(archive_path, self.root)
        return {ref.component_id: ref for ref in saved.component_refs}

    def test_cached_selection_skips_decode_but_fresh_inventory_proof_rejects_damage(self):
        author = self.author_component('cache', profile_id='author-cache-profile', bgra=(13, 27, 41, 255))
        observed = []
        with self.observe_author_decodes(observed) as decode, \
                patch.object(imported_activation, 'build_native_engine',
                             wraps=imported_activation.build_native_engine) as build:
            first = self.activate(display_refs=(author,))
            self.assertEqual(first['status'], 'catalog-activated')
            self.assertEqual(decode.call_count, 1)
            self.assertEqual(build.call_count, 1)

            catalog_path = self.root / 'catalog.json'
            catalog_before = catalog_path.read_bytes()
            repeated = self.activate()
            self.assertEqual(repeated['status'], 'already-active')
            self.assertEqual(decode.call_count, 1)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(catalog_path.read_bytes(), catalog_before)

            view = PackageView(self.root, author)
            payload_path = view.path('route.png')
            original_payload = payload_path.read_bytes()
            damaged_payload = original_payload[:-1] + bytes((original_payload[-1] ^ 1,))
            payload_path.write_bytes(damaged_payload)
            with self.assertRaises((OSError, ValueError)):
                self.activate()
            self.assertEqual(catalog_path.read_bytes(), catalog_before)
            self.assertEqual(payload_path.read_bytes(), damaged_payload)
            self.assertEqual(decode.call_count, 1)

            payload_path.write_bytes(original_payload)
            manifest_path = (self.root / 'packages' / author.package_id /
                             author.resource_version / 'manifest.json')
            original_manifest = manifest_path.read_bytes()
            damaged_manifest = original_manifest[:-1] + bytes((original_manifest[-1] ^ 1,))
            manifest_path.write_bytes(damaged_manifest)
            with self.assertRaises((OSError, ValueError)):
                self.activate()
            self.assertEqual(catalog_path.read_bytes(), catalog_before)
            self.assertEqual(manifest_path.read_bytes(), damaged_manifest)
            self.assertEqual(decode.call_count, 1)

    def test_same_base_with_new_author_decodes_the_new_pixels(self):
        first_author = self.author_component(
            'first', profile_id='author-first-profile', bgra=(21, 34, 55, 255))
        next_author = self.author_component(
            'next', profile_id='author-next-profile', bgra=(81, 102, 123, 255))
        observed = []
        with self.observe_author_decodes(observed) as decode, \
                patch.object(imported_activation, 'build_native_engine',
                             wraps=imported_activation.build_native_engine) as build:
            self.activate(display_refs=(first_author,))
            self.assertEqual(decode.call_count, 1)
            first_calls = build.call_count

            result = self.activate(display_refs=(next_author,))

        self.assertEqual(result['status'], 'catalog-activated')
        self.assertEqual(build.call_count, first_calls + 1)
        self.assertEqual(decode.call_count, 3)
        decoded_next = [raw for ref, raw in observed if ref == next_author]
        self.assertEqual(len(decoded_next), 1)
        actual_pixels = cv2.imdecode(np.frombuffer(decoded_next[0], dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        np.testing.assert_array_equal(actual_pixels, self.author_pixels[next_author])
        self.assertEqual(self.resolver.snapshot().displays, (first_author, next_author))

    def test_separately_valid_authors_cannot_be_spliced_into_profile_conflict(self):
        first_author = self.author_component(
            'profile-first', profile_id='author-shared-profile', bgra=(10, 20, 30, 255))
        second_author = self.author_component(
            'profile-second', profile_id='author-shared-profile', bgra=(40, 50, 60, 255))
        self.activate()
        self.assertEqual(self.activate(display_refs=(first_author,))['status'], 'catalog-activated')
        switched = self.activate(display_refs=(second_author,),
                                 remove_display_refs=(first_author,))
        self.assertEqual(switched['status'], 'catalog-activated')
        self.assertEqual(self.resolver.snapshot().displays, (second_author,))

        catalog_path = self.root / 'catalog.json'
        before = catalog_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'resource-display-profile-conflict'):
            self.activate(display_refs=(first_author,))
        self.assertEqual(catalog_path.read_bytes(), before)
        self.assertEqual(self.resolver.snapshot().displays, (second_author,))

    def test_cancelled_real_publication_does_not_prequalify_the_new_author_selection(self):
        self.activate()
        author = self.author_component(
            'cancel', profile_id='author-cancel-profile', bgra=(61, 72, 83, 255))
        catalog_path = self.root / 'catalog.json'
        before = catalog_path.read_bytes()
        cancelled = [False]
        fail_publication = [True]
        observed = []
        actual_publish = imported_activation.publication.activate_catalog_locked

        def publish_with_catalog_stage_cancel(root, document, **kwargs):
            if not fail_publication[0]:
                return actual_publish(root, document, **kwargs)
            previous_stage = kwargs.pop('on_stage', None)

            def stage(name):
                if previous_stage is not None:
                    previous_stage(name)
                if name == 'catalog-published':
                    cancelled[0] = True

            return actual_publish(root, document, on_stage=stage, **kwargs)

        with self.observe_author_decodes(observed), \
                patch.object(imported_activation.publication, 'activate_catalog_locked',
                             side_effect=publish_with_catalog_stage_cancel):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                self.activate(display_refs=(author,), cancelled=lambda: cancelled[0])
            self.assertEqual(len(observed), 1)
            self.assertEqual(catalog_path.read_bytes(), before)
            self.assertFalse((self.root / 'transactions' / 'pending-catalog.json').exists())

            fail_publication[0] = False
            cancelled[0] = False
            result = self.activate(display_refs=(author,), cancelled=lambda: cancelled[0])

        self.assertEqual(result['status'], 'catalog-activated')
        self.assertEqual(len(observed), 2)

    def test_same_component_refs_under_another_root_do_not_reuse_results(self):
        other_temp = tempfile.TemporaryDirectory()
        self.addCleanup(other_temp.cleanup)
        other_install = Path(other_temp.name).resolve()
        other_paths = RuntimePaths(other_install, False)
        other_root = Path(other_paths.imported_resource_root).resolve()
        other_saved = save_archive(self.source.output, other_root)
        other_refs = {ref.component_id: ref for ref in other_saved.component_refs}
        self.assertEqual(other_refs, self.refs)
        other_resolver = ResourceResolver(
            other_root, supported_capabilities=SUPPORTED_CAPABILITIES)

        with patch.object(imported_activation, 'build_native_engine',
                          wraps=imported_activation.build_native_engine) as build:
            self.assertEqual(self.activate()['status'], 'catalog-activated')
            first_root_builds = build.call_count
            with resource_runtime_lease(other_root):
                outcome = imported_activation.activate_context(
                    other_resolver, other_paths, other_refs['base'], other_refs['controls'],
                    qualifications=self.qualifications)

        self.assertEqual(outcome['status'], 'catalog-activated')
        self.assertEqual(first_root_builds, 1)
        self.assertEqual(build.call_count, first_root_builds + 1)

    def test_new_self_consistent_but_semantically_invalid_controls_do_not_use_old_result(self):
        self.activate()
        old_catalog = (self.root / 'catalog.json').read_bytes()
        bad_refs = self.save_revision('semantic-invalid-v2', wrong_control_floor=True)
        self.assertNotEqual(bad_refs['controls'], self.refs['controls'])
        PackageView(self.root, bad_refs['controls']).validate_inventory()

        with self.assertRaisesRegex(ValueError, 'original-controls-floor-context-mismatch'):
            self.activate(bad_refs)
        self.assertEqual((self.root / 'catalog.json').read_bytes(), old_catalog)
        self.assertEqual(self.resolver.snapshot().contexts['s0:nightmare']['controls'],
                         self.refs['controls'])


class JointContextSemanticQualificationTests(unittest.TestCase):
    """Cold concurrent qualification through two original-layout S0 contexts."""
    @classmethod
    def setUpClass(cls):
        source_temp = tempfile.TemporaryDirectory(prefix='original-joint-context-semantic-')
        cls.addClassCleanup(source_temp.cleanup)
        source_root = Path(source_temp.name).resolve()
        nightmare = OriginalS0NightmareSourceFixture(source_root / 'nightmare')
        cls.nightmare_zip = nightmare.export()['output']
        difficult = OriginalS0DifficultSourceFixture(source_root / 'difficult')
        difficult.export()
        cls.difficult_zip = difficult.output

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='joint-context-semantic-runtime-')
        self.addCleanup(temporary.cleanup)
        self.install_root = Path(temporary.name).resolve()
        self.paths = RuntimePaths(self.install_root, False)
        self.root = Path(self.paths.imported_resource_root).resolve()
        nightmare_saved = save_archive(self.nightmare_zip, self.root)
        difficult_saved = save_archive(self.difficult_zip, self.root)
        self.nightmare = {ref.component_id: ref for ref in nightmare_saved.component_refs}
        self.difficult = {ref.component_id: ref for ref in difficult_saved.component_refs}
        self.resolver = ResourceResolver(
            self.root, supported_capabilities=SUPPORTED_CAPABILITIES)
        self._runtime_lease = resource_runtime_lease(self.root)
        self.addCleanup(self._runtime_lease.close)
        self.contexts = (
            ('s0', 'nightmare', self.nightmare['base'], self.nightmare['controls']),
            ('s0', 'difficult', self.difficult['base'], self.difficult['controls']),
        )

    def bad_difficult_revision(self):
        members = {}
        with zipfile.ZipFile(self.difficult_zip) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = 'semantic-worker-bad-v2'
        profile_path = 'data/controls/profile.json'
        profile = json.loads(members[profile_path])
        profile['contexts']['s0:difficult']['floors'][0]['floorId'] = 'fixture-wrong-floor'
        members[profile_path] = _encoded(profile)
        for row in manifest['files']:
            raw = members[f"data/{row['componentId']}/{row['path']}"]
            row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = _encoded(manifest)
        archive_path = self.install_root / 'semantic-worker-bad-difficult.zip'
        with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        saved = save_archive(archive_path, self.root)
        return {ref.component_id: ref for ref in saved.component_refs}

    def test_two_cold_original_contexts_build_on_distinct_workers_and_publish(self):
        gate = Barrier(2)
        builds = []
        actual_build = imported_activation.build_native_engine

        def rendezvous_then_build(*args, **kwargs):
            gate.wait(timeout=15)
            engine = actual_build(*args, **kwargs)
            builds.append((get_ident(), kwargs['season'], kwargs['mode']))
            return engine

        with patch.object(imported_activation, 'build_native_engine',
                          side_effect=rendezvous_then_build):
            outcome = imported_activation.activate_contexts(
                self.resolver, self.paths, self.contexts,
                qualifications=SemanticQualifications())

        self.assertEqual(outcome['status'], 'catalog-activated')
        self.assertEqual(outcome['contexts'], ['s0:nightmare', 's0:difficult'])
        self.assertEqual(len(builds), 2)
        self.assertEqual({(season, mode) for _, season, mode in builds},
                         {('s0', 'nightmare'), ('s0', 'difficult')})
        self.assertEqual(len({thread_id for thread_id, _, _ in builds}), 2)
        active = self.resolver.snapshot()
        self.assertEqual(active.contexts['s0:nightmare']['recognitionBase'], self.nightmare['base'])
        self.assertEqual(active.contexts['s0:difficult']['recognitionBase'], self.difficult['base'])

    def test_real_context_failure_waits_for_cancelled_peer_before_preserving_catalog(self):
        imported_activation.activate_context(
            self.resolver, self.paths, self.nightmare['base'], self.nightmare['controls'],
            qualifications=SemanticQualifications())
        catalog_path = self.root / 'catalog.json'
        catalog_before = catalog_path.read_bytes()
        bad_difficult = self.bad_difficult_revision()
        contexts = (
            ('s0', 'difficult', bad_difficult['base'], bad_difficult['controls']),
            self.contexts[0],
        )

        gate = Barrier(2)
        peer_exited = Event()
        cancellation_checks = {}
        cancellation_lock = Lock()
        actual_read_scope = PackageView.read_scope
        actual_build = imported_activation.build_native_engine
        actual_validate = imported_activation._validate_context

        def observe_read_scope(view, cancelled=None):
            with cancellation_lock:
                cancellation_checks[get_ident()] = cancelled
            return actual_read_scope(view, cancelled)

        def observe_validate(inspected, paths, base_ref, controls_ref, **kwargs):
            key = (kwargs['season'], kwargs['mode'])
            try:
                return actual_validate(inspected, paths, base_ref, controls_ref, **kwargs)
            finally:
                if key == ('s0', 'nightmare'):
                    peer_exited.set()

        def synchronize_builds(*args, **kwargs):
            gate.wait(timeout=15)
            engine = actual_build(*args, **kwargs)
            if (kwargs['season'], kwargs['mode']) == ('s0', 'nightmare'):
                with cancellation_lock:
                    cancelled = cancellation_checks.get(get_ident())
                if cancelled is None:
                    raise AssertionError('peer PackageView scope did not expose its cancellation callback')
                deadline = monotonic() + 15
                while not cancelled():
                    if monotonic() >= deadline:
                        raise AssertionError('semantic failure did not cancel its peer context')
                    Event().wait(0.005)
            return engine

        with patch.object(PackageView, 'read_scope', new=observe_read_scope), \
                patch.object(imported_activation, '_validate_context', side_effect=observe_validate), \
                patch.object(imported_activation, 'build_native_engine',
                             side_effect=synchronize_builds):
            with self.assertRaisesRegex(ValueError, 'original-controls-floor-context-mismatch'):
                imported_activation.activate_contexts(
                    self.resolver, self.paths, contexts,
                    qualifications=SemanticQualifications())

        self.assertTrue(peer_exited.is_set())
        self.assertEqual(catalog_path.read_bytes(), catalog_before)
        self.assertFalse((self.root / 'transactions' / 'pending-catalog.json').exists())
        active = self.resolver.snapshot()
        self.assertEqual(active.contexts['s0:nightmare']['recognitionBase'], self.nightmare['base'])
        self.assertNotIn('s0:difficult', active.contexts)


if __name__ == '__main__':
    unittest.main()
