"""Actual copy/verify and Win32 image exclusion; registry is a narrow fixture.

No registry writes, historical executable, installed product or user data.
"""
from contextlib import contextmanager, nullcontext
from pathlib import Path
import configparser
import copy
import json
import os
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import installer_legacy_bridge as bridge
import star_map_reset_helper
from packaged_product_identity import PRODUCTION_APP_ID


class LegacyBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / 'old', self.root / 'new'
        self.profile = dict(product='星图', installerInstance=dict(
            id='production', product='星图', appId=PRODUCTION_APP_ID))
        self.write('_internal/installation-profile.json', json.dumps(self.profile))
        self.write('work/nightmare-memory/ui-settings.json', '{"automaticFailureEvidence":false}')
        self.write('work/nightmare-memory/custom-galleries.json', '{"galleries":[]}')
        self.write('work/user-routes/edited.json', '{"changedByUser":true}')
        self.write('MaintenanceTool.exe', 'synthetic maintenance image; never run')
        self.write('MaintenanceTool.dat', 'synthetic')
        self.write('components.xml', '<Packages/>')
        self.row = dict(hive='HKCU', view=64, key='synthetic-qtifw-record', values={
            'DisplayName': [1, '星图'], 'DisplayVersion': [1, '1.1.2'],
            'InstallLocation': [1, str(self.source)],
            'UninstallString': [1, '"' + str(self.source / 'MaintenanceTool.exe') + '"']})

    def write(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def snapshot(self):
        return {p.relative_to(self.source).as_posix(): p.read_bytes()
                for p in self.source.rglob('*') if p.is_file()}

    def rows(self, _source):
        return [copy.deepcopy(self.row)]

    def prepare(self, **kwargs):
        arguments = dict(registry_reader=self.rows, external_root=self.root / 'absent-external')
        if os.name != 'nt':
            arguments['source_guard'] = lambda *_: nullcontext()
        arguments.update(kwargs)
        return bridge.prepare(self.source, self.target, **arguments)

    def test_prepares_actual_files_and_retry_without_installing_or_deleting_source(self):
        original = self.snapshot()
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared-native-unverified')
        self.assertEqual(result, self.prepare())
        self.assertEqual(self.snapshot(), original)
        prepared = self.target / '.legacy-upgrade-prepared'
        for name in ('ui-settings.json', 'custom-galleries.json'):
            relative = Path('work/nightmare-memory') / name
            self.assertEqual((prepared / relative).read_bytes(), (self.source / relative).read_bytes())
        self.assertEqual(json.loads((prepared / 'work/user-routes/edited.json').read_text()),
                         {'changedByUser': True})
        self.assertFalse(result['nativeInstalled'])
        self.assertFalse(result['oldEntryRetired'])
        self.assertFalse(result['sourceDeleted'])
        self.assertEqual(result['evidence'], 'fixture')

    def test_registration_product_tool_and_type_mismatches_are_rejected(self):
        for field, value in (('DisplayName', [1, 'other']), ('InstallLocation', [1, str(self.root)]),
                             ('UninstallString', [1, '"' + str(self.source / 'MaintenanceTool.exe') + '" --purge']),
                             ('DisplayVersion', [4, 123])):
            with self.subTest(field=field):
                original = copy.deepcopy(self.row)
                self.row['values'][field] = value
                with self.assertRaises(bridge.LegacyBridgeError):
                    self.prepare()
                self.assertFalse(self.target.exists())
                self.row = original

    def test_duplicate_and_machine_records_are_not_adopted(self):
        with self.assertRaisesRegex(bridge.LegacyBridgeError, 'unique-current-user'):
            self.prepare(registry_reader=lambda _: [self.row, self.row])
        self.row['hive'] = 'HKLM'
        with self.assertRaisesRegex(bridge.LegacyBridgeError, 'unique-current-user'):
            self.prepare()

    def test_external_resources_and_editor_assets_are_copied_as_one_quarantined_bundle(self):
        external = self.root / 'external-resources'
        for relative in ('work/map-packages/import.lock',
                         'work/map-packages/package/map.json', 'assets/custom-maps/manifest.json'):
            path = external / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic-' + relative, encoding='utf-8')
        roots = ('assets/custom-maps', '_internal/assets/custom-maps',
                 '_internal/qt-editor-base', 'work/imported-resources')
        for relative in roots:
            self.write(relative + '/manifest.json', 'synthetic-' + relative)
        result = self.prepare(external_root=external)
        self.assertEqual(result['status'], 'prepared-native-unverified')
        self.assertEqual(result['assetRoots'], sorted(('external', 'outer-custom-maps',
            'internal-custom-maps', 'editor-base', 'imported-resources')))
        prepared = self.target / '.legacy-upgrade-prepared/work/map-packages/legacy-unverified/asset-roots'
        for relative in ('work/map-packages/package/map.json',
                         'assets/custom-maps/manifest.json'):
            self.assertEqual((prepared / 'external' / relative).read_bytes(),
                             (external / relative).read_bytes())
        for label, relative in zip(('outer-custom-maps', 'internal-custom-maps',
                                    'editor-base', 'imported-resources'), roots):
            self.assertEqual((prepared / label / 'manifest.json').read_bytes(),
                             (self.source / relative / 'manifest.json').read_bytes())
        self.assertFalse((prepared / 'external/work/map-packages/import.lock').exists())
        self.assertEqual((external / 'work/map-packages/import.lock').read_text(),
                         'synthetic-work/map-packages/import.lock')
        self.assertEqual(len(result['inventorySha256']), 64)

    def test_external_root_requires_the_existing_resource_lock(self):
        external = self.root / 'external-resources'
        external.mkdir()
        with self.assertRaisesRegex(bridge.LegacyBridgeError, 'external-resource-lock-missing'):
            self.prepare(external_root=external)
        self.assertFalse(self.target.exists())

    def test_external_resource_lock_busy_fails_without_copy(self):
        from resource_transaction import resource_runtime_lease
        external = self.root / 'external-resources'
        path = external / 'work/map-packages/import.lock'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'')
        (external / 'work/map-packages/package').mkdir()
        (external / 'work/map-packages/package/payload.bin').write_bytes(b'package')
        original = self.snapshot()
        with resource_runtime_lease(external):
            with self.assertRaisesRegex(ValueError, 'resource-import-busy-or-recovery-required'):
                self.prepare(external_root=external)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.snapshot(), original)
        self.assertEqual((external / 'work/map-packages/package/payload.bin').read_bytes(), b'package')

    def test_asset_changes_between_inventory_and_copy_are_rejected(self):
        external = self.root / 'external-resources'
        path = external / 'assets/custom-maps/map.bin'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'before')
        lock = external / 'work/map-packages/import.lock'
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b'')
        real_inventory = bridge.legacy_migration._inventory
        first = True
        def inventory(*args, **kwargs):
            nonlocal first
            result = real_inventory(*args, **kwargs)
            if first:
                first = False
                path.write_bytes(b'changed')
            return result
        with patch.object(bridge.legacy_migration, '_inventory', side_effect=inventory):
            with self.assertRaisesRegex(ValueError, 'copy-mismatch|source-changed'):
                self.prepare(external_root=external)
        self.assertEqual((external / 'assets/custom-maps/map.bin').read_bytes(), b'changed')
        self.assertFalse((self.target / '.legacy-upgrade-prepared/work').exists())

    def test_interrupted_asset_copy_resumes_same_bundle_without_touching_sources(self):
        external = self.root / 'external-resources'
        path = external / 'assets/custom-maps/map.bin'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'asset bytes')
        lock = external / 'work/map-packages/import.lock'
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b'')
        original = self.snapshot()
        rename = bridge.legacy_migration.rename_no_replace
        failed = False
        def interrupt(source, target):
            nonlocal failed
            if not failed and Path(source).name == 'map.bin.migration-copy':
                failed = True
                raise OSError('synthetic process interruption')
            return rename(source, target)
        with patch.object(bridge.legacy_migration, 'rename_no_replace', side_effect=interrupt):
            with self.assertRaises(ValueError):
                self.prepare(external_root=external)
        self.assertTrue(failed)
        result = self.prepare(external_root=external)
        self.assertEqual(result['status'], 'prepared-native-unverified')
        copied = self.target / '.legacy-upgrade-prepared/work/map-packages/legacy-unverified/asset-roots/external/assets/custom-maps/map.bin'
        self.assertEqual(copied.read_bytes(), b'asset bytes')
        self.assertEqual(self.snapshot(), original)
        self.assertEqual((external / 'assets/custom-maps/map.bin').read_bytes(), b'asset bytes')

    def test_repository_qtifw_wrapper_and_program_payload_are_distinct(self):
        program = self.source / 'program'
        program.mkdir()
        for relative in ('_internal', 'work'):
            (self.source / relative).rename(program / relative)
        original = self.snapshot()
        result = self.prepare()
        self.assertEqual(result['status'], 'prepared-native-unverified')
        prepared = self.target / '.legacy-upgrade-prepared'
        self.assertEqual((prepared / 'work/nightmare-memory/ui-settings.json').read_bytes(),
                         (program / 'work/nightmare-memory/ui-settings.json').read_bytes())
        self.assertEqual(original, self.snapshot())

    def test_current_user_shared_registry_view_is_enumerated_once(self):
        opens = []
        class Key:
            def __init__(self, hive, path):
                self.hive, self.path = hive, path
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
        def open_key(hive, path, reserved, access):
            if isinstance(hive, Key):
                return Key(hive.hive, path)
            opens.append((hive, access))
            return Key(hive, path)
        values = self.row['values']
        fixture = types.SimpleNamespace(HKEY_CURRENT_USER='user', HKEY_LOCAL_MACHINE='machine',
            KEY_READ=1, KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512, REG_SZ=1,
            OpenKey=open_key, EnumKey=lambda *_: self.row['key'],
            QueryInfoKey=lambda key: ((1 if key.hive == 'user' else 0), 0, 0)
                if key.path == bridge.UNINSTALL else (0, len(values), 0),
            QueryValueEx=lambda key, field: (values[field][1], values[field][0]),
            EnumValue=lambda key, index: (list(values)[index], values[list(values)[index]][1],
                                         values[list(values)[index]][0]))
        with patch.dict('sys.modules', {'winreg': fixture}):
            rows = bridge._registry_rows(self.source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['view'], 0)
        self.assertEqual([item for item in opens if item[0] == 'user'], [('user', 1)])

    def test_changed_target_preserves_both_sides(self):
        self.prepare()
        original = self.snapshot()
        changed = self.target / '.legacy-upgrade-prepared/work/user-routes/edited.json'
        changed.write_text('later edit')
        with self.assertRaisesRegex(ValueError, 'destination-changed'):
            self.prepare()
        self.assertEqual(changed.read_text(), 'later edit')
        self.assertEqual(self.snapshot(), original)

    def test_gallery_appearing_before_source_guard_stops_before_copy(self):
        @contextmanager
        def guard(source, tool, program):
            self.write('_internal/assets/custom-maps/new-map/manifest.json', '{}')
            with bridge._closed_source(source, tool, program) if os.name == 'nt' else nullcontext():
                yield
        with self.assertRaisesRegex(bridge.LegacyBridgeError, 'asset-root-set-changed'):
            self.prepare(source_guard=guard)
        self.assertFalse(self.target.exists())

    def test_external_root_appearing_after_copy_is_not_preparation_success(self):
        external = self.root / 'late-external'
        migrate = bridge.legacy_migration.migrate
        def copy_then_external(*args, **kwargs):
            result = migrate(*args, **kwargs)
            external.mkdir()
            return result
        with patch.object(bridge.legacy_migration, 'migrate', side_effect=copy_then_external):
            with self.assertRaisesRegex(bridge.LegacyBridgeError, 'asset-root-set-changed'):
                self.prepare(external_root=external)
        self.assertTrue((self.target / '.legacy-upgrade-prepared/legacy-migration.json').is_file())
        self.assertTrue((self.source / 'work/user-routes/edited.json').is_file())

    def test_native_probe_change_after_copy_is_not_success(self):
        original = self.snapshot()
        count = 0
        def changing_rows(source):
            nonlocal count
            count += 1
            row = self.rows(source)[0]
            if count == 3:
                row['values']['Publisher'] = [1, 'later edit']
            return [row]
        with self.assertRaisesRegex(bridge.LegacyBridgeError, 'source-identity-changed'):
            self.prepare(registry_reader=changing_rows)
        self.assertEqual(self.snapshot(), original)
        self.assertTrue((self.target / '.legacy-upgrade-prepared/legacy-migration.json').exists())

    def test_inno_root_is_routed_to_existing_preserve_upgrade(self):
        self.write('unins001.exe', 'synthetic uninstaller; never run')
        self.row['key'] = bridge.INNO_KEY
        self.row['values']['UninstallString'] = [1, '"' + str(self.source / 'unins001.exe') + '"']
        with self.assertRaisesRegex(bridge.LegacyBridgeError, 'inno-upgrade-must-use-registered-root'):
            self.prepare()
        self.assertFalse(self.target.exists())

    def test_overlapping_roots_never_mutate_source(self):
        original = self.snapshot()
        for target in (self.source, self.source / 'child', self.root):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'overlapping'):
                bridge.prepare(self.source, target, registry_reader=self.rows)
        self.assertEqual(self.snapshot(), original)

    def test_preparation_junction_cannot_redirect_copy_outside_selected_target(self):
        self.target.mkdir()
        outside = self.root / 'outside-selected-target'
        outside.mkdir()
        junction = self.target / '.legacy-upgrade-prepared'
        if os.name == 'nt':
            completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        else:
            junction.symlink_to(outside, target_is_directory=True)
        original = self.snapshot()
        with self.assertRaises(ValueError):
            self.prepare()
        # Also exercise the standalone core; resolving first used to hide this
        # exact reparse boundary before any destination check or mkdir.
        identity, _ = bridge.probe_source(self.source, registry_reader=self.rows)
        with self.assertRaises(ValueError):
            bridge.legacy_migration.migrate(self.source, junction, identity=identity)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.snapshot(), original)

    @unittest.skipUnless(os.name == 'nt', 'real Win32 image handles')
    def test_current_reset_helper_dispatches_copy_and_reads_independent_receipt(self):
        result_dir = self.root / 'result'
        result_dir.mkdir()
        with patch('installer_uninstall._baked_profile', return_value=self.profile), \
             patch.object(bridge, '_registry_rows', side_effect=self.rows), \
             patch('installer_native_observation.WindowsReadOnlyBackend._known_folder_path',
                   return_value=str(self.root / 'absent-localappdata')):
            code = star_map_reset_helper.main(['legacy-prepare', '--source', str(self.source),
                '--target', str(self.target), '--result-directory', str(result_dir)])
        self.assertEqual(code, 0)
        receipt = configparser.ConfigParser()
        receipt.read(result_dir / 'result.ini', encoding='utf-16')
        self.assertEqual(receipt['result']['status'], 'prepared-native-unverified')
        self.assertEqual(receipt['result']['nativeInstalled'], 'False')

    def test_result_overlap_never_writes_source(self):
        empty = self.source / 'empty'
        empty.mkdir()
        original = self.snapshot()
        self.assertEqual(bridge.main(['--source', str(self.source), '--target', str(self.target),
                                     '--result-directory', str(empty)]), 2)
        self.assertEqual(self.snapshot(), original)


if __name__ == '__main__':
    unittest.main()
