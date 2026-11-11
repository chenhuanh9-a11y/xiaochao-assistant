"""Real journal/readiness/native template with a controlled completion observer.

Borrowed-handle acquisition and the complete capture/verify/resume observer are
replaced. That observer reads real temporary program bytes; it does not produce
a native Windows object witness. Program inventory, preflight and protected
resource bytes are real. This is not a native Setup or installation acceptance.
"""
import base64
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import installer_native_observation as native
import installer_resource_lease as bridge
from installer_upgrade_coordinator import Coordinator, native_context
import installer_upgrade_journal as journal
import resource_upgrade_reset as resources


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ExternalObservation:
    def __init__(self, context):
        self.context = context

    def preflight_native_evidence(self, root, **context):
        return native.preflight_native_evidence(root, **context)

    def capture_native_completion(self, root, **context):
        for name, row in context['program_inventory'].items():
            raw = (Path(root) / name).read_bytes()
            if (len(raw), digest(raw)) != (row['bytes'], row['sha256']):
                raise ValueError('controlled-native-program-drift')
        return dict(externalWindowsObservation='controlled', target=context['target_identity'])

    def verify_native_completion(self, evidence, root, **context):
        if context != self.context or evidence != self.capture_native_completion(root, **context):
            raise ValueError('controlled-native-context-drift')
        return evidence

    def resume_context_from_evidence(self, evidence, root):
        return self.context


@unittest.skipUnless(os.name == 'nt', 'real Windows lexical program-inventory context')
class ProgramOnlyInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / 'install'
        self.root.mkdir()
        self.sid = 'S-1-5-21-1-2-3-1000'
        executable = b'synthetic program, never executed'
        self.target = dict(sourceCommit='a' * 40, displayVersion='1.2.3', numericVersion='1.2.3.4',
                           executableSha256=digest(executable), resourceInventorySha256='c' * 64)
        report = json.dumps(self.target).encode()
        ownership = '星图.exe\nrelease-report.json\n_installer-owned-files.txt\n_internal/runtime.dll\n'.encode()
        programs = {'星图.exe': executable, 'release-report.json': report,
                    '_installer-owned-files.txt': ownership, '_internal/runtime.dll': b'synthetic runtime'}
        self.inventory = dict(schema='xingtu-installer-program-inventory-v1', sourceCommit='a' * 40,
            ownershipSha256=digest(ownership), ownershipBase64=base64.b64encode(ownership).decode(),
            files={name: dict(bytes=len(raw), sha256=digest(raw)) for name, raw in programs.items()})
        for name, raw in programs.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.inventory_path = self.folder / 'program-inventory.json'
        self.inventory_path.write_bytes(json.dumps(self.inventory).encode())
        self.args = SimpleNamespace(action='prepare', install_root=str(self.root), confirmed=True,
            session_id=str(uuid.uuid4()), target_report_sha256=digest(report),
            plan_sha256=digest(self.inventory_path.read_bytes()), restore_watcher=False,
            candidate_directory=None, transaction_id=None, resource_action='preserve',
            program_inventory=str(self.inventory_path), program_inventory_sha256=digest(self.inventory_path.read_bytes()),
            uninstall_exe=str(self.root / 'unins000.exe'), group_shortcut=str(self.folder / 'links/星图.lnk'),
            uninstall_shortcut=str(self.folder / 'links/卸载星图.lnk'), desktop_shortcut=str(self.folder / 'desktop/星图.lnk'),
            group_enabled=False, desktop_enabled=False)
        self.observer = ExternalObservation(native_context(self.args, self.target))
        self.protected = {}
        for name in ('_internal/references/old-base.json', 'work/map-packages/legacy.receipt.json',
                     'work/user-routes/personal.json', 'work/settings.json'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('preserved:' + name).encode())
            self.protected[path] = (path.read_bytes(), path.stat().st_ino)
        stack = ExitStack()
        self.addCleanup(stack.close)
        owner = bridge._HandleOwner(SimpleNamespace(CloseHandle=lambda _: True), 123)
        stack.enter_context(patch.object(bridge, '_duplicate_verified', return_value=owner))
        self.lease = stack.enter_context(bridge.borrowed_installer_lease(self.root))
        for name in ('inspect_resource_upgrade', 'prepare_resource_upgrade', 'finish_resource_upgrade'):
            stack.enter_context(patch.object(resources, name, side_effect=AssertionError('legacy reset API called: ' + name)))

    def execute(self, action, target=None):
        self.args.action = action
        return Coordinator(self.args, resources, self.target if target is None else target,
            self.lease, self.sid, observer=self.observer).execute()

    def unchanged(self):
        for path, expected in self.protected.items():
            self.assertEqual((path.read_bytes(), path.stat().st_ino), expected)
        self.assertFalse((self.root / '_internal/managed-base-resources').exists())

    def test_full_success_preserves_resources_and_never_claims_resource_commit(self):
        for action in ('prepare', 'start-native', 'native-complete', 'commit'):
            result = self.execute(action)
            self.assertFalse(result['resourceCommitted'])
            self.assertNotIn('transactionId', result)
            self.unchanged()
        self.assertTrue(result['installerFinalized'])
        self.assertFalse((self.root / journal.PENDING).exists())
        self.assertEqual(self.execute('status')['status'], 'committed')

    def test_inspection_uses_existing_inventory_digest_without_resource_plan(self):
        from star_map_reset_helper import _execute
        self.args.action = 'inspect'
        result = _execute(self.args, resources, self.target)
        self.assertEqual(result['status'], 'inspected')
        self.assertEqual(result['resourceAction'], 'preserve')
        self.assertEqual(result['planSha256'], self.args.program_inventory_sha256)
        uuid.UUID(result['sessionId'])
        self.assertFalse((self.root / journal.PENDING).exists())
        self.unchanged()

    def test_pending_resources_and_handoffs_block_before_journal_creation(self):
        names = ('pending-resource-reset.json', 'pending-upgrade.json', 'pending-base-repair.json',
                 'pending-additions.json', 'pending-removal.json', 'pending-author-snapshot.json',
                 'qt-import-pending.json', 'qt-recovery-pending.json', 'main-import-request.json')
        for name in names:
            with self.subTest(name=name):
                marker = self.root / 'work/map-packages' / name
                marker.write_bytes(b'unknown pending operation')
                try:
                    with self.assertRaises(ValueError):
                        self.execute('prepare')
                    self.assertEqual(marker.read_bytes(), b'unknown pending operation')
                    self.assertFalse((self.root / journal.PENDING).exists())
                    self.unchanged()
                finally:
                    marker.unlink()

    def test_pre_native_cancel_only_clears_owned_marker(self):
        self.execute('prepare')
        result = self.execute('rollback')
        self.assertEqual(result['status'], 'aborted')
        self.assertFalse(result['resourceCommitted'])
        self.assertFalse((self.root / journal.PENDING).exists())
        self.unchanged()

    def test_new_handoff_after_prepare_blocks_native_start_without_clearing_it(self):
        self.execute('prepare')
        marker = self.root / 'work/map-packages/qt-import-pending.json'
        marker.write_bytes(b'new import pending')
        with self.assertRaises(ValueError):
            self.execute('start-native')
        self.assertEqual(marker.read_bytes(), b'new import pending')
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid)['status'], 'prepared')
        self.unchanged()

    def test_handoff_before_commit_keeps_native_complete_for_forward_recovery(self):
        for action in ('prepare', 'start-native', 'native-complete'):
            self.execute(action)
        marker = self.root / 'work/map-packages/qt-recovery-pending.json'
        marker.write_bytes(b'new recovery pending')
        with self.assertRaises(ValueError):
            self.execute('commit')
        self.assertEqual(marker.read_bytes(), b'new recovery pending')
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid)['status'], 'native-complete')
        self.unchanged()

    def test_uncertain_native_boundary_never_rolls_back_or_starts_again(self):
        self.execute('prepare')
        self.execute('start-native')
        for action in ('rollback', 'start-native'):
            with self.assertRaises(ValueError):
                self.execute(action)
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid)['status'], 'native-in-progress')
        self.unchanged()

    def test_changed_inventory_cannot_replace_reviewed_program_plan(self):
        self.execute('prepare')
        changed = deepcopy(self.inventory)
        changed['files']['_internal/runtime.dll']['sha256'] = 'f' * 64
        self.inventory_path.write_bytes(json.dumps(changed).encode())
        self.args.program_inventory_sha256 = digest(self.inventory_path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'program-plan-mismatch'):
            self.execute('start-native')
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid)['status'], 'prepared')
        self.unchanged()

    def test_native_program_drift_refuses_completion(self):
        self.execute('prepare')
        self.execute('start-native')
        (self.root / '星图.exe').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'program-drift'):
            self.execute('native-complete')
        self.assertEqual(journal.read_status(self.root, owner_sid=self.sid)['status'], 'native-in-progress')
        self.unchanged()

    def test_later_helper_resumes_from_old_bound_mode_and_context(self):
        for action in ('prepare', 'start-native', 'native-complete'):
            self.execute(action)
        self.args.resource_action = 'reset'
        self.args.program_inventory = str(self.folder / 'new-package-has-no-old-inventory.json')
        result = self.execute('resume-native-complete', dict(self.target, sourceCommit='f' * 40))
        self.assertTrue(result['installerFinalized'])
        self.assertFalse(result['resourceCommitted'])
        self.assertFalse((self.root / journal.PENDING).exists())
        self.unchanged()


if __name__ == '__main__':
    unittest.main()
