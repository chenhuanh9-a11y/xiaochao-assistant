"""Pending transactions keep real Session consumers gated until verified recovery."""
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from client import QtClient
from client_session import SessionService
import test_client_resources as fixtures


class RecoveryLeaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service
        self.dispatches = []
        calls = self.dispatches
        root = self.fixture.root

        class Runtime:
            def snapshot(self):
                return dict(mode="nightmare", profileId="",
                            availableCommands=["inspect_engine_resources", "start_round"])

            def dispatch(self, action, payload, cancel):
                calls.append((action, (root / "work/map-packages/pending-additions.json").exists()))
                return {"action": action}

        self.session = SessionService(Runtime())
        self.service.setLeaseProvider(self.session)
        # Match QtClient's explicit startup/resource-change preparation state.
        # This is not a context-only refresh that may reuse a prepared snapshot.
        self.owner = SimpleNamespace(_closed=False, _preparation_context_only=False,
            coordinator=SimpleNamespace(closing=False),
            _initialize_timer=Mock(), resources=self.service, session=self.session)
        self.service.resourcesChanged.connect(lambda: QtClient._initialize_resources(self.owner))

        def close():
            self.owner._closed = True
            self.service.shutdown()
            self.service.pool.waitForDone(5000)
            fixtures.APP.processEvents()
            self.session.shutdown()
            self.session._pool.waitForDone(5000)
        self.addCleanup(close)

    def interrupted_additions(self):
        original = fixtures.importer.os.link

        def publish(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if Path(target) == self.fixture.root / fixtures.IMAGE:
                raise OSError("fixture failure after actual publication")
            return result

        with patch.object(fixtures.importer.os, "link", side_effect=publish), \
                patch.object(fixtures.importer, "_rollback_published", return_value=False):
            self.fixture.install()
        self.assertTrue((self.fixture.root / "work/map-packages/pending-additions.json").exists())
        self.assertTrue((self.fixture.root / fixtures.IMAGE).exists())

    def assert_gated(self, lease=None):
        self.assertTrue(self.service.recoveryRequired)
        self.assertTrue(self.session.snapshot["resourceLocked"])
        if lease is not None:
            self.assertEqual(self.service._lease_id, lease)
        QtClient._initialize_resources(self.owner)
        # Exercise the actual Session guard as well as QtClient's early return.
        # Both commands are advertised by Runtime, so unavailability cannot
        # accidentally stand in for the resource lease blocking consumption.
        for action in ("inspect_engine_resources", "start_round"):
            self.session.dispatch(action)
            self.assertFalse(self.session.busy)
        self.session._pool.waitForDone(5000)
        fixtures.APP.processEvents()
        self.assertEqual(self.dispatches, [])

    def request_recovery(self):
        self.service.requestRecovery()
        self.fixture.settle()
        self.assertEqual(self.service.pendingKind, "recovery")

    def test_failed_live_import_blocks_actual_client_preparation_until_verified_recovery(self):
        self.interrupted_additions()
        self.assert_gated()
        lease = self.service._lease_id
        self.service.setRecoveryAllowedProvider(lambda: True)
        self.request_recovery()
        self.assert_gated(lease)
        self.service.confirmOperation(True)
        self.fixture.settle()
        self.assertFalse((self.fixture.root / "work/map-packages/pending-additions.json").exists())
        self.assertFalse((self.fixture.root / fixtures.IMAGE).exists())
        self.assertFalse(self.service.recoveryRequired)
        self.assertFalse(self.session.snapshot["resourceLocked"])
        self.assertEqual(self.dispatches, [])
        QtClient._initialize_resources(self.owner)
        self.session._pool.waitForDone(5000)
        fixtures.APP.processEvents()
        self.assertEqual(self.dispatches, [("inspect_engine_resources", False)])

    def test_refresh_history_cancel_and_denied_recovery_keep_same_lease(self):
        self.interrupted_additions()
        lease = self.service._lease_id
        self.service.refresh()
        self.fixture.settle()
        self.assert_gated(lease)
        self.service.inspectRecovery()
        self.fixture.settle()
        self.assert_gated(lease)
        allowed = [True]
        self.service.setRecoveryAllowedProvider(lambda: allowed[0])
        self.request_recovery()
        self.service.confirmOperation(False)
        self.assert_gated(lease)
        self.request_recovery()
        allowed[0] = False
        self.service.confirmOperation(True)
        self.assert_gated(lease)
        with self.assertRaises(RuntimeError):
            self.service.setLeaseProvider(self.session)

    def test_helper_failure_and_handoff_retain_pending_until_safe_shutdown(self):
        self.interrupted_additions()
        lease = self.service._lease_id
        self.service.setRecoveryLauncher(Mock(side_effect=OSError("fixture helper unavailable")))
        self.request_recovery()
        self.service.confirmOperation(True)
        self.fixture.settle()
        self.assertFalse(self.service.recoveryHandoffPending)
        self.assert_gated(lease)
        self.service.setRecoveryLauncher(lambda action, plan: "fixture-request")
        self.request_recovery()
        self.service.confirmOperation(True)
        self.fixture.settle()
        self.assertTrue(self.service.recoveryHandoffPending)
        self.assert_gated(lease)
        self.owner._closed = True
        self.service.shutdown()
        self.assertFalse(self.session.snapshot["resourceLocked"])

    def test_cancelled_first_history_detects_pending_without_publishing_result(self):
        self.fixture.upgrade(interrupted=True)
        self.assertFalse(self.service.recoveryRequired)
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        original = self.service._recovery_history

        def history(cancel):
            result = original(cancel)
            entered.set()
            self.assertTrue(finish.wait(5))
            return result

        with patch.object(self.service, "_recovery_history", side_effect=history):
            self.service.inspectRecovery()
            self.assertTrue(entered.wait(3))
            self.service.cancel()
            finish.set()
            self.fixture.settle()
        self.assertEqual(self.service.recoveryTransactions, [])
        self.assert_gated()

    def test_shutdown_retains_lease_until_inflight_pending_worker_finishes(self):
        self.interrupted_additions()
        entered, finish = threading.Event(), threading.Event()
        self.addCleanup(finish.set)
        original = self.service._recovery_history

        def history(cancel):
            result = original(cancel)
            entered.set()
            self.assertTrue(finish.wait(5))
            return result

        with patch.object(self.service, "_recovery_history", side_effect=history):
            self.service.inspectRecovery()
            self.assertTrue(entered.wait(3))
            self.owner._closed = True
            self.service.shutdown()
            self.assertTrue(self.session.snapshot["resourceLocked"])
            finish.set()
            self.service.pool.waitForDone(5000)
            fixtures.APP.processEvents()
        self.assertFalse(self.session.snapshot["resourceLocked"])
        self.assertEqual(self.service.recoveryTransactions, [])


if __name__ == "__main__":
    unittest.main()
