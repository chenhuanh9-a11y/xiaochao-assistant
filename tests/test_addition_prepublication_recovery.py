"""Real process-loss preparation discovery. Temporary synthetic installs only."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import map_package_recovery_plan as recovery
import map_package_removal as removal
import map_package_upgrade as tx
import resource_transaction as lease
import star_map_import as importer
import test_map_package_upgrade as fixtures


SCRIPT = '''import errno, os, sys
from pathlib import Path
import star_map_import as importer
import map_package_upgrade as tx
if sys.argv[4]=='move':
 os.link=lambda *a,**k: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP,'no links'))
write,move=tx._write_new,tx._move_owned
cut=sys.argv[3]
def stop_write(path,data):
 write(path,data)
 if Path(path).name==cut: os._exit(76)
def stop_move(source,target,*a,**k):
 move(source,target,*a,**k)
 if cut=='proof-moved' and Path(target).name=='addition-journal.json.publication.json': os._exit(76)
tx._write_new,tx._move_owned=stop_write,stop_move
importer.install_package(Path(sys.argv[1]),Path(sys.argv[2]),application_stopped=True)
'''


def unsupported(*args, **kwargs):
    raise OSError(errno.EOPNOTSUPP, 'fixture no hard links')


def stamp_tree(root):
    return {path.relative_to(root).as_posix(): (path.read_bytes(), tx._identity(path.stat()))
            for path in root.rglob('*') if path.is_file() and path.name != 'import.lock'}


class AdditionsPreparationRecoveryTests(unittest.TestCase):
    def crash(self, cut, mode='move'):
        fixture = fixtures.UpgradeTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = subprocess.run([sys.executable, '-c', SCRIPT, str(fixture.old),
                                 str(fixture.root), cut, mode], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 76, result.stderr)
        self.assertFalse((fixture.root / tx.ADDITIONS_PENDING).exists())
        self.assertFalse((fixture.root / fixtures.OLD_PATH).exists())
        return fixture

    def test_four_real_crash_windows_recover_reinstall_and_remove(self):
        cuts = ('addition-journal.json', 'addition-journal.json.publishing',
                'addition-journal.json.publication.json.tmp', 'proof-moved')
        for cut in cuts:
            with self.subTest(cut=cut), patch.object(os, 'link', side_effect=unsupported):
                fixture = self.crash(cut)
                before = stamp_tree(fixture.root)
                with lease.resource_write_lock(fixture.root):
                    plans = recovery.history_locked(fixture.root)
                    self.assertEqual(len(plans), 1)
                    self.assertEqual(plans[0]['status'], 'additions-pending')
                    self.assertTrue(plans[0]['pending'])
                    self.assertEqual(stamp_tree(fixture.root), before)
                    with self.assertRaisesRegex(lease.ResourceTransactionError, 'additions-recovery-required'):
                        lease.assert_resources_ready(fixture.root)
                with self.assertRaisesRegex(ValueError, 'additions-recovery-required'):
                    importer.install_package(fixture.old, fixture.root, application_stopped=True)
                with lease.resource_write_lock(fixture.root):
                    self.assertEqual(recovery.execute_locked(fixture.root, plans[0], 'recover')['status'],
                                     'additions-rolled-back')
                    self.assertEqual(tx.recover_pending_additions(fixture.root)['status'], 'no-pending-additions')
                    lease.assert_resources_ready(fixture.root)
                importer.install_package(fixture.old, fixture.root, application_stopped=True)
                plan = removal.inspect_package_removal(fixture.root, 'difficult-zhan10')
                result = removal.remove_packages(fixture.root, 'difficult-zhan10', application_idle=True,
                                                 expected_plan_sha256=plan['planSha256'])
                self.assertEqual(result['status'], 'packages-removed')
                self.assertFalse((fixture.root / fixtures.OLD_PATH).exists())

    def test_hardlink_preparation_still_uses_legacy_link_witness(self):
        fixture = self.crash('addition-journal.json', 'hardlink')
        with lease.resource_write_lock(fixture.root):
            plan = recovery.history_locked(fixture.root)[0]
            self.assertEqual(recovery.execute_locked(fixture.root, plan, 'recover')['status'], 'additions-rolled-back')
            lease.assert_resources_ready(fixture.root)
        importer.install_package(fixture.old, fixture.root, application_stopped=True)
        self.assertEqual((fixture.root / fixtures.OLD_PATH).read_bytes(), b'old-route')

    def test_unknown_modified_and_same_bytes_foreign_preparation_objects_are_preserved(self):
        cases = ('payload-bytes', 'payload-identity', 'receipt-identity', 'source-bytes', 'proof-bytes', 'unknown')
        for case in cases:
            with self.subTest(case=case), patch.object(os, 'link', side_effect=unsupported):
                fixture = self.crash('addition-journal.json.publication.json.tmp')
                stage = next((fixture.root / tx.STATE).glob('staging-*'))
                target = stage / fixtures.OLD_PATH
                if case == 'payload-bytes':
                    target.write_bytes(b'foreign user edit')
                elif case in {'payload-identity', 'receipt-identity'}:
                    if case == 'receipt-identity': target = stage / 'receipt.json'
                    replacement = target.with_name('replacement')
                    replacement.write_bytes(target.read_bytes()); os.replace(replacement, target)
                elif case == 'source-bytes':
                    (stage / 'addition-journal.json.publishing').write_bytes(b'partial private data')
                elif case == 'proof-bytes':
                    (stage / 'addition-journal.json.publication.json.tmp').write_bytes(b'{')
                else:
                    (stage / 'private-note').write_bytes(b'unknown private bytes')
                before = stamp_tree(fixture.root)
                with lease.resource_write_lock(fixture.root):
                    with self.assertRaises(ValueError): tx.recover_pending_additions(fixture.root)
                self.assertEqual(stamp_tree(fixture.root), before)

    def test_discovery_never_hashes_payload_and_bound_is_explicit(self):
        fixture = self.crash('addition-journal.json')
        target = importer._target
        def no_payload_paths(root, relative):
            if relative == fixtures.OLD_PATH:
                raise AssertionError('discovery touched payload path')
            return target(root, relative)
        with lease.resource_write_lock(fixture.root):
            with patch.object(tx, '_matches', side_effect=AssertionError('discovery read payload')), \
                    patch.object(importer, '_target', side_effect=no_payload_paths):
                self.assertEqual(len(tx.prepared_additions(fixture.root)), 1)
                with self.assertRaisesRegex(lease.ResourceTransactionError, 'additions-recovery-required'):
                    lease.assert_resources_ready(fixture.root)
            with patch.object(tx, 'MAX_HISTORY', 1):
                with self.assertRaisesRegex(ValueError, 'additions-history-limit'):
                    tx.prepared_additions(fixture.root)

    def test_legacy_orphan_with_later_install_is_reported_without_mutation(self):
        fixture = self.crash('addition-journal.json.publishing')
        # Reproduce the older runtime's missing gate, solely to construct its
        # historical state. The production gate is active during verification.
        with patch.object(tx, 'prepared_additions', return_value=[]), patch.object(os, 'link', side_effect=unsupported):
            importer.install_package(fixture.old, fixture.root, application_stopped=True)
        before = stamp_tree(fixture.root)
        with lease.resource_write_lock(fixture.root):
            plan = recovery.history_locked(fixture.root)[0]
            with self.assertRaisesRegex(ValueError, 'additions-preparation-live-target-present; 当前已有图包文件'):
                recovery.execute_locked(fixture.root, plan, 'recover')
        self.assertEqual(stamp_tree(fixture.root), before)

    def test_changed_preparation_after_inspection_needs_new_plan(self):
        fixture = self.crash('addition-journal.json.publishing')
        with lease.resource_write_lock(fixture.root): plan = recovery.history_locked(fixture.root)[0]
        stage = next((fixture.root / tx.STATE).glob('staging-*'))
        target = stage / fixtures.OLD_PATH
        target.write_bytes(b'changed after inspection')
        before = stamp_tree(fixture.root)
        with lease.resource_write_lock(fixture.root):
            with self.assertRaisesRegex(ValueError, 'recovery-plan-changed'):
                recovery.execute_locked(fixture.root, plan, 'recover')
        self.assertEqual(stamp_tree(fixture.root), before)


class LegacyAdditionHistoryTests(unittest.TestCase):
    """The pre-mode eight-field reader form and ten-field producer contract.

    Historical 7b98482 recovery cleared pending before private cleanup and did
    not produce an outcome record. Do not invent one merely to satisfy a newer
    discovery gate. Payload deletion still uses the existing removal validator.
    """
    def legacy(self, extended=False):
        import hashlib
        import json
        import uuid
        fixture = fixtures.UpgradeTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        stage = fixture.root / tx.STATE / 'staging-legacy-history'
        target = stage / fixtures.OLD_PATH
        target.parent.mkdir(parents=True)
        target.write_bytes(b'legacy-staged-only')
        row = fixtures.row(fixtures.OLD_PATH, target.read_bytes())
        transaction = str(uuid.uuid4())
        journal = dict(schema=tx.ADDITIONS_SCHEMA, packageId='difficult-zhan10', transactionId=transaction,
                       stage=stage.relative_to(fixture.root).as_posix(), rootIdentity=tx._identity(fixture.root.stat()),
                       files=[row], entries=[dict(new=row, newIdentity=tx._identity(target.stat()))], oldReceiptSha256=None)
        if extended:
            receipt = tx._encoded(dict(packageId='difficult-zhan10', additionTransactionId=transaction,
                                       files=[row], requiresFiles=fixture.dependencies, archiveSha256='a' * 64))
            journal.update(stagedIdentities={row['path']: tx._identity(target.stat())},
                           newReceipt=dict(path='receipt.json', bytes=len(receipt), sha256=hashlib.sha256(receipt).hexdigest()))
        (stage / 'addition-journal.json').write_text(json.dumps(journal), encoding='utf-8')
        (stage / 'unknown.bin').write_bytes(b'private unknown bytes')
        return fixture, stage, journal

    def test_both_exact_legacy_shapes_keep_safe_list_and_remove_contract(self):
        for extended in (False, True):
            with self.subTest(extended=extended):
                fixture, stage, _ = self.legacy(extended)
                unknown = stage / 'unknown.bin'
                identity = tx._identity(unknown.stat())
                with lease.resource_write_lock(fixture.root):
                    lease.assert_resources_ready(fixture.root)
                    self.assertEqual(recovery.history_locked(fixture.root), [])
                rows = removal.managed_packages_snapshot(fixture.root)
                self.assertTrue(any(row['packageId'] == 'difficult-zhan10' for row in rows))
                plan = removal.inspect_package_removal(fixture.root, 'difficult-zhan10')
                removal.remove_packages(fixture.root, 'difficult-zhan10', application_idle=True,
                                        expected_plan_sha256=plan['planSha256'])
                self.assertFalse((stage / fixtures.OLD_PATH).exists())
                self.assertEqual(unknown.read_bytes(), b'private unknown bytes')
                self.assertEqual(tx._identity(unknown.stat()), identity)

    def test_corrupt_or_unrecognized_legacy_declarations_fail_closed(self):
        import copy
        import json
        changes = ('schema', 'root', 'stage', 'extra', 'identity', 'allowlist', 'partial-extended', 'digest', 'entry-mismatch')
        for change in changes:
            with self.subTest(change=change):
                fixture, stage, journal = self.legacy(change == 'entry-mismatch')
                bad = copy.deepcopy(journal)
                if change == 'schema': bad['schema'] = 'unknown-schema'
                elif change == 'root': bad['rootIdentity'] = [1, 2]
                elif change == 'stage': bad['stage'] = 'work/map-packages/staging-someone-else'
                elif change == 'extra': bad['unknownField'] = 'not a known historical format'
                elif change == 'identity': bad['entries'][0]['newIdentity'] = [1]
                elif change == 'allowlist':
                    bad['files'][0]['path'] = '../private-file'
                    bad['entries'][0]['new']['path'] = '../private-file'
                elif change == 'partial-extended': bad['stagedIdentities'] = {}
                elif change == 'digest': bad['oldReceiptSha256'] = 'not-a-sha'
                else: bad['stagedIdentities'][fixtures.OLD_PATH] = [1, 2]
                (stage / 'addition-journal.json').write_text(json.dumps(bad), encoding='utf-8')
                before = stamp_tree(fixture.root)
                with lease.resource_write_lock(fixture.root):
                    with self.assertRaisesRegex(lease.ResourceTransactionError, 'additions-recovery-required'):
                        lease.assert_resources_ready(fixture.root)
                self.assertEqual(stamp_tree(fixture.root), before)

    def test_legacy_exception_does_not_claim_replaced_payload(self):
        for extended in (False, True):
            with self.subTest(extended=extended):
                fixture, stage, _ = self.legacy(extended)
                target = stage / fixtures.OLD_PATH
                replacement = target.with_name('foreign')
                replacement.write_bytes(target.read_bytes()); os.replace(replacement, target)
                before = stamp_tree(fixture.root)
                with self.assertRaisesRegex(ValueError, 'history-changed'):
                    removal.inspect_package_removal(fixture.root, 'difficult-zhan10')
                self.assertEqual(stamp_tree(fixture.root), before)

    def test_bad_optional_legacy_outcome_still_blocks(self):
        import json
        fixture, stage, journal = self.legacy()
        (stage / tx.ADDITIONS_OUTCOME).write_text(json.dumps(dict(schema=tx.ADDITIONS_OUTCOME_SCHEMA,
            transactionId=journal['transactionId'], journalSha256='0' * 64, status='committed')))
        before = stamp_tree(fixture.root)
        with lease.resource_write_lock(fixture.root):
            with self.assertRaisesRegex(lease.ResourceTransactionError, 'additions-recovery-required'):
                lease.assert_resources_ready(fixture.root)
        self.assertEqual(stamp_tree(fixture.root), before)

    def test_explicit_new_mode_never_takes_legacy_no_marker_exception(self):
        import json
        fixture, stage, journal = self.legacy(True)
        journal['publicationMode'] = 'hardlink-v1'
        (stage / 'addition-journal.json').write_text(json.dumps(journal), encoding='utf-8')
        with lease.resource_write_lock(fixture.root):
            self.assertEqual(len(tx.prepared_additions(fixture.root)), 1)
            with self.assertRaisesRegex(lease.ResourceTransactionError, 'additions-recovery-required'):
                lease.assert_resources_ready(fixture.root)
