"""Crash the real CLI in child processes; only native adapters are fixtures.

The JSON store below is a test-only stand-in for Windows registration. Product
code never consumes these files. File scanning, locks, hashing, and deletion
run through the actual Win32 business path, without patching deletion methods.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'tests')]
import installer_uninstall as u
import installer_uninstall_recovery as recovery
import test_installer_uninstall as fixtures


class _DiskFixture:
    def __init__(self, path, fault=''):
        self.path, self.fault = Path(path), fault
        self.data = json.loads(self.path.read_text('utf-8'))

    def save(self):
        temporary = self.path.with_suffix('.next')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def registrations(self): return self.data['rows'][:]
    def run_value(self, name):
        row = self.data['run'].get(name)
        return tuple(row) if row is not None else None
    def exit_values(self, prefix):
        return {key: tuple(value) for key, value in self.data['exit'].items() if key.startswith(prefix)}
    def delete_run_if_same(self, name, expected):
        if self.run_value(name) != expected:
            raise u.UninstallError('fixture-run-changed')
        del self.data['run'][name]
        self.save()
    def delete_exit_if_same(self, name, expected):
        if tuple(self.data['exit'][name]) != expected:
            raise u.UninstallError('fixture-exit-changed')
        del self.data['exit'][name]
        self.save()
    def read(self, identity): return self.data['recovery'].get(identity)
    def replace(self, identity, expected, replacement):
        if self.read(identity) != expected:
            raise u.UninstallError('fixture-recovery-changed')
        if replacement is None:
            del self.data['recovery'][identity]
        else:
            self.data['recovery'][identity] = replacement
        self.save()
        if self.fault == 'after-prepared-save' and replacement and json.loads(replacement)['phase'] == 'prepared':
            os._exit(95)
        if self.fault == 'after-final-delete' and replacement is None:
            os._exit(94)
    def snapshot_native(self, row):
        return dict(row=row) if row in self.data['rows'] else None
    def delete_native(self, row, snapshot):
        if self.fault == 'before-native-delete':
            os._exit(93)
        actual = self.snapshot_native(row)
        if actual is not None:
            if actual != snapshot:
                raise u.UninstallError('fixture-native-changed')
            self.data['rows'].remove(row)
            self.save()


@contextmanager
def _maintenance_fixture(_profile):
    # Test only: unique named object, never the installed product's mutex.
    app_id = str(uuid.uuid4()).upper()
    name = 'Global\\XingtuMaintenance-{' + app_id + '}'
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes, api.CreateMutexW.restype = [wt.LPVOID, wt.BOOL, wt.LPCWSTR], wt.HANDLE
    api.CloseHandle.argtypes, api.CloseHandle.restype = [wt.HANDLE], wt.BOOL
    parent = api.CreateMutexW(None, False, name)
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    profile = dict(product='Fixture', installerInstance=dict(id='mutex-fixture', appId=app_id, product='Fixture'))
    identity = dict(product='Fixture', appId=app_id, folder='fixture', executable='fixture.exe')
    try:
        with patch.dict(u._IDENTITIES, {'mutex-fixture': identity}):
            with recovery.hold_maintenance(profile):
                # Simulate the parent's handle disappearing. The real product
                # function must retain the object until this worker ends.
                api.CloseHandle(parent)
                parent = None
                ctypes.set_last_error(0)
                probe = api.CreateMutexW(None, False, name)
                exists = ctypes.get_last_error()
                if probe:
                    api.CloseHandle(probe)
                if exists != 183:
                    raise AssertionError('helper did not retain maintenance object')
                yield
    finally:
        if parent:
            api.CloseHandle(parent)


def _child(operation, state_path, root, result, lease_root, digest, fault):
    state = _DiskFixture(state_path, fault)
    profile = state.data['profile']
    expected_lease = Path(lease_root if operation == 'recover' else root)

    @contextmanager
    def lease(actual_root, **_parent_fixture):
        if u._path_key(actual_root) != u._path_key(expected_lease):
            raise u.UninstallError('fixture-lease-root-mismatch')
        api = u._kernel()
        handle, _ = u._open_handle(api, expected_lease / 'work/map-packages/import.lock',
                                   directory=False, read=True, write=True, share=0)
        try:
            yield
        finally:
            api.CloseHandle(handle)

    argv = ['--install-root', root, '--result-directory', result,
            '--parent-pid', '1', '--parent-created', '1', '--parent-executable', sys.executable,
            '--parent-sha256', hashlib.sha256((Path(root) / 'unins000.exe').read_bytes()).hexdigest()
            if operation != 'recover' else 'a' * 64,
            '--lock-handle', '5', '--helper-inventory-sha256', digest]
    if operation == 'recover':
        argv += ['--recover', '--recovery-lease-root', lease_root]
    else:
        argv += ['--program-ownership-sha256', state.data['ownershipDigest']]
    with patch.dict(os.environ, {'LOCALAPPDATA': state.data['local']}):
        return u.main(argv, registry_provider=state, recovery_store=state,
                      owner_sid_provider=lambda: state.data['sid'], profile_provider=lambda: profile,
                      lease_provider=lease, maintenance_provider=_maintenance_fixture)


@unittest.skipUnless(os.name == 'nt', 'Win32 subprocess fault injection')
class RecoveryProcessTests(unittest.TestCase):
    def setUp(self):
        task_tmp = ROOT / 'work/tmp'
        task_tmp.mkdir(parents=True, exist_ok=True)
        self.environ = patch.dict(os.environ, {'TEMP': str(task_tmp), 'TMP': str(task_tmp),
                                              'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        self.environ.start()
        self.addCleanup(self.environ.stop)
        self.fx = fixtures.InstallerUninstallTests('runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.root, self.resources, self.startup, profile = self.fx._production_tree()
        names = ['star-map-uninstall-helper.exe', '_star_map_uninstall_runtime/python313.dll',
                 '_star_map_uninstall_runtime/installation-profile.json']
        raw = ('\n'.join(hashlib.sha256((self.root / name).read_bytes()).hexdigest() + ' ' +
                         name.replace('/', '\\') for name in names) + '\n').encode('utf-8-sig')
        (self.root / '_uninstall-helper-files.txt').write_bytes(raw)
        self.digest = hashlib.sha256(raw).hexdigest()
        registry = fixtures._RegistryFixture(self.root, sid=self.fx.sid)
        self.state_path = self.fx.case / 'native-fixture.json'
        self.state_path.write_text(json.dumps(dict(rows=registry.rows, run=registry.run_values,
            exit=registry.exit_data, recovery={}, sid=self.fx.sid, local=str(self.fx.local), profile=profile,
            ownershipDigest=hashlib.sha256((self.root / '_installer-owned-files.txt').read_bytes()).hexdigest())), 'utf-8')

    def invoke(self, operation, name, fault=''):
        result = self.fx.case / (name + '-result')
        result.mkdir()
        lease_root = self.fx.case / (name + '-lease')
        self.fx._write(lease_root / 'work/map-packages/import.lock', b'')
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--fixture-child', operation,
            str(self.state_path), str(self.root), str(result), str(lease_root), self.digest, fault],
            cwd=ROOT, capture_output=True, timeout=20)
        return child.returncode, result, child.stderr.decode('utf-8', errors='replace')

    def native_start(self):
        lock = self.root / 'work/map-packages/import.lock'
        lock.unlink()
        lock.parent.rmdir()
        lock.parent.parent.rmdir()
        (self.root / 'unins000.exe').unlink()

    def test_crash_before_first_delete_retains_evidence_then_original_entry_can_retry(self):
        settings = self.root / 'work/nightmare-memory/ui-settings.json'
        before = settings.read_bytes()
        code, result, error = self.invoke('prepare', 'first', 'after-prepared-save')
        self.assertEqual(code, 95, error)
        self.assertEqual(settings.read_bytes(), before)
        self.assertFalse((result / 'result.ini').exists())
        record = json.loads(_DiskFixture(self.state_path).read('production'))
        self.assertEqual(record['phase'], 'prepared')
        code, _, error = self.invoke('prepare', 'retry')
        self.assertEqual(code, 0, error)
        self.native_start()
        code, _, error = self.invoke('recover', 'finish')
        self.assertEqual(code, 0, error)
        self.assertFalse(self.root.exists())
        self.assertEqual(_DiskFixture(self.state_path).data['recovery'], {})

    def test_worker_crash_after_program_removal_restarts_to_finish_registration(self):
        code, _, error = self.invoke('prepare', 'prepare')
        self.assertEqual(code, 0, error)
        self.native_start()
        code, result, error = self.invoke('recover', 'crash', 'before-native-delete')
        self.assertEqual(code, 93, error)
        self.assertFalse(self.root.exists())
        self.assertFalse((result / 'result.ini').exists())
        state = _DiskFixture(self.state_path)
        self.assertTrue(state.data['rows'])
        self.assertTrue(state.read('production'))
        code, result, error = self.invoke('recover', 'retry')
        self.assertEqual(code, 0, error)
        state = _DiskFixture(self.state_path)
        self.assertEqual(state.data['rows'], [])
        self.assertEqual(state.data['recovery'], {})
        self.assertEqual(self.fx._receipt(result)['status'], 'cleared')

    def test_lost_final_receipt_cannot_reauthorize_a_new_tree(self):
        code, _, error = self.invoke('prepare', 'prepare')
        self.assertEqual(code, 0, error)
        self.native_start()
        code, result, error = self.invoke('recover', 'lost', 'after-final-delete')
        self.assertEqual(code, 94, error)
        self.assertFalse((result / 'result.ini').exists())
        self.assertFalse(self.root.exists())
        state = _DiskFixture(self.state_path)
        self.assertEqual(state.data['rows'], [])
        self.assertEqual(state.data['recovery'], {})
        neighbor = self.fx._write(self.root / 'new-unregistered.bin', b'new unrelated tree')
        code, result, error = self.invoke('recover', 'repeat')
        self.assertEqual(code, 2, error)
        self.assertEqual(neighbor.read_bytes(), b'new unrelated tree')
        self.assertEqual(self.fx._receipt(result)['mutationStarted'], 'False')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--fixture-child':
        raise SystemExit(_child(*sys.argv[2:]))
    unittest.main()
