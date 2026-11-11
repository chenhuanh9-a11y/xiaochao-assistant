"""Windows recovery tests using real temporary trees and native file handles."""
from contextlib import contextmanager
import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
import uuid
from ctypes import wintypes
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
APP = REPO / "app"
for path in (str(APP), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import installer_uninstall as uninstall
import test_installer_uninstall as _base_tests


_HELPER_FILES = (
    "star-map-uninstall-helper.exe",
    "_star_map_uninstall_runtime/installation-profile.json",
    "_star_map_uninstall_runtime/python313.dll",
)


class _RecoveryStoreFixture:
    """Narrow independent registration substitute with compare-and-swap semantics."""

    def __init__(self, registry):
        self.registry = registry
        self.values = {}
        self.native_snapshots = []
        self.native_delete_attempts = []

    def read(self, identity):
        return self.values.get(identity)

    def replace(self, identity, expected, new):
        if self.values.get(identity) != expected:
            return False
        if new is None:
            self.values.pop(identity, None)
        else:
            self.values[identity] = new
        return self.read(identity) == new

    def snapshot_native(self, row):
        for current in self.registry.rows:
            if current == row:
                snapshot = dict(row=copy.deepcopy(current),
                                values=[["DisplayName", 1, current["identity"]],
                                        ["InstallLocation", 1, current["install_location"]],
                                        ["UninstallString", 1, current["uninstall_string"]]])
                self.native_snapshots.append(copy.deepcopy(snapshot))
                return snapshot
        return None

    def delete_native(self, row, snapshot):
        self.native_delete_attempts.append((copy.deepcopy(row), copy.deepcopy(snapshot)))
        if snapshot is None or snapshot.get("row") != row:
            return False
        for index, current in enumerate(self.registry.rows):
            if current == row:
                del self.registry.rows[index]
                return True
        return False


@unittest.skipUnless(os.name == "nt", "Windows native file cleanup")
class InstallerUninstallRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.task_tmp = REPO / "work" / "tmp"
        self.task_tmp.mkdir(parents=True, exist_ok=True)
        self._temp_patch = patch.dict(os.environ, {
            "TEMP": str(self.task_tmp),
            "TMP": str(self.task_tmp),
        })
        self._temp_patch.start()
        self.addCleanup(self._temp_patch.stop)

        self.fixture = _base_tests.InstallerUninstallTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fx = self.fixture
        self._maintenance_name = "Local\\Codex-Test-XingtuMaintenance-" + uuid.uuid4().hex
        self._maintenance_observations = []

    @contextmanager
    def _maintenance_provider(self, _profile):
        """A real, test-unique Win32 parent mutex; never the production name."""
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        api.CreateMutexW.restype = wintypes.HANDLE
        api.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        api.OpenMutexW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        parent = api.CreateMutexW(None, False, self._maintenance_name)
        if not parent:
            raise ctypes.WinError(ctypes.get_last_error())
        child = None
        contender = None
        try:
            self.assertNotEqual(ctypes.get_last_error(), 183,
                                "test parent unexpectedly reused a mutex from another invoke")
            child = api.OpenMutexW(0x00100000, False, self._maintenance_name)
            if not child:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.set_last_error(0)
            contender = api.CreateMutexW(None, False, self._maintenance_name)
            if not contender:
                raise ctypes.WinError(ctypes.get_last_error())
            already_exists = ctypes.get_last_error()
            self._maintenance_observations.append(already_exists)
            self.assertEqual(already_exists, 183,
                             "a second CreateMutexW must observe the live parent object")
            yield
        finally:
            if contender:
                api.CloseHandle(contender)
            if child:
                api.CloseHandle(child)
            api.CloseHandle(parent)

    def _write_helper_inventory(self, root):
        rows = []
        for relative in _HELPER_FILES:
            path = root / relative
            self.assertTrue(path.is_file(), f"fixture is missing helper inventory object: {relative}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            windows_relative = relative.replace("/", "\\")
            rows.append(f"{digest} {windows_relative}")
        raw = ("\n".join(rows) + "\n").encode("utf-8")
        (root / "_uninstall-helper-files.txt").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _lease_for(expected_root):
        expected_root = Path(expected_root).resolve()

        @contextmanager
        def provider(actual_root, **_identity):
            actual_root = Path(actual_root).resolve()
            if os.path.normcase(str(actual_root)) != os.path.normcase(str(expected_root)):
                raise uninstall.UninstallError("fixture-lease-root-mismatch")
            lock = actual_root / "work" / "map-packages" / "import.lock"
            api = uninstall._kernel()
            handle, _ = uninstall._open_handle(api, lock, directory=False,
                                                read=True, write=True, share=0)
            try:
                yield
            finally:
                api.CloseHandle(handle)

        return provider

    def _make_result(self, name):
        result = self.fx.case / name
        result.mkdir()
        return result

    def _make_recovery_lease_root(self, name):
        lease_root = self.fx.case / name
        lock = lease_root / "work" / "map-packages" / "import.lock"
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b"")
        return lease_root

    @staticmethod
    def _simulate_inno_finalization(root, *, remove_native_log=True):
        # Setup removes the app-owned lock before invoking native Inno removal.
        lock = root / "work" / "map-packages" / "import.lock"
        lock.unlink()
        lock.parent.rmdir()
        lock.parent.parent.rmdir()
        # Native uninstall may already have removed these ordinary native files.
        native_files = ("unins000.exe", "unins000.dat") if remove_native_log else ("unins000.exe",)
        for name in native_files:
            path = root / name
            if path.exists():
                path.unlink()

    def _create_new_resources(self):
        resources = self.fx.local / "Xingtu" / "resources"
        lock = resources / "work" / "map-packages" / "import.lock"
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b"")
        package = resources / "packages" / "created-after-native-ready.zip"
        package.parent.mkdir(parents=True)
        package.write_bytes(b"new tree, not in the saved recovery record")
        return resources, lock, package

    @staticmethod
    def _truncate_native_handle(handle):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        set_pointer = api.SetFilePointerEx
        set_pointer.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
        set_pointer.restype = wintypes.BOOL
        set_end = api.SetEndOfFile
        set_end.argtypes = [wintypes.HANDLE]
        set_end.restype = wintypes.BOOL
        if not set_pointer(handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not set_end(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def _invoke(self, root, result, registry, profile, store, inventory_sha256, *,
                recover=False, lease_root=None, lease=None, parent_digest=None, caller_sid=None, ownership_digest=None,
                shortcuts=(), groups=()):
        argv = [
            "--install-root", str(root),
            "--result-directory", str(result),
            "--parent-pid", "1",
            "--parent-created", "1",
            "--parent-executable", sys.executable,
            "--parent-sha256", parent_digest or (hashlib.sha256((Path(root) / 'unins000.exe').read_bytes()).hexdigest()
                                               if not recover else 'a' * 64),
            "--lock-handle", "5",
            "--helper-inventory-sha256", inventory_sha256,
        ]
        if recover:
            if lease_root is None:
                raise AssertionError("recovery requires its separate parent-lease root")
            argv += ["--recover", "--recovery-lease-root", str(lease_root)]
        else:
            argv += ['--program-ownership-sha256', ownership_digest or
                     hashlib.sha256((Path(root) / '_installer-owned-files.txt').read_bytes()).hexdigest()]
        for path in shortcuts:
            argv += ['--shortcut', str(path)]
        for path in groups:
            argv += ['--shortcut-group', str(path)]
        if lease is None:
            lease = self._lease_for(lease_root if lease_root is not None else root)
        return uninstall.main(argv, registry_provider=registry,
                              owner_sid_provider=lambda: caller_sid or self.fx.sid,
                              profile_provider=lambda: profile,
                              lease_provider=lease,
                              recovery_store=store,
                              maintenance_provider=self._maintenance_provider)

    def test_recovery_authority_requires_the_registered_native_parent_image(self):
        root, _resources, _startup, profile = self.fx._production_tree()
        digest = self._write_helper_inventory(root)
        result = self._make_result('forged-parent-result')
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)
        store = _RecoveryStoreFixture(registry)
        before = self._snapshot_tree(root)
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, digest, parent_digest='0' * 64)
        self.assertEqual(code, 2)
        self.assertIn('recovery-native-parent-mismatch', (result / 'error.txt').read_text('utf-8-sig'))
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertEqual(store.values, {})
        self.assertEqual(self.fx._receipt(result)['mutationStarted'], 'False')

    def test_private_channel_preserves_original_owner_rule_when_caller_sid_differs(self):
        # Only the current-user identity provider is synthetic. Real Win32
        # ownership observations are unchanged; no ACL is modified.
        root, _resources, _startup, _ = self.fx._production_tree(external=False)
        profile = self.fx._profile(identity='client')
        raw = json.dumps(profile).encode('utf-8')
        for relative in ('installation-profile.json', '_internal/installation-profile.json',
                         '_star_map_uninstall_runtime/installation-profile.json'):
            (root / relative).write_bytes(raw)
        (root / '星图.exe').rename(root / 'XingtuQtClientTest.exe')
        digest = self._write_helper_inventory(root)
        expected = uninstall._IDENTITIES['client']
        registry = _base_tests._RegistryFixture(root, identity='client', app_id=expected['appId'], hive='HKLM')
        store = _RecoveryStoreFixture(registry)
        caller = 'S-1-5-21-111-222-333-4242'
        result = self._make_result('private-prepare')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, digest, caller_sid=caller)
        self.assertEqual(code, 0, (result / 'error.txt').read_text('utf-8-sig') if (result / 'error.txt').exists() else '')
        record = json.loads(store.read('client'))
        self.assertNotEqual(record['nodes']['']['owner'], record['sid'])
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        result = self._make_result('private-recover')
        lease_root = self._make_recovery_lease_root('private-lease')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, digest, recover=True,
                                lease_root=lease_root, caller_sid=caller)
        self.assertEqual(code, 0, (result / 'error.txt').read_text('utf-8-sig') if (result / 'error.txt').exists() else '')
        self.assertFalse(root.exists())
        self.assertEqual(store.values, {})

    def test_edited_program_ownership_cannot_claim_an_unknown_neighbor(self):
        root, _resources, _startup, profile = self.fx._production_tree()
        digest = self._write_helper_inventory(root)
        ownership = root / '_installer-owned-files.txt'
        original_digest = hashlib.sha256(ownership.read_bytes()).hexdigest()
        neighbor = self.fx._write(root / 'my-original-document.txt', b'unknown original')
        ownership.write_bytes(ownership.read_bytes() + b'\nmy-original-document.txt\n')
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)
        store = _RecoveryStoreFixture(registry)
        result = self._make_result('edited-inventory')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, digest, ownership_digest=original_digest)
        self.assertEqual(code, 2)
        self.assertEqual(neighbor.read_bytes(), b'unknown original')
        self.assertTrue((root / 'work/nightmare-memory/ui-settings.json').exists())
        self.assertEqual(store.values, {})
        self.assertIn('ownership-compiled-inventory-mismatch', (result / 'error.txt').read_text('utf-8-sig'))

    def test_an_unregistered_native_looking_neighbor_is_not_adopted(self):
        root, _resources, _startup, profile = self.fx._production_tree()
        digest = self._write_helper_inventory(root)
        neighbor = self.fx._write(root / 'unins999.dat', b'unknown native-looking neighbor')
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)
        store = _RecoveryStoreFixture(registry)
        result = self._make_result('extra-native')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, digest)
        self.assertEqual(code, 2)
        self.assertEqual(neighbor.read_bytes(), b'unknown native-looking neighbor')
        self.assertEqual(store.values, {})
        self.assertIn('recovery-unregistered-native-file', (result / 'error.txt').read_text('utf-8-sig'))

    def test_shortcut_group_owner_must_match_the_original_snapshot(self):
        root, _resources, _startup, profile = self.fx._production_tree()
        digest = self._write_helper_inventory(root)
        link = self.fx._write(self.fx.case / 'Owned Links' / 'Star.lnk', b'parent-verified synthetic shortcut')
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)
        store = _RecoveryStoreFixture(registry)
        result = self._make_result('group-prepare')
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, digest,
                                shortcuts=(link,), groups=(link.parent,))
        self.assertEqual(code, 0)
        # Fixture-only stale ownership observation; do not change real ACLs.
        self._rewrite_record(store, lambda record: record['groups'][str(link.parent)].update(owner='S-1-5-32-544'))
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        before = self._snapshot_tree(root)
        code, error, result, _lease = self._recover(root, registry, profile, store, digest, name='group-owner')
        self.assertEqual(code, 2)
        self.assertIn('recovery-shortcut-group-changed', error)
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertTrue(link.exists())
        self.assertEqual(self.fx._receipt(result)['mutationStarted'], 'False')

    def _prepare_native_ready(self, name="result", *, folder="XiaochaoAssistant"):
        root, _resources, _startup, profile = self.fx._production_tree(folder=folder)
        inventory_sha256 = self._write_helper_inventory(root)
        result = self._make_result(name)
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)
        store = _RecoveryStoreFixture(registry)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, inventory_sha256)
        error = (result / "error.txt").read_text("utf-8-sig") if (result / "error.txt").exists() else ""
        self.assertEqual(code, 0, error)
        self.assertEqual(len(store.values), 1, "normal cleanup must leave an independent recovery record")
        self.assertTrue(registry.rows, "pre-native cleanup must leave the ordinary registration for Inno")
        self.assertEqual(json.loads(next(iter(store.values.values())))["phase"], "native-ready")
        return root, result, registry, profile, store, inventory_sha256

    def _recover(self, root, registry, profile, store, inventory_sha256, *, name="recovery"):
        result = self._make_result(name + "-result")
        lease_root = self._make_recovery_lease_root(name + "-lease")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, inventory_sha256,
                                recover=True, lease_root=lease_root)
        error = (result / "error.txt").read_text("utf-8-sig") if (result / "error.txt").exists() else ""
        return code, error, result, lease_root

    @staticmethod
    def _rewrite_record(store, mutate):
        if len(store.values) != 1:
            raise AssertionError("expected one independent recovery registration")
        key, raw = next(iter(store.values.items()))
        record = json.loads(raw)
        mutate(record)
        store.values[key] = json.dumps(record, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"))

    def _snapshot_tree(self, root):
        entries = []
        for current, dirs, files in os.walk(root, followlinks=False):
            base = Path(current)
            for name in sorted(dirs + files):
                path = base / name
                relative = str(path.relative_to(root)).replace("/", "\\")
                if path.is_symlink():
                    entries.append((relative, "link", os.readlink(path)))
                elif path.is_dir():
                    entries.append((relative, "dir", None))
                else:
                    entries.append((relative, "file", path.read_bytes()))
        return tuple(sorted(entries))

    def test_native_ready_survives_missing_ordinary_registration_and_recovery_finishes(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        snapshot = store.native_snapshots[0]
        self.assertEqual(snapshot["row"]["install_location"], str(root))
        registry.rows.clear()  # Simulate Inno removing its ordinary native registration.
        original_tree = self._snapshot_tree(root)
        store_before = copy.deepcopy(store.values)
        legacy_result = self._make_result("missing-registration-legacy-result")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            legacy_code = self._invoke(root, legacy_result, registry, profile, store,
                                       inventory_sha256)
        self.assertEqual(legacy_code, 2)
        legacy_receipt = self.fx._receipt(legacy_result)
        self.assertEqual(legacy_receipt["mutationStarted"], "False")
        legacy_error = (legacy_result / "error.txt").read_text("utf-8-sig")
        self.assertIn("native-registration-not-unique", legacy_error)
        self.assertEqual(self._snapshot_tree(root), original_tree)
        self.assertEqual(store.values, store_before)
        self.assertTrue((root / "star-map-uninstall-helper.exe").exists())
        self.assertTrue((root / "_star_map_uninstall_runtime/python313.dll").exists())
        self._simulate_inno_finalization(root)

        code, error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256)
        self.assertEqual(code, 0, error)
        self.assertEqual(store.values, {})
        self.assertEqual(registry.rows, [])

    def test_recovery_cas_deletes_only_the_snapshotted_native_registration(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        saved_row = copy.deepcopy(registry.rows[0])
        self._simulate_inno_finalization(root)

        code, error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256)
        self.assertEqual(code, 0, error)
        self.assertEqual(registry.rows, [])
        self.assertEqual(store.native_delete_attempts, [(saved_row, store.native_snapshots[0])])
        self.assertEqual(store.values, {})

    def test_inno_truncated_same_native_log_is_allowed_after_ordinary_registration_is_gone(self):
        root, _resources, _startup, profile = self.fx._production_tree()
        inventory_sha256 = self._write_helper_inventory(root)
        result = self._make_result("native-log-result")
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)
        store = _RecoveryStoreFixture(registry)
        native_log = root / "unins000.dat"
        api = uninstall._kernel()
        handle, info = uninstall._open_handle(api, native_log, directory=False,
                                              read=True, write=True, share=0)
        original_id = list(uninstall._file_id(info))
        try:
            # The helper captures this object while its parent holds the exact
            # sharing-deny handle Inno uses for its uninstall log.
            with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
                code = self._invoke(root, result, registry, profile, store, inventory_sha256)
            error = (result / "error.txt").read_text("utf-8-sig") if (result / "error.txt").exists() else ""
            self.assertEqual(code, 0, error)
            self.assertEqual(len(store.values), 1)
            after_prepare = json.loads(next(iter(store.values.values())))
            self.assertEqual(after_prepare["phase"], "native-ready")
            logged = after_prepare["nodes"]["unins000.dat"]
            self.assertEqual(logged["identity"], original_id)
            self.assertTrue(logged["nativeLog"])
            self.assertNotIn("sha256", logged)
        finally:
            # Simulate Inno truncating the same open .dat object at uninstall
            # finalization; the file is retained because its handle is live.
            self._simulate_inno_finalization(root, remove_native_log=False)
            self._truncate_native_handle(handle)
            api.CloseHandle(handle)
        registry.rows.clear()
        self.assertTrue(native_log.exists())
        self.assertEqual(native_log.read_bytes(), b"")
        reopened, info = uninstall._open_handle(api, native_log, directory=False, read=True)
        try:
            self.assertEqual(list(uninstall._file_id(info)), original_id)
        finally:
            api.CloseHandle(reopened)
        code, error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256, name="truncated-native-log")
        self.assertEqual(code, 0, error)
        self.assertEqual(store.values, {})
        self.assertEqual(registry.rows, [])

    def test_recovery_store_write_refusal_stops_before_data_mutation(self):
        root, _resources, _startup, profile = self.fx._production_tree()
        inventory_sha256 = self._write_helper_inventory(root)
        result = self._make_result("store-write-refusal-result")
        registry = _base_tests._RegistryFixture(root, sid=self.fx.sid)

        class RefusingStore(_RecoveryStoreFixture):
            def replace(self, identity, expected, new):
                raise uninstall.UninstallError("fixture-recovery-store-write-denied")

        store = RefusingStore(registry)
        before = self._snapshot_tree(root)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            code = self._invoke(root, result, registry, profile, store, inventory_sha256)
        self.assertEqual(code, 2)
        self.assertEqual(self.fx._receipt(result)["mutationStarted"], "False")
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertTrue((root / "work/nightmare-memory/ui-settings.json").exists())
        self.assertTrue((self.fx.local / "Xingtu/resources/packages/package.zip").exists())
        self.assertEqual(store.values, {})

    def test_native_ready_rejects_new_resources_from_normal_and_recovery_entries(self):
        root, _result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        resources, lock, package = self._create_new_resources()
        root_before = self._snapshot_tree(root)
        store_before = copy.deepcopy(store.values)
        resource_before = self._snapshot_tree(resources)

        ordinary_result = self._make_result("native-ready-new-data-ordinary-result")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            ordinary = self._invoke(root, ordinary_result, registry, profile, store,
                                    inventory_sha256)
        self.assertEqual(ordinary, 2)
        self.assertEqual(self.fx._receipt(ordinary_result)["mutationStarted"], "False")
        self.assertEqual(self._snapshot_tree(root), root_before)
        self.assertEqual(self._snapshot_tree(resources), resource_before)
        self.assertEqual(store.values, store_before)

        recovery_code, _error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256,
            name="native-ready-new-data-recovery")
        self.assertEqual(recovery_code, 2)
        self.assertEqual(self._snapshot_tree(resources), resource_before)
        self.assertTrue(lock.exists())
        self.assertEqual(package.read_bytes(), b"new tree, not in the saved recovery record")
        self.assertEqual(store.values, store_before)

    def test_native_ready_recovery_clears_the_original_unfinished_lock_tail(self):
        root, _result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        lock = root / "work" / "map-packages" / "import.lock"
        self.assertTrue(lock.is_file())
        record = json.loads(next(iter(store.values.values())))
        self.assertTrue(record["nodes"]["work/map-packages/import.lock"]["resourceLock"])

        # _prepare_native_ready's normal invocation has released its real lock
        # handle, modelling Setup exiting before it starts native uninstall.
        code, error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256,
            name="unfinished-original-lock-tail")
        self.assertEqual(code, 0, error)
        self.assertFalse(root.exists())
        self.assertEqual(registry.rows, [])
        self.assertEqual(store.values, {})

    def test_cleared_store_rejects_replay_without_touching_files_or_registry(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        first, first_error, _first_result, lease_root = self._recover(
            root, registry, profile, store, inventory_sha256, name="first-recovery")
        self.assertEqual(first, 0, first_error)
        before = self._snapshot_tree(root)
        registry_before = copy.deepcopy(registry.rows)
        values_before = copy.deepcopy(store.values)
        second_result = self._make_result("replay-result")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            second = self._invoke(root, second_result, registry, profile, store, inventory_sha256,
                                  recover=True, lease_root=lease_root)
        self.assertEqual(second, 2)
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertEqual(registry.rows, registry_before)
        self.assertEqual(store.values, values_before)

    def test_unknown_neighbor_blocks_recovery_without_touching_registered_objects(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        store_before = copy.deepcopy(store.values)
        unknown = root / "added-after-native-ready.bin"
        unknown.write_bytes(b"must remain untouched")
        expected = self._snapshot_tree(root)
        code, _error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256)
        self.assertEqual(code, 2)
        self.assertEqual(self._snapshot_tree(root), expected)
        self.assertEqual(store.values, store_before)
        self.assertTrue(unknown.exists())

    def test_wrong_helper_inventory_digest_is_rejected_without_mutation(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        before = self._snapshot_tree(root)
        store_before = copy.deepcopy(store.values)
        recovery_result = self._make_result("wrong-digest-result")
        lease_root = self._make_recovery_lease_root("wrong-digest-lease")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            code = self._invoke(root, recovery_result, registry, profile, store, "0" * 64,
                                recover=True, lease_root=lease_root)
        self.assertEqual(code, 2)
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertEqual(store.values, store_before)

    def test_busy_helper_object_keeps_recovery_retryable(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        helper = root / _HELPER_FILES[0]
        before = self._snapshot_tree(root)
        store_before = copy.deepcopy(store.values)
        api = uninstall._kernel()
        handle, _ = uninstall._open_handle(api, helper, directory=False,
                                           read=True, write=True, share=0)
        try:
            blocked, _blocked_error, _blocked_result, lease_root = self._recover(
                root, registry, profile, store, inventory_sha256, name="busy-first")
            self.assertEqual(blocked, 2)
        finally:
            api.CloseHandle(handle)
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertEqual(store.values, store_before)

        retry_result = self._make_result("busy-retry-result")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            retried = self._invoke(root, retry_result, registry, profile, store, inventory_sha256,
                                   recover=True, lease_root=lease_root)
        error = (retry_result / "error.txt").read_text("utf-8-sig") if (retry_result / "error.txt").exists() else ""
        self.assertEqual(retried, 0, error)
        self.assertEqual(store.values, {})

    def test_readonly_native_residual_fails_after_real_deletes_then_retry_finishes(self):
        root, _result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root, remove_native_log=False)
        registry.rows.clear()
        native_log = root / "unins000.dat"
        os.chmod(native_log, stat.S_IREAD)
        original_id = list(json.loads(next(iter(store.values.values())))
                           ["nodes"]["unins000.dat"]["identity"])

        first_code, _first_error, first_result, lease_root = self._recover(
            root, registry, profile, store, inventory_sha256, name="readonly-first")
        self.assertEqual(first_code, 2)
        self.assertTrue(native_log.exists())
        self.assertEqual(len(store.values), 1)
        first_receipt = self.fx._receipt(first_result)
        self.assertEqual(first_receipt["mutationStarted"], "True")
        self.assertFalse((root / "_installer-owned-files.txt").exists())
        file_api = uninstall._kernel()
        current_handle, current_info = uninstall._open_handle(
            file_api, native_log, directory=False, read=True)
        try:
            self.assertEqual(list(uninstall._file_id(current_info)), original_id)
        finally:
            file_api.CloseHandle(current_handle)

        os.chmod(native_log, stat.S_IREAD | stat.S_IWRITE)
        retry_result = self._make_result("readonly-retry-result")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            retry = self._invoke(root, retry_result, registry, profile, store, inventory_sha256,
                                 recover=True, lease_root=lease_root)
        error = (retry_result / "error.txt").read_text("utf-8-sig") if (retry_result / "error.txt").exists() else ""
        self.assertEqual(retry, 0, error)
        self.assertEqual(store.values, {})

    def test_parent_lease_rejection_preserves_recovery_record_and_tree(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        before = self._snapshot_tree(root)
        store_before = copy.deepcopy(store.values)
        recovery_result = self._make_result("parent-reject-result")
        lease_root = self._make_recovery_lease_root("parent-reject-lease")

        @contextmanager
        def reject_lease(_root, **_identity):
            raise uninstall.UninstallError("fixture-parent-lease-rejected")
            yield  # pragma: no cover

        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.fx.local)}):
            code = self._invoke(root, recovery_result, registry, profile, store, inventory_sha256,
                                recover=True, lease_root=lease_root, lease=reject_lease)
        self.assertEqual(code, 2)
        self.assertEqual(self._snapshot_tree(root), before)
        self.assertEqual(store.values, store_before)
        self.assertEqual(registry.rows, [])

    def test_replaced_registered_object_is_refused(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        helper = root / _HELPER_FILES[0]
        api = uninstall._kernel()
        handle, before_info = uninstall._open_handle(api, helper, directory=False, read=True)
        try:
            before_id = list(uninstall._file_id(before_info))
        finally:
            api.CloseHandle(handle)
        replacement = self.fx.case / "replacement-helper.exe"
        replacement.write_bytes(helper.read_bytes())
        os.replace(replacement, helper)
        handle, after_info = uninstall._open_handle(api, helper, directory=False, read=True)
        try:
            self.assertNotEqual(list(uninstall._file_id(after_info)), before_id,
                                "replacement fixture must have a distinct Win32 file identity")
        finally:
            api.CloseHandle(handle)
        expected = self._snapshot_tree(root)
        store_before = copy.deepcopy(store.values)
        code, _error, _recovery_result, _lease_root = self._recover(
            root, registry, profile, store, inventory_sha256)
        self.assertEqual(code, 2)
        self.assertEqual(self._snapshot_tree(root), expected)
        self.assertEqual(store.values, store_before)

    def test_reparse_point_cannot_substitute_a_recorded_runtime_directory(self):
        root, result, registry, profile, store, inventory_sha256 = self._prepare_native_ready()
        self._simulate_inno_finalization(root)
        registry.rows.clear()
        runtime = root / "_star_map_uninstall_runtime"
        target = self.fx.case / "runtime-target"
        runtime.rename(target)
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(runtime), str(target)],
                                 capture_output=True, text=True)
        if created.returncode:
            target.rename(runtime)
            self.skipTest("Windows fixture cannot create a local junction: " + created.stderr.strip())
        try:
            before = {path.name: path.read_bytes() for path in target.iterdir() if path.is_file()}
            store_before = copy.deepcopy(store.values)
            code, _error, _recovery_result, _lease_root = self._recover(
                root, registry, profile, store, inventory_sha256)
            self.assertEqual(code, 2)
            self.assertEqual({path.name: path.read_bytes() for path in target.iterdir() if path.is_file()}, before)
            self.assertEqual(store.values, store_before)
            self.assertTrue(runtime.exists())
        finally:
            os.rmdir(runtime)  # Remove only the fixture junction, never its target.
            target.rename(runtime)

    def test_wrong_sid_product_and_registered_root_are_rejected(self):
        cases = (
            ("sid", lambda record: record.update(sid="S-1-5-21-999")),
            ("product", lambda record: record.update(identity="feature-test")),
            ("root", lambda record: record.update(root=str(self.fx.case / "other-install-root"))),
        )
        for label, mutate in cases:
            with self.subTest(identity_field=label):
                root, _result, registry, profile, store, inventory_sha256 = self._prepare_native_ready(
                    name=f"{label}-initial-result", folder=f"XiaochaoAssistant-{label}")
                self._simulate_inno_finalization(root)
                registry.rows.clear()
                self._rewrite_record(store, mutate)
                before = self._snapshot_tree(root)
                store_before = copy.deepcopy(store.values)
                code, _error, _recovery_result, _lease_root = self._recover(
                    root, registry, profile, store, inventory_sha256, name=f"{label}-recovery")
                self.assertEqual(code, 2)
                self.assertEqual(self._snapshot_tree(root), before)
                self.assertEqual(store.values, store_before)


if __name__ == "__main__":
    unittest.main()
