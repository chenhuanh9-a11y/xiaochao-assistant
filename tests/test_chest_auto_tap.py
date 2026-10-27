"""Offline tests: all native input boundaries are replaced, even on failure."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import chest_auto_tap  # noqa: E402
from chest_auto_tap import ChestAutoTap, validate  # noqa: E402

BASE = dict(enabled=True, tapKey="F", holdKey="F12", intervalMs=90)


def wait_for(predicate, timeout=1):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError("Timed out waiting for mocked worker")


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.held, self.foreground = False, True
        self.events, self.tappers = [], []
        # The native handle also stays unavailable throughout test cleanup.
        self.native = mock.patch.object(chest_auto_tap, "_user32", None)
        self.native.start()
        self.addCleanup(self.native.stop)
        for patch in (
            mock.patch.object(chest_auto_tap, "key_down", lambda vk: self.held),
            mock.patch.object(chest_auto_tap, "same_top_window", lambda a, b: self.foreground),
            mock.patch.object(chest_auto_tap, "foreground_window", lambda: 1),
            mock.patch.object(chest_auto_tap, "_send_key", self.send),
            mock.patch.object(chest_auto_tap, "RELEASE_RETRY_SECONDS", .01),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def tearDown(self):
        self.held = False
        for tapper in self.tappers:
            tapper._disable()
            tapper.stop()
        time.sleep(.03)  # Let the suspended daemons finish their mocked releases.

    def send(self, vk, up):
        self.events.append((vk, up, time.perf_counter()))
        return 0

    def tapper(self, **kwargs):
        tapper = ChestAutoTap(Path(self.folder.name) / f"c{len(self.tappers)}.json",
                              target_window=lambda: 1, **kwargs)
        tapper.available = True
        self.tappers.append(tapper)
        return tapper


class ValidateTests(OfflineTests):
    def test_accepts_defaults_and_mouse_trigger(self):
        self.assertEqual(validate(BASE, {}), BASE)
        self.assertEqual(validate(dict(BASE, holdKey="鼠标侧键 2"), {})["holdKey"], "鼠标侧键 2")

    def test_rejects_same_key_reserved_and_bad_interval(self):
        cases = [(dict(BASE, holdKey="F"), {}), (BASE, {0x7B: "地图快捷键"}),
                 (BASE, {ord("F"): "Ctrl+F"}), (dict(BASE, tapKey="鼠标侧键 1"), {})]
        cases.extend((dict(BASE, intervalMs=value), {}) for value in (5, None, "bad", "nan", "inf"))
        for settings, reserved in cases:
            with self.subTest(settings=settings, reserved=reserved), self.assertRaises(ValueError):
                validate(settings, reserved)


class StoreTests(OfflineTests):
    def test_defaults_avoid_reserved_and_do_not_start_worker(self):
        tapper = self.tapper(reserved=lambda: {0x77: "x"})
        self.assertEqual(tapper.settings["holdKey"], "F12")
        self.assertIsNone(tapper._thread)
        self.assertEqual(self.events, [])

    def test_update_persists_and_rejects_unavailable(self):
        tapper = self.tapper()
        tapper.available = False
        self.assertFalse(tapper.update(BASE)["success"])
        self.assertTrue(tapper.update(dict(BASE, enabled=False, tapKey="E", intervalMs=150))["success"])
        reloaded = ChestAutoTap(tapper.path, target_window=lambda: None)
        self.assertEqual((reloaded.settings["tapKey"], reloaded.settings["intervalMs"]), ("E", 150))
        self.assertIsNone(reloaded._thread)

    def test_loading_enabled_conflict_disables_with_real_reserved(self):
        path = Path(self.folder.name) / "c0.json"
        path.write_text(json.dumps(dict(schema=chest_auto_tap.SCHEMA, **BASE)), encoding="utf-8")
        tapper = self.tapper(reserved=lambda: {0x7B: "Ctrl+F12 地图"})
        self.assertFalse(tapper.settings["enabled"])
        self.assertIn("Ctrl+F12", tapper.snapshot()["error"])
        self.assertIsNone(tapper._thread)

    def test_disable_applies_despite_bad_settings_conflict_and_save_failure(self):
        reserved = {}
        tapper = self.tapper(reserved=lambda: reserved)
        self.assertTrue(tapper.update(BASE)["success"])
        reserved[0x7B] = "Ctrl+F12"
        with mock.patch.object(tapper, "_save", side_effect=OSError("read only")):
            result = tapper.update(dict(enabled=False, intervalMs="invalid", tapKey="bad"))
        self.assertFalse(result["success"])
        self.assertIn("已关闭", result["message"])
        self.assertIn("保存失败", result["message"])
        self.assertFalse(tapper.snapshot()["enabled"])
        self.assertTrue(tapper._stop.is_set())

    def test_disable_persists_even_if_other_fields_are_invalid(self):
        tapper = self.tapper()
        tapper.update(BASE)
        result = tapper.update(dict(enabled=False, intervalMs="bad"))
        self.assertFalse(result["success"])
        self.assertFalse(json.loads(tapper.path.read_text(encoding="utf-8"))["enabled"])

    def test_reconcile_disables_on_rebinding_and_keeps_reason_after_stop(self):
        reserved = {}
        tapper = self.tapper(reserved=lambda: reserved)
        tapper.update(BASE)
        reserved[ord("F")] = "Ctrl+F 搜索"
        result = tapper.reconcile_reserved()
        self.assertFalse(result["success"])
        self.assertFalse(tapper.snapshot()["enabled"])
        self.assertIn("Ctrl+F", result["message"])
        tapper.stop()
        self.assertIn("Ctrl+F", tapper.snapshot()["status"])
        self.assertFalse(json.loads(tapper.path.read_text(encoding="utf-8"))["enabled"])


class WorkerTests(OfflineTests):
    def test_taps_only_while_held_and_in_game(self):
        tapper = self.tapper()
        tapper.update(dict(BASE, intervalMs=20))
        time.sleep(.03)
        self.assertEqual(self.events, [])
        self.held = True
        wait_for(lambda: len(self.events) >= 6)
        self.held = False
        wait_for(lambda: not tapper.snapshot()["pendingReleaseVks"])
        count = len(self.events)
        self.held, self.foreground = True, False
        time.sleep(.04)
        self.assertEqual(len(self.events), count)
        self.assertIn("前台", tapper.snapshot()["status"])
        self.assertEqual([(vk, up) for vk, up, _ in self.events],
                         [(ord("F"), up) for _ in range(count // 2) for up in (False, True)])

    def test_short_interval_uses_requested_keydown_period(self):
        for interval in (20, 55, 89):
            with self.subTest(interval=interval):
                self.events.clear()
                self.held, self.foreground = True, True
                tapper = self.tapper()
                result = tapper.update(dict(BASE, intervalMs=interval))
                self.assertIn(f"每 {interval} 毫秒开始", result["message"])
                wait_for(lambda: sum(not up for _, up, _ in self.events) >= 4)
                tapper.stop()
                wait_for(lambda: not tapper.snapshot()["pendingReleaseVks"])
                downs = [at for _, up, at in self.events if not up]
                average = (downs[-1] - downs[0]) / (len(downs) - 1)
                self.assertGreaterEqual(average, interval / 1000 - .003)
                self.assertLess(average, interval / 1000 + .025)

    def test_focus_loss_releases_during_press(self):
        self.held = True
        tapper = self.tapper()
        tapper.update(dict(BASE, intervalMs=500))
        wait_for(lambda: bool(self.events))
        self.foreground = False
        wait_for(lambda: len(self.events) >= 2)
        self.assertTrue(self.events[1][1])
        self.assertLess(self.events[1][2] - self.events[0][2], .07)
        time.sleep(.04)
        self.assertEqual(len(self.events), 2)

    def test_keyup_failure_retries_and_error_survives_recovery_and_stop(self):
        self.held = True
        attempts = []
        def send(vk, up):
            self.send(vk, up)
            if up:
                attempts.append(vk)
                return 5 if len(attempts) < 3 else 0
            return 0
        with mock.patch.object(chest_auto_tap, "_send_key", send):
            tapper = self.tapper()
            tapper.update(BASE)
            wait_for(lambda: len(attempts) == 3 and not tapper.snapshot()["pendingReleaseVks"])
            self.assertEqual(sum(not up for _, up, _ in self.events), 1)
            self.assertFalse(tapper.snapshot()["enabled"])
            tapper.stop()
            self.assertIn("释放按键", tapper.snapshot()["error"])
            self.assertIn("Windows 未确认释放", tapper.snapshot()["status"])

    def test_exhausted_release_stays_in_ledger_and_blocks_reenable(self):
        self.held = True
        def send(vk, up):
            self.send(vk, up)
            return 5 if up else 0
        with mock.patch.object(chest_auto_tap, "_send_key", send):
            tapper = self.tapper()
            tapper.update(BASE)
            wait_for(lambda: tapper.snapshot()["releaseRetriesExhausted"])
            time.sleep(.03)
            count = len(self.events)
            tapper.stop()
            self.assertFalse(tapper.update(BASE)["success"])
            self.assertEqual(tapper.snapshot()["pendingReleaseVks"], [ord("F")])
            self.assertIn("耗尽", tapper.snapshot()["status"])
            time.sleep(.04)
            self.assertEqual(len(self.events), count)
            self.assertEqual(count, 1 + 1 + chest_auto_tap.RELEASE_RETRIES)

    def test_failed_keyup_retries_keep_spacing_despite_stop_wakes(self):
        self.held = True
        ups = []
        def send(vk, up):
            self.send(vk, up)
            if up:
                ups.append(time.perf_counter())
                return 5
            return 0
        with mock.patch.object(chest_auto_tap, "_send_key", send), \
                mock.patch.object(chest_auto_tap, "RELEASE_RETRY_SECONDS", .05):
            tapper = self.tapper()
            tapper.update(BASE)
            wait_for(lambda: len(ups) == 1)
            worker = tapper._thread
            with mock.patch.object(worker, "join", side_effect=AssertionError("UI must not join")):
                started = time.perf_counter()
                tapper.stop()
                self.assertTrue(tapper.update(dict(enabled=False))["success"])
                self.assertFalse(tapper.update(BASE)["success"])
                self.assertLess(time.perf_counter() - started, .1)
            wait_for(lambda: tapper.snapshot()["releaseRetriesExhausted"])
            self.assertEqual(len(ups), 1 + chest_auto_tap.RELEASE_RETRIES)
            for previous, current in zip(ups, ups[1:]):
                self.assertGreaterEqual(current - previous, .045)
            self.assertIs(tapper._thread, worker)
            self.assertEqual(tapper.snapshot()["pendingReleaseVks"], [ord("F")])
            self.assertFalse(tapper.snapshot()["enabled"])

    def test_stop_update_never_join_or_spawn_parallel_worker(self):
        self.held = True
        entered, release = threading.Event(), threading.Event()
        def send(vk, up):
            self.send(vk, up)
            if not up:
                entered.set()
                release.wait(1)
            return 0
        with mock.patch.object(chest_auto_tap, "_send_key", send):
            tapper = self.tapper()
            tapper.update(BASE)
            self.assertTrue(entered.wait(1))
            worker = tapper._thread
            try:
                with mock.patch.object(worker, "join", side_effect=AssertionError("UI must not join")):
                    started = time.perf_counter()
                    tapper.stop()
                    tapper.update(dict(enabled=False))
                    self.assertFalse(tapper.update(BASE)["success"])
                    self.assertLess(time.perf_counter() - started, .15)
                    self.assertIs(tapper._thread, worker)
                    self.assertEqual(tapper.snapshot()["pendingReleaseVks"], [ord("F")])
            finally:
                release.set()
            wait_for(lambda: not tapper.snapshot()["pendingReleaseVks"])
            self.held = False
            self.assertTrue(tapper.update(BASE)["success"])
            self.assertIs(tapper._thread, worker)

    def test_worker_exception_releases_uncertain_down(self):
        self.held = True
        def send(vk, up):
            self.send(vk, up)
            if not up:
                raise RuntimeError("uncertain delivery")
            return 0
        with mock.patch.object(chest_auto_tap, "_send_key", send):
            tapper = self.tapper()
            tapper.update(BASE)
            wait_for(lambda: len(self.events) >= 2)
            wait_for(lambda: not tapper.snapshot()["pendingReleaseVks"])
            self.assertFalse(tapper.snapshot()["enabled"])
            self.assertIn("uncertain delivery", tapper.snapshot()["error"])
            self.assertEqual([up for _, up, _ in self.events], [False, True])

    def test_exception_or_config_change_mid_press_releases_old_key(self):
        for exceptional in (False, True):
            with self.subTest(exceptional=exceptional):
                self.events.clear()
                self.held = True
                tapper = self.tapper()
                tapper.update(dict(BASE, intervalMs=500))
                wait_for(lambda: bool(self.events))
                if exceptional:
                    with mock.patch.object(chest_auto_tap, "key_down", side_effect=RuntimeError("read failed")):
                        wait_for(lambda: len(self.events) >= 2)
                        wait_for(lambda: not tapper.snapshot()["enabled"])
                else:
                    self.held = False
                    self.assertTrue(tapper.update(dict(tapKey="E"))["success"])
                    self.assertEqual(tapper.snapshot()["tapKey"], "E")
                    wait_for(lambda: len(self.events) >= 2)
                self.assertEqual([(vk, up) for vk, up, _ in self.events[:2]],
                                 [(ord("F"), False), (ord("F"), True)])
                tapper._disable()

    def test_keydown_failure_disables_without_claiming_a_successful_tap(self):
        self.held = True
        with mock.patch.object(chest_auto_tap, "_send_key", return_value=5) as send:
            tapper = self.tapper()
            tapper.update(BASE)
            wait_for(lambda: not tapper.snapshot()["enabled"])
            self.assertEqual(send.call_count, 1)
            self.assertEqual(tapper.snapshot()["pendingReleaseVks"], [])
            self.assertIn("发送按键失败", tapper.snapshot()["status"])

    def test_keyup_exception_is_retried_from_the_same_ledger(self):
        self.held = True
        ups = []
        def send(vk, up):
            self.send(vk, up)
            if up:
                ups.append(vk)
                if len(ups) == 1:
                    raise RuntimeError("keyup failed")
            return 0
        with mock.patch.object(chest_auto_tap, "_send_key", send):
            tapper = self.tapper()
            tapper.update(BASE)
            wait_for(lambda: len(ups) == 2 and not tapper.snapshot()["pendingReleaseVks"])
            self.assertFalse(tapper.snapshot()["enabled"])
            self.assertIn("keyup failed", tapper.snapshot()["error"])

    def test_worker_fault_during_save_cannot_be_overwritten_by_update(self):
        tapper = self.tapper()
        tapper.update(BASE)
        save = tapper._save
        first = True
        def fault_during_save(settings):
            nonlocal first
            save(settings)
            if first:
                first = False
                tapper._fault("mock worker failed while saving")
        with mock.patch.object(tapper, "_save", fault_during_save):
            result = tapper.update(dict(intervalMs=100))
        self.assertFalse(result["success"])
        self.assertFalse(tapper.snapshot()["enabled"])
        self.assertFalse(json.loads(tapper.path.read_text(encoding="utf-8"))["enabled"])
        self.assertIn("mock worker failed", tapper.snapshot()["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
