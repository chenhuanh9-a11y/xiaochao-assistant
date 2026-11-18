"""Actual metadata publication and child-process loss, without map/GUI claims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import imported_resource_activation as activation
from imported_resource_catalog import CatalogSnapshot, PENDING_CATALOG, SCHEMA, ResourceCatalogError, parse_catalog
from resource_transaction import resource_write_lock


CHILD = '''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from contextlib import nullcontext
from imported_resource_activation import activate_catalog_locked
from imported_resource_catalog import CatalogSnapshot, parse_catalog
from resource_transaction import resource_write_lock
root, document, stop, mode = Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]
def stage(name):
    if name == stop:
        os._exit(77)
forced = (patch('imported_resource_activation.locks.preflight_resource_publication', return_value='move-v1')
          if mode == 'move-v1' else nullcontext())
with resource_write_lock(root), forced:
    activate_catalog_locked(root, document, expected_catalog_stamp=CatalogSnapshot(root).catalog_stamp,
                            validate=parse_catalog, on_stage=stage)
'''


def document(*, four_contexts=False):
    return dict(schema=SCHEMA, generation=uuid.uuid4().hex,
                contexts={f'{season}:{mode}': dict(recognitionBase=None, controls=None, reviewedGroups=None)
                          for season in ('s0', 's1') for mode in ('difficult', 'nightmare')}
                if four_contexts else {}, displays=[])


def snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_dev, p.stat().st_ino)
            for p in root.rglob('*') if p.is_file()}


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def old(self):
        value = document()
        (self.root / 'catalog.json').write_text(json.dumps(value), encoding='utf-8')
        return value, CatalogSnapshot(self.root).catalog_stamp

    def lose_process(self, new, stage, mode):
        result = subprocess.run([sys.executable, '-c', CHILD, str(self.root), json.dumps(new), stage, mode],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)

    def recover(self):
        with resource_write_lock(self.root):
            return activation.recover_catalog_locked(self.root)

    def test_loss_at_each_publication_stage_restores_or_commits_same_object(self):
        for mode in ('hardlink-v1', 'move-v1'):
            for stage in ('pending', 'previous-retained', 'catalog-published', 'committed'):
                with self.subTest(mode=mode, stage=stage), tempfile.TemporaryDirectory(dir=self.root) as directory:
                    previous_root, self.root = self.root, Path(directory)
                    try:
                        old, stamp = self.old()
                        new = document(four_contexts=True)
                        self.lose_process(new, stage, mode)
                        with self.assertRaisesRegex(ResourceCatalogError, 'recovery-required'):
                            CatalogSnapshot(self.root)
                        result = self.recover()
                        actual = CatalogSnapshot(self.root)
                        if stage == 'committed':
                            self.assertEqual(actual.generation, new['generation'])
                            self.assertEqual(result['status'], 'catalog-committed')
                        else:
                            self.assertEqual(actual.generation, old['generation'])
                            self.assertEqual(actual.catalog_stamp, stamp)
                            self.assertEqual(result['status'], 'catalog-rolled-back')
                        before = snapshot(self.root)
                        self.assertEqual(self.recover()['status'], 'catalog-ready')
                        self.assertEqual(snapshot(self.root), before)
                    finally:
                        self.root = previous_root

    def test_first_activation_loss_recovers_absence(self):
        for stage in ('pending', 'previous-retained', 'catalog-published'):
            with self.subTest(stage=stage):
                self.lose_process(document(), stage, 'hardlink-v1')
                self.assertEqual(self.recover()['status'], 'catalog-rolled-back')
                self.assertIsNone(CatalogSnapshot(self.root).generation)
                self.assertFalse((self.root / 'catalog.json').exists())

    def test_interrupted_commit_preparation_or_lost_witness_never_grants_rollback(self):
        for mode in ('hardlink-v1', 'move-v1'):
            for stop in ('commit-prepared', 'committed'):
                with self.subTest(mode=mode, stop=stop), tempfile.TemporaryDirectory(dir=self.root) as directory:
                    previous_root, self.root = self.root, Path(directory)
                    try:
                        self.old()
                        new = document()
                        self.lose_process(new, stop, mode)
                        if stop == 'committed':
                            transaction_id = json.loads((self.root / PENDING_CATALOG).read_bytes())['transactionId']
                            marker = self.root / 'transactions' / transaction_id / 'committed.json'
                            marker.rename(marker.with_name('retained-missing-witness.json'))
                        before = snapshot(self.root)
                        with self.assertRaisesRegex(ResourceCatalogError, 'commit-outcome-uncertain'):
                            self.recover()
                        self.assertEqual(snapshot(self.root), before)
                        self.assertEqual(json.loads((self.root / 'catalog.json').read_bytes())['generation'],
                                         new['generation'])
                        with self.assertRaisesRegex(ResourceCatalogError, 'recovery-required'):
                            CatalogSnapshot(self.root)
                    finally:
                        self.root = previous_root

    def test_cancel_before_commit_restores_previous_and_after_commit_keeps_new(self):
        old, stamp = self.old()
        cancelled = [False]
        def before_commit(stage):
            if stage == 'catalog-published':
                cancelled[0] = True
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                activation.activate_catalog_locked(self.root, document(), expected_catalog_stamp=stamp,
                    validate=parse_catalog, cancelled=lambda: cancelled[0], on_stage=before_commit)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, stamp)
        new = document()
        cancelled[0] = False
        with resource_write_lock(self.root):
            result = activation.activate_catalog_locked(self.root, new, expected_catalog_stamp=stamp,
                validate=parse_catalog, cancelled=lambda: cancelled[0],
                on_stage=lambda stage: cancelled.__setitem__(0, stage == 'committed'))
        self.assertEqual(result['status'], 'catalog-activated')
        self.assertEqual(CatalogSnapshot(self.root).generation, new['generation'])

    def test_external_target_backup_pending_journal_or_commit_is_retained_on_conflict(self):
        for relative in ('catalog.json', 'old-catalog.json', 'pending', 'journal.json', 'committed.json'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(dir=self.root) as directory:
                previous_root, self.root = self.root, Path(directory)
                try:
                    self.old()
                    stage = 'committed' if relative == 'committed.json' else 'catalog-published'
                    self.lose_process(document(), stage, 'hardlink-v1')
                    pending = self.root / PENDING_CATALOG
                    transaction = json.loads(pending.read_bytes())['transactionId']
                    path = (pending if relative == 'pending' else self.root / 'catalog.json'
                            if relative == 'catalog.json' else self.root / 'transactions' / transaction / relative)
                    raw = path.read_bytes()
                    path.rename(path.with_name(path.name + '.retained-test'))
                    path.write_bytes(raw)  # Same bytes, a foreign filesystem object.
                    before = snapshot(self.root)
                    with self.assertRaises(ValueError):
                        self.recover()
                    self.assertEqual(snapshot(self.root), before)
                finally:
                    self.root = previous_root

    def test_late_pending_cleanup_failure_never_rolls_back_durable_commit(self):
        self.old()
        new = document()
        with resource_write_lock(self.root), patch.object(activation, '_clear_pending', side_effect=OSError('fixture cleanup failed')):
            with self.assertRaisesRegex(ResourceCatalogError, 'commit-outcome-uncertain'):
                activation.activate_catalog_locked(self.root, new,
                    expected_catalog_stamp=CatalogSnapshot(self.root).catalog_stamp, validate=parse_catalog)
        self.assertEqual(self.recover()['status'], 'catalog-committed')
        self.assertEqual(CatalogSnapshot(self.root).generation, new['generation'])

    def test_semantic_admission_receives_exact_document_and_failure_preserves_old(self):
        _, stamp = self.old()
        new = document()
        calls = []
        def validate(value):
            self.assertEqual(value, new)
            calls.append(value)
            if len(calls) == 2:
                raise ValueError('actual-decoder-failure')
        with resource_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'actual-decoder-failure'):
                activation.activate_catalog_locked(self.root, new, expected_catalog_stamp=stamp, validate=validate)
        self.assertEqual(len(calls), 2)
        self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, stamp)

    def test_retained_outcome_keeps_exact_witness_after_pending_retirement(self):
        _, stamp = self.old()
        with resource_write_lock(self.root):
            result = activation.activate_catalog_locked(self.root, document(),
                expected_catalog_stamp=stamp, validate=parse_catalog)
            directory = self.root / 'transactions' / result['transactionId']
            journal_stamp = activation._stamp(directory / 'journal.json')
            outcome = activation.retained_catalog_outcome_locked(self.root, directory.name,
                expected_journal_stamp=journal_stamp)
            self.assertTrue(outcome['committed'])
            self.assertFalse((self.root / PENDING_CATALOG).exists())
            marker = directory / 'committed.json'
            raw = marker.read_bytes()
            marker.rename(directory / 'foreign-original-witness')
            marker.write_bytes(raw)
            with self.assertRaises(ValueError):
                activation.retained_catalog_outcome_locked(self.root, directory.name,
                    expected_journal_stamp=journal_stamp)

    def test_staging_edit_or_same_byte_replacement_is_not_recaptured_as_owned(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                _, stamp = self.old()
                intended, foreign = document(), document()
                write = activation.publication._write_new
                def changed_after_write(path, raw):
                    identity = write(path, raw)
                    if path.name == 'new-catalog.json':
                        if replace:
                            path.rename(path.with_name('retained-created-catalog.json'))
                        path.write_bytes(raw if replace else json.dumps(foreign).encode())
                    return identity
                with resource_write_lock(self.root), patch.object(activation.publication, '_write_new',
                                                                  side_effect=changed_after_write):
                    with self.assertRaisesRegex(ResourceCatalogError, 'staged-metadata-changed'):
                        activation.activate_catalog_locked(self.root, intended,
                            expected_catalog_stamp=stamp, validate=parse_catalog)
                self.assertEqual(CatalogSnapshot(self.root).catalog_stamp, stamp)


if __name__ == '__main__':
    unittest.main()
