"""Real run() and path resolution with isolated files and injected failures only.

No installed app, native dialog, ACL modification, Qt engine or game is used.
"""
import builtins
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tools")]
import packaged_product_identity as identity
import qt_client_entry as entry


class StartupFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match product paths after Windows expands short temporary-directory names.
        self.root = Path(self.temp.name).resolve()
        self.install = self.root / "installation"
        self.runtime = self.install / "_internal"
        self.runtime.mkdir(parents=True)
        self.enterContext(patch.object(sys, "frozen", True, create=True))
        self.enterContext(patch.object(sys, "_MEIPASS", str(self.runtime), create=True))
        self.enterContext(patch.object(sys, "executable", str(self.install / "renamed.exe")))
        self.profile = dict(product=identity.PRODUCTION_PRODUCT, installerInstance=dict(
            id="production", product=identity.PRODUCTION_PRODUCT, appId=identity.PRODUCTION_APP_ID))

    def bake(self, profile=None):
        (self.runtime / "installation-profile.json").write_text(
            json.dumps(self.profile if profile is None else profile), encoding="utf-8")

    def load_paths(self):
        spec = importlib.util.spec_from_file_location("_startup_fixture_paths", ROOT / "experiments/qt_quick/client_paths.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
        return module


class QtProfileBoundaryTests(StartupFixture):
    def test_missing_internal_profile_cannot_seed_a_new_runtime_even_with_root_profile(self):
        (self.install / "installation-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "[Pp]rofile"):
            self.load_paths()
        self.assertFalse((self.install / "work").exists())

    def test_valid_production_stays_beside_executable(self):
        self.bake()
        paths = self.load_paths()
        self.assertTrue(paths.PRODUCTION)
        self.assertEqual(paths.DEFAULT_SANDBOX, self.install)

    def test_corrupt_internal_profile_cannot_be_replaced_by_root_metadata(self):
        (self.install / "installation-profile.json").write_text(json.dumps(self.profile), encoding="utf-8")
        for data in ("{}", "[]", "{", json.dumps(dict(self.profile, installerInstance={}))):
            with self.subTest(data=data):
                (self.runtime / "installation-profile.json").write_text(data, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "[Pp]rofile"):
                    self.load_paths()
        self.assertFalse((self.install / "work").exists())

    def test_missing_frozen_runtime_has_explicit_error_but_source_does_not_require_profile(self):
        with patch.object(sys, "_MEIPASS", None):
            with self.assertRaisesRegex(ValueError, "[Pp]rofile"):
                identity.baked_identity(require_qt=True)
            self.assertEqual(identity.baked_identity(), "legacy-tk")
        with patch.object(sys, "frozen", False):
            self.assertEqual(identity.baked_identity(require_qt=True), "legacy-tk")

    def test_explicit_isolated_and_source_keep_existing_paths(self):
        self.bake(dict(product="XingtuQtClientTest", installerInstance=dict(id="client",
            product="XingtuQtClientTest", appId="AF19AFD9-3016-44C9-9D20-6F216C5D8C51")))
        paths = self.load_paths()
        self.assertFalse(paths.PRODUCTION)
        self.assertEqual(paths.DEFAULT_SANDBOX, self.install / "work/qt-client-runtime")
        with patch.object(sys, "frozen", False):
            self.assertFalse(self.load_paths().PRODUCTION)

    def test_legacy_helpers_still_require_only_their_existing_profile_contract(self):
        self.assertEqual(identity.baked_identity(), "legacy-tk")
        self.assertEqual(identity.expected_application_executable(), identity.LEGACY_EXE)

    def test_production_rejects_a_different_installation_root(self):
        self.bake()
        paths = self.load_paths()
        self.assertEqual(paths.runtime_paths().installation_root, self.install)
        with self.assertRaisesRegex(ValueError, "Production data must stay beside"):
            paths.runtime_paths(self.root / "other-installation")

    def test_isolated_root_requires_both_owner_marker_and_work_boundary(self):
        self.bake(dict(product="XingtuQtClientTest", installerInstance=dict(id="client",
            product="XingtuQtClientTest", appId="AF19AFD9-3016-44C9-9D20-6F216C5D8C51")))
        paths = self.load_paths()
        sandbox = paths.DEFAULT_SANDBOX
        with self.assertRaisesRegex(ValueError, "own marked work directory"):
            paths.runtime_paths()
        sandbox.mkdir(parents=True)
        (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.assertEqual(paths.runtime_paths().installation_root, sandbox)
        outside = self.root / "other-installation"
        outside.mkdir()
        (outside / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "own marked work directory"):
            paths.runtime_paths(outside)

    def test_linked_profile_and_explicit_runtime_root_stay_rejected(self):
        self.bake()
        paths = self.load_paths()
        alias = self.root / "linked-installation"
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(self.install), str(alias))
        else:
            alias.symlink_to(self.install, target_is_directory=True)
        self.assertEqual(alias.resolve(), self.install)
        with patch.object(sys, "_MEIPASS", str(alias / "_internal")):
            with self.assertRaisesRegex(identity.InstallationProfileError, "cannot use links"):
                self.load_paths()
        with self.assertRaisesRegex(ValueError, "links or reparse points"):
            paths.runtime_paths(alias)


class StartupAliasFixtureTests(unittest.TestCase):
    def test_equivalent_temp_roots_keep_original_path_and_profile_assertions(self):
        # A real, existing hop/.. alias reproduces the fixture mismatch without
        # mocking Path.resolve or weakening the product's link checks. This is
        # portable path-alias coverage, not a native Windows 8.3-path claim.
        cases = (
            "test_valid_production_stays_beside_executable",
            "test_explicit_isolated_and_source_keep_existing_paths",
            "test_missing_internal_profile_cannot_seed_a_new_runtime_even_with_root_profile",
            "test_corrupt_internal_profile_cannot_be_replaced_by_root_metadata",
        )
        for name in cases:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as folder:
                hop = Path(folder) / "alias-hop"
                hop.mkdir()
                alias = hop / ".."
                self.assertNotEqual(alias, alias.resolve())
                temporary = types.SimpleNamespace(name=str(alias), cleanup=lambda: None)
                case = QtProfileBoundaryTests(name)
                try:
                    with patch.object(tempfile, "TemporaryDirectory", return_value=temporary):
                        case.setUp()
                    getattr(case, name)()
                finally:
                    case.doCleanups()


class QtEntryFailureTests(StartupFixture):
    def diagnostics(self):
        import qt_startup_diagnostics as diagnostics
        self.enterContext(patch.object(diagnostics, "_show_native_error"))
        self.enterContext(patch.object(diagnostics, "_candidate_directories", return_value=[
            self.install / "work", self.root / "localappdata/Xingtu/diagnostics",
            self.root / "temp/Xingtu/diagnostics"]))
        self.enterContext(patch.object(sys, "stderr", None))
        return diagnostics

    def fail_client_import(self, error):
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == "client":
                raise error
            return original(name, *args, **kwargs)
        self.enterContext(patch("builtins.__import__", side_effect=importing))

    def test_missing_profile_fails_before_client_import_with_one_visible_report(self):
        diagnostics = self.diagnostics()
        called = []
        with patch.dict(sys.modules, {"client": types.SimpleNamespace(main=lambda: called.append(True))}):
            self.assertEqual(entry.run(), 1)
        self.assertFalse(called)
        diagnostics._show_native_error.assert_called_once()
        text = (self.install / "work/qt-client-startup-error.txt").read_text(encoding="utf-8")
        self.assertIn("QT-STARTUP-PROFILE", text)
        self.assertFalse((self.install / "work/qt-client-runtime").exists())

    def test_dependency_failure_survives_denied_work_directory_without_private_text(self):
        self.bake()
        diagnostics = self.diagnostics()
        self.fail_client_import(ModuleNotFoundError("secret-token=config-value", name="PySide6.QtCore",
                                                  path=str(self.root / "private-config/QtCore.pyd")))
        original = Path.mkdir
        denied = self.install / "work"
        def mkdir(path, *args, **kwargs):
            if path == denied:
                raise PermissionError("private-path-config-value")
            return original(path, *args, **kwargs)
        with patch.object(Path, "mkdir", mkdir):
            self.assertEqual(entry.run(), 1)
        log = self.root / "localappdata/Xingtu/diagnostics/qt-client-startup-error.txt"
        text = log.read_text(encoding="utf-8")
        self.assertIn("QT-STARTUP-DEPENDENCY", text)
        self.assertIn("PySide6.QtCore", text)
        self.assertIn("dependencyBinary=QtCore.pyd", text)
        self.assertNotIn("private-config", text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("config-value", text)
        self.assertLess(log.stat().st_size, 16384)
        diagnostics._show_native_error.assert_called_once()
        message = diagnostics._show_native_error.call_args.args[0]
        self.assertIn(log.name, message)
        self.assertNotIn(str(self.root), message)

    def test_temp_fallback_and_all_denied_still_report_once(self):
        self.bake()
        diagnostics = self.diagnostics()
        self.fail_client_import(OSError(126, "sensitive DLL load error in user configuration"))
        original = Path.mkdir
        allowed = self.root / "temp"
        def mkdir(path, *args, **kwargs):
            if not path.is_relative_to(allowed):
                raise PermissionError("simulated access denied")
            return original(path, *args, **kwargs)
        with patch.object(Path, "mkdir", mkdir):
            self.assertEqual(entry.run(), 1)
        log = self.root / "temp/Xingtu/diagnostics/qt-client-startup-error.txt"
        self.assertIn("errno=126", log.read_text(encoding="utf-8"))
        diagnostics._show_native_error.assert_called_once()
        diagnostics._show_native_error.reset_mock()
        with patch.object(Path, "mkdir", side_effect=PermissionError("simulated all locations denied")):
            self.assertEqual(entry.run(), 1)
        diagnostics._show_native_error.assert_called_once()
        self.assertIn("未能写入", diagnostics._show_native_error.call_args.args[0])

    def test_main_failure_and_broken_stderr_cannot_hide_report(self):
        self.bake()
        diagnostics = self.diagnostics()
        def main():
            raise PermissionError(13, "secret user configuration")
        broken = types.SimpleNamespace(write=lambda value: (_ for _ in ()).throw(OSError("closed")))
        with patch.dict(sys.modules, {"client": types.SimpleNamespace(main=main)}), patch.object(sys, "stderr", broken):
            self.assertEqual(entry.run(), 1)
        text = (self.install / "work/qt-client-startup-error.txt").read_text(encoding="utf-8")
        self.assertIn("stage=run-client", text)
        self.assertNotIn("secret user", text)
        diagnostics._show_native_error.assert_called_once()

    def test_success_keeps_main_return_and_produces_no_failure_log(self):
        self.bake()
        diagnostics = self.diagnostics()
        with patch.dict(sys.modules, {"client": types.SimpleNamespace(main=lambda: 0)}):
            self.assertEqual(entry.run(), 0)
        diagnostics._show_native_error.assert_not_called()
        self.assertFalse((self.install / "work").exists())

    def test_chained_failure_preserves_structured_cause_and_overwrites_bounded_log(self):
        self.bake()
        diagnostics = self.diagnostics()
        def main():
            try:
                raise OSError(28, "private config=" + "x" * 50000)
            except OSError as cause:
                raise RuntimeError("private wrapper") from cause
        with patch.dict(sys.modules, {"client": types.SimpleNamespace(main=main)}):
            self.assertEqual(entry.run(), 1)
            log = self.install / "work/qt-client-startup-error.txt"
            first_size = log.stat().st_size
            self.assertEqual(entry.run(), 1)
        text = log.read_text(encoding="utf-8")
        self.assertIn("cause1.exception=OSError", text)
        self.assertIn("cause1.errno=28", text)
        self.assertNotIn("private", text)
        self.assertLessEqual(log.stat().st_size, first_size + 32)
        self.assertEqual(len(list((self.install / "work").iterdir())), 1)

    def test_diagnostic_defaults_use_three_explicit_locations(self):
        import qt_startup_diagnostics as diagnostics
        import os
        self.bake()
        local = self.root / "local"
        temp = self.root / "temp"
        with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}), patch("tempfile.gettempdir", return_value=str(temp)):
            self.assertEqual(list(diagnostics._candidate_directories()), [
                self.install / "work", local / "Xingtu/diagnostics", temp / "Xingtu/diagnostics"])

    def test_real_resource_errors_remain_distinguishable_at_entry_boundary(self):
        from resource_transaction import ResourceTransactionError
        self.bake()
        diagnostics = self.diagnostics()
        for code in ("resource-busy", "resource-runtime-already-active", "upgrade-recovery-required",
                     "base-recovery-required", "additions-recovery-required", "resource-hardlinks-unavailable"):
            def main():
                raise ResourceTransactionError(code, details={"private": "private config value"})
            with self.subTest(code=code), patch.dict(sys.modules, {"client": types.SimpleNamespace(main=main)}):
                diagnostics._show_native_error.reset_mock()
                self.assertEqual(entry.run(), 1)
                text = (self.install / "work/qt-client-startup-error.txt").read_text(encoding="utf-8")
                self.assertIn("resourceCode=" + code, text)
                self.assertNotIn("private config value", text)
                diagnostics._show_native_error.assert_called_once()
                message = diagnostics._show_native_error.call_args.args[0]
                self.assertNotIn(code, message)
                self.assertIn("qt-client-startup-error.txt", message)
                if code.endswith("recovery-required"):
                    self.assertIn("暂时无法启动", message)

    def test_native_boundary_makes_one_messagebox_call_without_qt(self):
        import ctypes
        from unittest.mock import Mock
        import qt_startup_diagnostics as diagnostics
        user32 = types.SimpleNamespace(MessageBoxW=Mock())
        with patch.object(sys, "platform", "win32"), patch.object(ctypes, "WinDLL", return_value=user32, create=True):
            diagnostics._show_native_error("fixture only; no native dialog")
        user32.MessageBoxW.assert_called_once()

    def test_managed_base_summary_preserves_issue_proof_without_untrusted_details(self):
        from managed_base_resources import ManagedBaseResourceError
        self.bake()
        diagnostics = self.diagnostics()
        issue = dict(path="_internal/references/fixture.json", reason="candidate-integrity-mismatch",
                     expected=dict(bytes=42, sha256="a" * 64), actual=dict(status="missing"))
        error = ManagedBaseResourceError("base-repair-preflight-blocked", [issue])
        # Exercise the final trust boundary even if a future caller adds private fields.
        error.details["private"] = "secret-user-config"
        error.details["issues"].append(dict(path="C:/Users/private/config.json", reason=["untrusted"],
                                            expected=dict(bytes="secret-user-config")))
        def main():
            raise error
        with patch.dict(sys.modules, {"client": types.SimpleNamespace(main=main)}):
            self.assertEqual(entry.run(), 1)
        text = (self.install / "work/qt-client-startup-error.txt").read_text(encoding="utf-8")
        self.assertIn("resourceCode=base-repair-preflight-blocked", text)
        self.assertIn("issue.0.path=_internal/references/fixture.json", text)
        self.assertIn("issue.0.expected.bytes=42", text)
        self.assertIn("issue.0.actual.status=missing", text)
        self.assertNotIn("secret-user-config", text)
        self.assertNotIn("C:/Users", text)
        message = diagnostics._show_native_error.call_args.args[0]
        self.assertIn("地图文件与安装记录不一致", message)
        self.assertNotIn("_internal/", message)

    def test_unknown_resource_code_is_not_serialized_as_arbitrary_text(self):
        from managed_base_resources import ManagedBaseResourceError
        self.bake()
        diagnostics = self.diagnostics()
        def main():
            raise ManagedBaseResourceError("secret-user-config")
        with patch.dict(sys.modules, {"client": types.SimpleNamespace(main=main)}):
            self.assertEqual(entry.run(), 1)
        text = (self.install / "work/qt-client-startup-error.txt").read_text(encoding="utf-8")
        self.assertIn("resourceCode=resource-initialization-failed", text)
        self.assertNotIn("secret-user-config", text)
        diagnostics._show_native_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
