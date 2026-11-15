"""Real owned Windows fixtures for the QtIFW background-close backend."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "app", ROOT / "tools"):
    if str(path) not in os.sys.path:
        os.sys.path.insert(0, str(path))

from game_startup import MAIN_NAME, WATCHER_NAME
import game_startup_watcher
from qtifw_process_control import close_background
import qtifw_trial_helper
import qtifw_upgrade


FIXTURE_SOURCE = r"""
#include <windows.h>
#include <wchar.h>

static BOOL ignore_close;

static LRESULT CALLBACK fixture_window_proc(HWND window, UINT message,
                                             WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) {
        if (ignore_close) return 0;
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command, int show) {
    (void)previous; (void)command; (void)show;
    PWSTR full_command = GetCommandLineW();
    ignore_close = wcsstr(full_command, L"--ignore-close") != NULL;
    BOOL hidden = wcsstr(full_command, L"--hidden") != NULL;
    const wchar_t *class_name = L"XingtuProcessControlFixture";
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = fixture_window_proc;
    wc.hInstance = instance;
    wc.lpszClassName = class_name;
    if (!RegisterClassW(&wc)) return 2;
    HWND window = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
        class_name, L"Xingtu process control fixture", WS_POPUP,
        -32000, -32000, 1, 1, NULL, NULL, instance, NULL);
    if (!window) return 3;
    ShowWindow(window, hidden ? SW_HIDE : SW_SHOWNOACTIVATE);
    MSG message;
    while (GetMessageW(&message, NULL, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
"""


@unittest.skipUnless(os.name == "nt", "requires real Win32 process handles")
class QtIfwProcessControlWindowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = Path(r"D:\mingw64\bin\gcc.exe")
        if not compiler.is_file():
            compiler = Path(shutil.which("gcc") or "")
        if not compiler.is_file():
            raise unittest.SkipTest("MinGW gcc.exe unavailable for benign Win32 fixtures")
        cls.compile_dir = tempfile.TemporaryDirectory(prefix="xingtu-process-control-build-")
        cls.fixture = Path(cls.compile_dir.name) / "process-fixture.exe"
        source = Path(cls.compile_dir.name) / "process-fixture.c"
        source.write_text(FIXTURE_SOURCE, encoding="utf-8")
        result = subprocess.run([str(compiler), "-mwindows", "-municode", str(source),
                                 "-o", str(cls.fixture)], capture_output=True, text=True,
                                timeout=30)
        if result.returncode:
            raise unittest.SkipTest("could not compile the isolated Win32 fixture: " + result.stderr[-500:])

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "compile_dir"):
            cls.compile_dir.cleanup()

    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix="xingtu-process-control-")
        self.base = Path(self.sandbox.name)
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        self.sandbox.cleanup()

    def target(self, name="target"):
        root = self.base / name
        (root / "program").mkdir(parents=True)
        return root

    def install_fixture(self, root, executable=MAIN_NAME):
        destination = root / "program" / executable
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.fixture, destination)
        return destination

    def launch(self, executable, *arguments):
        process = subprocess.Popen([str(executable), *arguments],
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.processes.append(process)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail("fixture exited before the process-control call")
            time.sleep(0.025)
        return process

    @contextmanager
    def admitted(self, root, names=(MAIN_NAME,)):
        inventory = {name: {"bytes": 1, "sha256": "fixture-only"} for name in names}
        policy = {"upgradeCapable": True, "files": inventory}
        with patch("qtifw_upgrade.check", return_value={"mode": "update", "target": str(root)}), \
             patch("qtifw_upgrade.pending", return_value=None), \
             patch("qtifw_upgrade._old_install",
                   return_value=({"files": inventory}, {"phase": "complete"}, {})):
            yield policy

    def test_closes_only_the_exact_managed_image_under_admitted_program(self):
        root = self.target()
        in_target = self.install_fixture(root)
        self.install_fixture(root, WATCHER_NAME)
        outside_root = self.base / "unrelated-install"
        outside_root.mkdir()
        outside = self.install_fixture(outside_root)
        targeted = self.launch(in_target)
        unrelated = self.launch(outside, "--ignore-close")

        with self.admitted(root, (MAIN_NAME, WATCHER_NAME)) as policy, \
             patch("game_startup_watcher.control", side_effect=(10, 0, 0)) as watcher_control:
            result = close_background(policy, root)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["remaining"], [])
        self.assertIsNotNone(targeted.poll())
        self.assertIsNone(unrelated.poll(), "same-name image outside target must remain untouched")
        self.assertEqual([call.args[:2] for call in watcher_control.call_args_list], [
            ("--status", root / "program"),
            ("--disable", root / "program"),
            ("--restore-after-upgrade", root / "program"),
        ])

    def test_hidden_main_window_can_close_without_force(self):
        root = self.target()
        executable = self.install_fixture(root)
        process = self.launch(executable, "--hidden")
        with self.admitted(root) as policy:
            result = close_background(policy, root)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["remaining"], [])
        self.assertIsNotNone(process.poll())

    def test_process_started_during_watcher_disable_is_reported_not_closed(self):
        root = self.target()
        main = self.install_fixture(root)
        self.install_fixture(root, WATCHER_NAME)
        late = []

        def watcher_control(command, program, windows):
            if command == "--disable":
                late.append(self.launch(main, "--hidden"))
            return 0

        with self.admitted(root, (MAIN_NAME, WATCHER_NAME)) as policy, \
             patch("game_startup_watcher.control", side_effect=watcher_control):
            result = close_background(policy, root)

        self.assertFalse(result["success"], result)
        self.assertEqual(result["reason"], "late-process-detected")
        self.assertEqual(result["matchedCount"], 0)
        self.assertIn(late[0].pid, [row["pid"] for row in result["remaining"]])
        self.assertIsNone(late[0].poll(), "late candidates are reported, never silently stopped")

    def test_graceful_timeout_reports_process_and_force_is_explicit(self):
        root = self.target()
        executable = self.install_fixture(root)
        process = self.launch(executable, "--ignore-close")

        with self.admitted(root) as policy:
            graceful = close_background(policy, root, force=False)
        self.assertFalse(graceful["success"])
        self.assertEqual(graceful["reason"], "processes-still-running")
        self.assertIn(process.pid, [row["pid"] for row in graceful["remaining"]])
        self.assertIsNone(process.poll(), "graceful close must not silently terminate the process")

        with self.admitted(root) as policy:
            forced = close_background(policy, root, force=True)
        self.assertTrue(forced["success"], forced)
        self.assertEqual(forced["remaining"], [])
        self.assertIsNotNone(process.poll())

    def test_unadmitted_target_does_not_touch_matching_process(self):
        root = self.target()
        executable = self.install_fixture(root)
        process = self.launch(executable, "--ignore-close")
        with patch("qtifw_upgrade.check", return_value={"mode": "fresh", "target": str(root)}):
            result = close_background({"upgradeCapable": True, "files": {MAIN_NAME: {}}}, root)
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "target-not-admitted:fresh")
        self.assertEqual(result["remaining"], [])
        self.assertIsNone(process.poll(), "fresh/invalid locations must not signal a process")

    def test_real_upgrade_admission_closes_owned_hidden_process(self):
        root = self.target("real-admitted-install")
        executable = self.install_fixture(root)
        executable_bytes = executable.read_bytes()
        instance = dict(id="production", appId="D04394A0-DACE-4FF9-874D-3DCB43EE53D1")
        old_values = {
            MAIN_NAME: executable_bytes,
            "_internal/installation-profile.json": json.dumps(dict(
                product="星图", sourceCommit="a" * 40, installerInstance=instance)).encode("utf-8"),
            "release-report.json": json.dumps(dict(
                sourceCommit="a" * 40, displayVersion="1.1.2")).encode("utf-8"),
        }
        rows = {name: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
                for name, data in old_values.items()}
        old_policy = dict(instance="a" * 32, payloadSource="a" * 40,
                          portable=True, fullUninstall=True, allowNewTargets=True, files=rows)
        owner = dict(instance=old_policy["instance"], payloadSource=old_policy["payloadSource"],
                     phase="complete", target=str(root))

        def write(path, data):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        for name, data in old_values.items():
            path = root / "program" / name
            if name == MAIN_NAME:
                self.assertEqual(path.read_bytes(), data)
            else:
                write(path, data)
        qtifw_trial_helper.write_owner(root, owner, new=True)
        write(root / "_installer-support/_internal/qtifw-trial-policy.json",
              json.dumps(old_policy).encode("utf-8"))
        write(root / "trial-maintenance.exe", b"benign fixture maintenance sentinel")
        packages = (qtifw_upgrade.PACKAGE, qtifw_upgrade.DATA_PACKAGE)
        component_data = "<Packages>" + "".join(
            f"<Package><Name>{name}</Name><Version>1.0.0</Version></Package>" for name in packages
        ) + "</Packages>"
        write(root / "components.xml", component_data.encode("utf-8"))
        new_policy = dict(
            instance="b" * 32, payloadSource="b" * 40, portable=True, fullUninstall=True,
            allowNewTargets=True, upgradeCapable=True,
            targetVersion=dict(version="1.1.8", displayVersion="1.1.8"),
            files={MAIN_NAME: dict(bytes=1, sha256="new-current-payload-not-extracted"),
                   WATCHER_NAME: dict(bytes=1, sha256="new-watcher-not-extracted")},
        )

        process = self.launch(executable, "--hidden")
        self.assertEqual(qtifw_upgrade.check(new_policy, root)["mode"], "update")
        with patch("game_startup_watcher.control", wraps=game_startup_watcher.control) as control:
            result = close_background(new_policy, root)
        control.assert_not_called()  # New payload inventory is not the installed watcher.
        self.assertTrue(result["success"], result)
        self.assertEqual(result["remaining"], [])
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
