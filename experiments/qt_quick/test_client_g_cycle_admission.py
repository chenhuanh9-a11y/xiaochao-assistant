"""Diagnostic waits must not move old commands across a real runtime cutoff.

S0/S1 and both active modes use existing synthetic capture/solver fixtures. The
observed lock delegates to the original RLock and only exposes arrival; neither
the production serial allocation nor cancellation guards are mocked.
"""
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock, patch

import test_client_live_runtime as live
import test_s1_formal_round_lock as formal


class EvidenceAdmissionTests(unittest.TestCase):
    def assert_ended_while_waiting(self, wait_at):
        for season in ('s0', 's1'):
            for mode in ('nightmare', 'difficult'):
                for start_requested in (False, True):
                    outcomes = []
                    for enabled in (False, True):
                        with self.subTest(wait_at=wait_at, season=season, mode=mode,
                                          start_requested=start_requested, evidence=enabled):
                            outcomes.append(self.run_waiting_map(season, mode, start_requested, enabled, wait_at))
                    if len(outcomes) == 2:
                        self.assertEqual(outcomes[0], outcomes[1], 'diagnostics changed the business outcome')

    def run_waiting_map(self, season, mode, start_requested, enabled, wait_at):
        fixture = live.LiveRuntimeContracts() if season == 's0' else formal.FormalRoundLockContracts()
        fixture.setUp()
        worker = None
        release = threading.Event()
        try:
            runtime = fixture.runtime
            if season == 's1':
                fixture.registry.engine.pack.mode = mode
                fixture.registry.catalog = replace(fixture.registry.catalog, mode=mode)
                fixture.enterContext(patch('s1_structure_selection.StructureCatalog.from_engine',
                                           return_value=fixture.registry.catalog))
                original_scan = fixture.registry.engine.qt_s1_structure_scan
                def adapter(frame, *, collect_fit_diagnostics=False, **kwargs):
                    return original_scan(frame, **kwargs)
                fixture.registry.engine.qt_s1_structure_scan = adapter
            self.assertTrue(runtime.dispatch('set_context', dict(season=season, mode=mode))['success'])
            if start_requested:
                self.assertTrue(runtime.dispatch('start_round')['success'])
            arrived = threading.Event()
            main, inner = threading.get_ident(), runtime._lock
            class ObservedLock:
                def __enter__(self):
                    if threading.get_ident() != main:
                        arrived.set()
                    inner.acquire()
                    return self

                def __exit__(self, *unused):
                    inner.release()
            runtime._lock = ObservedLock()
            serials, worker_serials = runtime._dispatch_serials, []
            class ObservedSerials:
                def __next__(self):
                    serial = next(serials)
                    if threading.get_ident() != main:
                        worker_serials.append(serial)
                    return serial
            runtime._dispatch_serials = ObservedSerials()
            if enabled:
                recorder = Mock(accepting=True, full_input=False)
                recorder.submit.return_value = dict(status='queued')
                if wait_at == 'accepting':
                    class WaitingRecorder:
                        full_input = False
                        submit = recorder.submit

                        @property
                        def accepting(self):
                            if threading.get_ident() != main:
                                arrived.set()
                                if not release.wait(3):
                                    raise AssertionError('diagnostic acceptance was not released')
                            return True
                    recorder = WaitingRecorder()
                runtime.g_cycle_evidence = recorder
                if wait_at == 'trace-error':
                    def failed_trace(*args):
                        with runtime._lock:
                            raise OSError('synthetic diagnostic initialization failure')
                    fixture.enterContext(patch('client_g_cycle_evidence._Trace', side_effect=failed_trace))
            state = runtime.snapshot()
            payload = {key: state[key] for key in ('season', 'mode', 'profileId', 'roundId', 'frameId')}
            payload.update(generation=22, openIntent=True, inputSequence=1)
            results, errors = [], []
            def dispatch():
                try:
                    results.append(runtime.dispatch('map', payload, threading.Event()))
                except BaseException as error:
                    errors.append(error)
            with runtime._lock:
                worker = threading.Thread(target=dispatch)
                worker.start()
                self.assertTrue(arrived.wait(3), 'old map did not reach its blocking boundary')
                self.assertTrue(runtime.dispatch('end_round')['success'])
                cutoff = runtime._dispatch_cutoff
                release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors, errors)
            if season == 's1':
                fixture.idle()
            self.assertEqual(len(results), 1)
            result = results[0]
            state = runtime.snapshot()
            outcome = (result['success'], result['reason'], result.get('stale', False),
                fixture.capture.count, state['roundActive'], state['displayAuthorized'])
            self.assertEqual(outcome, (False, 'cancelled-or-stale', True, 0, False, False))
            self.assertEqual(len(worker_serials), 1, 'diagnostic fallback allocated a second arrival')
            self.assertLessEqual(worker_serials[0], cutoff)
            if enabled and wait_at != 'trace-error':
                recorder.submit.assert_called_once()
                self.assertIsNone(recorder.submit.call_args.args[0])
                self.assertEqual(recorder.submit.call_args.args[1]['outcome'], 'cancelled')
            return outcome
        finally:
            release.set()
            if worker is not None:
                worker.join(5)
            fixture.doCleanups()

    def test_state_lock_wait_cannot_rebirth_old_map_after_end(self):
        self.assert_ended_while_waiting('state')

    def test_recorder_acceptance_wait_cannot_rebirth_old_map_after_end(self):
        self.assert_ended_while_waiting('accepting')

    def test_observer_setup_failure_reuses_original_fenced_arrival(self):
        self.assert_ended_while_waiting('trace-error')


if __name__ == '__main__':
    unittest.main()
