"""Source-scope tripwires plus opt-in Windows execution; no synthetic OS pass."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tests/windows/owned_dialog_probe.cs'
SCRIPT = ROOT / 'tests/windows/owned_dialog_probe.ps1'


class OwnedDialogProbeScopeTests(unittest.TestCase):
    def test_no_desktop_discovery_or_input_fallback(self):
        source = SOURCE.read_text(encoding='utf-8')
        for forbidden in ('AutomationElement.RootElement', 'EnumWindows(', 'SendKeys',
                          'SendInput(', 'mouse_event', 'keybd_event', 'SetForegroundWindow',
                          'Process.Start(', 'GetProcesses('):
            self.assertNotIn(forbidden, source)
        self.assertIn('AutomationElement.FromHandle(id.Handle)', source)
        self.assertIn('owned.TryGetValue(requested.Handle, out f)', source)

    def test_thread_and_timeout_contract_is_explicit(self):
        source = SOURCE.read_text(encoding='utf-8')
        for required in ('ApartmentState.STA', 'ApartmentState.MTA', 'controller.Join(45000)',
                         'Environment.Exit(124)', 'f.Attempts++', 'Now >= f.Deadline'):
            self.assertIn(required, source)
        self.assertNotIn('ExecutionPolicy', SCRIPT.read_text(encoding='utf-8'))

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('XINGTU_RUN_OWNED_DIALOG_PROBE') == '1',
                         'requires explicit disposable Windows runner opt-in')
    def test_owned_windows_via_uia(self):
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(SCRIPT)],
            capture_output=True, text=True, timeout=65, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS synthetic-only UIAutomation probe; Setup/game/UAC NOT tested',
                      result.stdout)


if __name__ == '__main__':
    unittest.main()
