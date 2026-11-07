"""Synthetic owned bytes only: exact normal-mode retirement and crash recovery."""
import copy
import json
import unittest
from unittest.mock import patch

import managed_base_resources as managed
import s1_resource_retirement as policy
from test_s1_production_base_resources import ProductionS1BaseResourceContracts


class NormalRetirementTests(unittest.TestCase):
    # Reuse fixture helpers, not the parent's independent three-mode scenarios.
    fixture = ProductionS1BaseResourceContracts.fixture
    row = staticmethod(ProductionS1BaseResourceContracts.row)
    allow = ProductionS1BaseResourceContracts.allow
    stage = ProductionS1BaseResourceContracts.stage

    def setUp(self):
        ProductionS1BaseResourceContracts.setUp(self)
        self.old = self.fixture(policy.SOURCE_VERSION)
        self.new = copy.deepcopy(self.old)
        self.new['version'] = policy.TARGET_VERSION
        self.new['data'] = {p: b for p, b in self.old['data'].items() if not p.startswith(policy.RETIRED_PREFIX)}
        self.new['rows'] = [self.row(p, b) for p, b in sorted(self.new['data'].items())]
        self.retired = [r for r in self.old['rows'] if r['path'].startswith(policy.RETIRED_PREFIX)]
        self.allow(self.old)

    def install_old(self):
        self.stage(self.old)
        managed.repair_managed_base_resources(self.root)
        self.old_state = (self.root / managed.STATE).read_bytes()
        self.stage(self.new)

    def assert_old_restored(self):
        self.assertEqual((self.root / managed.STATE).read_bytes(), self.old_state)
        for p, data in self.old['data'].items():
            self.assertEqual((self.root / p).read_bytes(), data)

    def test_retirement_fresh_install_has_only_active_modes(self):
        self.stage(self.new)
        managed.repair_managed_base_resources(self.root)
        self.assertTrue(all((self.root / p).exists() for p in self.new['data']))
        self.assertTrue(all(not (self.root / r['path']).exists() for r in self.retired))

    def test_retirement_removes_exact_owned_bytes_and_is_idempotent(self):
        self.install_old()
        custom = self.root / 'work/user-note.txt'; custom.write_bytes(b'user note')
        result = managed.repair_managed_base_resources(self.root)
        self.assertEqual(result['changed'], len(self.retired))
        self.assertTrue(all(not (self.root / r['path']).exists() for r in self.retired))
        self.assertEqual(managed._manifest(self.root / managed.STATE)['resourceVersion'], policy.TARGET_VERSION)
        self.assertEqual(custom.read_bytes(), b'user note')
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'healthy')
        self.assertFalse((self.root / managed.PENDING).exists())

    def test_retirement_missing_old_file_is_not_recreated(self):
        self.install_old()
        (self.root / self.retired[0]['path']).unlink()
        managed.repair_managed_base_resources(self.root)
        self.assertEqual(managed._manifest(self.root / managed.STATE)['resourceVersion'], policy.TARGET_VERSION)

    def test_retirement_preserves_modified_old_bytes_and_ownership(self):
        self.install_old()
        changed = self.root / self.retired[0]['path']; changed.write_bytes(b'user edit')
        result = managed.repair_managed_base_resources(self.root)
        self.assertEqual(result['status'], 'retirement-deferred')
        self.assertEqual(result['retirementDeferred'][0]['reason'], 'retired-mode-local-content-preserved')
        self.assertEqual(changed.read_bytes(), b'user edit')
        self.assertEqual((self.root / managed.STATE).read_bytes(), self.old_state)

    def test_retirement_deferral_does_not_bypass_active_repair(self):
        self.install_old()
        (self.root / self.retired[0]['path']).write_bytes(b'user edit')
        missing = self.root / self.new['rows'][0]['path']; missing.unlink()
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-repair-preflight-blocked'):
            managed.repair_managed_base_resources(self.root)
        self.assertFalse(missing.exists())
        self.assertEqual((self.root / managed.STATE).read_bytes(), self.old_state)

    def test_retirement_retains_dependency_and_unrecorded_package(self):
        self.install_old()
        for constraints in (({}, [('history', self.retired[0])], []), ({}, [], ['history'])):
            with self.subTest(constraints=constraints), patch.object(managed, '_package_constraints', return_value=constraints):
                result = managed.repair_managed_base_resources(self.root)
                self.assertEqual(result['status'], 'retirement-deferred')
                self.assert_old_restored()

    def test_retirement_startup_report_is_nonblocking_and_does_not_repeat_inventory(self):
        from client_resource_startup import production_resources
        self.install_old()
        (self.root / self.retired[0]['path']).write_bytes(b'user edit')
        report = {}
        with production_resources(self.root, production=True, startup_report=report) as recovery:
            self.assertIsNone(recovery)
        self.assertEqual(report, dict(status='retirement-deferred', reasons=['retired-mode-local-content-preserved']))
        self.assertEqual((self.root / managed.STATE).read_bytes(), self.old_state)

    def test_retirement_crash_restores_all_owned_objects(self):
        self.install_old()
        original = managed.transaction._move_owned
        count = 0
        def crash(*args, **kwargs):
            nonlocal count
            original(*args, **kwargs)
            count += 1
            if count == 1:
                raise KeyboardInterrupt('simulated process loss after removal')
        with patch.object(managed.transaction, '_move_owned', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            managed.repair_managed_base_resources(self.root)
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertEqual(managed.recover_managed_base_resources(self.root)['status'], 'rolled-back')
        self.assert_old_restored()
        self.assertFalse((self.root / managed.PENDING).exists())

    def test_retirement_exception_rolls_back_before_reporting(self):
        self.install_old()
        original = managed.transaction._move_owned
        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('synthetic write failure')
        with patch.object(managed.transaction, '_move_owned', side_effect=fail), self.assertRaises(OSError):
            managed.repair_managed_base_resources(self.root)
        self.assert_old_restored()

    def test_retirement_recovery_preserves_reappeared_unknown_content(self):
        self.install_old()
        original = managed.transaction._move_owned
        removed = []
        def crash(source, *args, **kwargs):
            original(source, *args, **kwargs)
            removed.append(source)
            raise KeyboardInterrupt('simulated crash')
        with patch.object(managed.transaction, '_move_owned', side_effect=crash), self.assertRaises(KeyboardInterrupt):
            managed.repair_managed_base_resources(self.root)
        removed[0].write_bytes(b'new user data')
        with self.assertRaises(managed.ManagedBaseResourceError):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(removed[0].read_bytes(), b'new user data')
        self.assertTrue((self.root / managed.PENDING).exists())

    def test_retirement_journal_cannot_expand_delete_authority(self):
        self.install_old()
        with patch.object(managed.transaction, '_move_owned', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            managed.repair_managed_base_resources(self.root)
        pointer = json.loads((self.root / managed.PENDING).read_bytes())
        path = self.root / managed.HISTORY / pointer['transactionId'] / 'journal.json'
        journal = json.loads(path.read_bytes())
        journal['retirement']['files'].append(self.new['rows'][0])
        path.write_bytes(managed._encoded(journal))
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'invalid-base-retirement-inventory'):
            managed.recover_managed_base_resources(self.root)
        self.assert_old_restored()


if __name__ == '__main__':
    unittest.main()
