"""Season boundaries using real Qt queues and synthetic runtimes, no game IO."""
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "qt_quick"))
from PySide6.QtGui import QGuiApplication
from client_session import SessionService
from client_coordinator import ClientCoordinator, _aligned_material_scope
from client_manual_map_service import ManualMapPickerService

APP = QGuiApplication.instance() or QGuiApplication([])

def drain(session):
    deadline = time.monotonic() + 3
    while session._jobs and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(.001)
    assert not session._jobs

class Runtime:
    def __init__(self):
        self.state = dict(season="s0", mode="nightmare", profileId="", roundId=1,
                          frameId="f", engineConnected=True, availableCommands=["set_context", "capture_now"])
        self.calls = []
        self.release = threading.Event()
        self.release.set()
        self.legacy = False
        self.wrong_season = False
    def snapshot(self): return dict(self.state)
    def dispatch(self, action, payload, cancelled):
        self.calls.append((action, payload.copy()))
        self.release.wait(2)
        if action == "set_context":
            self.state.update({k: payload[k] for k in ("season", "mode", "profileId")})
        request = payload.copy()
        output = {k: self.state[k] for k in ("season", "mode", "profileId", "roundId", "frameId")}
        if self.legacy:
            request.pop("season", None)
            output.pop("season", None)
        if self.wrong_season:
            self.state["season"] = output["season"] = "s0"
        return dict(success=True, action=action, requestContext=request, resultContext=output)

class SeasonSessionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.session = SessionService(self.runtime)
    def tearDown(self):
        self.runtime.release.set()
        self.session.shutdown()
        drain(self.session)
    def test_four_contexts_and_profile_changes_preserve_season(self):
        for season in ("s0", "s1"):
            for mode in ("nightmare", "difficult"):
                self.assertTrue(self.session.reconfigure(mode, "author", season))
                drain(self.session)
                self.assertEqual((self.session.currentSeason, self.session.appliedSeason), (season, season))
                self.assertEqual(self.session.snapshot["contextKey"], f"{season}:{mode}")
                self.assertTrue(self.session.setRouteProfile("next"))
                drain(self.session)
                self.assertEqual(self.runtime.state["season"], season)
    def test_preparing_and_active_round_reject_context_change_without_cancellation(self):
        for flag in ("roundActive", "roundPreparing", "roundWorkActive"):
            self.runtime.state[flag] = True
            before = self.session._generation
            self.assertFalse(self.session.reconfigure("nightmare", "", "s1"))
            self.assertFalse(self.session.reconfigure("difficult", "", "s0"))
            self.assertEqual(self.session._generation, before)
            self.runtime.state[flag] = False
    def test_queued_round_start_locks_context_before_snapshot_becomes_active(self):
        self.runtime.state["availableCommands"].append("start_round")
        self.runtime.release.clear()
        self.session.dispatch("start_round")
        generation = self.session._generation
        self.assertFalse(self.session.reconfigure("nightmare", "", "s1"))
        self.assertEqual(self.session._generation, generation)
        fake = SimpleNamespace(session=self.session, runtime=self.runtime)
        self.assertTrue(ClientCoordinator._game_context_locked(fake))

    def test_runtime_active_locks_even_before_session_receipt(self):
        fake = SimpleNamespace(session=SimpleNamespace(snapshot={}),
            runtime=SimpleNamespace(snapshot=lambda: {"roundPreparing": True}))
        self.assertTrue(ClientCoordinator._game_context_locked(fake))

    def test_repeat_pending_context_is_idempotent(self):
        self.runtime.release.clear()
        self.assertTrue(self.session.reconfigure("nightmare", "", "s1"))
        pending, generation = self.session._pending, self.session._generation
        self.assertTrue(self.session.reconfigure("nightmare", "", "s1"))
        self.assertEqual((self.session._pending, self.session._generation), (pending, generation))
        self.runtime.release.set()
        drain(self.session)
        self.assertEqual(len(self.runtime.calls), 1)
    def test_retry_cancelled_desired_context_does_not_claim_it_was_applied(self):
        # Model a cancelled queued request whose desired state remains on UI.
        self.session._season = "s1"
        self.assertTrue(self.session.reconfigure("nightmare", "", "s1"))
        self.assertTrue(self.session.busy)
        drain(self.session)
        self.assertEqual(self.runtime.state["season"], "s1")
        self.assertEqual(len(self.runtime.calls), 1)

    def test_s1_rejects_legacy_missing_season_receipt(self):
        self.runtime.legacy = True
        self.session.reconfigure("nightmare", "", "s1")
        drain(self.session)
        self.assertEqual(self.session.stale_results, 1)
        self.assertEqual(self.session.snapshot["lastReceipt"], {})
    def test_result_pair_cannot_claim_transition_to_other_season(self):
        self.runtime.wrong_season = True
        self.session.reconfigure("nightmare", "", "s1")
        drain(self.session)
        self.assertEqual(self.session.stale_results, 1)
        self.assertEqual(self.session.snapshot["lastReceipt"], {})
    def test_material_and_manual_picker_identity_include_season(self):
        a = dict(season="s0", mode="nightmare", mapId="same", roundId=1)
        b = dict(a, season="s1")
        self.assertNotEqual(_aligned_material_scope(a), _aligned_material_scope(b))
        fake = SimpleNamespace(session=SimpleNamespace(snapshot=a), _applied_mode=lambda: "nightmare")
        before = ManualMapPickerService._session_context(fake)
        fake.session.snapshot = b
        self.assertNotEqual(before, ManualMapPickerService._session_context(fake))
    def test_profile_lookup_never_borrows_legacy_s0_for_s1(self):
        values = dict(gameSeason="s1", gameMode="nightmare", routeProfileId="old", modeRouteProfiles={"nightmare":"old"})
        self.assertEqual(ClientCoordinator._settings_context(values), ("nightmare", "", "s1"))
    def test_s1_profile_write_does_not_pollute_legacy_s0(self):
        values = dict(gameSeason="s1", gameMode="nightmare", routeProfileId="s0-author")
        settings = SimpleNamespace(busy=False, values=values, saveFields=Mock())
        def set_value(key, value):
            values[key] = value
            return True
        settings.setValue = Mock(side_effect=set_value)
        fake = SimpleNamespace(settings=settings, resources=SimpleNamespace(busy=False),
                               session=SimpleNamespace(currentSeason="s1", currentMode="nightmare"))
        ClientCoordinator.setRouteProfile(fake, "s1-author")
        settings.saveFields.assert_called_once_with(["contextRouteProfiles"])
        self.assertEqual(values["routeProfileId"], "s0-author")
        self.assertNotIn("modeRouteProfiles", values)
        self.assertEqual(values["contextRouteProfiles"], {"s1:nightmare": "s1-author"})
        values["gameSeason"] = "s0"
        self.assertEqual(ClientCoordinator._settings_context(values), ("nightmare", "s0-author", "s0"))

    def test_coordinator_context_lock_and_repeat_do_not_save(self):
        for state in ({"roundActive":True}, {"roundPreparing":True}, {"roundWorkActive":True}):
            fake = SimpleNamespace(settings=SimpleNamespace(busy=False, persistedValues={}, setValue=Mock(), saveFields=Mock()),
                resources=SimpleNamespace(busy=False), session=SimpleNamespace(snapshot=state),
                _settings_context=ClientCoordinator._settings_context, notificationRequested=Mock(), changed=Mock(),
                _clear_reference=Mock(), _revoke_runtime=Mock())
            fake._game_context_locked = lambda: ClientCoordinator._game_context_locked(fake)
            ClientCoordinator.requestGameContext(fake, "s1", "nightmare")
            fake.settings.setValue.assert_not_called()
            ClientCoordinator.requestGameContext(fake, "s0", "nightmare")
            fake._revoke_runtime.assert_not_called()

if __name__ == "__main__": unittest.main()
