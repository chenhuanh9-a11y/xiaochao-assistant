"""Real temporary files/journal/native verifier; narrow registry/lease fixtures.

No product installation, registry write, historical EXE or real user data.
The task-3 asset adapter is explicitly a fixture limited to local work files.
"""
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from types import SimpleNamespace
import base64
import configparser
import copy
import hashlib
import json
import os
import unittest
from unittest.mock import patch
import uuid

import installer_legacy_bridge as bridge
import installer_legacy_handoff as handoff
import installer_native_observation as native
from installer_upgrade_journal import PENDING, read_status
from resource_transaction import rename_no_replace
import star_map_reset_helper as helper
import test_installer_legacy_bridge as bridge_fixtures
from test_installer_native_observation import OwnedFixtureBackend, SID, link_bytes, sz


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LocalNativeFixture(OwnedFixtureBackend):
    """Existing native verifier's file backend at this test's actual paths."""
    def local(self, path):
        native._path(str(path))
        result = Path(path)
        if not result.is_relative_to(self.directory_path):
            raise AssertionError('fixture escaped its temporary root')
        return result

    def shortcut_locations(self):
        return dict(programs=str(self.directory_path / 'Programs'),
                    desktop=str(self.directory_path / 'Desktop'))


class WorkFilesFixture:
    """NOT a production multi-root adapter. Real copy/move and fault injection."""
    def __init__(self, source, rows):
        self.source, self.rows = source, rows
        self.commits = 0
        self.fail_after = None
        self.lose_receipt = False
        self.bindings = []

    def prepare(self, source, prepared, **bindings):
        self.bindings.append(bindings)
        identity, _ = bridge.probe_source(source, registry_reader=self.rows)
        bridge.legacy_migration.migrate(source, prepared, identity=identity)
        return dict(fixtureOnly=True, files=bridge.legacy_migration._inventory(source))

    def verify_prepared(self, manifest, source, prepared, **bindings):
        return (manifest['files'] == bridge.legacy_migration._inventory(source)
                == bridge.legacy_migration._inventory(prepared))

    def commit(self, manifest, prepared, root, **bindings):
        self.commits += 1
        for index, (relative, expected) in enumerate(manifest['files'].items(), 1):
            before, after = prepared / 'work' / relative, root / 'work' / relative
            if after.exists():
                if bridge.legacy_migration._file(after) != expected:
                    raise ValueError('fixture-unknown-destination')
                continue
            if bridge.legacy_migration._file(before) != expected:
                raise ValueError('fixture-staged-data-changed')
            after.parent.mkdir(parents=True, exist_ok=True)
            rename_no_replace(before, after)
            if self.fail_after == index:
                raise OSError('fixture-interrupted-asset-commit')
        if self.lose_receipt:
            raise OSError('fixture-lost-commit-receipt')

    def verify_committed(self, manifest, root, **bindings):
        return all((root / 'work' / name).is_file()
                   and bridge.legacy_migration._file(root / 'work' / name) == row
                   for name, row in manifest['files'].items())


@unittest.skipUnless(os.name == 'nt', 'native verifier requires Windows local paths')
class HandoffTests(unittest.TestCase):
    write = bridge_fixtures.LegacyBridgeTests.write
    rows = bridge_fixtures.LegacyBridgeTests.rows
    snapshot = bridge_fixtures.LegacyBridgeTests.snapshot

    def setUp(self):
        bridge_fixtures.LegacyBridgeTests.setUp(self)
        self.target.mkdir()
        self.original = self.snapshot()
        self.lease = object()
        self.assets = WorkFilesFixture(self.source, self.rows)
        self.identity = dict(sourceCommit='a' * 40, displayVersion='1.2.0', numericVersion='1.2.0.0',
            executableSha256=sha(b'synthetic new program; never execute'), resourceInventorySha256='b' * 64)
        self.report = json.dumps(dict(self.identity, sourceDirty=False)).encode()
        self.ownership = '星图.exe\nrelease-report.json\n_installer-owned-files.txt\n'.encode()
        self.payload = {'星图.exe': b'synthetic new program; never execute',
                        'release-report.json': self.report, '_installer-owned-files.txt': self.ownership}
        inventory = dict(schema='xingtu-installer-program-inventory-v1', sourceCommit=self.identity['sourceCommit'],
            ownershipSha256=sha(self.ownership), ownershipBase64=base64.b64encode(self.ownership).decode(),
            files={name: dict(bytes=len(data), sha256=sha(data)) for name, data in self.payload.items()})
        inventory_path = self.root / 'current-inventory.json'
        inventory_path.write_bytes(json.dumps(inventory).encode())
        report_path = self.root / 'current-report.json'
        report_path.write_bytes(self.report)
        self.args = SimpleNamespace(action='legacy-prepare', install_root=str(self.target),
            legacy_source=str(self.source), session_id=str(uuid.uuid4()), confirmed=True,
            target_report=str(report_path), target_report_sha256=sha(self.report), plan_sha256=None,
            program_inventory=str(inventory_path), program_inventory_sha256=sha(inventory_path.read_bytes()),
            restore_watcher=False, uninstall_exe=None,
            group_shortcut=str(self.root / 'Programs/Xingtu/星图.lnk'),
            uninstall_shortcut=str(self.root / 'Programs/Xingtu/卸载星图.lnk'),
            desktop_shortcut=str(self.root / 'Desktop/星图.lnk'), group_enabled=True, desktop_enabled=False,
            source_commit=self.identity['sourceCommit'], display_version='1.2.0', numeric_version='1.2.0.0',
            parent_pid=os.getpid(), parent_created='1', parent_executable=str(self.root / 'fake-parent.exe'),
            parent_sha256='c' * 64, lock_handle=1)
        self.backend = LocalNativeFixture(self.root)
        self.observer = SimpleNamespace(preflight_native_evidence=native.preflight_native_evidence,
            capture_native_completion=partial(native.capture_native_completion, _backend=self.backend),
            verify_native_completion=partial(native.verify_native_completion, _backend=self.backend),
            resume_context_from_evidence=partial(native.resume_context_from_evidence, _backend=self.backend))

    def coordinator(self, **kwargs):
        options = dict(assets=self.assets, observer=self.observer, registry_reader=self.rows,
                       scope=lambda *_a, **_k: nullcontext())
        options.update(kwargs)
        return handoff.LegacyHandoff(self.args, self.identity, self.lease, SID, **options)

    def execute(self, action, **kwargs):
        self.args.action = 'legacy-' + action
        result = self.coordinator(**kwargs).execute()
        self.args.plan_sha256 = result['planSha256']
        return result

    def install_native_fixture(self):
        for name, data in self.payload.items():
            (self.target / name).write_bytes(data)
        self.args.uninstall_exe = str(self.target / 'unins017.exe')
        (self.target / 'unins017.exe').write_bytes(b'synthetic uninstaller; never execute')
        (self.target / 'unins017.dat').write_bytes(b'synthetic native log')
        self.backend.registry_value['values'] = [sz('InstallLocation', str(self.target) + '\\'),
            sz('DisplayVersion', self.args.display_version),
            sz('UninstallString', '"' + self.args.uninstall_exe + '"')]
        for name, target in ((self.args.group_shortcut, self.target / '星图.exe'),
                             (self.args.uninstall_shortcut, self.args.uninstall_exe)):
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(link_bytes(str(target)))

    def native_complete(self):
        self.execute('prepare')
        self.execute('start-native')
        self.install_native_fixture()
        self.execute('native-complete')

    def test_real_files_reach_verified_new_install_but_never_full_retirement(self):
        self.native_complete()
        result = self.execute('commit')
        self.assertEqual(result['status'], 'new-install-verified-old-entry-pending')
        self.assertTrue(result['nativeInstalled'])
        self.assertTrue(result['migrationCommitted'])
        for key in ('installerFinalized', 'upgradeComplete', 'sourceDeleted', 'oldEntryRetired'):
            self.assertFalse(result[key])
        self.assertTrue((self.target / PENDING).exists())
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual(self.execute('resume'), result)
        self.assertEqual(self.assets.commits, 1)
        record = self.target / 'work/installer-upgrades' / self.args.session_id / 'legacy-commit.json'
        self.assertEqual(json.loads(record.read_bytes())['phase'], 'old-entry-pending')

    def test_default_factory_is_the_complete_production_adapter(self):
        from installer_legacy_assets import CompleteAssetTransfer
        self.assertIsInstance(handoff.complete_asset_transfer(), CompleteAssetTransfer)

    def test_unknown_nonempty_target_is_not_adopted(self):
        (self.target / 'personal.txt').write_text('keep me')
        with self.assertRaisesRegex(ValueError, 'fresh-target-required'):
            self.execute('prepare')
        self.assertEqual(self.assets.bindings, [])
        self.assertEqual((self.target / 'personal.txt').read_text(), 'keep me')

    def test_old_unbound_preparation_is_not_authority(self):
        self.prepared = self.target / '.legacy-upgrade-prepared'
        self.prepared.mkdir()
        with self.assertRaisesRegex(ValueError, 'fresh-target-required'):
            self.execute('prepare')

    def test_prepare_and_lost_prepare_response_use_one_bound_transaction(self):
        result = self.execute('prepare')
        self.args.plan_sha256 = None  # lost public receipt, same actual session
        self.assertEqual(self.execute('prepare'), result)
        self.assertEqual(len(self.assets.bindings), 1)
        binding = self.assets.bindings[0]
        self.assertIs(binding['lease'], self.lease)
        self.assertEqual(binding['owner_sid'], SID)
        self.assertEqual(binding['session_id'], self.args.session_id)
        self.assertEqual(binding['target_identity'], self.identity)

    def test_out_of_order_and_caller_claim_never_create_native_success(self):
        self.execute('prepare')
        for action in ('native-complete', 'commit', 'resume'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.execute(action)
        self.assertEqual(read_status(self.target, owner_sid=SID)['status'], 'prepared')
        self.assertEqual(self.assets.commits, 0)

    def test_native_start_is_not_replayed_or_rolled_back(self):
        self.execute('prepare')
        self.execute('start-native')
        for action in ('prepare', 'start-native', 'cancel'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.execute(action)
        with self.assertRaises(ValueError):
            self.execute('native-complete')  # no files or native registration
        self.assertEqual(read_status(self.target, owner_sid=SID)['status'], 'native-in-progress')
        self.assertEqual(self.snapshot(), self.original)

    def test_unbound_session_owner_target_and_plan_cannot_resume(self):
        self.execute('prepare')
        for field, value in (('session_id', str(uuid.uuid4())), ('plan_sha256', 'f' * 64),
                             ('program_inventory_sha256', 'f' * 64)):
            with self.subTest(field=field):
                old = getattr(self.args, field)
                setattr(self.args, field, value)
                with self.assertRaises(ValueError):
                    self.execute('start-native')
                setattr(self.args, field, old)
        coordinator = self.coordinator()
        coordinator.owner = 'S-1-5-21-4-5-6-1000'
        with self.assertRaises(ValueError):
            coordinator.execute()

    def test_changed_staging_or_source_cannot_start(self):
        self.execute('prepare')
        staged = self.target / '.legacy-upgrade-prepared/work/user-routes/edited.json'
        staged.write_text('changed')
        with self.assertRaises(ValueError):
            self.execute('start-native')
        self.assertEqual(self.snapshot(), self.original)

    def test_wrong_registry_type_shortcut_and_program_are_rejected_by_real_observer(self):
        self.execute('prepare')
        self.execute('start-native')
        self.install_native_fixture()
        original = copy.deepcopy(self.backend.registry_value)
        self.backend.registry_value['values'][0]['type'] = 2
        with self.assertRaises(ValueError):
            self.execute('native-complete')
        self.backend.registry_value = original
        link = Path(self.args.group_shortcut)
        correct = link.read_bytes()
        link.write_bytes(link_bytes(str(self.target / 'other.exe')))
        with self.assertRaises(ValueError):
            self.execute('native-complete')
        link.write_bytes(correct)
        program = self.target / '星图.exe'
        program.write_bytes(b'different program')
        with self.assertRaises(ValueError):
            self.execute('native-complete')
        self.assertEqual(read_status(self.target, owner_sid=SID)['status'], 'native-in-progress')

    def test_lost_native_receipt_recaptures_nothing_and_verifies_old_evidence(self):
        self.native_complete()
        with patch.object(self.observer, 'capture_native_completion', side_effect=AssertionError('recapture')):
            self.assertTrue(self.execute('native-complete')['nativeInstalled'])
        (self.target / 'unins017.dat').write_bytes(b'changed after native receipt')
        with self.assertRaises(ValueError):
            self.execute('commit')
        self.assertEqual(self.assets.commits, 0)

    def test_interrupted_partial_asset_commit_reuses_native_evidence(self):
        self.native_complete()
        self.assets.fail_after = 1
        with self.assertRaisesRegex(OSError, 'interrupted-asset-commit'):
            self.execute('commit')
        self.assertEqual(read_status(self.target, owner_sid=SID)['status'], 'native-complete')
        self.assertFalse((self.target / 'work/installer-upgrades' / self.args.session_id / 'legacy-commit.json').exists())
        self.assets.fail_after = None
        with patch.object(self.observer, 'capture_native_completion', side_effect=AssertionError('native replay')):
            self.assertTrue(self.execute('resume')['migrationCommitted'])
        self.assertEqual(self.snapshot(), self.original)

    def test_lost_asset_commit_response_verifies_files_without_duplicate_publication(self):
        self.native_complete()
        self.assets.lose_receipt = True
        with self.assertRaisesRegex(OSError, 'lost-commit-receipt'):
            self.execute('commit')
        self.assertTrue(self.execute('resume')['migrationCommitted'])
        self.assertEqual(self.assets.commits, 1)

    def test_unverified_commit_never_writes_completed_record(self):
        self.native_complete()
        with patch.object(self.assets, 'verify_committed', return_value=False), \
             patch.object(self.assets, 'commit', return_value={'status': 'success'}):
            with self.assertRaisesRegex(ValueError, 'legacy-asset-commit-unverified'):
                self.execute('commit')
        self.assertTrue((self.target / PENDING).exists())

    def test_pre_native_cancel_is_terminal_and_preserves_original_and_copies(self):
        self.execute('prepare')
        result = self.execute('cancel')
        self.assertEqual(result['status'], 'aborted')
        self.assertEqual(self.execute('cancel'), result)
        self.assertFalse((self.target / PENDING).exists())
        self.assertTrue((self.target / '.legacy-upgrade-prepared/work/user-routes/edited.json').exists())
        self.assertEqual(self.snapshot(), self.original)
        with self.assertRaises(ValueError):
            self.execute('start-native')

    def test_deadline_after_transfer_is_not_preparation_success(self):
        coordinator = self.coordinator()
        prepare = self.assets.prepare
        def expired(*args, **kwargs):
            manifest = prepare(*args, **kwargs)
            coordinator.deadline = 0
            return manifest
        with patch.object(self.assets, 'prepare', side_effect=expired):
            with self.assertRaisesRegex(ValueError, 'deadline-exceeded'):
                coordinator.execute()
        self.assertFalse((self.target / PENDING).exists())
        self.assertTrue((self.target / '.legacy-upgrade-prepared/work/user-routes/edited.json').exists())
        self.assertEqual(self.snapshot(), self.original)

    def test_expired_operation_never_starts_copy(self):
        coordinator = self.coordinator()
        coordinator.deadline = 0
        with self.assertRaisesRegex(ValueError, 'deadline-exceeded'):
            coordinator.execute()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_fixture_native_evidence_is_not_accepted_by_production_observer(self):
        self.native_complete()
        state = read_status(self.target, owner_sid=SID)
        self.assertEqual(state['nativeEvidence']['source'], 'fixture-only')
        with self.assertRaisesRegex(ValueError, 'evidence-backend-mismatch'):
            native.resume_context_from_evidence(state['nativeEvidence'], self.target)

    def test_generic_preserve_cannot_finalize_or_reset_legacy_migration(self):
        from installer_upgrade_coordinator import Coordinator
        self.native_complete()
        self.args.resource_action = 'preserve'
        for action in ('prepare', 'commit', 'rollback', 'resume-native-complete', 'start-native'):
            self.args.action = action
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'legacy-handoff-recovery-required'):
                Coordinator(self.args, object(), self.identity, self.lease, SID).execute()
        self.assertTrue((self.target / PENDING).exists())
        self.assertEqual(self.assets.commits, 0)

    def test_low_level_journal_cannot_clear_legacy_pending_without_retirement(self):
        self.native_complete()
        coordinator = self.coordinator()
        with coordinator._source() as (identity, _):
            coordinator._load_plan(identity)
            with self.assertRaisesRegex(ValueError, 'legacy-retirement-unverified'):
                coordinator.journal.finish_complete(verify_native=lambda *_: True, verify_resource=lambda *_: True)
        self.assertTrue((self.target / PENDING).exists())

    def test_missing_assets_or_truthy_verifier_does_not_start_native(self):
        self.execute('prepare')
        with patch.object(self.assets, 'verify_prepared', return_value=1):
            with self.assertRaises(ValueError):
                self.execute('start-native')
        self.assertEqual(read_status(self.target, owner_sid=SID)['status'], 'prepared')

    def test_incomplete_preparation_never_publishes_native_authority(self):
        with patch.object(self.assets, 'verify_prepared', return_value=False):
            with self.assertRaisesRegex(ValueError, 'legacy-preparation-unverified'):
                self.execute('prepare')
        self.assertFalse((self.target / PENDING).exists())
        self.assertFalse((self.target / '.legacy-upgrade-prepared/handoff-plan.json').exists())
        self.assertEqual(self.snapshot(), self.original)

    def test_native_change_during_asset_commit_is_not_success_or_rollback(self):
        self.native_complete()
        commit = self.assets.commit
        def changed(*args, **kwargs):
            commit(*args, **kwargs)
            (self.target / 'unins017.dat').write_bytes(b'changed during publication')
        with patch.object(self.assets, 'commit', side_effect=changed):
            with self.assertRaises(ValueError):
                self.execute('commit')
        self.assertEqual(read_status(self.target, owner_sid=SID)['status'], 'native-complete')
        self.assertTrue((self.target / 'work/user-routes/edited.json').exists())
        self.assertEqual(self.snapshot(), self.original)

    def command(self):
        arguments = ['--action', self.args.action]
        names = ('install-root', 'result-directory', 'target-report', 'target-report-sha256',
            'source-commit', 'display-version', 'numeric-version', 'parent-created',
            'parent-executable', 'parent-sha256', 'parent-pid', 'lock-handle', 'legacy-source',
            'plan-sha256', 'session-id', 'program-inventory', 'program-inventory-sha256',
            'uninstall-exe', 'group-shortcut', 'uninstall-shortcut', 'desktop-shortcut')
        for name in names:
            value = getattr(self.args, name.replace('-', '_'))
            if value is not None:
                arguments.extend(['--' + name, str(value)])
        for name in ('confirmed', 'group-enabled', 'desktop-enabled', 'restore-watcher'):
            if getattr(self.args, name.replace('-', '_')):
                arguments.append('--' + name)
        return arguments

    def test_actual_helper_dispatch_and_published_receipt(self):
        self.args.result_directory = str(self.root / 'helper-result')
        Path(self.args.result_directory).mkdir()
        original_json = helper._json
        real_type = handoff.LegacyHandoff
        def packaged_json(path):
            if Path(path).name == 'installation-profile.json':
                return dict(self.profile, sourceCommit=self.identity['sourceCommit'])
            return original_json(path)
        def real_coordinator(args, target, lease, owner):
            self.assertIs(lease, self.lease)
            self.assertEqual(target, self.identity)
            self.assertEqual(owner, SID)
            return real_type(args, target, lease, owner, assets=self.assets, observer=self.observer,
                registry_reader=self.rows, scope=lambda *_a, **_k: nullcontext())
        with patch.object(helper, '_json', side_effect=packaged_json), \
             patch.object(helper, 'borrowed_installer_lease', return_value=nullcontext(self.lease)) as borrowed, \
             patch.object(native, 'current_user_sid', return_value=SID), \
             patch('installer_uninstall._baked_profile', return_value=self.profile), \
             patch.object(handoff, 'LegacyHandoff', side_effect=real_coordinator):
            for index, action in enumerate(('prepare', 'start-native', 'native-complete', 'commit', 'resume')):
                if action == 'native-complete':
                    self.install_native_fixture()
                self.args.action = 'legacy-' + action
                if index:
                    self.args.result_directory = str(self.root / ('helper-result-' + str(index)))
                    Path(self.args.result_directory).mkdir()
                self.assertEqual(helper.main(self.command()), 0)
                receipt = configparser.ConfigParser()
                receipt.read(Path(self.args.result_directory) / 'result.ini', encoding='utf-16')
                self.args.plan_sha256 = receipt['result']['planSha256']
        self.assertEqual(borrowed.call_count, 5)
        receipt = configparser.ConfigParser()
        receipt.read(Path(self.args.result_directory) / 'result.ini', encoding='utf-16')
        self.assertEqual(receipt['result']['status'], 'new-install-verified-old-entry-pending')
        self.assertEqual(receipt['result']['upgradeComplete'], 'False')

    def test_real_helper_rejects_wrong_parent_before_constructing_adapter(self):
        self.args.result_directory = str(self.root / 'rejected-parent')
        Path(self.args.result_directory).mkdir()
        original_json = helper._json
        def packaged_json(path):
            if Path(path).name == 'installation-profile.json':
                return dict(self.profile, sourceCommit=self.identity['sourceCommit'])
            return original_json(path)
        with patch.object(helper, '_json', side_effect=packaged_json), \
             patch.object(handoff, 'complete_asset_transfer', side_effect=AssertionError('unleased adapter')):
            self.assertEqual(helper.run(self.args), 2)
        self.assertEqual(list(self.target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
