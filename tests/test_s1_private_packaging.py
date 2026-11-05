"""Private Setup boundaries with synthetic files; no build, install or native input."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'app'), str(ROOT / 'experiments/qt_quick')]
import build_qt_client as build
import packaged_product_identity as identity
import s1_private_resources as native
import seed_sandbox
from s1_native_resources import MANIFEST, MODE_LAYOUTS, S1_FLOORS
from season_map_resources import SOURCE_VERSION


class PrivatePackagingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.resources = self.root / 'resources'
        self.resources.mkdir()

    @staticmethod
    def write(path, data=b'fixture-only'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def packs(self):
        import cv2
        import numpy as np
        reports = {}
        for mode in native.S1_MODES:
            base = self.resources / 'seasons/s1' / mode
            names = set(native.S1_INTERFACES.values()) - {'references/entries'}
            names.update(('references/native-similarity-groups.json', 'references/entries/entries.json',
                'references/entries/side-entries.json', 'references/entries/entry-passports.json'))
            maps = []
            for mid in sorted(MODE_LAYOUTS[mode]):
                layers = {}
                for floor in S1_FLOORS:
                    layer = f'references/entries/{mid}-{floor}-shared.npz'
                    layers[floor] = layer
                    names.update((layer, f'references/fast-structure/{mid}-{floor}-shared-fast.npz',
                        f'assets/game-native/{mid}/{floor}.png'))
                maps.append(dict(id=mid, sourceLayout=mid, sourceVersion=SOURCE_VERSION,
                    identityProfileId='shared-structure', layers=layers))
            rows = [dict(path=name, **self.write(base / name)) for name in sorted(names)]
            pack = dict(schemaVersion=1, season='s1', mode=mode, floors=list(S1_FLOORS),
                defaultEntryFloor=-1, maps=maps)
            data = json.dumps(pack).encode()
            pack_row = self.write(base / native.S1_INTERFACES['identityPack'], data)
            for row in rows:
                if row['path'] == native.S1_INTERFACES['identityPack']:
                    row.update(pack_row)
            manifest = dict(schemaVersion=1, season='s1', mode=mode, sourceVersion=SOURCE_VERSION,
                opencvVersion=cv2.__version__, numpyVersion=np.__version__,
                runtimeInterfaces=native.S1_INTERFACES, capabilities=dict(sideEntries=False, floorControls=False), files=rows)
            self.write(base / MANIFEST, json.dumps(manifest).encode())
            reports[mode] = dict(available=True, prepared=True, liveReady=False, liveAuthorization=False,
                season='s1', mode=mode, liveGaps=['s1-floor-controls-unregistered'])
        registry = Mock()
        registry.prepare.return_value = reports
        return registry

    def mutate_manifest(self, mode, callback):
        path = self.resources / 'seasons/s1' / mode / MANIFEST
        value = json.loads(path.read_text())
        callback(value)
        path.write_text(json.dumps(value))

    def inventory(self):
        for name in build.REQUIRED_RESOURCES:
            self.write(self.resources / name, b'{}')
        rows = {p.relative_to(self.resources).as_posix(): dict(bytes=p.stat().st_size,
                sha256=build.sha256(p)) for p in self.resources.rglob('*') if p.is_file()}
        path = self.root / 'inventory.json'
        path.write_text(json.dumps(dict(schema='xingtu-qt-resource-inventory-v1',
            profileId=build.LIGHT_PROFILE, resourceVersion='fixture', files=rows)))
        return path

    def test_exact_three_packs_use_real_registration_then_existing_typed_provider(self):
        registry = self.packs()
        with patch('client_providers.build_engine_factories', return_value=registry) as factory:
            result = native.validated_s1_resources(self.resources, required=True)
        self.assertEqual(len(result['files']), 561)
        factory.assert_called_once_with(self.resources.resolve())
        registry.prepare.assert_called_once_with(season='s1')
        self.assertTrue(all(row['available'] and not row['liveReady'] for row in result['providers'].values()))

    def test_absent_optional_s1_is_compatible_but_private_profile_requires_it(self):
        self.assertFalse(native.validated_s1_resources(self.resources)['included'])
        with self.assertRaisesRegex(ValueError, 'requires all three'):
            native.validated_s1_resources(self.resources, required=True)

    def test_incomplete_or_unknown_season_directory_is_rejected_before_provider(self):
        (self.resources / 'seasons/s1/difficult').mkdir(parents=True)
        with patch('client_providers.build_engine_factories') as factory:
            with self.assertRaisesRegex(ValueError, 'complete exact'):
                native.validated_s1_resources(self.resources)
            (self.resources / 'seasons/s2').mkdir()
            with self.assertRaisesRegex(ValueError, 'complete exact'):
                native.validated_s1_resources(self.resources)
            factory.assert_not_called()

    def test_corrupt_hash_and_undeclared_file_fail_before_provider(self):
        self.packs()
        path = next((self.resources / 'seasons/s1/difficulty2/assets').rglob('*.png'))
        original = path.read_bytes()
        path.write_bytes(b'corruption')
        with patch('client_providers.build_engine_factories') as factory:
            with self.assertRaisesRegex(ValueError, 'size/hash mismatch'):
                native.validated_s1_resources(self.resources)
            path.write_bytes(original)
            self.write(self.resources / 'seasons/s1/difficulty2/references/user-settings.json')
            with self.assertRaisesRegex(ValueError, 'exactly match'):
                native.validated_s1_resources(self.resources)
            factory.assert_not_called()

    def test_manifest_cannot_claim_settings_or_escape_mode_root(self):
        self.packs()
        extra = 'references/ui-settings.json'
        row = self.write(self.resources / 'seasons/s1/difficulty2' / extra)
        self.mutate_manifest('difficulty2', lambda data: data['files'].append(dict(path=extra, **row)))
        with self.assertRaisesRegex(ValueError, 'producer file scope'):
            native.validated_s1_resources(self.resources)
        for value in ('../outside.json', 'references\\map-pack.json', 'C:/outside.json'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                native._native_path(self.resources, value)

    def test_typed_failure_and_live_claim_do_not_pass_packaging(self):
        registry = self.packs()
        with patch('client_providers.build_engine_factories', return_value=registry):
            registry.prepare.return_value['difficulty2']['available'] = False
            with self.assertRaisesRegex(ValueError, 'typed/version provider'):
                native.validated_s1_resources(self.resources)
            registry.prepare.return_value['difficulty2'].update(available=True, liveReady=True)
            with self.assertRaisesRegex(ValueError, 'automatic live'):
                native.validated_s1_resources(self.resources)

    def test_numpy_producer_version_must_match(self):
        self.packs()
        self.mutate_manifest('difficulty2', lambda data: data.update(numpyVersion='unvalidated'))
        with self.assertRaisesRegex(ValueError, 'NumPy runtime'):
            native.validated_s1_resources(self.resources)

    def test_builder_new_profile_accepts_exact_s1_and_others_reject_it(self):
        registry = self.packs()
        inventory = self.inventory()
        with patch('client_providers.build_engine_factories', return_value=registry), patch.object(build, 'check_package', return_value=dict(success=True)):
            result = build.validate_resources(self.resources, inventory, instance='s0-s1-test')
            self.assertTrue(result[4]['s1']['included'])
            for instance in ('client', 'layout-preview', 'production'):
                with self.subTest(instance=instance), self.assertRaisesRegex(ValueError, 'only the exact S1'):
                    build.validate_resources(self.resources, inventory, instance=instance)

    def test_production_projection_cannot_silently_classify_seasons_as_program(self):
        with self.assertRaisesRegex(ValueError, 'does not support season'):
            build.production_file_projection(self.root, {'seasons/s1/nightmare/native-map-manifest.json': {}})
        self.write(self.root / '_internal/seasons/s1/nightmare/native-map-manifest.json')
        with self.assertRaisesRegex(ValueError, 'does not support season'):
            build.production_file_projection(self.root, {})

    def test_seed_copies_exact_native_subtree_without_work_or_settings(self):
        registry = self.packs()
        source = self.root / 'program'
        (source / '_internal/references').mkdir(parents=True)
        shutil.move(str(self.resources / 'seasons'), source / '_internal/seasons')
        self.write(source / 'work/user-state.json')
        self.write(source / 'ui-settings.json')
        target = self.root / 'work/seed'
        with patch('client_paths.WORK_ROOT', self.root / 'work'), patch('client_providers.build_engine_factories', return_value=registry):
            with redirect_stdout(io.StringIO()):
                seed_sandbox.seed(source, target)
        self.assertEqual(len([p for p in (target / '_internal/seasons').rglob('*') if p.is_file()]), 561)
        self.assertFalse((target / 'work').exists())
        self.assertFalse((target / 'ui-settings.json').exists())
        self.assertEqual((target / '.qt-lab-owner').read_text(), 'qt-quick-isolated-v1')

    def test_invalid_native_seed_never_creates_target(self):
        source = self.root / 'program'
        (source / '_internal/references').mkdir(parents=True)
        (source / '_internal/seasons/s1/nightmare').mkdir(parents=True)
        target = self.root / 'work/seed'
        with patch('client_paths.WORK_ROOT', self.root / 'work'):
            with self.assertRaises(ValueError):
                seed_sandbox.seed(source, target)
        self.assertFalse(target.exists())

    def test_new_fixed_baked_identity_and_bundled_permission(self):
        instance = dict(id='s0-s1-test', **build.INSTANCES['s0-s1-test'])
        self.assertEqual(instance['appId'], identity._ISOLATED[instance['id']])
        self.assertEqual(len({row['appId'] for row in build.INSTANCES.values()}), len(build.INSTANCES))
        profile = self.root / 'installation-profile.json'
        profile.write_text(json.dumps(dict(product=build.PRODUCT, installerInstance=instance)))
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', str(self.root), create=True):
            self.assertEqual(identity.baked_identity(require_qt=True), 'qt-isolated')
            self.assertEqual(identity.qt_application_model_id(), 'Xingtu.S0S1PrivateTest')
            with self.assertRaisesRegex(ValueError, 'Isolated'):
                identity.expected_application_executable()
            instance['appId'] = build.INSTANCES['production']['appId']
            profile.write_text(json.dumps(dict(product=build.PRODUCT, installerInstance=instance)))
            with self.assertRaises(identity.InstallationProfileError):
                identity.baked_identity(require_qt=True)
        paths = {'assets/zhan10-native/fixture.png': {}}
        self.assertEqual(build.selected_bundled_author_paths(paths, ['difficult-zhan10'], 's0-s1-test'), list(paths))
        with self.assertRaises(ValueError):
            build.selected_bundled_author_paths(paths, ['difficult-zhan10'], 'layout-preview')

    def test_self_check_reports_all_contexts_without_constructing_gui(self):
        import client
        registry = Mock()
        registry.capabilities.return_value = {mode: dict(available=True, liveReady=True) for mode in ('nightmare', 'difficult')}
        s1 = {mode: dict(available=True, liveReady=False, liveAuthorization=False) for mode in native.S1_MODES}
        output = io.StringIO()
        with patch.object(sys, 'argv', ['client', '--self-check', '--verify-resources']), patch.object(client, 'FROZEN', False), patch.object(client, 'PRODUCTION', False), patch.object(client, 'QGuiApplication', side_effect=AssertionError('GUI')), patch('client_providers.build_engine_factories', return_value=registry), patch.object(native, 'validated_s1_resources', return_value=dict(included=True, providers=s1)), redirect_stdout(output):
            status = client.main()
        result = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(set(result['resourceContexts']), {'s0:nightmare', 's0:difficult', 's1:nightmare', 's1:difficult', 's1:difficulty2'})
        self.assertFalse(result['nativeInputStarted'])
        self.assertTrue(result['ready'])
        self.assertFalse(result['resourceContexts']['s1:difficulty2']['liveReady'])


if __name__ == '__main__':
    unittest.main()
