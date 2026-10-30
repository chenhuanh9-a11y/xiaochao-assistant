"""Ordered click delivery with fake native services; never installs a hook."""
import time
import copy
import threading
import unittest
from unittest.mock import Mock, patch

from client_native_keys import PassThroughKeyboardSource
from client_input import NativeShortcutBackend
from client_coordinator import ClientCoordinator
from client_session import SessionService
import test_client_coordinator as coordinator_seams
import test_client_game_floor_click as floor_seams
from client_live_runtime import _AlignmentHost


class Source(PassThroughKeyboardSource):
    def __init__(self):
        super().__init__()
        self.active = False

    @property
    def running(self):
        return self.active

    def start(self):
        self.active = True
        return True

    def stop(self):
        self.active = False
        return True


class FloorInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        coordinator_seams.CoordinatorTests.setUpClass()
        cls.app = coordinator_seams.CoordinatorTests.app

    def setUp(self):
        self.native = Mock()
        self.native.is_window.return_value = True
        self.native.foreground_window.return_value = 42
        self.source = Source()
        self.keys = NativeShortcutBackend(self.native, source=self.source)
        self.keys.configure({"map": "G"})
        self.keys.bindWindow(42)
        self.keys.enable()
        self.provider = Mock(return_value=dict(mode="nightmare", roundId=1, cycleId=2, frameId="frame"))
        self.keys.setGamePointerContextProvider(self.provider)
        self.events = []
        self.keys.gamePointerPressed.connect(self.events.append)

    def tearDown(self):
        self.keys.shutdown()
        self.app.processEvents()

    def press(self, *, foreground=42, created_at=None):
        event = self.source._pointer_event(25, 31, foreground, created_at=created_at)
        if event:
            self.source.pointerReceived.emit(event)
        return event

    def test_only_bound_foreground_is_stamped_and_click_time_is_preserved(self):
        self.assertIsNone(self.press(foreground=77))
        self.provider.assert_not_called()
        before = time.monotonic() - .7
        event = self.press(created_at=before)
        self.app.processEvents()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0]["context"]["createdAt"], before)
        self.assertEqual(self.events[0]["context"]["frameId"], "frame")
        self.assertEqual(event["sequence"], 1)

    def test_ordered_clicks_survive_ui_queue_but_old_binding_does_not(self):
        self.press(); self.press()
        self.app.processEvents()
        self.assertEqual([event["sequence"] for event in self.events], [1, 2])
        self.press()
        self.keys.bindWindow(99)
        self.app.processEvents()
        self.assertEqual(len(self.events), 2)

    def test_focus_text_edit_and_shutdown_reject_queued_input(self):
        for guard in ("foreground", "text", "edit"):
            with self.subTest(guard=guard):
                self.native.foreground_window.return_value = 42
                self.keys.setTextEntryActive(False)
                self.keys.setEditingBindings(False)
                self.press()
                if guard == "foreground": self.native.foreground_window.return_value = 77
                elif guard == "text": self.keys.setTextEntryActive(True)
                else: self.keys.setEditingBindings(True)
                self.app.processEvents()
                self.assertEqual(self.events, [])
        self.keys.setEditingBindings(False)
        self.native.foreground_window.return_value = 42
        self.press()
        self.keys.shutdown()
        self.app.processEvents()
        self.assertEqual(self.events, [])
        self.assertEqual(self.source._pointer_target, 0)
        self.assertIsNone(self.source._pointer_context_provider)

    def test_busy_context_is_dropped_without_queueing_or_keyboard_failure(self):
        self.provider.return_value = None
        self.assertIsNone(self.press())
        self.keys._pointer_failed("controlled-mouse-hook-failure")
        self.assertTrue(self.keys.enabled)
        self.assertEqual(self.keys.snapshot["floorClickObserverError"], "controlled-mouse-hook-failure")
        self.assertEqual(self.events, [])

    def test_busy_pose_click_runs_through_input_coordinator_and_session_lane(self):
        """Real runtime/session; only native APIs, pixels and solver are seams."""
        floor = floor_seams.FloorClickContracts()
        floor.setUp()
        services = coordinator_seams.CoordinatorTests()
        services.setUp()
        session = coordinator = None
        entered, release = threading.Event(), threading.Event()
        try:
            floor.prepare()
            session = SessionService(floor.runtime)
            services.settings.persistedValues.update(gameMode="nightmare",
                routeProfileId=floor.runtime.snapshot()["profileId"])
            services.settings.values = copy.deepcopy(services.settings.persistedValues)
            native = Mock()
            native.foreground_window.return_value = 42
            with patch("client_input.NativeWindowAPI", return_value=native):
                coordinator = ClientCoordinator(backend=services.backend, settings=services.settings,
                    resources=services.resources, session=session, windows=services.windows,
                    native_input=self.keys, capture=floor.capture, runtime=floor.runtime,
                    images=services.images, window_catalog=Mock(return_value=[]))
            coordinator._bound = "42"
            coordinator._foreground_timer.stop(); coordinator._tracking_timer.stop()
            original = _AlignmentHost.fit
            first = True
            def fit(*args):
                nonlocal first
                if first:
                    first = False
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("bounded busy pose was not released")
                return copy.deepcopy(floor.fit)
            def until(predicate):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    if predicate(): return
                    time.sleep(.001)
                raise AssertionError("bounded floor input/session deadline")
            with patch.object(_AlignmentHost, "fit", side_effect=fit):
                session.dispatch("capture_now")
                self.assertTrue(entered.wait(3))
                x, y = floor.xy(2)
                event = self.source._pointer_event(x, y, 42, created_at=floor.now)
                self.assertIsNotNone(event)
                self.source.pointerReceived.emit(event)
                until(lambda:floor.runtime._game_floor_intent is not None)
                self.assertFalse(floor.runtime.snapshot()["displayAuthorized"])
                self.assertEqual(floor.runtime.snapshot()["floor"], "2")
                self.assertTrue(session.busy)
                floor.read["selectedFloor"] = 2
                release.set()
                until(lambda:not session.busy and session._pool.activeThreadCount() == 0)
            self.assertGreaterEqual(session.stale_results, 1)
            self.assertEqual(session.snapshot["floor"], "2")
            render = floor.runtime.render_payload(session.snapshot["render"]["token"])
            self.assertEqual(render["floor"], "2")
            self.assertFalse(render["liveAuthorization"])
            self.assertEqual(coordinator.alignedData, {})  # Synthetic is never a game overlay.
            self.assertIs(_AlignmentHost.fit, original)
        finally:
            release.set()
            if coordinator:
                coordinator._closed = True
                for timer in (coordinator._foreground_timer, coordinator._tracking_timer,
                        coordinator._context_timer, coordinator._material_timer, coordinator._close_timer):
                    timer.stop()
            if session:
                session.stop()
                session._pool.waitForDone(3000)
                session.shutdown()
            floor.doCleanups()
            services.tearDown()


if __name__ == "__main__":
    unittest.main()
