"""Windows temp-tree uninstall contract; registry and caller lease are narrow fixtures."""
from contextlib import contextmanager
import configparser
import json
import os
from pathlib import Path
import sys
import subprocess
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import installer_uninstall as uninstall
from game_startup import install_key, run_command, run_name
from packaged_product_identity import PRODUCTION_APP_ID, PRODUCTION_PRODUCT, _ISOLATED


class _RegistryFixture:
    def __init__(self, root, *, identity="production", app_id=PRODUCTION_APP_ID, hive="HKCU", sid=None):
        self.root = Path(root)
        self.rows = [dict(identity=identity, app_id=app_id, hive=hive, view=64,
                          sid=sid if hive == "HKCU" else None,
                          install_location=str(root),
                          uninstall_string='"' + str(self.root / "unins000.exe") + '"')]
        self.run_values, self.exit_data = {}, {}
        if identity == "production":
            self.run_values[run_name(root)] = (1, run_command(root))
            self.exit_data[run_name(root) + "-17"] = (1, '["123:456"]')
            self.exit_data["XingtuGameWatcher-other-install-17"] = (1, "[]")

    def registrations(self): return list(self.rows)
    def run_value(self, name): return self.run_values.get(name)
    def exit_values(self, prefix):
        return {name: value for name, value in self.exit_data.items() if name.startswith(prefix)}
    def delete_run_if_same(self, name, expected):
        if self.run_values.get(name) != expected:
            raise uninstall.UninstallError("fixture-run-value-changed")
        del self.run_values[name]
        return True
    def delete_exit_if_same(self, name, expected):
        if self.exit_data.get(name) != expected:
            raise uninstall.UninstallError("fixture-exit-value-changed")
        del self.exit_data[name]
        return True


class NativeRegistryAdapterTests(unittest.TestCase):
    def test_native_value_type_order_matches_preflight_for_run_and_exit(self):
        class Key:
            def __enter__(self): return self
            def __exit__(self, *_): pass
        key = Key()
        # Python's real QueryValueEx API returns (value, type), while our
        # provider's explicit public representation is (type, value).
        shim = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_QUERY_VALUE=1,
            KEY_SET_VALUE=2, KEY_WOW64_64KEY=4, OpenKey=Mock(return_value=key),
            QueryValueEx=Mock(return_value=('fixture-data', 1)), DeleteValue=Mock())
        provider = uninstall._RegistryProvider('fixture-sid')
        with patch.dict(sys.modules, {'winreg': shim}):
            for method in (provider.delete_run_if_same, provider.delete_exit_if_same):
                self.assertTrue(method('fixture-name', (1, 'fixture-data')))
            self.assertEqual(shim.DeleteValue.call_count, 2)
            shim.QueryValueEx.return_value = ('later-change', 1)
            for method in (provider.delete_run_if_same, provider.delete_exit_if_same):
                with self.assertRaises(uninstall.UninstallError) as caught:
                    method('fixture-name', (1, 'fixture-data'))
                self.assertIn('fixture-name', caught.exception.remaining[0])
            self.assertEqual(shim.DeleteValue.call_count, 2)


@unittest.skipUnless(os.name == 'nt', 'Windows native file cleanup')
class InstallerUninstallTests(unittest.TestCase):
    def setUp(self):
        temp_root = os.environ.get("TEMP") or os.environ.get("TMP")
        if not temp_root:
            self.fail("TEMP and TMP must point to the task's ../tmp directory")
        self.temp = tempfile.TemporaryDirectory(prefix="installer-uninstall-", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.case = self.base / "fixture"
        self.case.mkdir()
        self.local = self.case / "LocalAppData"
        self.local.mkdir()
        self.sid = uninstall._current_user_sid()

    def _profile(self, *, identity="production"):
        if identity == "production":
            return dict(product=PRODUCTION_PRODUCT,
                        installerInstance=dict(id="production", product=PRODUCTION_PRODUCT,
                                               appId=PRODUCTION_APP_ID))
        return dict(product="XingtuQtClientTest",
                    installerInstance=dict(id=identity, product="XingtuQtClientTest",
                                           appId=_ISOLATED[identity]))

    @staticmethod
    def _write(path, data=b"fixture"):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _production_tree(self, *, folder="XiaochaoAssistant", extra_root=False, pending=False,
                         external=True, external_pending=False):
        root = self.case / folder
        root.mkdir()
        profile = self._profile()
        profile_bytes = json.dumps(profile).encode("utf-8")
        owned = ["_internal/installation-profile.json", "_internal/runtime.dll", "XingtuGameWatcher.exe",
                 '_star_map_uninstall_runtime/python313.dll',
                 '_star_map_uninstall_runtime/installation-profile.json']
        self._write(root / "installation-profile.json", profile_bytes)
        self._write(root / "_internal/installation-profile.json", profile_bytes)
        self._write(root / "_internal/runtime.dll", b"changed user bytes")
        self._write(root / "_internal/assets/base.bin", b"base asset")
        self._write(root / "星图.exe", b"program")
        self._write(root / "XingtuGameWatcher.exe", b"watcher")
        self._write(root / "release-report.json", b"report")
        self._write(root / "version.json", b"version")
        self._write(root / "_installer-owned-files.txt",
                    ("\n".join(name.replace("/", "\\") for name in owned) + "\n").encode("utf-8-sig"))
        self._write(root / "_uninstall-helper-files.txt", b"native protection inventory")
        self._write(root / "star-map-uninstall-helper.exe", b"helper copied to temp")
        self._write(root / "unins000.exe", b"native uninstaller")
        self._write(root / "unins000.dat", b"native log")
        self._write(root / "work/nightmare-memory/ui-settings.json", b"modified settings")
        self._write(root / "work/map-packages/import.lock", b"")
        self._write(root / "work/installer-backups/previous.bin", b"migration backup")
        self._write(root / ".xingtu-uninstall-retained/files/orphan.bin", b"old retained data")
        self._write(root / "assets/custom-maps/user-route.json", b"edited custom route")
        self._write(root / "_star_map_uninstall_runtime/python313.dll", b"helper runtime")
        self._write(root / '_star_map_uninstall_runtime/installation-profile.json', profile_bytes)
        if extra_root:
            self._write(root / "unknown-neighbor.bin", b"must be preserved")
        if pending:
            self._write(root / "work/pending-installer-upgrade.json", b"{}")
        resources = self.local / "Xingtu" / "resources"
        if external:
            self._write(resources / "work/map-packages/import.lock", b"")
            self._write(resources / "packages/package.zip", b"modified imported map package")
            if external_pending:
                self._write(resources / "work/pending-installer-upgrade.json", b"{}")
        startup = self.local / "Xingtu" / "game-startup" / install_key(root)
        self._write(startup / "session/nested/state.json", b"session state")
        return root, resources, startup, profile

    @contextmanager
    def _real_app_lease(self, root, **_identity):
        api = uninstall._kernel()
        handle, _ = uninstall._open_handle(api, Path(root) / "work/map-packages/import.lock",
                                           directory=False, read=True, share=0)
        try:
            yield
        finally:
            api.CloseHandle(handle)

    def _invoke(self, root, result, registry, profile, *, lease=None, timeout=None):
        argv = ["--install-root", str(root), "--result-directory", str(result),
                "--parent-pid", "1", "--parent-created", "1",
                "--parent-executable", sys.executable, "--parent-sha256", "a" * 64,
                "--lock-handle", "5"]
        if timeout is not None:
            argv += ["--timeout-seconds", str(timeout)]
        return uninstall.main(argv, registry_provider=registry,
                              owner_sid_provider=lambda: self.sid,
                              profile_provider=lambda: profile,
                              lease_provider=lease or self._real_app_lease)

    @staticmethod
    def _receipt(result):
        receipt = configparser.ConfigParser(interpolation=None)
        receipt.read(Path(result) / "result.ini", encoding="utf-16")
        return receipt["result"]

    def test_main_clears_modified_app_and_production_data_with_real_windows_handles(self):
        root, resources, startup, profile = self._production_tree(folder="User Chosen Product")
        result = self.case / "result"
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        registry.rows.append(dict(registry.rows[0], view=32))
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(code, 0, (result / "error.txt").read_text("utf-8-sig")
                         if (result / "error.txt").exists() else "no error.txt")
        receipt = self._receipt(result)
        self.assertEqual(receipt["status"], "cleared")
        self.assertEqual(receipt["mutationStarted"], "True")
        self.assertEqual((root / "unins000.exe").read_bytes(), b"native uninstaller")
        self.assertEqual((root / "unins000.dat").read_bytes(), b"native log")
        self.assertTrue((root / "work/map-packages/import.lock").exists())
        self.assertEqual(sorted(path.name for path in root.iterdir()), [
            '_installer-owned-files.txt', '_star_map_uninstall_runtime', '_uninstall-helper-files.txt',
            'installation-profile.json', 'star-map-uninstall-helper.exe', 'unins000.dat', 'unins000.exe', 'work'])
        self.assertFalse((root / "work/nightmare-memory").exists())
        self.assertFalse(resources.exists())
        self.assertFalse(startup.exists())
        self.assertNotIn(run_name(root), registry.run_values)
        self.assertNotIn(run_name(root) + "-17", registry.exit_data)
        self.assertIn("XingtuGameWatcher-other-install-17", registry.exit_data)
        self.assertEqual((result / "remaining.txt").read_text("utf-8-sig"), "")

    def test_unknown_root_neighbor_fails_before_any_mutation(self):
        root, _resources, _startup, profile = self._production_tree(extra_root=True, external=False)
        result = self.case / "result"
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(code, 2)
        receipt = self._receipt(result)
        self.assertEqual(receipt["status"], "error")
        self.assertEqual(receipt["mutationStarted"], "False")
        self.assertTrue((root / "unknown-neighbor.bin").exists())
        self.assertTrue((root / "work/nightmare-memory/ui-settings.json").exists())
        self.assertIn("unknown-neighbor.bin", (result / "remaining.txt").read_text("utf-8-sig"),
                      (result / "error.txt").read_text("utf-8-sig"))

    def test_external_delete_failure_keeps_retry_program_then_retry_succeeds(self):
        root, resources, _startup, profile = self._production_tree()
        package = resources / 'packages/package.zip'
        package.chmod(0o444)
        registry = _RegistryFixture(root, sid=self.sid)
        result = self.case / 'first-result'
        result.mkdir()
        retry_files = ['installation-profile.json', '_installer-owned-files.txt',
            '_uninstall-helper-files.txt', 'star-map-uninstall-helper.exe',
            '_star_map_uninstall_runtime/python313.dll']
        before = {name: (root / name).read_bytes() for name in retry_files}
        try:
            with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
                first = self._invoke(root, result, registry, profile)
            self.assertEqual(first, 2)
            self.assertEqual(self._receipt(result)['mutationStarted'], 'True')
            self.assertIn('package.zip', (result / 'remaining.txt').read_text('utf-8-sig'))
            self.assertEqual({name: (root / name).read_bytes() for name in retry_files}, before)
        finally:
            package.chmod(0o666)
        retry = self.case / 'retry-result'
        retry.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            second = self._invoke(root, retry, registry, profile)
        self.assertEqual(second, 0, (retry / 'error.txt').read_text('utf-8-sig') if (retry / 'error.txt').exists() else '')
        self.assertFalse(resources.exists())
        self.assertTrue((root / 'star-map-uninstall-helper.exe').exists())

    def test_later_files_inside_retry_runtime_are_cleared_before_native_uninstall(self):
        root, _resources, _startup, profile = self._production_tree()
        later = root / '_star_map_uninstall_runtime/cache/later-added.log'
        self._write(later, b'not installed by native Inno')
        result = self.case / 'result'
        result.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile)
        self.assertEqual(code, 0, (result / 'error.txt').read_text('utf-8-sig') if (result / 'error.txt').exists() else '')
        self.assertFalse(later.parent.exists())
        self.assertTrue((root / '_star_map_uninstall_runtime/python313.dll').exists())
        self.assertTrue((root / '_star_map_uninstall_runtime/installation-profile.json').exists())

    def test_time_limit_after_real_deletions_stops_and_allows_clean_retry(self):
        root, resources, _startup, profile = self._production_tree()
        result = self.case / 'timeout-result'
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        original = uninstall._mark_delete
        deleted = []

        def slow_real_delete(api, handle, relative):
            original(api, handle, relative)
            deleted.append(relative)
            time.sleep(0.3)

        started = time.monotonic()
        # Only delay actual native deletes. Identity, scan, OS handles, lease,
        # mutation state, receipt and retry all execute the production path.
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}), \
                patch.object(uninstall, '_mark_delete', side_effect=slow_real_delete):
            code = self._invoke(root, result, registry, profile, timeout=1)
        self.assertEqual(code, 2)
        receipt = self._receipt(result)
        self.assertEqual(receipt['errorCode'], 'cleanup-time-limit')
        self.assertEqual(receipt['mutationStarted'], 'True')
        self.assertIn('已停止', receipt['message'])
        self.assertTrue(deleted)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue((root / 'star-map-uninstall-helper.exe').exists())
        self.assertTrue((root / 'unins000.dat').exists())
        self.assertTrue((root / '_star_map_uninstall_runtime/installation-profile.json').exists())
        after_timeout = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
        time.sleep(0.2)
        self.assertEqual(after_timeout, sorted(str(p.relative_to(root)) for p in root.rglob('*')))
        retry = self.case / 'timeout-retry'
        retry.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, retry, registry, profile)
        self.assertEqual(code, 0, (retry / 'error.txt').read_text('utf-8-sig')
                         if (retry / 'error.txt').exists() else '')
        self.assertEqual(self._receipt(retry)['status'], 'cleared')
        self.assertFalse(resources.exists())

    def test_missing_program_after_incomplete_install_still_clears_owned_data(self):
        root, resources, _startup, profile = self._production_tree()
        (root / '星图.exe').unlink()
        (root / 'XingtuGameWatcher.exe').unlink()
        result = self.case / 'incomplete-result'
        result.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile)
        self.assertEqual(code, 0, (result / 'error.txt').read_text('utf-8-sig')
                         if (result / 'error.txt').exists() else '')
        self.assertFalse(resources.exists())
        self.assertTrue((root / 'star-map-uninstall-helper.exe').exists())

    def test_timeout_after_external_lock_deletion_leaves_retryable_empty_tail(self):
        root, resources, _startup, profile = self._production_tree()
        registry = _RegistryFixture(root, sid=self.sid)
        first = self.case / 'external-tail-timeout'
        first.mkdir()
        original = uninstall._ExternalLease.delete

        def slow_real_lock_delete(lease):
            original(lease)
            time.sleep(1.1)

        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}), \
                patch.object(uninstall._ExternalLease, 'delete', slow_real_lock_delete):
            self.assertEqual(self._invoke(root, first, registry, profile, timeout=1), 2)
        self.assertEqual(self._receipt(first)['errorCode'], 'cleanup-time-limit')
        self.assertTrue(resources.exists())
        self.assertFalse((resources / 'work/map-packages/import.lock').exists())
        self.assertFalse(any(p.is_file() for p in resources.rglob('*')))
        retry = self.case / 'external-tail-retry'
        retry.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, retry, registry, profile)
        self.assertEqual(code, 0, (retry / 'error.txt').read_text('utf-8-sig')
                         if (retry / 'error.txt').exists() else '')
        self.assertFalse(resources.exists())

    def test_missing_lock_with_unknown_empty_directory_does_not_enter_empty_tail_recovery(self):
        root, resources, _startup, profile = self._production_tree()
        (resources / 'packages/package.zip').unlink()
        (resources / 'packages').rmdir()
        (resources / 'work/map-packages/import.lock').unlink()
        unknown = resources / 'unknown-owner'
        unknown.mkdir()
        result = self.case / 'unknown-empty-tail'
        result.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            self.assertEqual(self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile), 2)
        self.assertEqual(self._receipt(result)['errorCode'], 'external-resource-lock-missing')
        self.assertEqual(self._receipt(result)['mutationStarted'], 'False')
        self.assertTrue(unknown.exists())
        self.assertTrue((root / 'work/nightmare-memory/ui-settings.json').exists())

    @contextmanager
    def _running_python_image(self, directory):
        image = directory / '星图.exe'
        shutil.copyfile(sys.executable, image)
        dll = directory / 'python313.dll'
        shutil.copyfile(Path(sys.base_prefix) / 'python313.dll', dll)
        ready = directory / 'running-image.ready'
        code = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("ready"); sys.stdin.read(1)'
        process = subprocess.Popen([str(image), '-S', '-c', code, str(ready)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'PYTHONHOME': sys.base_prefix},
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), 'synthetic executable did not become ready')
            self.assertIsNone(process.poll())
            ready.unlink()
            yield image, dll
        finally:
            try:
                if process.poll() is None:
                    process.communicate(b'x', timeout=5)
                else:
                    process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
                self.fail('synthetic process needed forced teardown; not a graceful-exit pass')

    def test_running_exact_install_image_blocks_then_exit_allows_retry(self):
        root, _resources, _startup, profile = self._production_tree()
        ownership = root / '_installer-owned-files.txt'
        with ownership.open('a', encoding='utf-8') as stream:
            stream.write('python313.dll\n')
        registry = _RegistryFixture(root, sid=self.sid)
        result = self.case / 'running-result'
        result.mkdir()
        with self._running_python_image(root), \
                patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, result, registry, profile)
            self.assertEqual(code, 2)
            self.assertEqual(self._receipt(result)['mutationStarted'], 'False')
            self.assertTrue((root / 'work/nightmare-memory/ui-settings.json').exists())
        retry = self.case / 'running-retry'
        retry.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            self.assertEqual(self._invoke(root, retry, registry, profile), 0)

    def test_foreign_running_image_with_same_name_is_untouched(self):
        root, _resources, _startup, profile = self._production_tree()
        foreign = self.case / 'other-install'
        foreign.mkdir()
        result = self.case / 'foreign-running-result'
        result.mkdir()
        with self._running_python_image(foreign) as (image, dll), \
                patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            self.assertEqual(self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile), 0)
            self.assertTrue(image.exists())
            self.assertTrue(dll.exists())

    def test_metadata_cannot_be_written_between_read_and_delete_plan(self):
        root, _resources, _startup, profile = self._production_tree()
        result = self.case / 'result'
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        original = uninstall._load_ownership
        blocked = []
        def probe(folder, api, observations=None):
            value = original(folder, api, observations)
            try:
                (folder / '_installer-owned-files.txt').write_bytes(b'unknown-neighbor.bin\n')
            except OSError:
                blocked.append(True)
            return value
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}), \
                patch.object(uninstall, '_load_ownership', side_effect=probe):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(blocked, [True])
        self.assertEqual(code, 0, (result / 'error.txt').read_text('utf-8-sig') if (result / 'error.txt').exists() else '')

    def test_external_missing_lock_fails_before_clearing_app(self):
        root, resources, _startup, profile = self._production_tree()
        (resources / 'work/map-packages/import.lock').unlink()
        result = self.case / 'result'
        result.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile)
        self.assertEqual(code, 2)
        self.assertEqual(self._receipt(result)['mutationStarted'], 'False')
        self.assertIn('import.lock', (result / 'remaining.txt').read_text('utf-8-sig'))
        self.assertTrue((root / 'work/nightmare-memory/ui-settings.json').exists())

    def test_hard_link_to_original_is_refused_without_clearing_either_copy(self):
        root, _resources, _startup, profile = self._production_tree()
        original = self.case / 'original.bin'
        original.write_bytes(b'external original within disposable fixture')
        os.link(original, root / 'work/linked.bin')
        result = self.case / 'result'
        result.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile)
        self.assertEqual(code, 2)
        self.assertEqual(self._receipt(result)['mutationStarted'], 'False')
        self.assertEqual(original.read_bytes(), b'external original within disposable fixture')
        self.assertTrue((root / 'work/linked.bin').exists())

    def test_other_registration_at_drive_root_blocks_descendant(self):
        root, _resources, _startup, profile = self._production_tree()
        registry = _RegistryFixture(root, sid=self.sid)
        registry.rows.append(dict(identity='feature-test', app_id=_ISOLATED['feature-test'],
            hive='HKLM', view=64, install_location=root.anchor,
            uninstall_string=str(Path(root.anchor) / 'unins000.exe'), sid=None))
        result = self.case / 'result'
        result.mkdir()
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(code, 2)
        self.assertEqual(self._receipt(result)['mutationStarted'], 'False')
        self.assertTrue((root / 'work/nightmare-memory/ui-settings.json').exists())

    def test_junction_ancestor_is_refused_even_when_external_targets_are_missing(self):
        root, _resources, startup, profile = self._production_tree(external=False)
        (startup / 'session/nested/state.json').unlink()
        (startup / 'session/nested').rmdir()
        (startup / 'session').rmdir()
        startup.rmdir()
        startup.parent.rmdir()
        vendor = self.local / 'Xingtu'
        vendor.rmdir()
        target = self.case / 'other-data'
        target.mkdir()
        (target / 'original.txt').write_bytes(b'keep')
        created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(vendor), str(target)], capture_output=True)
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        try:
            result = self.case / 'result'
            result.mkdir()
            with patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}):
                code = self._invoke(root, result, _RegistryFixture(root, sid=self.sid), profile)
            self.assertEqual(code, 2)
            self.assertEqual(self._receipt(result)['mutationStarted'], 'False')
            self.assertEqual((target / 'original.txt').read_bytes(), b'keep')
            self.assertTrue((root / 'work/nightmare-memory/ui-settings.json').exists())
            self.assertIn('Xingtu', (result / 'remaining.txt').read_text('utf-8-sig'))
        finally:
            os.rmdir(vendor)  # Remove the exact fixture junction, not its target.

    def test_pending_transaction_blocks_full_clear(self):
        root, _resources, _startup, profile = self._production_tree(pending=True, external=False)
        result = self.case / "result"
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(code, 2)
        self.assertEqual(self._receipt(result)["mutationStarted"], "False")
        self.assertTrue((root / "work/pending-installer-upgrade.json").exists())
        self.assertTrue((root / "work/nightmare-memory/ui-settings.json").exists())

    def test_external_resource_lock_busy_blocks_before_any_mutation(self):
        root, resources, _startup, profile = self._production_tree()
        result = self.case / "result"
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        api = uninstall._kernel()
        ext_handle, _ = uninstall._open_handle(api, resources / "work/map-packages/import.lock",
                                               directory=False, read=True, write=True, share=0)
        try:
            with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}):
                code = self._invoke(root, result, registry, profile)
        finally:
            api.CloseHandle(ext_handle)
        self.assertEqual(code, 2)
        self.assertEqual(self._receipt(result)["mutationStarted"], "False")
        self.assertTrue((root / "work/nightmare-memory/ui-settings.json").exists())
        self.assertTrue((resources / "packages/package.zip").exists())

    def test_external_pending_transaction_blocks_full_clear(self):
        root, resources, _startup, profile = self._production_tree(external_pending=True)
        result = self.case / "result"
        result.mkdir()
        registry = _RegistryFixture(root, sid=self.sid)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(code, 2)
        self.assertEqual(self._receipt(result)["mutationStarted"], "False")
        self.assertTrue((resources / "work/pending-installer-upgrade.json").exists())
        self.assertTrue((root / "work/nightmare-memory/ui-settings.json").exists())

    def test_allowlisted_private_hklm_profile_uses_the_same_core(self):
        root = self.case / "custom-private-folder"
        root.mkdir()
        profile = self._profile(identity="feature-test")
        raw_profile = json.dumps(profile).encode("utf-8")
        self._write(root / "installation-profile.json", raw_profile)
        self._write(root / "XingtuQtClientTest.exe", b"private app")
        self._write(root / "unins000.exe", b"native uninstaller")
        self._write(root / "unins000.dat", b"native log")
        self._write(root / "_installer-owned-files.txt",
            b"_internal\\private.dll\n_star_map_uninstall_runtime\\installation-profile.json\n")
        self._write(root / '_star_map_uninstall_runtime/installation-profile.json', raw_profile)
        self._write(root / "_internal/private.dll", b"changed private data")
        self._write(root / "work/map-packages/import.lock", b"")
        result = self.case / "result"
        result.mkdir()
        registry = _RegistryFixture(root, identity="feature-test",
                                    app_id=_ISOLATED["feature-test"], hive="HKLM")
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.local)}):
            code = self._invoke(root, result, registry, profile)
        self.assertEqual(code, 0, (result / "error.txt").read_text("utf-8-sig")
                         if (result / "error.txt").exists() else "no error.txt")
        self.assertEqual(self._receipt(result)["status"], "cleared")
        self.assertTrue((root / "unins000.exe").exists())
        self.assertTrue((root / "unins000.dat").exists())
        self.assertFalse((root / "XingtuQtClientTest.exe").exists())
        self.assertFalse((root / "_internal").exists())


if __name__ == "__main__":
    unittest.main()
