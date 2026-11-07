"""Offline policy checks; not evidence of native UAC/Run/installer validation."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from game_startup import EpisodeLedger, handle_observation, title_allowed, run_command, run_name


class GameStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "episodes.json"
        self.ledger = EpisodeLedger(self.path)
        self.launches = []

    def observe(self, games=((123, 1000),), state="absent", enabled=True, result="requested"):
        def launch():
            self.launches.append(result)
            return result
        return handle_observation(games, state, self.ledger, launch, lambda: enabled)

    def test_strict_titles(self):
        for title in ("第五人格", "Identity V", "IdentityV", "Identity V - Global", " identity v "):
            self.assertTrue(title_allowed(title), title)
        for title in ("第五人格小助手", "Identity Video", "IdentityVFake", "浏览器 - 第五人格", "", None):
            self.assertFalse(title_allowed(title), title)

    def test_already_running_game_launches_on_first_observation(self):
        self.assertEqual(self.observe(), "requested")
        self.assertEqual(self.launches, ["requested"])

    def test_cancel_does_not_prompt_again_even_after_watcher_restart(self):
        self.observe(result="cancelled-or-failed")
        self.ledger = EpisodeLedger(self.path)
        self.observe()
        self.assertEqual(len(self.launches), 1)

    def test_explicit_main_exit_and_hidden_game_do_not_restart_main(self):
        self.observe(state="present")
        self.observe(games=())
        self.observe(state="absent")
        self.assertEqual(self.launches, [])

    def test_exit_recorded_before_first_watcher_observation(self):
        calls = []
        handle_observation(((123, 1000),), "absent", self.ledger,
                           lambda: calls.append(True), lambda: True, ["123:1000"])
        self.assertEqual(calls, [])
        self.assertEqual(self.ledger.keys, ["123:1000"])

    def test_pid_reuse_is_new_episode(self):
        self.observe()
        self.observe(games=((123, 1001),))
        self.assertEqual(len(self.launches), 2)

    def test_duplicate_windows_launch_once(self):
        self.observe(games=((123, 1000), (123, 1000)))
        self.assertEqual(len(self.launches), 1)

    def test_multiple_simultaneous_games_request_one_launch(self):
        self.observe(games=((123, 1000), (124, 1002)))
        self.observe(games=((123, 1000), (124, 1002)))
        self.assertEqual(len(self.launches), 1)

    def test_unknown_main_identity_fails_closed(self):
        self.observe(state="unknown")
        self.observe()
        self.assertEqual(self.launches, [])

    def test_disable_between_detection_and_launch(self):
        self.observe(enabled=False)
        self.assertEqual(self.launches, [])

    def test_ledger_write_failure_prevents_uac(self):
        with patch("game_startup.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                self.observe()
        self.assertEqual(self.launches, [])
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_corrupt_state_does_not_reset_and_prompt(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"bad": true}')
        with self.assertRaises(ValueError):
            EpisodeLedger(self.path)
        self.path.write_text('broken')
        with self.assertRaises(ValueError):
            EpisodeLedger(self.path)

    def test_capacity_never_evicts_live_or_unknown_episodes(self):
        self.ledger.keys = [f"{pid}:1000" for pid in range(256)]
        self.ledger.alive = lambda key: None
        with self.assertRaises(ValueError):
            self.ledger.reserve(999, 1000)
        self.assertEqual(len(self.ledger.keys), 256)
        self.ledger.alive = lambda key: key == "0:1000"
        self.assertTrue(self.ledger.reserve(999, 1000))
        self.assertEqual(self.ledger.keys, ["0:1000", "999:1000"])

    def test_reservation_is_on_disk_before_shell_call(self):
        def launch():
            self.assertEqual(json.loads(self.path.read_text()), ["123:1000"])
        handle_observation(((123, 1000),), "absent", self.ledger, launch, lambda: True)

    def test_registration_has_no_user_controlled_arguments(self):
        directory = Path(self.temp.name) / "space dir"
        command = run_command(directory)
        self.assertEqual(command, subprocess.list2cmdline([str(directory.resolve() / "XingtuGameWatcher.exe")]))
        self.assertEqual(run_name(directory), run_name(directory))
        self.assertNotEqual(run_name(directory), run_name(directory / "other"))

    def test_main_identity_matches_actual_qt_production_build(self):
        from game_startup import MAIN_NAME
        from packaged_product_identity import QT_EXE, LEGACY_EXE
        tree = ast.parse((ROOT / "tools/build_qt_client.py").read_text())
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "INSTANCES" for t in node.targets))
        production = next(value for key, value in zip(assignment.value.keys, assignment.value.values)
                          if isinstance(key, ast.Constant) and key.value == "production")
        product = next(ast.literal_eval(item.value) for item in production.keywords if item.arg == "product")
        self.assertEqual(MAIN_NAME, product + ".exe")
        self.assertEqual(MAIN_NAME, QT_EXE)
        self.assertNotEqual(MAIN_NAME, LEGACY_EXE)
        installer = (ROOT / "tools/qt-client.iss").read_text()
        self.assertIn('InstallerExeName != "' + MAIN_NAME + '"', installer)

    def test_source_execution_cannot_register_or_run(self):
        result = subprocess.run([sys.executable, str(ROOT / "app/game_startup_watcher.py")], check=False)
        self.assertEqual(result.returncode, 2)

    def test_watcher_has_no_heavy_import_graph(self):
        forbidden = {"PySide6", "cv2", "numpy", "psutil", "identityv_live_mvp", "client"}
        for name in ("game_startup", "game_startup_win32", "game_startup_watcher"):
            tree = ast.parse((ROOT / "app" / (name + ".py")).read_text())
            for node in ast.walk(tree):
                imports = ([n.name for n in node.names] if isinstance(node, ast.Import) else
                           [node.module] if isinstance(node, ast.ImportFrom) else [])
                self.assertFalse(forbidden.intersection((n or "").split('.')[0] for n in imports))

class NativeAdapterPolicyTests(unittest.TestCase):
    """Fake API behavior only: no claims about Windows ABI or permissions."""
    def enumerate(self, title="第五人格", executable=r"C:\Game\dwrg.exe", session=7,
                  visible=True, process_error=False):
        from game_startup_win32 import Windows
        class User:
            def IsWindowVisible(self, hwnd):
                return visible
            def GetWindowTextW(self, hwnd, buffer, size):
                buffer.value = title
                return len(title)
            def GetWindowThreadProcessId(self, hwnd, pid):
                pid._obj.value = 123
                return 1
            def EnumWindows(self, callback, extra):
                callback(55, 0)
                callback(56, 0)
                return True
        native = Windows.__new__(Windows)
        native.user = User()
        native.callback_type = lambda callback: callback
        native.session = 7
        native.session_for = lambda pid: session
        def process(pid):
            if process_error:
                raise OSError("Access denied")
            return executable, 1000
        native.process = process
        return native.games()

    def test_native_adapter_deduplicates_windows_by_pid_creation(self):
        self.assertEqual(self.enumerate(), [(123, 1000)])

    def test_native_adapter_requires_same_session(self):
        self.assertEqual(self.enumerate(session=8), [])

    def test_native_adapter_rejects_wrong_executable_and_title(self):
        self.assertEqual(self.enumerate(executable=r"C:\Game\other.exe"), [])
        self.assertEqual(self.enumerate(title="第五人格小助手"), [])

    def test_native_adapter_does_not_infer_identity_when_unreadable(self):
        self.assertEqual(self.enumerate(process_error=True), [])
        self.assertEqual(self.enumerate(visible=False), [])


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.values = {}
        class Key:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def query(key, name):
            if name not in self.values: raise FileNotFoundError(name)
            return self.values[name]
        def delete(key, name):
            if name not in self.values: raise FileNotFoundError(name)
            del self.values[name]
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, REG_SZ=1, KEY_SET_VALUE=2, KEY_QUERY_VALUE=1,
            OpenKey=lambda *args: Key(), CreateKeyEx=lambda *args: Key(), QueryValueEx=query,
            SetValueEx=lambda key, name, reserved, kind, value: self.values.update({name: (value, kind)}),
            DeleteValue=delete)
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, winreg=registry).start()
        patch("game_startup.os", SimpleNamespace(name="nt")).start()

    def test_missing_registration_is_off_and_disable_is_idempotent(self):
        from game_startup import enabled, set_enabled
        self.assertFalse(enabled(self.directory))
        set_enabled(self.directory, False)
        self.assertEqual(self.values, {})

    def test_enable_requires_complete_install_and_exact_command(self):
        from game_startup import enabled, set_enabled, WATCHER_NAME, MAIN_NAME
        with self.assertRaises(OSError): set_enabled(self.directory, True)
        for name in (WATCHER_NAME, MAIN_NAME): (self.directory / name).touch()
        set_enabled(self.directory, True)
        self.assertTrue(enabled(self.directory))
        self.values[run_name(self.directory)] = (run_command(self.directory) + " --unexpected", 1)
        self.assertFalse(enabled(self.directory))

    def test_disable_only_removes_own_installation_value(self):
        from game_startup import set_enabled
        self.values["unrelated"] = ("keep", 1)
        self.values[run_name(self.directory)] = (run_command(self.directory), 1)
        set_enabled(self.directory, False)
        self.assertEqual(self.values, {"unrelated": ("keep", 1)})

    def test_disable_refuses_unrecognized_command_under_the_owned_name(self):
        from game_startup import set_enabled
        self.values[run_name(self.directory)] = ("changed externally", 1)
        with self.assertRaises(OSError):
            set_enabled(self.directory, False)
        self.assertEqual(self.values[run_name(self.directory)], ("changed externally", 1))


if __name__ == "__main__":
    unittest.main()
