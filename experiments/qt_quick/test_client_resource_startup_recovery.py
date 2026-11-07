"""Source + Temp handoff tests; no native dialog, installed EXE or helper launch."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "tests"), str(Path(__file__).parent)]
import client_resource_startup_recovery as recovery


class StartupRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = dict(transactionId=str(uuid4()), packageId="nightmare-suju", status="prepared",
                         pending=True, fileCount=3, blockedPaths=[], planSha256="a" * 64)
        self.error = SimpleNamespace(code="upgrade-recovery-required")
        self.inspect = Mock(side_effect=lambda root, transaction_id=None: dict(self.plan))
        self.confirm = Mock(return_value=True)
        self.launch = Mock(return_value=SimpleNamespace(pid=42, poll=lambda: None))
        self.persist = Mock()
        self.cancel = Mock()

    def run_offer(self, **kwargs):
        defaults = dict(confirm=self.confirm, inspect=self.inspect, launch=self.launch,
                        persist=self.persist, cancel=self.cancel)
        defaults.update(kwargs)
        return recovery.offer_startup_recovery(self.root, self.error, **defaults)

    def test_unsupported_and_unknown_ownership_do_not_inspect_or_prompt(self):
        for code in ("base-repair-preflight-blocked", "resource-busy", "arbitrary-recovery-required", None, []):
            with self.subTest(code=code):
                self.error.code = code
                self.assertIsNone(self.run_offer())
        self.inspect.assert_not_called()
        self.confirm.assert_not_called()
        self.launch.assert_not_called()

    def test_cancel_exits_without_launch_marker_or_second_inspection(self):
        self.confirm.return_value = False
        result = self.run_offer()
        self.assertEqual((result.status, result.exit_code), ("cancelled", 0))
        self.inspect.assert_called_once_with(self.root)
        self.confirm.assert_called_once()
        self.assertIn("取消恢复并退出", self.confirm.call_args.args[0])
        self.persist.assert_not_called()
        self.launch.assert_not_called()
        self.cancel.assert_not_called()

    def test_confirm_rechecks_exact_plan_then_persists_before_launch_and_requires_exit(self):
        order = []
        self.persist.side_effect = lambda *args: order.append("persist")
        self.launch.side_effect = lambda *args: (order.append("launch") or SimpleNamespace(pid=42, poll=lambda: None))
        result = self.run_offer()
        self.assertEqual(order, ["persist", "launch"])
        self.assertEqual((result.status, result.exit_code), ("helper-started", 0))
        self.assertEqual(len(result.request_id), 32)
        self.inspect.assert_any_call(self.root, self.plan["transactionId"])
        self.launch.assert_called_once_with(self.root, self.plan, result.request_id)
        self.cancel.assert_not_called()
        message = self.confirm.call_args.args[0]
        for value in ("未提交则恢复旧资源", "已提交则只完成临时副本清理", "文件数：3"):
            self.assertIn(value, message)
        self.assertNotIn(self.plan["transactionId"], message)
        self.assertNotIn(self.plan["planSha256"][:16], message)

    def test_additions_removal_and_base_upgrade_alias_have_exact_plan_kinds(self):
        for code, status, message in (
            ("additions-recovery-required", "additions-pending", "撤回本次新增"),
            ("removal-recovery-required", "removal-pending", "还原本次删除"),
            ("author-upgrade-recovery-required", "committed", "清理临时副本"),
            ("upgrade-cleanup-incomplete", "upgrade-cleanup-pending", "清理临时副本"),
        ):
            with self.subTest(code=code):
                self.error.code = code
                self.plan["status"] = status
                self.plan["packageId"] = "all-managed" if status == "removal-pending" else "nightmare-suju"
                self.assertEqual(self.run_offer().status, "helper-started")
                self.assertIn(message, self.confirm.call_args.args[0])
                if status in {"committed", "upgrade-cleanup-pending"}:
                    self.assertNotIn("恢复到本次升级前", self.confirm.call_args.args[0])

    def test_wrong_pending_kind_or_invalid_plan_never_gets_consent(self):
        invalid = [dict(self.plan, pending=False), dict(self.plan, status="additions-pending"),
                   dict(self.plan, transactionId="arbitrary"), dict(self.plan, fileCount=True),
                   dict(self.plan, packageId="private/path"), dict(self.plan, planSha256="bad"),
                   dict(self.plan, blockedPaths=["unowned-resource"])]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(recovery.StartupRecoveryError):
                self.run_offer(inspect=lambda *args: plan)
        self.confirm.assert_not_called()
        self.launch.assert_not_called()

    def test_changed_plan_after_confirmation_never_persists_or_launches(self):
        self.inspect.side_effect = [self.plan, dict(self.plan, planSha256="b" * 64)]
        with self.assertRaisesRegex(recovery.StartupRecoveryError, "changed-reinspect"):
            self.run_offer()
        self.confirm.assert_called_once()
        self.persist.assert_not_called()
        self.launch.assert_not_called()

    def test_write_denied_before_spawn_cannot_leave_a_helper_waiting(self):
        self.persist.side_effect = PermissionError("fixture denied")
        with self.assertRaises(PermissionError):
            self.run_offer()
        self.launch.assert_not_called()
        self.cancel.assert_not_called()

    def test_launch_failure_or_immediate_exit_cancels_and_propagates_once(self):
        self.launch.side_effect = OSError("fixture launch failure")
        with self.assertRaises(OSError):
            self.run_offer()
        self.cancel.assert_called_once()
        self.launch.side_effect = None
        self.launch.return_value = SimpleNamespace(pid=42, poll=lambda: 2)
        self.cancel.reset_mock()
        with self.assertRaisesRegex(recovery.StartupRecoveryError, "launch-not-confirmed"):
            self.run_offer()
        self.cancel.assert_called_once()

    def test_cancel_failure_is_explicit_and_preserves_launch_cause(self):
        original = OSError("fixture launch failure")
        self.launch.side_effect = original
        self.cancel.side_effect = PermissionError("fixture cancel failure")
        with self.assertRaisesRegex(recovery.StartupRecoveryError, "cancel-unconfirmed") as caught:
            self.run_offer()
        self.assertIs(caught.exception.__cause__, original)

    def test_default_inspection_holds_only_short_writer_lock(self):
        import star_map_import as importer
        import map_package_recovery_plan
        held = []
        @contextmanager
        def locked(root, *, require_closed):
            self.assertFalse(require_closed)
            held.append(True)
            try:
                yield root, root / "work/map-packages"
            finally:
                held.pop()
        def inspected(*args):
            self.assertEqual(held, [True])
            return self.plan
        def confirm(message):
            self.assertEqual(held, [])
            return False
        with patch.object(importer, "_writer_locked_installation", locked), patch.object(map_package_recovery_plan, "inspect_locked", inspected):
            result = recovery.offer_startup_recovery(self.root, self.error, confirm=confirm)
        self.assertEqual(result.status, "cancelled")

    def test_default_launch_uses_only_exact_installed_helper_and_recover_action(self):
        import star_map_recovery_helper
        with patch.object(star_map_recovery_helper, "launch_recovery_helper", return_value=self.launch.return_value) as launch:
            recovery._launch(self.root, self.plan, "f" * 32)
        launch.assert_called_once_with(self.root / "star-map-recovery-helper.exe", self.root,
                                       self.plan, "recover", "f" * 32)

    def test_real_temp_marker_matches_existing_next_launch_receipt_reader(self):
        (self.root / "work/map-packages").mkdir(parents=True)
        recovery._persist_pending(self.root, self.plan, "f" * 32)
        path = self.root / "work/map-packages/qt-recovery-pending.json"
        self.assertEqual(json.loads(path.read_text()), dict(requestId="f" * 32, planSha256="a" * 64, action="recover"))
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_native_confirmation_defaults_to_no_and_never_implicitly_accepts(self):
        import ctypes
        box = Mock(return_value=7)
        with patch.object(sys, "platform", "win32"), patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(MessageBoxW=box), create=True):
            self.assertFalse(recovery._confirm("fixture only"))
            self.assertTrue(box.call_args.args[3] & 0x100)
            box.return_value = 6
            self.assertTrue(recovery._confirm("fixture only"))
            box.return_value = 0
            with self.assertRaisesRegex(recovery.StartupRecoveryError, "confirmation-unavailable"):
                recovery._confirm("fixture only")


class ActualPendingHandoffTests(unittest.TestCase):
    """Actual journals/lease/request/helper recovery; native spawn/wait replaced."""
    def run_pending(self, kind):
        from test_star_map_recovery_helper import RecoveryHelperTests
        from resource_transaction import resource_runtime_lease, assert_resources_ready, ResourceTransactionError
        import star_map_import_helper as process
        import star_map_recovery_helper as helper
        fixture = RecoveryHelperTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plan = fixture.plan(pending=True) if kind == "upgrade" else fixture.addition_plan()
        child = SimpleNamespace(pid=42, poll=lambda: None)
        with resource_runtime_lease(fixture.root):
            with self.assertRaises(ResourceTransactionError) as caught:
                assert_resources_ready(fixture.root)
            with patch.object(process, "_launch_parent_bound_helper", return_value=child) as launch:
                outcome = recovery.offer_startup_recovery(fixture.root, caught.exception, confirm=lambda message: True)
            self.assertEqual(outcome.status, "helper-started")
            args = launch.call_args.args[3]
            request = dict(request_file=args[1], request_sha256=args[3], install_root=fixture.root,
                           request_id=outcome.request_id, parent_pid=42, parent_created="1234")
            marker = json.loads((fixture.root / "work/map-packages/qt-recovery-pending.json").read_text())
            self.assertEqual(marker["planSha256"], plan["planSha256"])
            self.assertTrue(helper._request_path(fixture.root, outcome.request_id).is_file())
            # No source recovery is performed by the handoff while the parent owns resources.
            self.assertTrue((fixture.root / ("work/map-packages/pending-upgrade.json" if kind == "upgrade"
                                            else "work/map-packages/pending-additions.json")).exists())
        # The real helper service now gets the lease after simulated parent exit.
        code, result = fixture.run_request(request)
        self.assertEqual(code, 0, result)
        fixture.fixture.assert_old()
        self.assertEqual(result["planSha256"], plan["planSha256"])
        return result

    def test_actual_upgrade_pending_reaches_existing_recovery_helper(self):
        self.assertEqual(self.run_pending("upgrade")["operationResult"]["status"], "rolled-back")

    def test_actual_additions_pending_reaches_existing_recovery_helper(self):
        self.assertEqual(self.run_pending("additions")["operationResult"]["status"], "additions-rolled-back")

    def test_actual_removal_pending_reaches_existing_recovery_helper(self):
        from test_map_package_removal import PackageRemovalTests
        from resource_transaction import resource_runtime_lease, assert_resources_ready, ResourceTransactionError
        import star_map_import as importer
        import star_map_import_helper as process
        import star_map_recovery_helper as helper
        fixture = PackageRemovalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.install()
        before = fixture.snapshot()
        fixture.interrupt()
        with resource_runtime_lease(fixture.root):
            with self.assertRaises(ResourceTransactionError) as caught:
                assert_resources_ready(fixture.root)
            self.assertEqual(caught.exception.code, "removal-recovery-required")
            with patch.object(process, "_launch_parent_bound_helper",
                              return_value=SimpleNamespace(pid=42, poll=lambda: None)) as launch:
                result = recovery.offer_startup_recovery(fixture.root, caught.exception, confirm=lambda message: True)
            args = launch.call_args.args[3]
            request = dict(request_file=args[1], request_sha256=args[3], install_root=fixture.root,
                           request_id=result.request_id, parent_pid=42, parent_created="1234")
        with patch.object(process, "wait_for_application_exit", return_value="exited-matching-process-handle"), \
                patch.object(importer, "_check_closed"):
            code, receipt = helper.run_helper(**request)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["operationResult"]["status"], "removal-rolled-back")
        for name, contents in before.items():
            self.assertEqual((fixture.root / name).read_bytes(), contents, name)


if __name__ == "__main__":
    unittest.main()
