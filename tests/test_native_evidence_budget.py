"""Offline capacity admission, not a native Windows installation test."""
import hashlib
import json
import ntpath
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import installer_native_observation as native
import installer_upgrade_journal as journal
from installer_upgrade_coordinator import Coordinator, native_context


ROOT = r'C:\Users\fixture\App\Xingtu'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def context(count=0, depth=0, label='directory'):
    names = ['星图.exe', 'release-report.json', native.OWNERSHIP_NAME]
    names += ['/'.join([label] * depth + [f'candidate-{i}.png']) for i in range(count)]
    ownership = ('\n'.join(names) + '\n').encode('utf-8')
    inventory = {name: dict(bytes=7, sha256='a' * 64) for name in names}
    inventory[native.OWNERSHIP_NAME] = dict(bytes=len(ownership), sha256=digest(ownership))
    return dict(target_identity=dict(sourceCommit='a' * 40, displayVersion='1.2.3',
        numericVersion='1.2.3.4', executableSha256='a' * 64, resourceInventorySha256='b' * 64),
        target_report_sha256='a' * 64, ownership_bytes=ownership, ownership_sha256=digest(ownership),
        program_inventory=inventory, uninstall_exe=ROOT + r'\unins000.exe',
        shortcut_targets=[dict(path=fr'C:\links\{i}.lnk', target=ROOT + r'\星图.exe', enabled=True)
                          for i in range(3)])


def node_count(value):
    if isinstance(value, dict):
        return 1 + sum(1 + node_count(v) for v in value.values())
    if isinstance(value, list):
        return 1 + sum(map(node_count, value))
    return 1


class ModelBackend(native.WindowsReadOnlyBackend):
    """Exercise production ancestor enumeration with bounded synthetic metadata."""
    def __init__(self):
        self._directories = {}

    def _open(self, path, **kwargs):
        return path

    def _info(self, path, *, directory):
        row = dict(physicalPath=('\\\\?\\Volume{ffffffff-ffff-ffff-ffff-ffffffffffff}\\' + path[3:]).rstrip('\\').casefold(),
            identity=dict(volumeSerial=2**64-1, fileId='f'*32), created=2**64-1, attributes=2**32-1)
        if not directory:
            row.update(written=2**64-1, links=1)
        return row


def materialized_known(root, kw):
    contract = native._contract(root, kw['target_identity'], kw['target_report_sha256'],
        kw['ownership_bytes'], kw['ownership_sha256'], kw['program_inventory'],
        kw['uninstall_exe'], kw['shortcut_targets'])
    backend = ModelBackend()
    program = {}
    for name, expected in contract['programInventory'].items():
        path = ntpath.join(root, name.replace('/', '\\'))
        row = dict(state='file', ancestors=backend._parents(path), **backend._info(path, directory=False), **expected)
        if name == 'release-report.json':
            row['rawBase64'] = 'A' * (4 * ((expected['bytes'] + 2) // 3))
        program[name] = row
    return dict(schema=native.SCHEMA, source='win32-read-only', sha256='f'*64, contract=contract,
        observation=dict(users={}, root=backend.directory(root), program=program,
                         uninstall={}, registry={}, shortcuts=[]))


class NativeBudgetTests(unittest.TestCase):
    def test_counts_match_materialized_production_ancestor_shape(self):
        for label in ('directory', '地图😀', 'İ'):
            kw = context(5, 11, label)
            actual = materialized_known(ROOT, kw)
            result = native.preflight_native_evidence(ROOT, **kw)
            self.assertEqual(result['knownBytes'], len(native._encoded(actual)))
            self.assertEqual(result['knownNodes'], node_count(actual))
            journal._bounded(actual, evidence=True)

    def test_same_count_shallow_passes_deep_3000_rejects(self):
        self.assertGreater(native.preflight_native_evidence(ROOT, **context(3000))['knownNodes'], 0)
        for count in (3000, 4000):
            with self.assertRaisesRegex(native.NativeObservationError, 'evidence-budget-exceeded'):
                native.preflight_native_evidence(ROOT, **context(count, 11))

    def test_byte_and_node_boundaries_plus_minus_one(self):
        kw = context(2, 3)
        result = native.preflight_native_evidence(ROOT, **kw)
        for field, reserve, total, wrapper in (
                ('knownBytes', 'NATIVE_RESERVE_BYTES', native.MAX_METADATA, native.JOURNAL_RESERVE_BYTES),
                ('knownNodes', 'NATIVE_RESERVE_NODES', journal.MAX_JSON_NODES, native.JOURNAL_RESERVE_NODES)):
            for delta in (-1, 0, 1):
                with self.subTest(field=field, delta=delta), patch.object(native, reserve,
                        total - wrapper - result[field] - delta):
                    if delta < 0:
                        with self.assertRaisesRegex(native.NativeObservationError, 'evidence-budget-exceeded'):
                            native.preflight_native_evidence(ROOT, **kw)
                    else:
                        self.assertEqual(result[field], native.preflight_native_evidence(ROOT, **kw)[field])

    def test_report_base64_string_cap_checked_without_allocation(self):
        kw = context()
        kw['program_inventory']['release-report.json']['bytes'] = 3 * (journal.MAX_JSON_STRING // 4)
        native.preflight_native_evidence(ROOT, **kw)
        kw['program_inventory']['release-report.json']['bytes'] += 1
        with self.assertRaisesRegex(native.NativeObservationError, 'evidence-budget-exceeded'):
            native.preflight_native_evidence(ROOT, **kw)

    def test_journal_depth_container_key_and_string_constraints(self):
        for setting, value in [('MAX_JSON_DEPTH', 6), ('MAX_JSON_ITEMS', 2),
                               ('MAX_JSON_KEY', 10), ('MAX_JSON_STRING', 60)]:
            with self.subTest(setting=setting), patch.object(journal, setting, value):
                with self.assertRaisesRegex(native.NativeObservationError, 'evidence-budget-exceeded'):
                    native.preflight_native_evidence(ROOT, **context())

    def test_no_native_backend_or_old_registry_or_links_read(self):
        with patch.object(native, 'WindowsReadOnlyBackend', side_effect=AssertionError('native IO')):
            native.preflight_native_evidence(ROOT, **context())

    def test_prepare_3000_rejects_before_begin_or_resources(self):
        kw = context(3000, 11)
        args = SimpleNamespace(action='prepare', install_root=ROOT, confirmed=True)
        api, ledger = Mock(), Mock()
        coordinator = Coordinator(args, api, kw['target_identity'], object(), 'fixture')
        with patch('installer_upgrade_coordinator.read_status', return_value={'status': 'not-needed'}), \
                patch.object(coordinator, '_journal', return_value=ledger), \
                patch('installer_upgrade_coordinator.native_context', return_value=kw):
            with self.assertRaisesRegex(native.NativeObservationError, 'evidence-budget-exceeded'):
                coordinator.execute()
        self.assertEqual(ledger.mock_calls, [])
        self.assertEqual(api.mock_calls, [])

    def test_current_root_rechecked_before_start_native(self):
        kw = context(1400)
        native.preflight_native_evidence(ROOT, **kw)
        root = 'C:\\' + '\\'.join(['long-install-directory'] * 20)
        kw['uninstall_exe'] = root + r'\unins000.exe'
        args = SimpleNamespace(action='start-native', install_root=root, transaction_id=None)
        ledger = Mock()
        ledger.status.return_value = dict(resourceTransactionId=None)
        coordinator = Coordinator(args, Mock(), kw['target_identity'], object(), 'fixture')
        with patch('installer_upgrade_coordinator.read_status', return_value={'status': 'prepared'}), \
                patch.object(coordinator, '_journal', return_value=ledger), \
                patch('installer_upgrade_coordinator.native_context', return_value=kw) as fresh:
            with self.assertRaisesRegex(native.NativeObservationError, 'evidence-budget-exceeded'):
                coordinator.execute()
        fresh.assert_called_once_with(args, kw['target_identity'])
        ledger.start_native.assert_not_called()

    def test_pinned_inventory_rehashed_on_every_read(self):
        import base64
        import tempfile
        kw = context()
        document = dict(schema='xingtu-installer-program-inventory-v1', sourceCommit=kw['target_identity']['sourceCommit'],
            ownershipSha256=kw['ownership_sha256'], ownershipBase64=base64.b64encode(kw['ownership_bytes']).decode(),
            files=kw['program_inventory'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inventory.json'
            data = json.dumps(document).encode()
            path.write_bytes(data)
            args = SimpleNamespace(program_inventory=str(path), program_inventory_sha256=digest(data),
                install_root=ROOT, target_report_sha256=kw['target_report_sha256'], uninstall_exe=kw['uninstall_exe'],
                group_shortcut=kw['shortcut_targets'][0]['path'], uninstall_shortcut=kw['shortcut_targets'][1]['path'],
                desktop_shortcut=kw['shortcut_targets'][2]['path'], group_enabled=True, desktop_enabled=True)
            native.preflight_native_evidence(ROOT, **native_context(args, kw['target_identity']))
            path.write_bytes(data + b' ')
            with self.assertRaisesRegex(ValueError, 'installer-program-inventory-changed'):
                native_context(args, kw['target_identity'])
