"""Real multi-root adapter/files/journal; only native registration is synthetic.

No installed program, registry write or fixture switch in production CLI.
"""
from pathlib import Path
from functools import partial
import copy
import json
import os
import time
import unittest
import shutil
from unittest.mock import patch

import installer_legacy_assets as assets
import installer_legacy_handoff as handoff
import installer_upgrade_journal as journal
import legacy_migration as migration
import installer_native_observation as native
import test_installer_legacy_handoff as handoff_tests


@unittest.skipUnless(os.name == 'nt', 'native paths and exclusion require Windows')
class AssetIntegrationTests(unittest.TestCase):
    write = handoff_tests.HandoffTests.write
    rows = handoff_tests.HandoffTests.rows
    snapshot = handoff_tests.HandoffTests.snapshot
    coordinator = handoff_tests.HandoffTests.coordinator
    execute = handoff_tests.HandoffTests.execute
    install_native_fixture = handoff_tests.HandoffTests.install_native_fixture
    native_complete = handoff_tests.HandoffTests.native_complete
    command = handoff_tests.HandoffTests.command

    def setUp(self):
        handoff_tests.HandoffTests.setUp(self)
        self.external = self.root / 'local/Xingtu/resources'
        self.assets = assets.CompleteAssetTransfer(external_root=self.external,
            registry_reader=self.rows, production_roots=lambda: {str(self.source), str(self.target)})
        self.observer.registered_uninstaller = partial(native.registered_uninstaller, _backend=self.backend)

    def add_roots(self):
        for relative in ('assets/custom-maps', '_internal/assets/custom-maps',
                         '_internal/qt-editor-base', 'work/imported-resources'):
            self.write(relative + '/library.bin', 'synthetic ' + relative)
        for relative, data in (('work/map-packages/import.lock', b''),
                               ('assets/custom-maps/library.bin', b'external synthetic library')):
            path = self.external / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_production_adapter_multiroot_actual_bytes_and_idempotent_commit(self):
        self.add_roots()
        original = self.snapshot()
        self.native_complete()
        result = self.execute('commit')
        self.assertTrue(result['migrationCommitted'])
        self.assertFalse(result['upgradeComplete'])
        self.assertFalse(result['resourcesActivated'])
        self.assertEqual(result['resourceStatus'], 'reimport-required')
        manifest = json.loads((self.target / '.legacy-upgrade-prepared/handoff-plan.json').read_text())['assets']
        self.assertEqual(len(manifest['assetRoots']), 5)
        for name, row in manifest['files'].items():
            self.assertEqual(migration._file(self.target / 'work' / name), row)
        self.assertEqual(self.execute('resume'), result)
        self.assertEqual(self.snapshot(), original)
        self.assertFalse((self.target / 'work/map-packages/manifest.json').exists())

    def test_commit_interrupted_after_one_move_resumes_same_inode_without_native_replay(self):
        self.native_complete()
        real = assets.rename_no_replace
        moved = []
        def interrupted(source, target):
            real(source, target)
            moved.append(target)
            if len(moved) == 1:
                raise OSError('synthetic publication interruption')
        with patch.object(assets, 'rename_no_replace', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.execute('commit')
        stamp = assets._stamp(moved[0])
        with patch.object(self.observer, 'capture_native_completion', side_effect=AssertionError('native replay')):
            self.assertTrue(self.execute('resume')['migrationCommitted'])
        self.assertEqual(assets._stamp(moved[0]), stamp)

    def test_equal_byte_target_replacement_is_not_owned(self):
        self.native_complete()
        self.execute('commit')
        path = self.target / 'work/user-routes/edited.json'
        data = path.read_bytes()
        path.rename(path.with_suffix('.retained'))
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'destination-conflict'):
            self.execute('resume')

    def test_original_source_changed_after_prepare_cannot_start_native(self):
        self.execute('prepare')
        self.write('work/user-routes/edited.json', 'changed original')
        with self.assertRaisesRegex(ValueError, 'manifest-changed'):
            self.execute('start-native')
        self.assertEqual(journal.read_status(self.target, owner_sid=self.args_owner)['status'], 'prepared')

    @property
    def args_owner(self):
        from test_installer_native_observation import SID
        return SID

    def test_shared_external_root_is_rejected_before_preparation(self):
        self.add_roots()
        self.assets.registrations = lambda: {str(self.source), str(self.root / 'another-install')}
        with self.assertRaisesRegex(ValueError, 'external-resource-root-shared'):
            self.execute('prepare')
        self.assertFalse((self.target / '.legacy-upgrade-prepared').exists())

    def test_unknown_target_inserted_after_prepare_cannot_start_native(self):
        self.execute('prepare')
        (self.target / 'unowned.txt').write_text('do not adopt')
        with self.assertRaisesRegex(ValueError, 'fresh-target-required'):
            self.execute('start-native')

    def test_unknown_target_data_after_native_is_preserved_and_not_certified(self):
        self.native_complete()
        path = self.target / 'work/nightmare-memory/unlisted.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('unknown user content')
        with self.assertRaisesRegex(ValueError, 'legacy-unlisted-target-file'):
            self.execute('commit')
        self.assertEqual(path.read_text(), 'unknown user content')

    def test_snapshots_and_dependency_payloads_stay_in_same_quarantine(self):
        self.write('work/map-packages/history/snapshot.zip', 'synthetic historical snapshot')
        self.execute('prepare')
        path = self.target / '.legacy-upgrade-prepared/work/map-packages/legacy-unverified/metadata/history/snapshot.zip'
        self.assertEqual(path.read_text(), 'synthetic historical snapshot')

    def test_existing_quarantine_never_admits_other_old_metadata_as_active(self):
        self.write('work/map-packages/legacy-unverified/retained.txt', 'old quarantined bytes')
        self.write('work/map-packages/managed-base-state.json', '{}')
        self.native_complete()
        self.execute('commit')
        active = self.target / 'work/map-packages/managed-base-state.json'
        self.assertFalse(active.exists())
        metadata = self.target / 'work/map-packages/legacy-unverified/metadata'
        self.assertEqual((metadata / 'managed-base-state.json').read_text(), '{}')
        self.assertEqual((metadata / 'legacy-unverified/retained.txt').read_text(), 'old quarantined bytes')

    def test_replaced_target_directory_cannot_reuse_an_intent(self):
        self.execute('prepare')
        retained = self.root / 'retained-target'
        self.target.rename(retained)
        self.target.mkdir()
        shutil.copy2(retained / '.legacy-upgrade-intent.json', self.target / '.legacy-upgrade-intent.json')
        with self.assertRaisesRegex(ValueError, 'legacy-plan-binding-mismatch'):
            self.execute('prepare')

    def test_plan_write_interruption_after_copy_is_reconstructible_from_intent(self):
        real = handoff._write_new
        def interrupted(path, data):
            if path.name == 'handoff-plan.json':
                raise OSError('synthetic torn private plan write')
            return real(path, data)
        with patch.object(handoff, '_write_new', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.execute('prepare')
        self.assertTrue((self.target / '.legacy-upgrade-prepared/work/user-routes/edited.json').exists())
        self.assertEqual(self.execute('prepare')['status'], 'prepared')

    def test_torn_genesis_before_directory_publication_is_not_adopted(self):
        real = journal._write_new
        def interrupted(path, data):
            if path.name == 'anchor.json':
                path.write_bytes(b'{torn')
                raise OSError('synthetic torn private genesis')
            return real(path, data)
        with patch.object(journal, '_write_new', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.execute('prepare')
        self.assertEqual(self.execute('prepare')['status'], 'prepared')
        self.assertTrue(any(p.name.startswith('.initializing-') for p in (self.target / 'work/installer-upgrades').iterdir()))

    def test_lost_genesis_pending_publication_recovers_verified_first_generation(self):
        real = journal.os.link
        def interrupted(source, target):
            if Path(target) == self.target / journal.PENDING:
                raise OSError('synthetic lost pointer publication')
            return real(source, target)
        with patch.object(journal.os, 'link', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.execute('prepare')
        self.assertEqual(self.execute('prepare')['status'], 'prepared')

    def test_lost_native_receipt_resume_captures_full_current_native_state(self):
        self.execute('prepare')
        self.execute('start-native')
        self.install_native_fixture()
        self.args.uninstall_exe = None
        self.assertTrue(self.execute('resume')['migrationCommitted'])

    def test_fake_native_registry_or_missing_program_is_not_recovered(self):
        self.execute('prepare')
        self.execute('start-native')
        self.install_native_fixture()
        (self.target / '星图.exe').write_bytes(b'wrong native bytes')
        with self.assertRaises(ValueError):
            self.execute('resume')

    def test_expired_production_copy_stops_at_chunk_checkpoint(self):
        self.execute('prepare')
        coordinator = self.coordinator()
        coordinator.deadline = time.monotonic() + 0.01
        real = migration._file
        def slow(path):
            time.sleep(0.02)
            return real(path)
        with patch.object(migration, '_file', side_effect=slow):
            with self.assertRaisesRegex(ValueError, 'deadline-exceeded'):
                coordinator.execute()

    def test_manifest_forgery_and_cross_session_are_rejected(self):
        self.execute('prepare')
        plan = self.target / '.legacy-upgrade-prepared/handoff-plan.json'
        original = journal._parse(journal._read(plan)[0])
        changed = copy.deepcopy(original)
        changed['assets']['files']['../../outside'] = dict(bytes=0, sha256='a' * 64)
        plan.write_bytes(journal._bounded(changed))
        with self.assertRaises(ValueError):
            self.execute('start-native')

    def test_actual_helper_parser_factory_adapter_and_complete_receipts(self):
        from contextlib import nullcontext
        import configparser
        import star_map_reset_helper as helper
        import installer_uninstall
        self.add_roots()
        real = handoff.LegacyHandoff
        original_json = helper._json
        def packaged(path):
            if Path(path).name == 'installation-profile.json':
                return dict(self.profile, sourceCommit=self.identity['sourceCommit'])
            return original_json(path)
        def factory(args, target, lease, owner):
            # The production factory is used. Only native observations/lease
            # are narrow fixtures; copy/publication/verifiers remain actual.
            return real(args, target, lease, owner, observer=self.observer,
                        registry_reader=self.rows, scope=lambda *_a, **_k: nullcontext())
        for index, action in enumerate(('prepare', 'start-native', 'native-complete', 'commit')):
            if action == 'native-complete':
                self.install_native_fixture()
            self.args.action = 'legacy-' + action
            self.args.result_directory = str(self.root / ('actual-helper-' + str(index)))
            Path(self.args.result_directory).mkdir()
            with patch.object(helper, '_json', side_effect=packaged), \
                 patch.object(helper, 'borrowed_installer_lease', return_value=nullcontext(self.lease)), \
                 patch.object(native, 'current_user_sid', return_value=self.args_owner), \
                 patch.object(installer_uninstall, '_baked_profile', return_value=self.profile), \
                 patch.object(handoff, 'LegacyHandoff', side_effect=factory), \
                 patch.object(handoff, 'complete_asset_transfer', return_value=self.assets):
                self.assertEqual(helper.main(self.command()), 0)
            receipt = configparser.ConfigParser()
            receipt.read(Path(self.args.result_directory) / 'result.ini', encoding='utf-16')
            self.args.plan_sha256 = receipt['result']['planSha256']
            self.assertEqual(receipt['result']['ownerSid'], self.args_owner)
            self.assertEqual(receipt['result']['parentPid'], str(os.getpid()))
            self.assertEqual(receipt['result']['programInventorySha256'], self.args.program_inventory_sha256)
        self.assertEqual(receipt['result']['migrationCommitted'], 'True')
        self.assertEqual(receipt['result']['upgradeComplete'], 'False')


class InnoLegacyEventContractTests(unittest.TestCase):
    def test_real_event_branches_call_current_parent_bound_helper(self):
        root = Path(__file__).resolve().parents[1]
        setup = (root / 'tools/qt-client.iss').read_text('utf-8-sig')
        legacy = (root / 'tools/qt-legacy-migration.iss').read_text('utf-8-sig')
        reset = (root / 'tools/qt-resource-reset.iss').read_text('utf-8-sig')
        self.assertIn("InstallerInspectLegacyTarget := LegacyMigrationSelected", setup)
        self.assertIn("FileExists(ResourceResetRoot + '\\work\\pending-installer-upgrade.json') then begin", legacy)
        self.assertIn('if LegacyMigrationPrepared then begin\n      if not StartLegacyNative', setup)
        self.assertIn('CompleteLegacyNative(WatcherReason)', setup)
        for action in ('prepare', 'start-native', 'native-complete', 'commit', 'resume', 'cancel'):
            self.assertIn("CallResourceReset('legacy-" + action + "'", legacy)
        self.assertIn("Params := Params + ' --legacy-source '", reset)
        self.assertIn('RunBoundedUninstallProcess', reset)
        bounded = (root / 'tools/qt-uninstall-data.iss').read_text('utf-8-sig')
        self.assertIn('if IsUninstaller then Form := UninstallProgressForm;', bounded)
        self.assertNotIn("Params := 'legacy-prepare", legacy)
