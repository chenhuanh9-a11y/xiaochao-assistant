"""Unregistered authors traverse the real ZIP, transaction, loader and pixel paths.

The tiny base catalog/structure context replaces game resources, not import,
ownership, matrices, resource discovery, PNG decoding or removal/upgrade code.
"""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments/qt_quick'))
import star_map_import as importer
from optional_author_displays import OptionalAuthorDisplays
from map_resource_model import MapResourceModel
from map_resource_context import load_browse_context
from map_package_removal import managed_packages_snapshot, inspect_package_removal, remove_packages
from client_route_preferences import prepare_display_layer
from nightmare_author_coordinate_adapter import CANONICAL_SCHEMA


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def record(path, data):
    return dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


class StandardAuthorPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='standard-author-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / 'install'
        self.root.mkdir()
        self.configure('nightmare')

    def configure(self, mode):
        self.mode = mode
        self.profile = f'author-{mode}-never-registered'
        self.assertNotIn(self.profile, importer.PACKAGE_IDS)
        self.map_id = mode + '-one'
        self.resource = dict(layoutId='layout-one', structureRawRecordSha256='a' * 64, floor='1')
        self.version_key = 'layout-one@' + 'a' * 64 + '/1'
        self.coordinate = dict(canonicalPackId=mode + '-base', coordinateReferenceProfileId='shared-structure')
        base = dict(mapId=self.map_id, sourceVersion='game-one', resourceKey=self.resource)
        second_resource = dict(layoutId='layout-one', structureRawRecordSha256='b' * 64, floor='2')
        second_base = dict(mapId=self.map_id, sourceVersion='game-two', resourceKey=second_resource)
        dependencies = (importer.DIFFICULT_AUTHOR_DEPENDENCIES if mode == 'difficult' else importer.BASE_DEPENDENCIES)
        self.dependencies = []
        for path in sorted(dependencies):
            data = (encoded(dict(entries=[base, second_base])) if path.endswith('version-bindings.json')
                    else encoded(self.coordinate) if path.endswith('coordinate-pack.json')
                    else encoded(dict(schemaVersion=1)))
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            self.dependencies.append(record(path, data))
        version = self.root / '_internal/version.json'
        version.write_bytes(encoded(dict(displayVersion='1.0.7', numericVersion='1.0.0.11')))
        self.asset = f'assets/authors/{self.profile}/map-one-1.png'
        self.png = io.BytesIO()
        Image.new('RGBA', (7, 5), (17, 41, 93, 203)).save(self.png, format='PNG')
        self.declaration = dict(profileId=self.profile, mode=mode, displayName='新作者路线',
            authorName='测试作者', routeFamilyName='速刷', packageVersion='1.0')
        variant = dict(id='canonical', displayName='路线', resourceBase='asset-root',
            identityStatus='display-only', displayPolicy='requires-current-shared-structure-fit',
            layers={'1': self.asset})
        self.catalog = dict(schemaVersion=1, profiles=[dict(id=self.profile, displayName='新作者路线',
            authorName='测试作者', routeFamilyName='速刷', runtimeEnabled=True, settingsSelectable=True,
            alignedOverlayEligible=True, maps={self.map_id: [variant]})])
        self.matrix = [[2, 0, 3], [0, 2, 7], [0, 0, 1]]
        self.binding = dict(profileId=self.profile, mapId=self.map_id, floor='1', assetPath=self.asset,
            sourceVersion='game-one', nativeResourceKey=self.resource, structureVersionKey=self.version_key,
            matrixAssetToCanonical=self.matrix, matrixAssetToBase=self.matrix,
            identityAuthority=False, structureValidationAuthority=False, currentFrameGeometryFitRequired=True,
            crossFloorProjectionAllowed=False, registration=dict(status='confirmed'),
            coordinateBridge=dict(baseToNativeIsIdentity=True, authorGeometryErrorUnchanged=True))
        self.bindings = dict(schema='canonical-author-display-bindings-v1', profileId=self.profile,
            **self.coordinate, entries=[self.binding])
        manifest = self.root / '_internal/references' / ('nightmare-map-pack.json' if mode == 'nightmare' else 'difficult-runtime.json')
        manifest.write_bytes(encoded(dict(packId='fixture', mode=mode, displayName='基础地图',
            defaultEntryFloor='1', floors=['1', '2'], maps=[dict(id=self.map_id, name='地图一',
                resourceBase='resource-root', layers={'1': 'base/one.png', '2': 'base/two.png'})])))
        self.pack = SimpleNamespace(mode=mode, maps=[SimpleNamespace(id=self.map_id, name='地图一',
            layers={'1': None, '2': None})], route_profiles={}, default_entry_floor='1', layer_role='structure')
        self.adapters = SimpleNamespace(canonical_pack_id=self.coordinate['canonicalPackId'],
            coordinate_reference_profile_id='shared-structure', schema=CANONICAL_SCHEMA, _raw={},
            maps={self.map_id: dict(floors={'1': dict(coordinateSpace='native', shapeHW=[30, 40])})},
            profiles={'shared-structure': dict(maps={self.map_id: dict(floors={'1': dict(
                usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
                evidence=dict(algebraicallyExact=True), matrixProfileToCanonical=np.eye(3).tolist())})})})
        self.versions = {('shared-structure', self.map_id, 'canonical', '1'):
            dict(expected=dict(sourceVersion='game-one', structureVersionKey=self.version_key))}

    def package(self, *, extra=None, name='author.zip', image=None):
        prefix = f'_internal/references/author-packs/{self.profile}/'
        payload = {prefix + 'catalog.json': encoded(self.catalog), prefix + 'display-bindings.json': encoded(self.bindings),
                   '_internal/' + self.asset: self.png.getvalue() if image is None else image}
        payload.update(extra or {})
        metadata = dict(schemaVersion=2, revision=1, packageType='author-display', packageId=self.profile,
            standardAuthor=self.declaration, requiresFiles=self.dependencies,
            files=[record(path, data) for path, data in payload.items()])
        path = self.folder / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(importer.MANIFEST, encoded(metadata))
            for target, data in payload.items():
                archive.writestr('data/' + target, data)
        return path

    def load(self):
        return OptionalAuthorDisplays(self.root / '_internal', self.pack, None, self.adapters,
                                      self.versions, mode=self.mode)

    def test_new_author_install_discover_select_render_restart_and_remove_in_both_modes(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                self.configure(mode)
                archive = self.package(name=mode + '.zip')
                inspected = importer.inspect_package(archive, self.root)
                self.assertTrue(importer.supports_live_author_import(inspected))
                installed = importer.install_author_package_live(archive, self.root, application_idle=True)
                self.assertEqual(installed['status'], 'author-installed-live')
                self.assertFalse(installed['restartRequired'])
                loaded = self.load()
                self.assertFalse(loaded.errors)
                self.assertEqual(loaded.profiles, {self.profile})
                # Independent expected point catches lost/double-applied transforms.
                actual = loaded.adapters.matrix_profile_to_canonical(self.profile, self.map_id, '1') @ [4, 5, 1]
                np.testing.assert_array_equal(actual, [11, 17, 1])
                self.assertIsNone(loaded.binding(self.profile, self.map_id, '2'))
                pixels = loaded.pixels(self.root / '_internal' / self.asset, 'aligned-overlay')
                np.testing.assert_array_equal(pixels[2, 3], [93, 41, 17, 203])
                reference = SimpleNamespace(shape=(30, 40), clean_mask=np.ones((30, 40), np.uint8),
                    metadata=dict(canvas=dict(coordinateSpace='native'), binding={}), path=self.root / 'structure.npz')
                second_reference = SimpleNamespace(shape=(17, 19), clean_mask=np.ones((17, 19), np.uint8),
                    metadata=dict(canvas=dict(coordinateSpace='native-floor-two'), binding={'floor': '2'}),
                    path=self.root / 'second-structure.npz')
                provider = lambda _profile, _map, floor: reference if str(floor) == '1' else second_reference
                engine = SimpleNamespace(pack=self.pack, qt_catalog=loaded.catalog, qt_adapters=loaded.adapters,
                    qt_optional_displays=loaded, qt_shared_provider=provider)
                display = prepare_display_layer(engine, self.map_id, '1', self.profile)
                self.assertEqual(display['displayKind'], 'author-route')
                self.assertEqual(display['effectiveProfileId'], self.profile)
                self.assertEqual(display['displayLabel'], '新作者路线')
                np.testing.assert_array_equal(display['pixels'][2, 3], [93, 41, 17, 203])
                np.testing.assert_array_equal(display['matrixDisplayToReference'] @ [4, 5, 1], [11, 17, 1])
                fallback = prepare_display_layer(engine, self.map_id, '2', self.profile)
                self.assertEqual(fallback['effectiveProfileId'], 'shared-structure')
                self.assertEqual(fallback['pixels'].shape, (17, 19, 4))
                np.testing.assert_array_equal(fallback['matrixDisplayToReference'], np.eye(3))
                model = MapResourceModel(self.pack, loaded.catalog)
                self.assertEqual(model.profiles[self.profile], '新作者路线')
                self.assertEqual(model.maps(self.profile)[0].floors, ('1',))
                context = load_browse_context(self.root / '_internal', mode)
                self.assertFalse(context.error)
                summary = next(row for row in context.profiles if row.profile_id == self.profile)
                self.assertEqual(summary.package_id, self.profile)
                self.assertEqual(summary.available_layers, 1)
                self.assertEqual(self.load().profiles, {self.profile})
                self.check_preference_and_choices(loaded)
                managed = {row['packageId']: row for row in managed_packages_snapshot(self.root)}
                self.assertEqual(managed[self.profile]['status'], 'installed')
                self.assertEqual(managed[self.profile]['mode'], mode)
                self.assertEqual(managed[self.profile]['title'], '新作者路线')
                self.assertTrue(managed[self.profile]['canRemove'])
                plan = inspect_package_removal(self.root, self.profile)
                remove_packages(self.root, self.profile, application_idle=True, expected_plan_sha256=plan['planSha256'])
                self.assertFalse(self.load().profiles)
                self.assertTrue(all((self.root / row['path']).is_file() for row in self.dependencies))

    def check_preference_and_choices(self, loaded):
        # Existing GUI methods run against their real catalog snapshot without creating a window/game session.
        path = Path(__file__).resolve().parents[1] / 'app/identityv_daily_assistant.py'
        tree = ast.parse(path.read_text('utf-8-sig'))
        names = {'normalize_route_profile_id', '_author_route_options', '_route_profile_label', '_restore_route_profile_preference'}
        nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = dict(gallery_of=lambda _: None, DEFAULT_ROUTE_PROFILE_ID='wang-qingxin',
            ROUTE_PROFILE_LABELS={'shared-structure': '结构底图'}, SHARED_STRUCTURE_PROFILE_ID='shared-structure',
            SUJU_ROUTE_PROFILE_ID='suju')
        exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), namespace)
        self.assertEqual(namespace['normalize_route_profile_id'](self.profile), self.profile)
        subject = SimpleNamespace(_shared_structure_mode_enabled=lambda: True,
            optional_author_displays=loaded, author_route_catalog=loaded.catalog, game_mode=self.mode,
            settings={'routeProfileId': self.profile})
        self.assertEqual(namespace['_author_route_options'](subject), ((self.profile, '新作者路线'),))
        self.assertEqual(namespace['_route_profile_label'](subject, self.profile), '新作者路线')
        namespace['_restore_route_profile_preference'](subject)
        self.assertEqual(subject.route_profile_id, self.profile)

    def test_standard_packages_depend_on_resources_instead_of_product_version_allowlist(self):
        (self.root / '_internal/version.json').write_bytes(encoded(dict(displayVersion='2.0', numericVersion='2.0.0.1')))
        self.assertEqual(importer.inspect_package(self.package(), self.root)['schemaVersion'], 2)
        (self.root / self.dependencies[0]['path']).write_bytes(b'changed base')
        with self.assertRaisesRegex(importer.ImportRejected, 'missing-or-incompatible-dependency'):
            importer.inspect_package(self.package(), self.root)

    def test_invalid_bindings_and_images_never_publish(self):
        original = copy.deepcopy(self.binding)
        changes = [dict(floor='2'), dict(structureVersionKey='old'), dict(sourceVersion='old'),
                   dict(identityAuthority=True), dict(matrixAssetToBase=np.eye(3).tolist()),
                   dict(coordinateBridge=['invalid']), dict(registration=['invalid']),
                   dict(matrixAssetToCanonical=[[10 ** 400, 0, 0], [0, 1, 0], [0, 0, 1]]),
                   dict(matrixAssetToCanonical=[[0, 0, 0], [0, 0, 0], [0, 0, 1]])]
        for change in changes:
            with self.subTest(change=change):
                self.bindings['entries'] = [dict(original, **change)]
                with self.assertRaises(importer.ImportRejected):
                    importer.install_author_package_live(self.package(), self.root, application_idle=True)
                self.assertFalse((self.root / '_internal' / self.asset).exists())
        self.bindings['entries'] = [original]
        with self.assertRaises((importer.ImportRejected, OSError)):
            importer.inspect_package(self.package(image=b'not a png'), self.root)

    def test_package_cannot_write_another_author_or_core(self):
        for path in ('_internal/assets/authors/author-nightmare-someone-else/map.png',
                     '_internal/references/nightmare-map-pack.json', '_internal/app.py'):
            with self.subTest(path=path), self.assertRaises(importer.ImportRejected):
                importer.inspect_package(self.package(extra={path: b'foreign'}), self.root)

    def test_owned_update_and_interrupted_update_recovery_keep_standard_discovery(self):
        first = self.package()
        importer.install_author_package_live(first, self.root, application_idle=True)
        image = io.BytesIO()
        Image.new('RGBA', (7, 5), (1, 2, 3, 255)).save(image, format='PNG')
        self.declaration['packageVersion'] = '1.1'
        second = self.package(name='updated.zip', image=image.getvalue())
        self.assertTrue(importer.supports_live_author_import(importer.inspect_package(second, self.root)))
        result = importer.install_author_package_live(second, self.root, application_idle=True)
        self.assertEqual(self.load().profiles, {self.profile})
        self.assertEqual((self.root / '_internal' / self.asset).read_bytes(), image.getvalue())
        self.assertEqual(result['status'], 'author-upgraded-live')
        # Interrupt only the final target publication; transaction/recovery code runs unchanged.
        import map_package_upgrade as transaction
        link = transaction.os.link
        target = self.root / '_internal' / self.asset
        def interrupt(source, destination, *args, **kwargs):
            if Path(destination) == target:
                raise KeyboardInterrupt('simulated process loss')
            return link(source, destination, *args, **kwargs)
        self.declaration['packageVersion'] = '1.2'
        third = self.package(name='interrupted.zip')
        with patch.object(transaction.os, 'link', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                importer.install_author_package_live(third, self.root, application_idle=True)
        importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(target.read_bytes(), image.getvalue())
        self.assertEqual(self.load().profiles, {self.profile})

    def test_prepared_folder_builder_uses_player_validator_and_does_not_install(self):
        archive = self.package()
        prepared = self.folder / 'prepared'
        prepared.mkdir()
        with zipfile.ZipFile(archive) as source:
            metadata = json.loads(source.read(importer.MANIFEST))
            metadata.pop('requiresFiles')
            metadata.pop('files')
            (prepared / importer.MANIFEST).write_bytes(encoded(metadata))
            for name in source.namelist():
                if name.startswith('data/'):
                    target = prepared / name[5:]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read(name))
        command = subprocess.run([sys.executable, str(Path(importer.__file__)), 'pack', str(prepared),
            str(self.root), '--output', str(self.folder / 'built.zip')], capture_output=True, text=True,
            encoding='utf-8', timeout=30)
        self.assertEqual(command.returncode, 0, command.stderr + command.stdout)
        result = json.loads(command.stdout)
        inspected = importer.inspect_package(result['output'], self.root)
        self.assertEqual(inspected['standardAuthor'], self.declaration)
        self.assertEqual(result['sha256'], importer._sha(Path(result['output'])))
        self.assertFalse(self.load().profiles)
        with self.assertRaises(FileExistsError):
            importer.build_standard_author_package(prepared, self.root, self.folder / 'built.zip')


if __name__ == '__main__':
    unittest.main()
