"""Real Session lease/scheduler -> Coordinator -> ordered input contracts.

Provider inventory/cache and missing-resource parsing are production; successful
engines, capture, windows and fit are synthetic. No native IO or user assets.
"""
from contextlib import contextmanager, nullcontext
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
            c._bound = "123"; c._applied_context = (mode, "profile-one", "s0")
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
                f._build = lambda mode, *, season="s0": runtime_fixture.fixture_engine(mode)
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
                     patch("floor_control_region.locate_vertical_floor_controls", return_value=runtime_fixture.synthetic_read(mode)), \
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
                f._build = lambda mode, *, season="s0": runtime_fixture.fixture_engine(mode)
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

    def test_ready_mode_switch_without_map_input_rearms_previous_missing_reason(self):
        with self.chain("nightmare") as (o,c,s,r,f,k,n,a,root):
            original_build = f._build
            f._build = lambda mode, *, season="s0": (runtime_fixture.fixture_engine(mode)
                if mode == "difficult" else original_build(mode, season=season))
            s.dispatch("inspect_engine_resources"); self.drain(o,s)
            self.press(k)
            self.assertEqual(len(self.resource_notices(n)), 1)
            self.assertFalse(s.snapshot["liveEngineAvailable"])
            o.settings.persistedValues["gameMode"] = "difficult"
            o.settings.saved.emit(); self.drain(o,s)
            self.assertEqual(s.snapshot["mode"], "difficult")
            self.assertTrue(s.snapshot["liveEngineAvailable"])
            self.assertIsNone(c._pending_context)
            self.assertIsNone(c._map_resource_problem())
            # No key is pressed in the ready interval: the completed context
            # callback, rather than a later map request, must rearm notices.
            self.assertFalse(c._map_resource_notified)
            self.assertNotIn("map", [action for action, _ in a])
            o.settings.persistedValues["gameMode"] = "nightmare"
            o.settings.saved.emit(); self.drain(o,s)
            self.assertEqual(s.snapshot["mode"], "nightmare")
            self.assertIsNone(c._pending_context)
            self.assertFalse(s.snapshot["liveEngineAvailable"])
            self.press(k); self.press(k)
            self.assertEqual(len(self.resource_notices(n)), 2)
            self.assertEqual(self.resource_notices(n)[0], self.resource_notices(n)[1])

    def test_missing_retained_resources_keep_mode_reason_redaction_and_ready_rearm(self):
        from client_providers import _read
        retained_resources = {
            "nightmare": ("references/nightmare-map-pack.json",
                          "references/nightmare-shared-structure-index.json",
                          "references/native-similarity-groups.json"),
            "difficult": ("difficult-native-manifest.json",
                          "references/native-similarity-groups.json"),
        }
        for mode, resources in retained_resources.items():
            for relative in resources:
                with self.subTest(mode=mode, resource=relative), self.chain(mode) as (o,c,s,r,f,k,n,a,root):
                    def build(current_mode, *, season="s0"):
                        if current_mode == mode: _read(root, relative)
                        return runtime_fixture.fixture_engine(current_mode)
                    f._build = build
                    s.dispatch("inspect_engine_resources"); self.drain(o,s)
                    reason = s.snapshot["liveUnavailableReason"]
                    self.assertTrue(reason.startswith(mode + ":ProviderUnavailable:"), reason)
                    self.assertIn("missing-resource:" + relative, reason)
                    self.press(k); self.press(k)
                    self.assertIn("困难模式" if mode == "difficult" else "噩梦模式", c.runtimeStatus)
                    self.assertIn("缺少", c.runtimeStatus)
                    self.assertEqual(len(self.resource_notices(n)), 1)
                    self.assertNotIn(str(root), self.resource_notices(n)[0][1])
                    self.assertNotIn(relative, self.resource_notices(n)[0][1])
                    # A real resource-change/recheck rearms the identical reason.
                    f._build = lambda current_mode, *, season="s0": runtime_fixture.fixture_engine(current_mode)
                    c._resources_changed()
                    s.dispatch("inspect_engine_resources"); self.drain(o,s)
                    self.assertTrue(s.snapshot["liveEngineAvailable"])
                    f._build = build
                    c._resources_changed()
                    s.dispatch("inspect_engine_resources"); self.drain(o,s)
                    self.press(k)
                    self.assertEqual(len(self.resource_notices(n)), 2)
                    self.assertEqual(self.resource_notices(n)[0], self.resource_notices(n)[1])
                    self.assertFalse(k._map_input.open_intent)

    def test_read_damage_permission_and_file_disappearance_keep_original_error(self):
        from client_providers import ProviderUnavailable, _read
        for mode in ("nightmare", "difficult"):
            for error_type in (ValueError, PermissionError, FileNotFoundError):
                with self.subTest(mode=mode, error=error_type.__name__), self.chain(mode) as (o,c,s,r,f,k,n,a,root):
                    relative = "references/nightmare-map-pack.json" if mode == "nightmare" else "difficult-native-manifest.json"
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("{damaged" if error_type is ValueError else "{}", encoding="utf8")
                    error = error_type(str(root / "private-fixture-detail"))
                    def build(current_mode, *, season="s0"):
                        if current_mode == mode: _read(root, relative)
                        return runtime_fixture.fixture_engine(current_mode)
                    f._build = build
                    # Malformed JSON is parsed normally. IO failures are injected
                    # at read_text after the normal is_file admission succeeds.
                    with patch.object(Path, "read_text", side_effect=error) if error_type is not ValueError else nullcontext():
                        with self.assertRaises(ProviderUnavailable) as raised:
                            f[mode]()
                        self.assertIsInstance(raised.exception.__cause__, error_type)
                        if error_type is not ValueError:
                            self.assertIs(raised.exception.__cause__, error)
                        s.dispatch("inspect_engine_resources"); self.drain(o,s)
                    self.press(k); self.press(k)
                    reason = s.snapshot["liveUnavailableReason"]
                    self.assertTrue(reason.startswith(mode + ":"), reason)
                    self.assertIn(type(raised.exception.__cause__).__name__, reason)
                    self.assertIn("困难模式" if mode == "difficult" else "噩梦模式", c.runtimeStatus)
                    if error_type is FileNotFoundError:
                        self.assertIn("缺少", c.runtimeStatus)
                    else:
                        self.assertIn("未通过校验或无法读取", c.runtimeStatus)
                        self.assertNotIn("缺少", c.runtimeStatus)
                    self.assertEqual(len(self.resource_notices(n)), 1)
                    self.assertNotIn(str(root), self.resource_notices(n)[0][1])
                    self.assertNotIn("private-fixture-detail", self.resource_notices(n)[0][1])

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
