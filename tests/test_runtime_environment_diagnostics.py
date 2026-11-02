import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from runtime_environment_diagnostics import freeze_runtime_inputs, collect_runtime_environment

class Native:
    def __init__(self): self.calls = []
    def integrity(self, pid=None):
        self.calls.append(pid)
        if pid is not None: raise OSError(5, 'OpenProcess denied')
        return dict(status='available', level='medium', rid=8192)
    def window_pid(self, hwnd): return 42
    def window(self, hwnd): return dict(status='available', dpi=144)

def test_frozen_inputs_are_detached():
    screens = [{'dpi': 144}]
    inputs = freeze_runtime_inputs(screens=screens)
    screens[0]['dpi'] = 96
    result = collect_runtime_environment(inputs, platform_name='linux')
    assert result['screens'] == [{'dpi': 144}]
    assert result['qtProcessIntegrity']['status'] == 'unavailable'
    assert result['capture']['reason']

def test_bound_process_denial_is_unavailable_without_escalation():
    native = Native()
    result = collect_runtime_environment(freeze_runtime_inputs(bound_identity={'hwnd': 9, 'pid': 42}), native=native, platform_name='win32')
    assert native.calls == [None, 42]
    assert result['qtProcessIntegrity']['level'] == 'medium'
    assert result['gameProcessIntegrity'] == dict(status='unavailable', reason='OpenProcess denied', errorCode=5)
    assert result['boundWindow']['dpi'] == 144

def test_stale_binding_never_reads_unbound_process():
    native = Native()
    result = collect_runtime_environment(freeze_runtime_inputs(bound_identity={'hwnd': 9, 'pid': 43}), native=native, platform_name='win32')
    assert native.calls == [None]
    assert result['gameProcessIntegrity']['status'] == 'unavailable'

def test_no_binding_never_discovers_processes():
    native = Native()
    result = collect_runtime_environment(freeze_runtime_inputs(), native=native, platform_name='win32')
    assert native.calls == [None]
    assert result['boundWindow']['reason'] == 'bound HWND/PID not supplied'

# Exercise production Win32 structure/API marshalling with a fake API only.
def test_native_geometry_has_client_and_monitor_without_names_or_mode_guess():
    import ctypes
    from ctypes import wintypes as w
    from runtime_environment_diagnostics import WindowsDiagnostics
    class Api:
        def GetWindowRect(self, hwnd, pointer):
            r = pointer._obj; r.left, r.top, r.right, r.bottom = 90, 180, 1390, 930
            return 1
        def GetClientRect(self, hwnd, pointer):
            r = pointer._obj; r.left, r.top, r.right, r.bottom = 0, 0, 1280, 720
            return 1
        def ClientToScreen(self, hwnd, pointer):
            pointer._obj.x, pointer._obj.y = 100, 200
            return 1
        def MonitorFromWindow(self, hwnd, flags):
            assert flags == 0
            return 7
        def GetMonitorInfoW(self, monitor, pointer):
            m = pointer._obj
            m.rcMonitor.left, m.rcMonitor.top, m.rcMonitor.right, m.rcMonitor.bottom = 0, 0, 1920, 1080
            m.rcWork.left, m.rcWork.top, m.rcWork.right, m.rcWork.bottom = 0, 0, 1920, 1040
            m.dwFlags = 1
            return 1
        def IsZoomed(self, hwnd): return 0
        def IsIconic(self, hwnd): return 0
    native = WindowsDiagnostics.__new__(WindowsDiagnostics)
    native.user, native.w = Api(), w
    row = native.window(9)
    assert row['clientArea'] == dict(status='available', size=[1280, 720], screenBounds=[100,200,1380,920])
    assert row['monitor']['resolution'] == [1920, 1080]
    assert row['fullscreenMode']['reason']
    assert row['dpi']['reason'] == 'GetDpiForWindow API absent'
    assert not {'title', 'deviceName', 'username'} & row.keys()


def test_source_revision_is_exact_checkout_and_missing_is_explicit():
    import tempfile
    from runtime_environment_diagnostics import source_revision
    import subprocess
    root = Path(__file__).parents[1]
    assert source_revision(root)['commit'] == subprocess.check_output(['git','rev-parse','HEAD'], cwd=root, text=True).strip()
    with tempfile.TemporaryDirectory() as folder:
        assert source_revision(folder)['reason'] == 'source checkout metadata absent'


def test_windows_own_hidden_window_native_diagnostics_smoke():
    import os
    import unittest
    if sys.platform != 'win32':
        raise unittest.SkipTest('native Win32 smoke runs on Windows CI only')
    import ctypes
    from ctypes import wintypes as w
    from runtime_environment_diagnostics import WindowsDiagnostics
    native=WindowsDiagnostics()
    create=native.user.CreateWindowExW
    create.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.c_int,ctypes.c_int,
                     ctypes.c_int,ctypes.c_int,w.HWND,w.HMENU,w.HINSTANCE,ctypes.c_void_p]
    create.restype=w.HWND
    native.user.DestroyWindow.argtypes=[w.HWND]
    # Empty title; no WS_VISIBLE, hooks, input injection, game or desktop capture.
    hwnd=create(0,'STATIC','',0x00CF0000,20,20,640,480,None,None,None,None)
    assert hwnd, 'CreateWindowExW failed for owned hidden diagnostic test window'
    try:
        result=collect_runtime_environment(freeze_runtime_inputs(bound_identity={'hwnd':int(hwnd),'pid':os.getpid()}))
        assert result['qtProcessIntegrity']['status']=='available', result
        assert result['gameProcessIntegrity']['status']=='available', result
        window=result['boundWindow']
        assert window['status']=='available',window
        assert window['clientArea']['status']=='available',window
        assert all(value>0 for value in window['clientArea']['size']),window
        assert window['monitor']['status']=='available' or window['monitor'].get('reason'),window
        assert window['fullscreenMode']['status']=='unavailable',window
    finally:
        assert native.user.DestroyWindow(hwnd)


if __name__ == '__main__':
    import unittest
    suite = unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in list(globals().items())
                               if name.startswith('test_'))
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not outcome.wasSuccessful())
