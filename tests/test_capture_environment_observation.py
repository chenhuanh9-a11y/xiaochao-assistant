"""Capture observation uses the real provider with synthetic backend pixels."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'experiments' / 'qt_quick'))
from test_client_capture_full import Native, Backend
from client_capture import ClientCaptureProvider, CaptureUnavailable

class CaptureObservationTests(unittest.TestCase):
    def test_success_records_existing_backend_and_monotonic_duration(self):
        provider = ClientCaptureProvider(Native(), Backend())
        provider.bindFullClient(42)
        provider.enable()
        frame = provider.capture()
        observed = provider.snapshot['lastCaptureObservation']
        self.assertEqual(observed['backend'], frame.acquisition)
        self.assertEqual(observed['status'], 'captured')
        self.assertGreaterEqual(observed['durationMs'], 0)
        observed['status'] = 'mutated'
        self.assertEqual(provider.snapshot['lastCaptureObservation']['status'], 'captured')

    def test_failure_remains_failure_and_records_guard_duration(self):
        native = Native()
        provider = ClientCaptureProvider(native, Backend())
        provider.bindFullClient(42)
        provider.enable()
        native.foreground_window = lambda: 99
        with self.assertRaises(CaptureUnavailable) as raised:
            provider.capture()
        self.assertGreaterEqual(raised.exception.capture_trace['durationMs'], 0)
        self.assertEqual(provider.snapshot['lastCaptureObservation']['status'], 'failed')
        self.assertEqual(provider.snapshot['status'], 'failed')

if __name__ == '__main__': unittest.main()
