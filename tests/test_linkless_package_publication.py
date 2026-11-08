"""Real temporary-file protocol tests with unsupported hard links, not Windows QA."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import map_package_upgrade as tx
import star_map_import as imp
import author_resource_snapshots as snapshots
import author_snapshot_removal as snapshot_removal
from resource_transaction import resource_write_lock
import test_map_package_upgrade as upgrade_fixture
from test_map_package_upgrade import OLD_PATH, ADDED_PATH
import test_author_resource_snapshots as snapshot_fixture
from test_author_resource_snapshots import Crash


def unsupported(*args, **kwargs):
    raise OSError(errno.EOPNOTSUPP, 'fixture filesystem has no hard links')


class LinklessImportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = upgrade_fixture.UpgradeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.links = patch.object(os, 'link', side_effect=unsupported)
        self.links.start()
        self.addCleanup(self.links.stop)

    def install(self, archive):
        return imp.install_package(archive, self.root, application_stopped=True)

    def test_install_upgrade_repeat_and_terminal_metadata(self):
        self.fixture.installed_old()
        output = self.install(self.fixture.new)
        self.assertEqual(output['replaced'], 1)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'q90-route')
        self.assertEqual(self.install(self.fixture.new)['status'], 'already-installed')
        journal = self.root / tx.STATE / 'upgrades' / output['upgradeTransactionId'] / 'journal.json'
        self.assertEqual(json.loads(journal.read_bytes())['publicationMode'], 'move-v1')
        self.assertFalse((self.root / tx.PENDING).exists())
        self.assertFalse((self.root / tx.ADDITIONS_PENDING).exists())

    def test_upgrade_publish_failure_restores_same_original_objects(self):
        self.fixture.installed_old()
        old_identity = tx._identity((self.root / OLD_PATH).stat())
        receipt_identity = tx._identity(self.fixture.receipt.stat())
        real = tx._move_owned
        def fail(source, target, *args, **kwargs):
            if Path(target) == self.root / ADDED_PATH:
                raise OSError('fixture disk failure')
            return real(source, target, *args, **kwargs)
        with patch.object(tx, '_move_owned', side_effect=fail):
            with self.assertRaises(OSError): self.install(self.fixture.new)
        self.fixture.assert_old()
        self.assertEqual(tx._identity((self.root / OLD_PATH).stat()), old_identity)
        self.assertEqual(tx._identity(self.fixture.receipt.stat()), receipt_identity)

    def test_additions_real_process_loss_after_payload_move(self):
        script = '''import errno, os, sys
from pathlib import Path
import star_map_import as imp
import map_package_upgrade as tx
os.link=lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no links'))
real=tx._move_owned
def crash(source,target,*args,**kwargs):
 real(source,target,*args,**kwargs)
 if str(target).endswith('floor-1.png'): os._exit(71)
tx._move_owned=crash
imp.install_package(Path(sys.argv[1]),Path(sys.argv[2]),application_stopped=True)
'''
        done = subprocess.run([sys.executable, '-c', script, str(self.fixture.old), str(self.root)])
        self.assertEqual(done.returncode, 71)
        self.assertTrue((self.root / OLD_PATH).exists())
        with resource_write_lock(self.root):
            result = tx.recover_pending_additions(self.root)
        self.assertEqual(result['status'], 'additions-rolled-back')
        self.assertFalse((self.root / OLD_PATH).exists())
        self.install(self.fixture.old)

    def test_upgrade_process_loss_after_every_actual_move(self):
        script = """import errno, os, sys
from pathlib import Path
import star_map_import as imp
import map_package_upgrade as tx
os.link=lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no links'))
real=tx._move_owned
count=0
def crash(source,target,*args,**kwargs):
 global count
 real(source,target,*args,**kwargs)
 if 'move-probe-' not in str(source) and not str(target).endswith('.publication.json'):
  count+=1
  if count==int(sys.argv[3]): os._exit(72)
tx._move_owned=crash
imp.install_package(Path(sys.argv[1]),Path(sys.argv[2]),application_stopped=True)
"""
        for cut in range(1, 10):
            with self.subTest(move=cut):
                f = upgrade_fixture.UpgradeTests(); f.setUp()
                try:
                    f.installed_old()
                    original = tx._identity((f.root / OLD_PATH).stat())
                    done = subprocess.run([sys.executable, '-c', script, str(f.new), str(f.root), str(cut)])
                    self.assertEqual(done.returncode, 72)
                    with resource_write_lock(f.root):
                        result = tx.recover_pending_upgrade(f.root)
                    if cut == 9:
                        self.assertEqual(result['status'], 'upgrade-committed')
                        self.assertEqual((f.root / OLD_PATH).read_bytes(), b'q90-route')
                    else:
                        f.assert_old()
                        self.assertEqual(tx._identity((f.root / OLD_PATH).stat()), original)
                    self.assertFalse((f.root / tx.PENDING).exists())
                finally:
                    f.doCleanups()

    def test_retired_history_survives_interrupted_metadata_prune(self):
        self.fixture.installed_old()
        result = self.install(self.fixture.new)
        directory = self.root / tx.STATE / 'upgrades' / result['upgradeTransactionId']
        for name in ('pending-pointer.json', 'pending-pointer.json.publication.json'):
            (directory / name).unlink()
            with resource_write_lock(self.root):
                _, journal = tx._load_journal(self.root, directory.name)
                tx._retired_plan(directory, journal)

    def test_upgrade_same_bytes_foreign_pending_blocks_before_rollback(self):
        self.fixture.installed_old()
        real = tx._move_owned
        def crash(source, target, *args, **kwargs):
            real(source, target, *args, **kwargs)
            if Path(target) == self.root / OLD_PATH: raise Crash()
        with patch.object(tx, '_move_owned', side_effect=crash):
            with self.assertRaises(Crash): self.install(self.fixture.new)
        target = self.root / tx.PENDING
        foreign = target.with_name('foreign')
        foreign.write_bytes(target.read_bytes()); os.replace(foreign, target)
        image = (self.root / OLD_PATH).read_bytes()
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'publication-target-changed'):
                tx.recover_pending_upgrade(self.root)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), image)

    def test_second_process_loss_during_upgrade_rollback_resumes(self):
        self.fixture.installed_old()
        actual = tx._move_owned
        def crash(source, target, *args, **kwargs):
            actual(source, target, *args, **kwargs)
            if Path(target) == self.root / ADDED_PATH: raise Crash()
        with patch.object(tx, '_move_owned', side_effect=crash):
            with self.assertRaises(Crash): self.install(self.fixture.new)
        script = """import errno, os, sys
from pathlib import Path
import map_package_upgrade as tx
from resource_transaction import resource_write_lock
os.link=lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no links'))
real=tx._move_owned
def crash(*args,**kwargs):
 real(*args,**kwargs)
 os._exit(73)
tx._move_owned=crash
with resource_write_lock(Path(sys.argv[1])): tx.recover_pending_upgrade(Path(sys.argv[1]))
"""
        done = subprocess.run([sys.executable, '-c', script, str(self.root)])
        self.assertEqual(done.returncode, 73)
        with resource_write_lock(self.root): tx.recover_pending_upgrade(self.root)
        self.fixture.assert_old()

    def test_cancel_after_addition_move_restores_without_publishing_receipt(self):
        actual = tx._move_owned
        cancelled = False
        def move(source, target, *args, **kwargs):
            nonlocal cancelled
            actual(source, target, *args, **kwargs)
            if Path(target) == self.root / OLD_PATH: cancelled = True
        with patch.object(tx, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                imp.install_author_package_live(self.fixture.old, self.root, application_idle=True, cancelled=lambda: cancelled)
        self.assertFalse((self.root / OLD_PATH).exists())
        self.assertFalse((self.root / tx.ADDITIONS_PENDING).exists())
        self.assertFalse((self.root / tx.STATE / 'difficult-zhan10.json').exists())

    def test_stage_replacement_after_journaling_is_not_published_or_claimed(self):
        actual = tx._move_owned
        foreign = {}
        def replace(source, target, *args, **kwargs):
            if Path(target) == self.root / OLD_PATH:
                path = Path(source)
                replacement = path.with_name('foreign')
                replacement.write_bytes(path.read_bytes()); os.replace(replacement, path)
                foreign['path'], foreign['identity'] = path, tx._identity(path.stat())
            return actual(source, target, *args, **kwargs)
        with patch.object(tx, '_move_owned', side_effect=replace):
            with self.assertRaises(ValueError): self.install(self.fixture.old)
        self.assertFalse((self.root / OLD_PATH).exists())
        self.assertEqual(tx._identity(foreign['path'].stat()), foreign['identity'])
        self.assertTrue((self.root / tx.ADDITIONS_PENDING).exists())

    def test_upgrade_final_receipt_rechecks_already_published_identity(self):
        self.fixture.installed_old()
        actual, foreign = tx._move_owned, {}
        def replace(source, target, *args, **kwargs):
            if Path(target) == self.root / ADDED_PATH:
                path = self.root / OLD_PATH
                replacement = path.with_name('foreign')
                replacement.write_bytes(path.read_bytes()); os.replace(replacement, path)
                foreign['identity'] = tx._identity(path.stat())
            return actual(source, target, *args, **kwargs)
        with patch.object(tx, '_move_owned', side_effect=replace):
            with self.assertRaisesRegex(ValueError, 'rollback-incomplete'):
                self.install(self.fixture.new)
        self.assertEqual(tx._identity((self.root / OLD_PATH).stat()), foreign['identity'])
        self.assertEqual(self.fixture.receipt.read_bytes(), self.fixture.old_receipt)
        directory, journal = tx._load_journal(self.root, tx._pending_id(self.root))
        original = next(entry for entry in journal['entries'] if entry['new']['path'] == OLD_PATH)
        self.assertTrue(tx._matches(directory / 'backup' / OLD_PATH, original['old'], original['oldIdentity']))

    def test_foreign_proof_created_before_publication_is_not_overwritten_or_cleaned(self):
        actual, foreign = tx._write_new, {}
        def create(path, data):
            actual(path, data)
            if str(path).endswith('.publication.json.tmp'):
                proof = Path(str(path)[:-4])
                proof.write_bytes(b'private data by another writer')
                foreign['path'], foreign['identity'] = proof, tx._identity(proof.stat())
        with patch.object(tx, '_write_new', side_effect=create):
            with self.assertRaises(OSError) as caught:
                self.install(self.fixture.old)
        # The Windows handle rename exposes raw ERROR_FILE_EXISTS / ALREADY_EXISTS
        # as errno; POSIX renameat2 reports EEXIST. Other I/O failures must fail.
        collision_codes = {80, 183} if os.name == 'nt' else {errno.EEXIST}
        self.assertIn(caught.exception.errno, collision_codes)
        self.assertEqual(foreign['path'].read_bytes(), b'private data by another writer')
        self.assertEqual(tx._identity(foreign['path'].stat()), foreign['identity'])
        self.assertFalse((self.root / OLD_PATH).exists())
        self.assertFalse((self.root / tx.ADDITIONS_PENDING).exists())

    def test_prepublication_process_loss_recovers_source_temp_proof_and_final_proof(self):
        script = """import errno, os, sys
from pathlib import Path
import star_map_import as imp
import map_package_upgrade as tx
os.link=lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no links'))
cut=sys.argv[3]
write,move=tx._write_new,tx._move_owned
def stop_write(path,data):
 write(path,data)
 if Path(path).name==cut: os._exit(76)
def stop_move(source,target,*args,**kwargs):
 move(source,target,*args,**kwargs)
 if Path(target).name==cut: os._exit(76)
tx._write_new,tx._move_owned=stop_write,stop_move
imp.install_package(Path(sys.argv[1]),Path(sys.argv[2]),application_stopped=True)
"""
        import map_package_recovery_plan as recovery
        cuts=('pending-pointer.json.publishing', 'pending-pointer.json.publication.json.tmp',
              'pending-pointer.json.publication.json')
        for cut in cuts:
            for route in ('startup', 'explicit'):
                with self.subTest(cut=cut, route=route):
                    f=upgrade_fixture.UpgradeTests(); f.setUp()
                    try:
                        f.installed_old()
                        done=subprocess.run([sys.executable,'-c',script,str(f.new),str(f.root),cut])
                        self.assertEqual(done.returncode,76)
                        self.assertIsNone(tx._pending_id(f.root)); f.assert_old()
                        with resource_write_lock(f.root):
                            if route=='startup':
                                self.assertEqual(tx.retire_completed_upgrades(f.root)['retiredTransactions'],1)
                            else:
                                plan=recovery.history_locked(f.root)[0]
                                self.assertEqual(recovery.execute_locked(f.root,plan,'recover')['status'],'rolled-back')
                            self.assertEqual(recovery.history_locked(f.root),[])
                        f.assert_old()
                    finally: f.doCleanups()

    def test_prepublication_foreign_or_partial_source_is_preserved(self):
        for phase in ('source', 'temp-proof', 'final-proof'):
            with self.subTest(phase=phase):
                f=upgrade_fixture.UpgradeTests(); f.setUp()
                try:
                    f.installed_old()
                    actual_write,actual_move=tx._write_new,tx._move_owned
                    def stop_write(path,data):
                        actual_write(path,data)
                        wanted='pending-pointer.json.publishing' if phase=='source' else 'pending-pointer.json.publication.json.tmp'
                        if phase!='final-proof' and Path(path).name==wanted: raise Crash()
                    def stop_move(source,target,*args,**kwargs):
                        actual_move(source,target,*args,**kwargs)
                        if phase=='final-proof' and str(target).endswith('pending-pointer.json.publication.json'): raise Crash()
                    with patch.object(tx,'_write_new',side_effect=stop_write), patch.object(tx,'_move_owned',side_effect=stop_move):
                        with self.assertRaises(Crash): imp.install_package(f.new,f.root,application_stopped=True)
                    directory=next((f.root / tx.STATE / 'upgrades').iterdir())
                    source=directory/'pending-pointer.json.publishing'
                    replacement=directory/'foreign'
                    replacement.write_bytes(b'partial private' if phase=='source' else source.read_bytes())
                    os.replace(replacement,source)
                    foreign=source.read_bytes(),tx._identity(source.stat())
                    with resource_write_lock(f.root):
                        with self.assertRaises(ValueError): tx.retire_completed_upgrades(f.root)
                    self.assertEqual((source.read_bytes(),tx._identity(source.stat())),foreign)
                    f.assert_old()
                finally: f.doCleanups()

    def interrupted_addition(self):
        actual = tx._move_owned
        def crash(source, target, *args, **kwargs):
            actual(source, target, *args, **kwargs)
            if Path(target) == self.root / OLD_PATH: raise Crash()
        with patch.object(tx, '_move_owned', side_effect=crash):
            with self.assertRaises(Crash): self.install(self.fixture.old)

    def test_same_bytes_foreign_pending_and_payload_rejected(self):
        self.interrupted_addition()
        pending = self.root / tx.ADDITIONS_PENDING
        foreign = pending.with_name('foreign')
        foreign.write_bytes(pending.read_bytes()); os.replace(foreign, pending)
        before = (self.root / OLD_PATH).read_bytes()
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'publication-target-changed'):
                tx.recover_pending_additions(self.root)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), before)

    def test_same_bytes_foreign_payload_rejected(self):
        self.interrupted_addition()
        target = self.root / OLD_PATH
        foreign = target.with_name('foreign')
        foreign.write_bytes(target.read_bytes()); os.replace(foreign, target)
        identity = tx._identity(target.stat())
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'rollback-conflict'): tx.recover_pending_additions(self.root)
        self.assertEqual(tx._identity(target.stat()), identity)


class LinklessSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = snapshot_fixture.AuthorSnapshotTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.profile = self.fixture.root, self.fixture.profile
        self.links = patch.object(os, 'link', side_effect=unsupported)
        self.links.start(); self.addCleanup(self.links.stop)

    def test_install_duplicate_and_history_removal_ownership(self):
        archive = self.fixture.package()
        installed = self.fixture.install(archive)
        self.assertTrue(self.fixture.install(archive)['receiptPreserved'])
        with resource_write_lock(self.root):
            result = snapshot_removal.collect(self.root, self.profile)
        self.assertFalse(result['retained'])
        self.assertTrue(result['sources'])
        directory = self.root / snapshots.STORE / 'transactions' / installed['snapshotTransactionId']
        journal = json.loads((directory / 'journal.json').read_bytes())
        self.assertEqual(journal['publicationMode'], 'move-v1')

    def test_interrupted_switch_restores_previous_activation_object(self):
        self.fixture.install(self.fixture.package())
        active = self.root / snapshots.activation_path(self.profile)
        old = active.read_bytes(); identity = tx._identity(active.stat())
        self.fixture.interrupt_publish(self.fixture.package(color=(12, 34, 56)))
        self.fixture.recover()
        self.assertEqual(active.read_bytes(), old)
        self.assertEqual(tx._identity(active.stat()), identity)

    def test_deactivation_same_object_transaction_and_committed_witness(self):
        self.fixture.install(self.fixture.package())
        with resource_write_lock(self.root):
            plan = snapshots.inspect_deactivation(self.root, self.profile)
            snapshots.deactivate_locked(self.root, plan)
        self.assertIsNone(snapshots._read_activation(self.root, self.profile)[0]['snapshotId'])
        with resource_write_lock(self.root):
            self.assertFalse(snapshot_removal.collect(self.root, self.profile)['retained'])

    def test_real_process_loss_after_activation_publication_restores_original(self):
        self.fixture.install(self.fixture.package())
        active = self.root / snapshots.activation_path(self.profile)
        before, identity = active.read_bytes(), tx._identity(active.stat())
        archive = self.fixture.package(fmt='jpeg')
        script = """import errno, os, sys
from pathlib import Path
import author_resource_snapshots as snapshots
import star_map_import as imp
os.link=lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP, 'no links'))
snapshots._commit_outcome=lambda *a,**k: os._exit(74)
imp.install_author_package_live(Path(sys.argv[1]),Path(sys.argv[2]),application_idle=True,versioned=True)
"""
        done = subprocess.run([sys.executable, '-c', script, str(archive), str(self.root)])
        self.assertEqual(done.returncode, 74)
        self.fixture.recover()
        self.assertEqual(active.read_bytes(), before)
        self.assertEqual(tx._identity(active.stat()), identity)

    def test_same_bytes_foreign_commit_marker_cannot_authorize_recovery(self):
        with patch.object(snapshots, '_clear_pointer', side_effect=OSError('interrupted cleanup')):
            with self.assertRaises(OSError): self.fixture.install(self.fixture.package())
        directory, journal = snapshots._load_pending(self.root)
        target = directory / 'committed.json'
        foreign = directory / 'foreign'
        foreign.write_bytes(target.read_bytes()); os.replace(foreign, target)
        active = self.root / snapshots.activation_path(self.profile)
        before = active.read_bytes()
        with self.assertRaisesRegex(ValueError, 'commit-outcome-changed'): self.fixture.recover()
        self.assertEqual(active.read_bytes(), before)
        self.assertTrue(self.fixture.pending().exists())
