"""Production asset/parser/helper routes across retirement and uninstall.

Temporary Windows objects, journals, byte publication and deletion are real.
Registration, package, native observation and parent identity are narrow
fixtures. No historical image, product Setup or user data is executed.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import installer_legacy_handoff as handoff
import installer_native_observation as native
import installer_resource_lease as lease_api
import installer_uninstall as uninstall
import installer_upgrade_coordinator as coordinator
import star_map_reset_helper as helper
import test_installer_legacy_retirement as legacy
import test_installer_uninstall as uninstall_tests
import test_installer_uninstall_recovery as recovery_tests
from installer_upgrade_journal import PENDING, read_status
from test_installer_native_observation import SID


def digest(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(os.name == 'nt', 'actual Windows handles and object deletion')
class FinalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.case = legacy.LegacyRetirementTests('runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case._make_parser_owned_qtifw_layout()
        for relative, data in (('work/map-packages/import.lock', b''),
                               ('assets/custom-maps/library.bin', b'external private library')):
            path = self.case.external / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.recovery = recovery_tests.InstallerUninstallRecoveryTests('runTest')
        self.recovery.setUp()
        self.addCleanup(self.recovery.doCleanups)
        case = self.case
        # Put uninstall metadata in the native inventory BEFORE preparation.
        profile = json.dumps(case.profile).encode('utf-8')
        case.payload.update({
            'installation-profile.json': profile,
            '_internal/installation-profile.json': profile,
            'star-map-uninstall-helper.exe': b'synthetic helper; never execute',
            '_star_map_uninstall_runtime/installation-profile.json': profile,
            '_star_map_uninstall_runtime/python313.dll': b'synthetic runtime',
        })
        rows = [digest(case.payload[name]) + ' ' + name.replace('/', '\\')
                for name in recovery_tests._HELPER_FILES]
        case.payload['_uninstall-helper-files.txt'] = ('\n'.join(rows) + '\n').encode()
        case.ownership = ('\n'.join(sorted(case.payload)) + '\n').encode()
        case.payload['_installer-owned-files.txt'] = case.ownership
        inventory = dict(schema='xingtu-installer-program-inventory-v1',
            sourceCommit=case.identity['sourceCommit'], ownershipSha256=digest(case.ownership),
            ownershipBase64=base64.b64encode(case.ownership).decode(),
            files={name: dict(bytes=len(data), sha256=digest(data))
                   for name, data in case.payload.items()})
        raw = json.dumps(inventory).encode()
        Path(case.args.program_inventory).write_bytes(raw)
        case.args.program_inventory_sha256 = digest(raw)
        self.counter = 0

    @contextmanager
    def routes(self):
        case = self.case
        original_json, real_handoff = helper._json, handoff.LegacyHandoff
        real_coordinator = coordinator.Coordinator

        def packaged_json(path):
            if Path(path).name == 'installation-profile.json':
                return dict(case.profile, sourceCommit=case.identity['sourceCommit'])
            return original_json(path)

        def legacy_factory(args, target, lease, owner):
            return real_handoff(args, target, lease, owner, assets=case.assets,
                observer=case.observer, registry_reader=case.rows,
                retirement=case.retirement)

        def current_factory(args, api, target, lease, owner):
            return real_coordinator(args, api, target, lease, owner,
                observer=case.observer)

        def parent_handle(root, **_identity):
            # Replace only cross-process parent verification; retain a real
            # share-zero lock, runtime lease and validated pending permit.
            lock = Path(root) / 'work/map-packages/import.lock'
            lock.parent.mkdir(parents=True, exist_ok=True)
            if not lock.exists():
                lock.write_bytes(b'')
            api = uninstall._kernel()
            handle, _ = uninstall._open_handle(api, lock, directory=False, read=True, share=0)
            return lease_api._HandleOwner(api, handle)

        with patch.object(helper, '_json', side_effect=packaged_json), \
             patch.object(lease_api, '_duplicate_verified', side_effect=parent_handle), \
             patch.object(native, 'current_user_sid', return_value=SID), \
             patch.object(uninstall, '_owner_sid', return_value=SID), \
             patch.object(uninstall, '_current_user_sid', return_value=SID), \
             patch.object(uninstall, '_baked_profile', return_value=case.profile), \
             patch.object(handoff, 'LegacyHandoff', side_effect=legacy_factory), \
             patch.object(coordinator, 'Coordinator', side_effect=current_factory):
            yield

    def call(self, action, *, expected=0):
        case = self.case
        self.counter += 1
        result_dir = case.root / ('final-helper-' + str(self.counter))
        result_dir.mkdir()
        case.args.action, case.args.result_directory = action, str(result_dir)
        argv = case.command()
        if hasattr(case.args, 'resource_action'):
            argv += ['--resource-action', case.args.resource_action]
        with self.routes():
            code = helper.main(argv, maintenance_provider=self.recovery._maintenance_provider)
        error = result_dir / 'error.txt'
        self.assertEqual(code, expected, error.read_text('utf-8-sig') if error.exists() else '')
        if expected == 3:
            self.assertFalse((result_dir / 'result.ini').exists())
            return
        import configparser
        receipt = configparser.ConfigParser()
        receipt.read(result_dir / 'result.ini', encoding='utf-16')
        if receipt.has_option('result', 'planSha256'):
            case.args.plan_sha256 = receipt['result']['planSha256']
        return receipt['result']

    def committed(self):
        self.call('legacy-prepare')
        self.call('legacy-start-native')
        # The native installer is the replaced boundary; byte consumers are real.
        for name in self.case.payload:
            (self.case.target / name).parent.mkdir(parents=True, exist_ok=True)
        self.case.install_native_fixture()
        self.call('legacy-native-complete')
        self.assertEqual(self.call('legacy-commit')['upgradeComplete'], 'False')

    def assert_retired(self):
        case = self.case
        self.assertFalse(case.source.exists())
        self.assertFalse(case.registration.present)
        for relative in (PENDING, '.legacy-upgrade-intent.json', '.legacy-upgrade-prepared'):
            self.assertFalse((case.target / relative).exists(), relative)
        self.assertEqual(read_status(case.target, owner_sid=SID)['status'], 'not-needed')

    def uninstall(self, *, expected=0, recover=False):
        case, fx = self.case, self.recovery
        registry = uninstall_tests._RegistryFixture(case.target, sid=fx.fx.sid)
        registry.rows[0]['uninstall_string'] = '"' + str(case.target / 'unins017.exe') + '"'
        store = recovery_tests._RecoveryStoreFixture(registry)
        inventory_sha = digest((case.target / '_uninstall-helper-files.txt').read_bytes())
        result = fx._make_result('final-uninstall')
        local = case.external.parent.parent
        with patch.dict(os.environ, {'LOCALAPPDATA': str(local)}):
            code = fx._invoke(case.target, result, registry, case.profile, store, inventory_sha,
                              parent_digest=digest((case.target / 'unins017.exe').read_bytes()))
        error = result / 'error.txt'
        self.assertEqual(code, expected, error.read_text('utf-8-sig') if error.exists() else '')
        if expected:
            self.assertEqual(fx.fx._receipt(result)['mutationStarted'], 'False')
            self.assertEqual(store.values, {})
            self.assertIn('unknown-install-root-entry', error.read_text('utf-8-sig'))
            return
        self.assertEqual(json.loads(next(iter(store.values.values())))['phase'], 'native-ready')
        self.assertFalse((case.target / 'work/installer-upgrades').exists())
        self.assertFalse((case.target / 'work/user-routes/edited.json').exists())
        if recover:
            # Only the parent's known native finalization is simulated here.
            for relative in ('work/map-packages/import.lock', 'unins017.exe', 'unins017.dat'):
                (case.target / relative).unlink()
            for relative in ('work/map-packages', 'work'):
                (case.target / relative).rmdir()
            registry.rows.clear()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(local)}), \
                 patch.object(handoff.LegacyHandoff, 'execute',
                              side_effect=AssertionError('recovery entered legacy install')):
                code, error, _result, _lease = fx._recover(
                    case.target, registry, case.profile, store, inventory_sha, name='final-recover')
            self.assertEqual(code, 0, error)
            self.assertEqual(store.values, {})
            self.assertFalse(case.target.exists())

    def test_retirement_then_preserve_upgrade_and_full_uninstall_recovery(self):
        self.committed()
        result = self.call('legacy-retire')
        for name in ('upgradeComplete', 'oldEntryRetired', 'installerFinalized'):
            self.assertEqual(result[name], 'True')
        self.assert_retired()
        self.assertEqual(self.call('legacy-status')['upgradeComplete'], 'True')
        case = self.case
        case.args.session_id = str(uuid.uuid4())
        case.args.plan_sha256 = case.args.program_inventory_sha256
        case.args.resource_action = 'preserve'
        case.args.candidate_directory = case.args.transaction_id = None
        for action in ('prepare', 'start-native', 'native-complete', 'commit'):
            result = self.call(action)
        self.assertEqual(result['installerFinalized'], 'True')
        self.assertFalse((case.target / PENDING).exists())
        self.uninstall(recover=True)

    def test_unretired_full_clear_never_consumes_migration_evidence(self):
        self.committed()
        before = self.recovery._snapshot_tree(self.case.target)
        self.uninstall(expected=2)
        self.assertEqual(self.recovery._snapshot_tree(self.case.target), before)
        self.assertTrue(self.case.registration.present)
        self.assertTrue((self.case.target / PENDING).exists())

    def test_lost_final_receipt_resumes_without_old_registration_or_source(self):
        self.committed()
        real_write = helper._write_new

        def lose_receipt(path, data):
            if Path(path).name == 'result.ini':
                raise OSError('synthetic loss after final journal completion')
            return real_write(path, data)

        with patch.object(helper, '_write_new', side_effect=lose_receipt):
            self.call('legacy-retire', expected=3)
        self.assert_retired()
        with patch.object(handoff.bridge, 'probe_source', side_effect=AssertionError('old reprobe')):
            self.assertEqual(self.call('legacy-resume')['upgradeComplete'], 'True')
        before = self.recovery._snapshot_tree(self.case.target)
        with patch.object(handoff, '_write_new', side_effect=AssertionError('status wrote business data')):
            self.assertEqual(self.call('legacy-status')['upgradeComplete'], 'True')
        self.assertEqual(self.recovery._snapshot_tree(self.case.target), before)


@unittest.skipUnless(os.name == 'nt', 'actual Windows hard-link objects')
class JournalPinCleanupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work/tmp')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root = self.directory / 'app'
        history = self.root / 'work/installer-upgrades' / str(uuid.uuid4())
        self.paths = [history / part / (str(uuid.uuid4()) + '.json')
                      for part in ('objects', 'pins', 'generations')]
        for path in self.paths:
            path.parent.mkdir(parents=True)
        self.paths[0].write_bytes(b'owned journal-shaped fixture')
        for path in self.paths[1:]:
            os.link(self.paths[0], path)
        self.outside = self.directory / 'external.json'

    @contextmanager
    def plan(self):
        plan = uninstall._TreePlan(uninstall._kernel(), self.root, mode='app',
            owned=('_star_map_uninstall_runtime/installation-profile.json',))
        try:
            yield plan.scan(exe_name='星图.exe')
        finally:
            plan.close()

    def test_complete_owned_alias_group_is_removed_through_original_handles(self):
        with self.plan() as plan:
            plan.delete_files(None)
            self.assertTrue(all(not path.exists() for path in self.paths))
            plan.delete_directories(None)

    def test_external_alias_is_rejected_before_mutation(self):
        os.link(self.paths[0], self.outside)
        with self.assertRaisesRegex(uninstall.UninstallError, 'hard-link-refused'):
            with self.plan():
                self.fail('outside alias admitted')
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertEqual(self.outside.read_bytes(), b'owned journal-shaped fixture')

    def test_internal_links_outside_journal_namespace_remain_rejected(self):
        ordinary = [self.root / 'work' / ('ordinary-' + str(index) + '.json')
                    for index in range(len(self.paths))]
        for source, target in zip(self.paths, ordinary):
            source.rename(target)
        with self.assertRaisesRegex(uninstall.UninstallError, 'hard-link-refused'):
            with self.plan():
                self.fail('ordinary data alias admitted')
        self.assertTrue(all(path.exists() for path in ordinary))

    def test_new_external_alias_after_scan_is_rejected_before_mutation(self):
        with self.plan() as plan:
            os.link(self.paths[0], self.outside)
            with self.assertRaisesRegex(uninstall.UninstallError, 'hard-link-refused'):
                plan.delete_files(lambda: self.fail('mutation began'))
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertTrue(self.outside.exists())

    def test_same_byte_alias_replacement_after_scan_is_rejected(self):
        data = self.paths[0].read_bytes()
        with self.plan() as plan:
            self.paths[0].rename(self.outside)
            self.paths[0].write_bytes(data)
            with self.assertRaisesRegex(uninstall.UninstallError, 'hard-link-refused'):
                plan.delete_files(lambda: self.fail('mutation began'))
        self.assertTrue(all(path.exists() for path in self.paths))
        self.assertEqual(self.outside.read_bytes(), data)

    def test_interruption_after_first_alias_deletion_retries_remaining_group(self):
        real = uninstall._mark_delete
        removed = []

        def lose_receipt(*args, **kwargs):
            real(*args, **kwargs)
            removed.append(args[2])
            raise OSError('lost one journal unlink receipt')

        with self.assertRaisesRegex(OSError, 'lost one journal'), self.plan() as plan, \
             patch.object(uninstall, '_mark_delete', side_effect=lose_receipt):
            plan.delete_files(None)
        self.assertEqual(len(removed), 1)
        self.assertEqual(sum(path.exists() for path in self.paths), 2)
        with self.plan() as plan:
            plan.delete_files(None)
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_rename_outside_after_last_path_read_is_denied_by_delete_handle(self):
        real_mark, real_path = uninstall._mark_delete, uninstall._handle_path
        attempts = []

        def delete_with_rename_attempt(api, handle, relative, **kwargs):
            def read_then_rename(current_api, current_handle):
                value = real_path(current_api, current_handle)
                if current_handle == handle and not attempts:
                    # Attempt the exact unsafe ordering: the final path check
                    # already returned its old value, but disposition has not run.
                    with self.assertRaises(OSError):
                        (self.root / relative).rename(self.outside)
                    attempts.append(relative)
                return value
            with patch.object(uninstall, '_handle_path', side_effect=read_then_rename):
                return real_mark(api, handle, relative, **kwargs)

        with self.plan() as plan, patch.object(uninstall, '_mark_delete',
                                               side_effect=delete_with_rename_attempt):
            plan.delete_files(None)
        self.assertEqual(len(attempts), 1)
        self.assertFalse(self.outside.exists())
        self.assertTrue(all(not path.exists() for path in self.paths))


if __name__ == '__main__':
    unittest.main()
