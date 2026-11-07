"""Same-object no-hardlink protocol tests; not a Windows Setup acceptance."""
import errno
import json
import os
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import installer_upgrade_journal as installer
import resource_upgrade_reset as reset
import resource_transaction as resource
import test_installer_upgrade_journal as installer_fixture
import test_resource_upgrade_reset as reset_fixture


def unsupported(*_args, **_kwargs):
    raise OSError(errno.EOPNOTSUPP, 'synthetic volume without hardlinks')


class LinklessUpgradeTests(unittest.TestCase):
    def fixture(self, cls):
        case = cls('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_reset_commit_and_cancel_preserve_old_object_and_user_data(self):
        for commit in (False, True):
            case = self.fixture(reset_fixture.ResourceUpgradeResetTests)
            plan = case.plan(); old = case.old_snapshot(plan)
            with patch.object(resource.os, 'link', side_effect=unsupported):
                outcome = case.prepare(plan)
                directory, journal = reset._load(case.root, outcome['transactionId'])
                self.assertEqual(journal['publication'], 'move-v1')
                self.assertTrue(all(not (directory / 'new' / str(i)).exists()
                                    for i in range(len(journal['newFiles']))))
                result = reset.finish_resource_upgrade(case.root, outcome['transactionId'], setup_succeeded=commit)
                self.assertEqual(result['status'], 'committed' if commit else 'rolled-back')
            case.unchanged_user_data()
            if not commit:
                case.assert_old_restored(old)
            else:
                for i, row in enumerate(plan['oldFiles']):
                    self.assertEqual(reset._stamp(directory / 'old' / str(i))['identity'], row['identity'])
                self.assertEqual(reset.recover_resource_upgrade(case.root)['status'], 'not-needed')

    def test_reset_crash_after_every_owned_move_recovers_original_objects(self):
        first = self.fixture(reset_fixture.ResourceUpgradeResetTests)
        calls = []
        original = reset.transaction._move_owned
        def count(source, target, *a, **kw):
            value = original(source, target, *a, **kw)
            if not Path(source).parent.name.startswith('move-probe-'):
                calls.append((source, target))
            return value
        with patch.object(resource.os, 'link', side_effect=unsupported), patch.object(reset.transaction, '_move_owned', side_effect=count):
            first.prepare()
        self.assertGreater(len(calls), 1)
        for stop in range(1, len(calls) + 1):
            case = self.fixture(reset_fixture.ResourceUpgradeResetTests)
            snapshot = case.old_snapshot(case.plan())
            current = 0
            def interrupt(source, target, *a, **kw):
                nonlocal current
                value = original(source, target, *a, **kw)
                if Path(source).parent.name.startswith('move-probe-'):
                    return value
                current += 1
                if current == stop:
                    raise KeyboardInterrupt('process loss')
                return value
            with self.subTest(move=stop), patch.object(resource.os, 'link', side_effect=unsupported):
                with patch.object(reset.transaction, '_move_owned', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                    case.prepare()
                self.assertEqual(reset.recover_resource_upgrade(case.root)['status'], 'rolled-back')
                case.assert_old_restored(snapshot)

    def test_reset_unknown_replacement_is_not_adopted_or_deleted(self):
        case = self.fixture(reset_fixture.ResourceUpgradeResetTests)
        with patch.object(resource.os, 'link', side_effect=unsupported):
            outcome = case.prepare()
            directory, journal = reset._load(case.root, outcome['transactionId'])
            target = case.root / journal['newFiles'][0]['path']
            foreign = target.with_name(target.name + '.foreign')
            foreign.write_bytes(target.read_bytes())
            os.replace(foreign, target)
            stamp = reset._stamp(target)
            with self.assertRaisesRegex(ValueError, 'rollback-blocked'):
                reset.finish_resource_upgrade(case.root, outcome['transactionId'])
            self.assertEqual(reset._stamp(target), stamp)
            self.assertTrue((case.root / reset.PENDING).exists())
            case.unchanged_user_data()

    def test_reset_same_bytes_replaced_marker_blocks_cleanup(self):
        case = self.fixture(reset_fixture.ResourceUpgradeResetTests)
        with patch.object(resource.os, 'link', side_effect=unsupported):
            outcome = case.prepare()
            marker = case.root / reset.PENDING
            other = marker.with_suffix('.foreign'); other.write_bytes(marker.read_bytes()); os.replace(other, marker)
            stamp = reset._stamp(marker)
            with self.assertRaisesRegex(ValueError, 'pending-changed'):
                reset.finish_resource_upgrade(case.root, outcome['transactionId'])
            self.assertEqual(reset._stamp(marker), stamp)

    def test_installer_forward_lifecycle_and_abort_are_linkless(self):
        for complete in (False, True):
            case = self.fixture(installer_fixture.InstallerUpgradeJournalTests)
            with patch.object(resource.os, 'link', side_effect=unsupported):
                case.ready()
                self.assertEqual(case.instance.status()['publication'], 'move-v1')
                if complete:
                    case.instance.start_native(verify_resource=lambda _: True)
                    case.instance.record_native_complete(case.evidence, verify_native=lambda *_: True)
                    result = case.finish()
                    self.assertEqual(result['status'], 'completed')
                else:
                    result = case.instance.abort_pre_native(verify_resource=lambda _: True)
                    self.assertEqual(result['status'], 'aborted')
                self.assertFalse(result['pending'])
                self.assertEqual(case.instance.status()['status'], result['status'])
                self.assertEqual(case.preserved.stat().st_ino, case.preserved_identity)

    def test_installer_same_byte_generation_or_marker_replacements_block(self):
        for kind in ('generation', 'marker', 'proof'):
            case = self.fixture(installer_fixture.InstallerUpgradeJournalTests)
            with patch.object(resource.os, 'link', side_effect=unsupported):
                case.ready()
                target = (case.generations()[-1] if kind == 'generation' else case.marker if kind == 'marker'
                          else sorted((case.directory / 'published').iterdir())[-1])
                case.replace_same_bytes(target)
                with self.assertRaises(ValueError):
                    case.instance.status()
                self.assertTrue(case.marker.exists())

    def test_installer_missing_latest_generation_does_not_rewind_native_phase(self):
        case = self.fixture(installer_fixture.InstallerUpgradeJournalTests)
        with patch.object(resource.os, 'link', side_effect=unsupported):
            case.native()
            latest = case.generations()[-1]
            latest.unlink()
            with self.assertRaisesRegex(ValueError, 'publication-proof'):
                case.instance.status()
            self.assertTrue(case.marker.exists())

    def test_installer_crash_after_each_publication_has_complete_or_no_authority(self):
        original = installer.rename_no_replace
        for stop in range(1, 4):
            case = self.fixture(installer_fixture.InstallerUpgradeJournalTests)
            count = 0
            def interrupt(source, target):
                nonlocal count
                value = original(source, target)
                count += 1
                if count == stop:
                    raise KeyboardInterrupt('lost process')
                return value
            with self.subTest(move=stop), patch.object(resource.os, 'link', side_effect=unsupported):
                with patch.object(installer, 'rename_no_replace', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                    case.instance.begin()
                result = installer.read_status(case.root, owner_sid=case.sid)
                self.assertEqual(result['status'], 'prepared' if stop == 3 else 'not-needed')

    def test_installer_lost_native_receipt_flush_stays_forward_only(self):
        case = self.fixture(installer_fixture.InstallerUpgradeJournalTests)
        original = installer.rename_no_replace
        with patch.object(resource.os, 'link', side_effect=unsupported):
            case.native()
            def interrupt(source, target):
                value = original(source, target)
                if target.parent.name == 'generations':
                    raise KeyboardInterrupt('lost response')
                return value
            with patch.object(installer, 'rename_no_replace', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
                case.instance.record_native_complete(case.evidence, verify_native=lambda *_: True)
            self.assertEqual(case.instance.status()['status'], 'native-complete')
            self.assertFalse(case.finish()['pending'])

    def test_reset_real_process_exit_during_old_and_new_moves_recovers(self):
        for phase in ('old', 'new'):
            case = self.fixture(reset_fixture.ResourceUpgradeResetTests)
            snapshot = case.old_snapshot(case.plan())
            program = """
import errno, os, sys
from pathlib import Path
import resource_upgrade_reset as r
r.os.link = lambda *a, **kw: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no links'))
original = r.transaction._move_owned
root, phase = Path(sys.argv[1]), sys.argv[2]
def exit_after(source, target, *a, **kw):
    value = original(source, target, *a, **kw)
    if not Path(source).parent.name.startswith('move-probe-'):
        if phase == 'old' and Path(target).parent.name == 'old' or phase == 'new' and Path(source).parent.name == 'new':
            os._exit(41)
    return value
r.transaction._move_owned = exit_after
plan = r.inspect_resource_upgrade(root)
r.prepare_resource_upgrade(root, expected_plan_sha256=plan['planSha256'], confirmed=True)
"""
            result = subprocess.run([sys.executable, '-c', program, str(case.root), phase],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 41, result.stderr)
            with patch.object(resource.os, 'link', side_effect=unsupported):
                self.assertEqual(reset.recover_resource_upgrade(case.root)['status'], 'rolled-back')
            case.assert_old_restored(snapshot)

    def test_legacy_receipts_without_pending_can_start_without_links(self):
        import test_legacy_receipt_startup_retention as legacy
        for name in ('test_every_registered_author_missing_dependencies_can_start_without_archive_allowlist',
                     'test_unregistered_valid_old_author_is_preserved_without_becoming_a_supported_profile'):
            case = legacy.LegacyReceiptStartupRetentionTests(name)
            case.setUp(); self.addCleanup(case.doCleanups)
            with patch.object(resource.os, 'link', side_effect=unsupported):
                getattr(case, name)()

    def test_explicit_overwrite_reset_quarantines_damaged_legacy_receipt(self):
        case = self.fixture(reset_fixture.ResourceUpgradeResetTests)
        receipt = case.root / 'work/map-packages/nightmare-suju.json'
        case.write(receipt, b'{damaged old receipt')
        identity = receipt.stat().st_ino
        with patch.object(resource.os, 'link', side_effect=unsupported):
            outcome = case.prepare()
            directory = Path(outcome['retainedDirectory'])
            reset.finish_resource_upgrade(case.root, outcome['transactionId'], setup_succeeded=True)
        self.assertFalse(receipt.exists())
        saved = [p for p in (directory / 'old').iterdir() if p.read_bytes() == b'{damaged old receipt']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].stat().st_ino, identity)
        case.unchanged_user_data()
