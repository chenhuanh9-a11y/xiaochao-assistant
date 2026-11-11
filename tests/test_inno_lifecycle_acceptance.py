import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "inno_lifecycle_acceptance.py"
spec = importlib.util.spec_from_file_location("inno_lifecycle_acceptance", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LifecycleAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "sandbox"
        self.root.mkdir()
        package = self.base / "setup.exe"
        package.write_bytes(b"harmless synthetic test fixture")
        package_row = {"path": str(package), "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                       "version": "fixture", "sourceCommit": "fixture"}
        self.data = {"allowedRoots": [str(self.base)],
                     "packages": {key: dict(package_row) for key in runner.PACKAGES},
                     "scenarios": [{"name": "fresh-install", "root": str(self.root), "steps": []}]}

    def errors(self):
        return runner.validate(self.data)

    def test_plan_is_blocked_preflight_and_never_claims_execution(self):
        report = runner.run(self.data, execute=False)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["executionEnabled"])
        self.assertEqual(report["scenarios"][0]["status"], "preflight-only")

    def test_execute_is_blocked_even_when_spec_self_asserts_both_booleans(self):
        self.data.update(disposableWindows=True, productIdentityVerified=True)
        with mock.patch.object(runner.platform, "platform", return_value="Linux"), \
                mock.patch("subprocess.run") as called:
            report = runner.run(self.data, execute=True)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("no authenticated disposable Windows runner", " ".join(report["inputErrors"]))
        called.assert_not_called()

    def test_wrong_hash_blocks(self):
        self.data["packages"]["setup"]["sha256"] = "0" * 64
        self.assertTrue(any("hash mismatch" in error for error in self.errors()))

    def test_missing_real_history_package_blocks(self):
        del self.data["packages"]["historicalQtIfw"]
        self.assertTrue(any("historicalQtIfw" in error for error in self.errors()))

    def test_arbitrary_command_is_rejected(self):
        self.data["scenarios"][0]["steps"] = [{"command": ["python", "-c", "print(1)"]}]
        self.assertTrue(any("requests a command" in error for error in self.errors()))

    def test_interpreter_and_parameter_injection_fields_are_rejected(self):
        for key in ("executable", "script", "interpreter", "args", "arguments"):
            with self.subTest(key=key):
                self.data["scenarios"][0]["steps"] = [{key: "anything"}]
                self.assertTrue(any("requests a command" in error for error in self.errors()))

    def test_unknown_named_action_is_rejected(self):
        self.data["scenarios"][0]["steps"] = [{"action": "powershell"}]
        self.assertTrue(any("unsupported action" in error for error in self.errors()))

    def test_observation_outside_scenario_and_allowed_roots_is_rejected(self):
        outside = self.base.parent / "outside-marker"
        self.data["scenarios"][0]["assertions"] = [{"kind": "path", "path": str(outside), "label": "x"}]
        errors = self.errors()
        self.assertTrue(any("outside allowedRoots" in error for error in errors))
        self.assertTrue(any("outside its scenario root" in error for error in errors))

    def test_registry_observations_are_rejected(self):
        self.data["scenarios"][0]["assertions"] = [{"kind": "registry", "key": "HKLM\\anything",
                                                       "value": "anything", "label": "x"}]
        self.assertTrue(any("registry observations are disabled" in error for error in self.errors()))

    def test_relative_path_is_rejected(self):
        self.data["scenarios"][0]["assertions"] = [{"kind": "path", "path": "relative", "label": "x"}]
        self.assertTrue(any("must be absolute" in error for error in self.errors()))

    def test_parent_traversal_is_rejected(self):
        self.data["scenarios"][0]["assertions"] = [{"kind": "path", "path": str(self.root / ".." / "setup.exe"), "label": "x"}]
        self.assertTrue(any("traversal" in error for error in self.errors()))

    def test_unc_path_is_rejected(self):
        self.data["scenarios"][0]["assertions"] = [{"kind": "path", "path": r"\\server\share\data", "label": "x"}]
        self.assertTrue(any("network/device" in error for error in self.errors()))

    def test_symlink_escape_is_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.base / "setup.exe")
        self.data["scenarios"][0]["assertions"] = [{"kind": "path", "path": str(link), "label": "x"}]
        self.assertTrue(any("symbolic link" in error for error in self.errors()))

    def test_bad_observation_shape_is_rejected(self):
        self.data["scenarios"][0]["assertions"] = [{"kind": "json-value", "path": str(self.root / "state.json"), "label": "x"}]
        self.assertTrue(any("needs a field selector" in error for error in self.errors()))

    def test_all_steps_are_preflighted_before_any_execution_and_bad_late_step_blocks(self):
        self.data["scenarios"][0]["steps"] = [{"label": "valid named future action"},
                                               {"label": "late escape", "command": ["anything"]}]
        with mock.patch.object(runner.platform, "platform", return_value="Linux"), \
                mock.patch("subprocess.run", side_effect=AssertionError("must not start a process")) as run:
            report = runner.run(self.data, execute=True)
        self.assertEqual(report["status"], "blocked")
        run.assert_not_called()

    def test_prepared_native_unverified_can_never_be_reported_passed(self):
        self.data["scenarios"][0]["resultEvidence"] = [{"status": "prepared-native-unverified", "nativeInstalled": False}]
        report = runner.run(self.data, execute=False)
        self.assertNotEqual(report["status"], "passed")


if __name__ == "__main__":
    unittest.main()
