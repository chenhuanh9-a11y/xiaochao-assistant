import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "inno_lifecycle_acceptance.py"
spec = importlib.util.spec_from_file_location("inno_lifecycle_acceptance", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LifecycleAcceptanceTests(unittest.TestCase):
    def fixture(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        package = base / "setup.exe"
        package.write_bytes(b"fixture only")
        package_row = {"path": str(package), "sha256": hashlib.sha256(b"fixture only").hexdigest(),
                       "version": "test-fixture", "sourceCommit": "fixture"}
        return base, {"allowedRoots": [str(base)], "packages": {k: dict(package_row) for k in
                ("setup", "historicalQtIfw", "historicalInno")},
                "scenarios": [{"name": "qtifw-to-inno", "root": str(base / "isolated"), "steps": []}]}

    def test_default_plan_does_not_execute_installation(self):
        _, data = self.fixture()
        report = runner.run(data, execute=False)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["executionRequested"])
        self.assertEqual(report["scenarios"][0]["status"], "skipped")

    def test_missing_historical_package_blocks_instead_of_simulating_history(self):
        _, data = self.fixture()
        del data["packages"]["historicalQtIfw"]
        errors = runner.validate(data)
        self.assertTrue(any("historicalQtIfw" in x for x in errors))

    def test_prepared_only_assertion_never_passes_lifecycle(self):
        _, data = self.fixture()
        data.update(disposableWindows=True, productIdentityVerified=True)
        data["scenarios"][0]["resultEvidence"] = [{"status": "prepared-native-unverified",
                                                   "nativeInstalled": False}]
        with mock.patch.object(runner.platform, "system", return_value="Windows"):
            report = runner.run(data, execute=True)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["scenarios"][0]["reason"], "prepared legacy copy is not native installation")

    def test_mismatched_hash_blocks_execution(self):
        _, data = self.fixture()
        data["packages"]["setup"]["sha256"] = "0" * 64
        self.assertTrue(any("hash mismatch" in x for x in runner.validate(data)))


if __name__ == "__main__":
    unittest.main()
