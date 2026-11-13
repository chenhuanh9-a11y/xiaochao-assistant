"""Qt-free lifecycle coverage for the runtime owner around the real grid service."""
import os
from pathlib import Path
import sys
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / part) for part in
                ("app", "experiments/qt_quick", "hybrid/python", "tests")]

import fast_structure_alignment as fast
from csharp_grid_backend import GridBackend, GridFailure
from csharp_grid_runtime import CSharpGridRuntime
from single_map_structure import current_geometry
from test_hybrid_grid_backend import ambiguous_scene


EXE = Path(os.environ["STARMAP_GRID_EXE"]) if os.environ.get("STARMAP_GRID_EXE") else ROOT / "hybrid/StarMap.Grid/bin/Release/net10.0/StarMap.Grid.exe"


class CSharpGridRuntimeTests(unittest.TestCase):
    def test_malformed_terminal_stays_failed_when_preferred_candidate_catches_it(self):
        malformed = [dict(ok=True, rows=None), dict(ok=True, rows=[["bad", .5, [1, 2]]]),
            dict(ok=True, rows=[[.8, True, [1, 2]]]), dict(ok=True, rows=[[.8, .5, {}]]),
            dict(ok="true", rows=[]), dict(ok=True, rows=[], id=True)]
        for terminal in malformed:
            with self.subTest(terminal=terminal):
                runtime = self.runtime()
                with self.assertRaises(GridFailure):
                    with runtime.request_scope() as cancelled:
                        def start(client):
                            client._messages = queue.Queue()
                        def send(client, packet):
                            reply = dict(event="result", session=packet["session"], id=packet["id"])
                            reply.update(terminal)
                            client._messages.put(reply)
                        # Only communication is replaced. The actual proposal,
                        # candidate validation and whole-request owner execute.
                        with patch.object(GridBackend, "_start", start), patch.object(GridBackend, "_send", send):
                            try:
                                with runtime.fit_scope():
                                    self.propose(cancelled=cancelled)
                            except GridFailure:
                                pass  # Actual scanner can swallow preferred-fit errors.
                        self.assertTrue(runtime._client.request_failed)
                        self.assertTrue(cancelled())
                self.assertTrue(runtime.request_failed)

    @classmethod
    def setUpClass(cls):
        if not EXE.is_file():
            raise RuntimeError("Build hybrid/StarMap.Grid in Release before this test.")
        source, cls.frame = ambiguous_scene()
        cls.prepared = fast.prepare(source)
        cls.context = current_geometry(cls.frame, observation_bounds=(0, 0, 640, 480))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def runtime(self, **kwargs):
        runtime = CSharpGridRuntime(EXE, self.temp.name, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def propose(self, *, cancelled=None):
        return fast.propose(self.frame, None, self.prepared, context=self.context,
                            cancelled=cancelled)

    def test_request_without_grid_has_no_grid_timeout(self):
        runtime = self.runtime(timeout_seconds=.01)
        with runtime.request_scope() as cancelled:
            time.sleep(.03)
            self.assertFalse(cancelled())
        self.assertEqual(runtime.calls, ())
        self.assertIsNone(runtime.process_id)

    def test_qt_request_routes_proposal_without_explicit_fit_scope(self):
        runtime = self.runtime()
        with patch.object(fast, "_grid", side_effect=AssertionError("Python grid used")):
            with runtime.request_scope() as cancelled:
                result = self.propose(cancelled=cancelled)
        self.assertIsNone(fast._grid_backend.get())
        self.assertEqual(result[2]["reason"], "fast-ambiguous")
        self.assertEqual(len(runtime.calls), 1)
        self.assertGreater(runtime.calls[0]["candidateCount"], 0)
        self.assertIsNotNone(runtime.process_id)

    def test_python_work_after_returned_grid_has_no_grid_timeout(self):
        runtime = self.runtime()
        # Warm the real process before applying the short component budget.
        with runtime.fit_scope() as cancelled:
            self.propose(cancelled=cancelled)
        initial_pid = runtime.process_id
        runtime.timeout_seconds = .15
        with runtime.request_scope() as cancelled, runtime.fit_scope():
            search = fast._grid_backend.get()
            search([[1., 2.]] * 40, self.prepared, 1., None, cancelled)
            self.assertEqual(len(runtime.calls), 1)
            time.sleep(.2)
            self.assertFalse(cancelled())
            with runtime.fit_scope():
                search([[1., 2.]] * 40, self.prepared, 1., None, cancelled)
            self.assertEqual([call["requestId"] for call in runtime.calls], [2, 2])
            self.assertEqual([call["gridId"] for call in runtime.calls], [2, 3])
            self.assertEqual(runtime.process_id, initial_pid)

    def test_host_cancel_during_grid_prevents_retry_in_same_outer_request(self):
        host_cancel = threading.Event()
        searches_started = []

        def events(event):
            if event["event"] == "started":
                searches_started.append(event["gridId"])
                host_cancel.set()

        runtime = self.runtime(event_sink=events)
        with self.assertRaisesRegex(InterruptedError, "grid-cancelled"):
            with runtime.request_scope() as qt_cancel:
                with self.assertRaises(InterruptedError), runtime.fit_scope(cancelled=host_cancel.is_set):
                    self.propose(cancelled=host_cancel.is_set)
                host_cancel.clear()
                self.assertTrue(qt_cancel())
                # Even if a later candidate omits the Qt cancellation check,
                # the failed search cannot be reset by opening another fit.
                with self.assertRaises(InterruptedError), runtime.fit_scope():
                    self.propose(cancelled=None)
        self.assertEqual(searches_started, [1])
        self.assertIsNone(runtime.process_id)

    def test_grid_timeout_during_native_work_stays_failed(self):
        armed = False
        def events(event):
            if armed and event["event"] == "started":
                time.sleep(.2)

        runtime = self.runtime(event_sink=events)
        with runtime.fit_scope() as cancelled:
            self.propose(cancelled=cancelled)
        armed = True
        runtime.timeout_seconds = .15
        with self.assertRaisesRegex(InterruptedError, "grid-timeout"):
            with runtime.request_scope() as qt_cancel:
                with self.assertRaisesRegex(InterruptedError, "grid-timeout"), runtime.fit_scope():
                    self.propose(cancelled=qt_cancel)
                self.assertTrue(qt_cancel())
        self.assertTrue(runtime.request_failed)
        self.assertIsNone(runtime.process_id)

    def test_nested_fit_uses_one_real_request_and_resets_context(self):
        runtime = self.runtime()
        self.assertIsNone(runtime.process_id)
        self.assertEqual(runtime.calls, ())
        other_thread_values = []

        with runtime.request_scope() as request_cancelled:
            with runtime.fit_scope() as fit_cancelled:
                self.assertTrue(callable(fast._grid_backend.get()))
                self.assertFalse(request_cancelled())
                self.assertFalse(fit_cancelled())
                worker = threading.Thread(target=lambda: other_thread_values.append(
                    fast._grid_backend.get()))
                worker.start()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(self.propose(cancelled=fit_cancelled)[2]["reason"],
                                 "fast-ambiguous")
        self.assertEqual(other_thread_values, [None])
        self.assertIsNone(fast._grid_backend.get())
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0]["requestId"], 1)
        self.assertIsNotNone(runtime.process_id)

        first_pid = runtime.process_id
        with runtime.fit_scope() as cancelled:
            self.propose(cancelled=cancelled)
        self.assertEqual(runtime.process_id, first_pid)
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0]["requestId"], 2)

    def test_nested_derived_cancel_callback_does_not_recurse(self):
        runtime = self.runtime()
        with runtime.request_scope() as combined_cancelled:
            derived_cancelled = lambda: combined_cancelled()
            with runtime.fit_scope(cancelled=derived_cancelled) as fit_cancelled:
                self.assertFalse(fit_cancelled())
                self.assertFalse(derived_cancelled())
            self.assertFalse(combined_cancelled())
        self.assertEqual(runtime.calls, ())

    def test_caught_preferred_fit_failure_stays_visible_and_next_fit_restarts(self):
        started = threading.Event()
        cancel_trigger = threading.Event()
        cancel_armed = [True]
        first_process_ids = []

        def events(event):
            if event["event"] in ("started", "progress"):
                started.set()
                first_process_ids.append(event["processId"])
                if cancel_armed[0]:
                    cancel_trigger.set()
                    cancel_armed[0] = False

        runtime = self.runtime(event_sink=events)
        with patch.object(fast, "_grid", side_effect=AssertionError("Python fallback used")):
            with self.assertRaises(InterruptedError):
                with runtime.request_scope(cancelled=cancel_trigger.is_set) as combined_cancelled:
                    try:
                        with runtime.fit_scope() as fit_cancelled:
                            self.propose(cancelled=fit_cancelled)
                    except Exception as caught:
                        # Mirrors s1_structure_selection's preferred-fit recovery.
                        self.assertIsInstance(caught, (GridFailure, InterruptedError))
                    self.assertTrue(started.wait(timeout=5))
                    cancel_trigger.clear()
                    self.assertTrue(combined_cancelled())

        self.assertTrue(runtime.request_failed)
        self.assertIsNone(runtime.process_id)
        self.assertTrue(first_process_ids)

        with runtime.fit_scope() as cancelled:
            self.assertFalse(cancelled())
            self.assertEqual(self.propose(cancelled=cancelled)[2]["reason"], "fast-ambiguous")
        self.assertFalse(runtime.request_failed)
        self.assertIsNotNone(runtime.process_id)
        self.assertNotEqual(runtime.process_id, first_process_ids[0])

    def test_waiting_request_is_cancellable_without_replacing_active_context(self):
        runtime = self.runtime()
        owner_ready = threading.Event()
        run_propose = threading.Event()
        waiter_started = threading.Event()
        waiter_cancelled = threading.Event()
        owner_results = []
        waiter_results = []

        def owner():
            try:
                with runtime.request_scope():
                    with runtime.fit_scope() as cancelled:
                        owner_ready.set()
                        self.assertTrue(run_propose.wait(timeout=5))
                        owner_results.append(self.propose(cancelled=cancelled)[2]["reason"])
            except BaseException as error:
                owner_results.append(error)

        def waiter():
            waiter_started.set()
            try:
                with runtime.fit_scope(cancelled=waiter_cancelled.is_set):
                    waiter_results.append("unexpectedly-acquired")
            except BaseException as error:
                waiter_results.append(error)

        owner_thread = threading.Thread(target=owner)
        owner_thread.start()
        self.assertTrue(owner_ready.wait(timeout=3))
        waiter_thread = threading.Thread(target=waiter)
        waiter_thread.start()
        self.assertTrue(waiter_started.wait(timeout=2))
        waiter_cancelled.set()
        waiter_thread.join(timeout=3)
        self.assertFalse(waiter_thread.is_alive())
        self.assertEqual(len(waiter_results), 1)
        self.assertIsInstance(waiter_results[0], InterruptedError)

        run_propose.set()
        owner_thread.join(timeout=10)
        self.assertFalse(owner_thread.is_alive())
        self.assertEqual(owner_results, ["fast-ambiguous"])
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0]["requestId"], 1)
        self.assertFalse(runtime.request_failed)

    def test_close_invalidates_active_fit_then_drains_and_stops_client(self):
        started = threading.Event()
        release_event_handler = threading.Event()
        fit_cancelled = []
        fit_result = []

        def events(event):
            if event["event"] == "started":
                started.set()
                self.assertTrue(release_event_handler.wait(timeout=5))

        runtime = self.runtime(event_sink=events)

        def fit():
            try:
                with runtime.fit_scope() as cancelled:
                    fit_cancelled.append(cancelled)
                    self.propose(cancelled=cancelled)
            except BaseException as error:
                fit_result.append(error)

        fit_thread = threading.Thread(target=fit)
        fit_thread.start()
        self.assertTrue(started.wait(timeout=5))
        close_thread = threading.Thread(target=runtime.close)
        close_thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not fit_cancelled[0]():
            time.sleep(.005)
        self.assertTrue(fit_cancelled[0]())
        release_event_handler.set()
        fit_thread.join(timeout=8)
        close_thread.join(timeout=8)
        self.assertFalse(fit_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertTrue(fit_result)
        self.assertIsInstance(fit_result[0], (GridFailure, InterruptedError))
        self.assertIsNone(runtime.process_id)
        with self.assertRaisesRegex(GridFailure, "grid-runtime-closed"):
            with runtime.fit_scope():
                pass


if __name__ == "__main__":
    unittest.main()
