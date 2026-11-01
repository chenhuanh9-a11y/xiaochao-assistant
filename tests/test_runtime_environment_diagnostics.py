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

if __name__ == '__main__':
    import unittest
    suite = unittest.TestSuite(unittest.FunctionTestCase(fn) for name, fn in list(globals().items())
                               if name.startswith('test_'))
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not outcome.wasSuccessful())
