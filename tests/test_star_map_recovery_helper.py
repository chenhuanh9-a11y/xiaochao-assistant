"""Synthetic resources and mocked parent waiting; no native process is inspected."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import test_map_package_upgrade as fixture_module
OLD_PATH, ADDED_PATH = fixture_module.OLD_PATH, fixture_module.ADDED_PATH
import map_package_upgrade as upgrade
from map_package_recovery_plan import inspect_locked
import star_map_import as importer
import star_map_import_helper as process
import star_map_recovery_helper as helper


class RecoveryHelperTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.UpgradeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        # Explicitly exclude Windows process/EXE checks from these fixtures.
        self.closed = self.enterContext(patch.object(importer, "_check_closed"))
        self.fixture.installed_old()

    def plan(self, pending=False):
        if pending:
            original = upgrade.os.link
            def interrupt(source, target, *args, **kwargs):
                if Path(target) == self.root / ADDED_PATH:
                    raise KeyboardInterrupt("synthetic process loss")
                return original(source, target, *args, **kwargs)
            with patch.object(upgrade.os, "link", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    importer.install_package(self.fixture.new, self.root, application_stopped=True)
            transaction_id = None
        else:
            transaction_id = importer.install_package(self.fixture.new, self.root,
                application_stopped=True)["upgradeTransactionId"]
        with importer._writer_locked_installation(self.root, require_closed=False):
            return inspect_locked(self.root, transaction_id)

    def request(self, plan, action="rollback", request_id="synthetic-request"):
        with patch.object(process, "_launch_parent_bound_helper", return_value=Mock()) as launch:
            helper.launch_recovery_helper(self.root / "star-map-recovery-helper.exe",
                self.root, plan, action, request_id)
        args = launch.call_args.args[3]
        return dict(request_file=args[1], request_sha256=args[3], install_root=self.root,
                    request_id=request_id, parent_pid=42, parent_created="1234")

    def run_request(self, request):
        with patch.object(process, "wait_for_application_exit",
                          return_value="exited-matching-process-handle"):
            return helper.run_helper(**request)

    def test_completed_upgrade_rollback_and_distinct_atomic_receipt(self):
        plan = self.plan()
        request = self.request(plan)
        code, result = self.run_request(request)
        self.assertEqual(code, 0, result)
        self.fixture.assert_old()
        self.assertEqual(result["planSha256"], plan["planSha256"])
        self.assertEqual(result["transactionId"], plan["transactionId"])
        self.assertEqual(json.loads(helper.receipt_path(self.root).read_text("utf-8")), result)
        self.assertFalse(process.receipt_path(self.root).exists())

    def test_pending_recovery_uses_original_transaction_and_restores_old_data(self):
        plan = self.plan(pending=True)
        code, result = self.run_request(self.request(plan, "recover"))
        self.assertEqual(code, 0, result)
        self.fixture.assert_old()
        self.assertFalse((self.root / upgrade.PENDING).exists())

    def test_non_normal_parent_exit_never_mutates_resources(self):
        request = self.request(self.plan())
        with patch.object(process, "wait_for_application_exit",
                          side_effect=importer.ImportRejected("parent-exit-not-normal: 1")):
            code, result = helper.run_helper(**request)
        self.assertEqual(code, 2)
        self.assertIn("parent-exit-not-normal", result["reason"])
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b"q90-route")

    def test_cancelled_close_never_commits_recovery(self):
        request = self.request(self.plan())
        helper.cancel_recovery_request(self.root, request["request_id"])
        code, result = self.run_request(request)
        self.assertEqual(code, 2)
        self.assertIn("cancelled", result["reason"])
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b"q90-route")

    def test_second_instance_check_is_not_bypassed_after_parent_exit(self):
        request = self.request(self.plan())
        self.closed.side_effect = importer.ImportRejected("application-running-or-not-writable")
        code, result = self.run_request(request)
        self.assertEqual(code, 2)
        self.assertIn("application-running", result["reason"])
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b"q90-route")

    def test_changed_target_rejects_stale_consent_and_preserves_user_edit(self):
        request = self.request(self.plan())
        replacement = self.fixture.folder / "player-route"
        replacement.write_bytes(b"player-edited")
        replacement.replace(self.root / OLD_PATH)
        code, result = self.run_request(request)
        self.assertEqual(code, 2)
        self.assertIn("recovery-plan-changed", result["reason"])
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b"player-edited")

    def test_request_cannot_change_action_after_spawn(self):
        request = self.request(self.plan())
        path = Path(request["request_file"])
        payload = json.loads(path.read_text("utf-8"))
        payload["action"] = "recover"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(process, "wait_for_application_exit") as wait:
            code, result = helper.run_helper(**request)
        wait.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(result["reason"], "recovery-request-changed")

    def test_result_write_failure_reports_no_success_to_caller(self):
        request = self.request(self.plan())
        with patch.object(process, "_write_receipt", side_effect=OSError("synthetic disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.run_request(request)
        self.assertFalse(helper.receipt_path(self.root).exists())

    def test_shared_launcher_passes_retained_handle_without_shell_or_native_calls(self):
        executable = self.root / "第五人格小抄助手.exe"
        helper_exe = self.root / "star-map-recovery-helper.exe"
        helper_exe.write_bytes(b"synthetic non-executable")
        api = Mock()
        api.OpenProcess.return_value = 123
        api.SetHandleInformation.return_value = True
        identity = dict(pid=42, creationTime="1234", executable=str(executable))
        with patch.object(process, "_kernel", return_value=api), \
                patch.object(process, "_identity", return_value=identity), \
                patch.object(process.subprocess, "Popen") as spawn:
            process._launch_parent_bound_helper(helper_exe, self.root, "launch-fixture",
                                               ["--request-file", "fixture"])
        argv = spawn.call_args.args[0]
        self.assertEqual(argv[argv.index("--parent-handle") + 1], "123")
        self.assertEqual(spawn.call_args.kwargs["startupinfo"].lpAttributeList, {"handle_list": [123]})
        self.assertTrue(spawn.call_args.kwargs["close_fds"])
        self.assertNotIn("shell", spawn.call_args.kwargs)
        api.CloseHandle.assert_called_once_with(123)


if __name__ == "__main__":
    unittest.main()
