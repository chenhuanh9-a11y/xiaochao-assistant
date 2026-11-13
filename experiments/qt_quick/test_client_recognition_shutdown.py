"""Real race/runtime/exit polling; only numerical measurements are controlled.

The inherited imported fixture keeps source preparation, competition and runtime
real. Event-blocked verification represents a kernel which cannot stop midway;
these tests establish draining and reuse, not image accuracy or performance.
"""
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QTimer

from client_live_runtime import LiveRuntime
from client_startup_cleanup import shutdown_client
from entry_evidence_work import FrameSnapshot
from recognition_race import RecognitionObservation, RecognitionRace
from test_client_live_runtime import FixtureCapture
import test_client_local_identity_commit as identity_fixture
import test_client_coordinator as coordinator_fixture


class RuntimeRecognitionShutdownTests(unittest.TestCase):
    setUpClass = identity_fixture.LocalIdentityCommitTests.__dict__['setUpClass']
    setUp = identity_fixture.LocalIdentityCommitTests.setUp
    opening = identity_fixture.LocalIdentityCommitTests.opening
    drain_race = identity_fixture.LocalIdentityCommitTests.drain_race

    def blocked_loser(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def measured_verify(lane, row):
            if lane == 'structure':
                entered.set()
                self.assertTrue(release.wait(5), 'release the blocked numerical loser')
            elif lane == 'candidate':
                self.assertTrue(entered.wait(5))
        self.before_verify = measured_verify
        with patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 8.):
            result = self.opening()
        self.assertTrue(result['success'], result)
        self.assertTrue(entered.is_set())
        self.assertFalse(release.is_set())
        self.assertTrue(self.engine.qt_recognition_race.busy)
        return result, release

    def coordinator(self):
        coordinator_fixture.CoordinatorTests.setUpClass()
        fixture = coordinator_fixture.CoordinatorTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        coordinator = fixture.coordinator
        coordinator.runtime = self.runtime
        coordinator.capture = SimpleNamespace(disable=Mock())
        zero = SimpleNamespace(activeThreadCount=lambda: 0)
        fixture.resources.pool = fixture.settings.pool = fixture.session._pool = zero
        fixture.resources.canCancel = False
        fixture.session.stop = Mock()
        return coordinator

    def test_coordinator_waits_for_real_loser_then_same_engine_serves_new_runtime(self):
        result, release = self.blocked_loser()
        race = self.engine.qt_recognition_race
        coordinator = self.coordinator()
        beats = []
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: beats.append(True))
        heartbeat.start(0)
        try:
            with patch.object(self.runtime, '_commit_identity_rows', wraps=self.runtime._commit_identity_rows) as commit:
                coordinator.requestClose()
                coordinator._finish_close()
                self.assertFalse(coordinator.closeReady, 'exit must not mistake an active numerical loser for drained')
                self.assertTrue(self.runtime.private_evidence_busy)
                self.assertFalse(release.is_set(), 'stop must not join the kernel on the UI caller')
                for _ in range(3):
                    QCoreApplication.processEvents()
                self.assertTrue(beats, 'the existing Qt event loop remains responsive')
                self.assertFalse(coordinator.closeReady)
                before = {key: self.runtime.snapshot()[key] for key in ('mapId', 'identityLocked', 'displayAuthorized')}
                release.set()
                self.drain_race()
                self.assertFalse(self.runtime.private_evidence_busy)
                coordinator._finish_close()
                self.assertTrue(coordinator.closeReady)
                self.assertEqual(commit.call_count, 0, 'a late loser cannot reenter identity submission')
                self.assertEqual({key: self.runtime.snapshot()[key] for key in before}, before)
                self.assertIsNone(self.runtime.render_payload(result['renderToken']))
        finally:
            heartbeat.stop()
            release.set()
            self.drain_race()

        replacement = LiveRuntime(engine_factories=self.factories,
            capture_provider=FixtureCapture(), evidence_kind='synthetic')
        self.addCleanup(replacement.shutdown)
        self.assertTrue(replacement.dispatch('set_context', dict(season='s0', mode='difficult'))['success'])
        self.assertTrue(replacement.dispatch('start_round')['success'])
        repeated_stop = threading.Event()
        def verify_reuse(lane, row):
            if lane == 'candidate':
                # Coordinator and final cleanup can both stop an old runtime.
                # That repeat must not cancel a replacement runtime's new run.
                self.runtime.stop_private_evidence()
                repeated_stop.set()
            elif lane == 'structure':
                self.assertTrue(repeated_stop.wait(5))
        self.before_verify = verify_reuse
        with patch.object(replacement, '_observe', return_value=(
                dict(detected=True, mapOpen=True), '1', dict(selectedFloor=1, mapOpen=True))), \
             patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 8.):
            reopened = replacement.dispatch('map', dict(openIntent=True, inputSequence=1))
        self.assertTrue(reopened['success'], reopened)
        self.assertTrue(repeated_stop.is_set())
        self.assertIs(replacement._engine.qt_recognition_race, race)
        self.assertTrue(replacement.snapshot()['identityLocked'])
        self.assertEqual(replacement.snapshot()['mapId'], self.target)

    def test_stopped_runtime_does_not_admit_another_observation(self):
        race = self.engine.qt_recognition_race
        self.runtime.stop_private_evidence()
        with patch.object(race, 'run', wraps=race.run) as run:
            result = self.opening()
        self.assertFalse(result['success'], result)
        run.assert_not_called()
        self.assertFalse(self.runtime.private_evidence_busy)

    def test_startup_cleanup_waits_for_real_loser_while_processing_events(self):
        _, release = self.blocked_loser()
        coordinator_fixture.CoordinatorTests.setUpClass()
        client = SimpleNamespace(runtime=self.runtime)
        beats = []
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: beats.append(True))
        heartbeat.start(0)
        QTimer.singleShot(30, release.set)
        try:
            shutdown_client(client)
            self.assertTrue(release.is_set(), 'cleanup must not complete before the kernel was released')
            self.assertTrue(beats)
            self.assertTrue(client._shutdown_complete)
            self.assertFalse(self.runtime.private_evidence_busy)
        finally:
            heartbeat.stop()
            release.set()
            self.drain_race()

    def test_original_private_queue_is_still_included_in_busy(self):
        entered, release = threading.Event(), threading.Event()
        def task():
            entered.set()
            release.wait(5)
        self.runtime._s1_private_jobs.submit(task)
        self.assertTrue(entered.wait(2))
        try:
            self.runtime.stop_private_evidence()
            self.assertTrue(self.runtime.private_evidence_busy)
        finally:
            release.set()
            limit = time.monotonic() + 2
            while self.runtime.private_evidence_busy and time.monotonic() < limit:
                time.sleep(.001)
        self.assertFalse(self.runtime.private_evidence_busy)


class RecognitionCancellationTests(unittest.TestCase):
    def setUp(self):
        self.race = RecognitionRace()
        self.addCleanup(self.race.close)
        self.observation = RecognitionObservation(FrameSnapshot(np.zeros((8, 12, 3), np.uint8)), '1', 'base')

    def drain(self):
        limit = time.monotonic() + 2
        while self.race.busy and time.monotonic() < limit:
            time.sleep(.001)
        self.assertFalse(self.race.busy)

    def overlapping_preflights(self, newer_first):
        preflight = {name: threading.Event() for name in ('older', 'newer')}
        admit = {name: threading.Event() for name in preflight}
        running = {name: threading.Event() for name in preflight}
        release = {name: threading.Event() for name in preflight}
        results, started = {}, []
        lock = threading.Lock()
        def owner(name):
            checked = False
            def before_submission():
                nonlocal checked
                if not checked:
                    checked = True
                    preflight[name].set()
                    self.assertTrue(admit[name].wait(5))
                return False
            def kernel(lane):
                def run(observation, cancelled, progress, entry):
                    with lock:
                        started.append((name, lane))
                        if sum(which == name for which, _ in started) == 2:
                            running[name].set()
                    while not cancelled() and not release[name].wait(.001):
                        pass
                    return name + '-' + lane
                return run
            results[name] = self.race.run(self.observation, kernel('candidate'), kernel('structure'),
                                          identity_committed=before_submission)
        threads = {name: threading.Thread(target=owner, args=(name,)) for name in preflight}
        threads['older'].start()
        try:
            self.assertTrue(preflight['older'].wait(2))
            threads['newer'].start()
            self.assertTrue(preflight['newer'].wait(2))
            first = 'newer' if newer_first else 'older'
            admit[first].set()
            self.assertTrue(running[first].wait(2))
            admit['older' if newer_first else 'newer'].set()
            if newer_first:
                threads['older'].join(2)
                self.assertFalse(threads['older'].is_alive())
                self.assertCountEqual(started, [('newer', 'candidate'), ('newer', 'structure')])
            else:
                self.assertTrue(running['newer'].wait(2), 'normal submit must not revoke a later preflight')
                threads['older'].join(2)
                self.assertFalse(threads['older'].is_alive())
            self.assertEqual(results['older']['status'], 'superseded')
            self.assertTrue(threads['newer'].is_alive(), 'an old preflight must not retire the newer active run')
            self.assertFalse(self.race._active.retired.is_set())
            release['newer'].set()
            threads['newer'].join(2)
            self.assertFalse(threads['newer'].is_alive())
            self.assertEqual(results['newer']['status'], 'unresolved')
            self.assertEqual({lane for name, lane in started if name == 'newer'}, {'candidate', 'structure'})
        finally:
            for event in (*admit.values(), *release.values()):
                event.set()
            self.race.cancel()
            for thread in threads.values():
                if thread.ident is not None:
                    thread.join(2)
            self.drain()

    def test_newer_preflight_submits_after_older_started_without_explicit_cancel(self):
        self.overlapping_preflights(newer_first=False)

    def test_older_preflight_cannot_retire_newer_already_submitted_observation(self):
        self.overlapping_preflights(newer_first=True)

    def test_cancel_during_admission_prevents_late_submit_and_allows_a_new_run(self):
        entered, release = threading.Event(), threading.Event()
        results, started = [], []
        def before_submission():
            entered.set()
            self.assertTrue(release.wait(2))
            return False
        owner = threading.Thread(target=lambda: results.append(self.race.run(self.observation,
            lambda *args: started.append('candidate'), lambda *args: started.append('structure'),
            identity_committed=before_submission)))
        owner.start()
        try:
            self.assertTrue(entered.wait(2))
            self.race.cancel()
        finally:
            release.set()
            owner.join(2)
        self.assertFalse(owner.is_alive())
        self.assertEqual(results[0]['status'], 'cancelled')
        self.assertEqual(started, [])
        result = self.race.run(self.observation, lambda *args: 'new-candidate',
                              lambda *args: 'new-structure', identity_committed=lambda: False)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(result['lanes']['candidate']['result'], 'new-candidate')
        self.drain()

    def test_cancel_discards_pending_run_but_busy_waits_for_running_kernels(self):
        entered, release = threading.Barrier(3), threading.Event()
        results, pending_started = [], []
        def old_kernel(*args):
            entered.wait(timeout=2)
            release.wait(5)
        first = threading.Thread(target=lambda: results.append(self.race.run(
            self.observation, old_kernel, old_kernel, identity_committed=lambda: False)))
        second = threading.Thread(target=lambda: results.append(self.race.run(self.observation,
            lambda *args: pending_started.append('candidate'), lambda *args: pending_started.append('structure'),
            identity_committed=lambda: False)))
        first.start()
        try:
            entered.wait(timeout=2)
            second.start()
            limit = time.monotonic() + 2
            while not all(queue._pending is not None for queue in self.race._queues.values()) and time.monotonic() < limit:
                time.sleep(.001)
            self.assertTrue(all(queue._pending is not None for queue in self.race._queues.values()))
            self.race.cancel()
            second.join(2)
            self.assertFalse(second.is_alive(), 'cancel must not wait for native work')
            self.assertTrue(self.race.busy)
        finally:
            release.set()
            first.join(2)
            if second.ident is not None:
                second.join(2)
            self.drain()
        self.assertEqual(pending_started, [])
        self.assertEqual({result['status'] for result in results}, {'superseded', 'cancelled'})


if __name__ == '__main__':
    unittest.main()
