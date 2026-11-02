"""Real Session lease/scheduler -> Coordinator -> ordered input contracts.

Provider inventory/cache and missing-resource parsing are production; successful
engines, capture, windows and fit are synthetic. No native IO or user assets.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from client_coordinator import ClientCoordinator
from client_input import NativeShortcutBackend
from client_live_runtime import LiveRuntime
from client_providers import build_engine_factories
from client_session import SessionService
import test_client_coordinator as coordinator_fixture
import test_client_live_runtime as runtime_fixture


class ResourceFeedbackTests(unittest.TestCase):
    @contextmanager
    def chain(self, mode="nightmare"):
        owner = coordinator_fixture.CoordinatorTests()
        owner.setUpClass(); owner.setUp()
        old = owner.coordinator
        old._closed = True
        for timer in (old._foreground_timer, old._tracking_timer, old._context_timer): timer.stop()
        owner.session.changed.disconnect(old._session_changed)
        owner.resources.resourcesChanged.disconnect(old._resources_changed)
        owner.input.actionTriggered.disconnect(old._native_action)
        with tempfile.TemporaryDirectory() as directory:
            factories = build_engine_factories(directory)
            capture = runtime_fixture.FixtureCapture()
            runtime = LiveRuntime(engine_factories=factories, capture_provider=capture, evidence_kind="synthetic")
            runtime.dispatch("set_context", dict(mode=mode, profileId="profile-one"))
            session = SessionService(runtime)
            keys = NativeShortcutBackend(owner.native); keys._hwnd = 123
            with patch("client_input.NativeWindowAPI", return_value=owner.native):
                c = ClientCoordinator(backend=owner.backend, settings=owner.settings,
                    resources=owner.resources, session=session, windows=owner.windows,
                    native_input=keys, capture=capture, runtime=runtime, images=owner.images,
                    window_catalog=Mock(side_effect=AssertionError("discovery forbidden")))
            c._bound = "123"; c._applied_context = (mode, "profile-one")
            c._foreground_timer.stop(); c._tracking_timer.stop()
            notices, actions = [], []
            c.notificationRequested.connect(lambda *args: notices.append(args))
            session.actionRequested.connect(lambda action, payload: actions.append((action, payload)))
            try:
                yield owner, c, session, runtime, factories, keys, notices, actions, Path(directory)
            finally:
                c._closed = True
                for timer in (c._foreground_timer, c._tracking_timer, c._context_timer,
                              c._close_timer, c._material_timer): timer.stop()
                session.shutdown(); session._pool.waitForDone(5000)
                owner.app.processEvents(); keys.shutdown(); owner.tearDown()

    def press(self, keys):
        keys._dispatch_action("map", dict(createdAt=time.monotonic()))

    def drain(self, owner, session):
        deadline = time.monotonic() + 5
        while (session.busy or session._jobs) and time.monotonic() < deadline:
            owner.app.processEvents(); time.sleep(.001)
        owner.app.processEvents()
        self.assertFalse(session.busy)
        self.assertFalse(session._jobs)

    def resource_notices(self, notices):
        return [n for n in notices if n[0] == "地图资源未就绪"]

    def test_real_lease_rejects_open_once_and_next_edge_opens_after_ready(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (o,c,s,r,f,k,n,a,root):
                f._build = runtime_fixture.fixture_engine
                f.prepare(); s.refreshRuntime()
                lease = s.acquireResourceLease(); self.assertTrue(lease)
                self.press(k); self.press(k)
                self.assertEqual(len(self.resource_notices(n)), 1)
                self.assertIn("更新", self.resource_notices(n)[0][1])
                self.assertIn("更新", c.runtimeStatus)
                self.assertTrue(s.snapshot["resourceLocked"])
                self.assertFalse(s.snapshot["lastReceipt"])
                self.assertFalse(a)
                self.assertFalse(k._map_input.open_intent)
                s.releaseResourceLease(lease)
                # Successful map-open crosses real Session worker and runtime;
                # only visual detection/solver evidence is synthetic.
                with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)), \
                     patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=1)), \
                     patch.object(runtime_fixture._AlignmentHost, "fit", return_value=dict(success=False)):
                    self.press(k); self.drain(o,s)
                self.assertEqual(a[-1][0], "map")
                self.assertTrue(r.snapshot()["mapOpen"])
                self.press(k); self.drain(o,s)
                self.assertEqual(a[-1][0], "close_map")
                self.assertFalse(r.snapshot()["mapOpen"])
                r.dispatch("end_round", {}); s.refreshRuntime()
                lease = s.acquireResourceLease(); self.assertTrue(lease)
                self.press(k)
                self.assertEqual(len(self.resource_notices(n)), 2)
                s.releaseResourceLease(lease)

    def test_validation_missing_resources_recheck_and_ready_rearms_same_reason(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (o,c,s,r,f,k,n,a,root):
                self.press(k); self.press(k)
                self.assertIn("校验", c.runtimeStatus)
                self.assertEqual(len(self.resource_notices(n)), 1)
                s.dispatch("inspect_engine_resources"); self.drain(o,s)
                self.assertIn("missing-resource", s.snapshot["liveUnavailableReason"])
                self.press(k); self.press(k)
                self.assertIn("缺少", c.runtimeStatus)
                self.assertEqual(len(self.resource_notices(n)), 2)
                self.assertEqual([x for x,_ in a], ["inspect_engine_resources"])
                # Same resource-change hook used after import/delete, with its
                # existing invalidation and worker recheck; no transaction fake.
                build = f._build
                f._build = runtime_fixture.fixture_engine
                c._resources_changed()
                self.assertIn("校验", c.runtimeStatus)
                s.dispatch("inspect_engine_resources"); self.drain(o,s)
                self.assertNotIn("资源", c.runtimeStatus)
                f._build = build
                c._resources_changed()
                s.dispatch("inspect_engine_resources"); self.drain(o,s)
                self.press(k); self.press(k)
                self.assertEqual(len(self.resource_notices(n)), 3)
                self.assertIn("缺少", self.resource_notices(n)[-1][1])
                self.assertFalse(k._map_input.open_intent)

    def test_difficult_missing_ui_template_is_distinct_from_missing_pack(self):
        with self.chain("difficult") as (o,c,s,r,f,k,n,a,root):
            from client_providers import _load_difficult_map_ui
            def build(mode):
                if mode == "difficult": _load_difficult_map_ui(root / "arbitrary-fixture-name.npz")
                return runtime_fixture.fixture_engine(mode)
            f._build = build
            s.dispatch("inspect_engine_resources"); self.drain(o,s)
            self.assertIn("FileNotFoundError", s.snapshot["liveUnavailableReason"])
            self.press(k); self.press(k)
            self.assertIn("困难模式", c.runtimeStatus)
            self.assertIn("模板", c.runtimeStatus)
            self.assertEqual(len(self.resource_notices(n)), 1)
            self.assertNotIn(str(root), self.resource_notices(n)[0][1])
            # Fixing templates/readiness rearms an identical later failure.
            f._build = runtime_fixture.fixture_engine
            c._resources_changed()
            s.dispatch("inspect_engine_resources"); self.drain(o,s)
            self.assertTrue(s.snapshot["liveEngineAvailable"])
            f._build = build
            c._resources_changed()
            s.dispatch("inspect_engine_resources"); self.drain(o,s)
            self.press(k)
            self.assertEqual(len(self.resource_notices(n)), 2)

    def test_template_damage_and_permission_keep_original_error_without_claiming_missing(self):
        from client_providers import ProviderUnavailable, _load_difficult_map_ui
        for error in (ValueError("fixture-damaged"), PermissionError("fixture-denied")):
            with self.subTest(error=type(error).__name__), self.chain("difficult") as (o,c,s,r,f,k,n,a,root):
                with patch("difficult_map_ui.DifficultMapUI", side_effect=error):
                    with self.assertRaises(ProviderUnavailable) as raised:
                        _load_difficult_map_ui(root / "arbitrary-fixture-name.npz")
                    self.assertIs(raised.exception.__cause__, error)
                    self.assertIn(type(error).__name__, str(raised.exception))
                    def build(mode):
                        if mode == "difficult": return _load_difficult_map_ui(root / "arbitrary-fixture-name.npz")
                        return runtime_fixture.fixture_engine(mode)
                    f._build = build
                    s.dispatch("inspect_engine_resources"); self.drain(o,s)
                self.press(k); self.press(k)
                self.assertIn(type(error).__name__, s.snapshot["liveUnavailableReason"])
                self.assertIn("模板未通过校验或无法读取", c.runtimeStatus)
                self.assertNotIn("缺少", c.runtimeStatus)
                self.assertEqual(len(self.resource_notices(n)), 1)

    def test_unready_does_not_block_close_or_management_shortcuts(self):
        with self.chain() as (o,c,s,r,f,k,n,a,root):
            lease = s.acquireResourceLease(); self.assertTrue(lease)
            # A close remains a revoke/cancel/reset even though the Session
            # refuses its worker command under the exclusive lease.
            k.resetMapInput(True)
            with patch.object(c, "_revoke_runtime", wraps=c._revoke_runtime) as revoke:
                self.press(k)
                revoke.assert_called_with("map-closed")
            self.assertFalse(k._map_input.open_intent)
            self.assertFalse(self.resource_notices(n))
            with patch.object(c, "toggleControlsKeyboard") as panel:
                c._native_action("toggle-panel"); panel.assert_called_once()
            self.assertTrue(s.snapshot["resourceLocked"])
            s.releaseResourceLease(lease)


if __name__ == "__main__": unittest.main()
