"""Real threads and owner callbacks; no image algorithm or GUI acceptance."""
from dataclasses import replace
import threading
import time
import unittest

import numpy as np

from entry_evidence_work import FrameSnapshot
from recognition_race import RecognitionEvidence, RecognitionObservation, RecognitionRace


class RecognitionRaceTests(unittest.TestCase):
    def setUp(self):
        self.race = RecognitionRace()
        self.addCleanup(self.race.close)
        self.observation = RecognitionObservation(FrameSnapshot(np.zeros((8, 12, 3), np.uint8)), '1', 'base-v1')
        self.committed = False
        self.owner = threading.get_ident()

    def evidence(self, mid='map-a', **changes):
        value = RecognitionEvidence(mid, '1', self.observation.snapshot.frame_key, 'base-v1', dict(value=1))
        return replace(value, **changes)

    def run_race(self, candidate, structure, **callbacks):
        callbacks.setdefault('identity_committed', lambda: self.committed)
        callbacks.setdefault('deadline', time.monotonic()+2)
        return self.race.run(self.observation, candidate, structure, **callbacks)

    def drain(self):
        limit = time.monotonic()+2
        while self.race.busy and time.monotonic() < limit:
            time.sleep(.001)
        self.assertFalse(self.race.busy, 'cooperative test workers did not drain')

    def test_either_identity_wins_without_waiting_for_other_blocked_worker(self):
        for winning in ('candidate', 'structure'):
            with self.subTest(winning=winning):
                self.committed = False
                barrier, release = threading.Barrier(2), threading.Event()
                loser_finished, seen, stops = threading.Event(), [], {}
                def strategy(name):
                    def run(observation, cancelled, progress, entry):
                        stops[name] = cancelled
                        self.assertIs(observation.snapshot, self.observation.snapshot)
                        barrier.wait(timeout=2)
                        if name == winning:
                            self.assertEqual(entry(self.evidence()), 'scanner-reply')
                        else:
                            release.wait(2)
                            try:
                                entry(self.evidence('late-map'))
                            finally:
                                loser_finished.set()
                    return run
                def consume(name, value):
                    self.assertEqual(threading.get_ident(), self.owner)
                    self.assertTrue(self.observation.matches(value))
                    seen.append((name, value.map_id))
                    self.committed = True
                    return 'scanner-reply'
                try:
                    result = self.run_race(strategy('candidate'), strategy('structure'), on_entry=consume)
                    self.assertEqual((result['status'], result['winner']), ('identity-committed', winning))
                    self.assertEqual(seen, [(winning, 'map-a')])
                    self.assertFalse(loser_finished.is_set(), 'winner joined its blocked peer')
                    self.assertFalse(stops[winning]())
                    self.assertTrue(stops['structure' if winning == 'candidate' else 'candidate']())
                finally:
                    release.set()
                    self.assertTrue(loser_finished.wait(2))
                    self.drain()

    def test_callback_true_only_preserved_group_does_not_cancel_direct(self):
        shown, group_seen, replies, seen = threading.Event(), threading.Event(), [], []
        def candidate(observation, cancelled, progress, entry):
            replies.append(progress(self.evidence('preview-map'), transient=False))
            replies.append(entry(self.evidence('group-preserved')))
            return dict(reason='candidate-budget-unresolved')
        def structure(observation, cancelled, progress, entry):
            self.assertTrue(group_seen.wait(2))
            self.assertFalse(cancelled())
            return [self.evidence('direct-map')]
        def progress(name, value, **kwargs):
            self.assertEqual(threading.get_ident(), self.owner)
            seen.append(('preview', value.map_id))
            shown.set()
            return kwargs
        def entry(name, value):
            self.assertEqual(threading.get_ident(), self.owner)
            seen.append(('entry', value.map_id))
            group_seen.set()
            return True  # Scanner may stop; this was not a qualified identity.
        def terminal(name, value):
            self.assertEqual(threading.get_ident(), self.owner)
            if name == 'structure':
                self.assertTrue(self.observation.matches(value[0]))
                self.committed = True
        result = self.run_race(candidate, structure, on_progress=progress, on_entry=entry, on_terminal=terminal)
        self.assertEqual((result['status'], result['winner']), ('identity-committed', 'structure'))
        # The direct lane can complete before the candidate's next message; no
        # late entry callback is required once identity has actually committed.
        self.assertEqual(seen[0], ('preview', 'preview-map'))
        self.assertTrue(replies and replies[0] == dict(transient=False))
        self.drain()

    def test_candidate_terminal_unresolved_keeps_waiting_for_direct_completion(self):
        terminal_seen = threading.Event()
        candidate_result, direct_result = dict(unresolved=True), [self.evidence()]
        def candidate(*args):
            return candidate_result
        def structure(*args):
            self.assertTrue(terminal_seen.wait(2))
            return direct_result
        def terminal(name, value):
            if name == 'candidate':
                self.assertIs(value, candidate_result)
                terminal_seen.set()
                return True
            self.assertIs(value, direct_result)
            self.committed = True
        result = self.run_race(candidate, structure, on_terminal=terminal)
        self.assertEqual(result['winner'], 'structure')
        self.assertEqual(result['lanes']['candidate']['state'], 'completed')
        self.assertIs(result['lanes']['candidate']['result'], candidate_result)

    def test_progress_and_entry_synchronously_return_exact_owner_values(self):
        token, received, threads = object(), [], []
        values = [self.evidence(), np.arange(12)]
        def candidate(observation, cancelled, progress, entry):
            threads.append(threading.get_ident())
            received.append(progress(values, phase='first'))
            received.append(entry(values))
        def consume(name, payload, **kwargs):
            self.assertEqual(threading.get_ident(), self.owner)
            self.assertIs(payload, values, 'large evidence was copied')
            return token
        result = self.run_race(candidate, lambda *args: None, on_progress=consume, on_entry=consume)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(received, [token, token])
        self.assertNotEqual(threads, [self.owner])
        self.assertFalse(self.committed)

    def test_owner_scope_validation_rejects_foreign_frame_floor_resource_and_empty_map(self):
        rejected, committed, sent = [], [], threading.Event()
        def candidate(observation, cancelled, progress, entry):
            for changes in (dict(frame_key='old-frame'), dict(floor='2'),
                            dict(resource_key='old-base'), dict(map_id='')):
                self.assertFalse(entry(self.evidence(**changes)))
            sent.set()
        def structure(*args):
            self.assertTrue(sent.wait(2))
            return self.evidence()
        def consume(name, value):
            if not self.observation.matches(value):
                rejected.append(value)
                return False
            # These tests deliberately substitute qualification only; the real
            # runtime must additionally run its original complete verifier.
            committed.append(value.map_id)
            self.committed = True
        def terminal(name, value):
            if name == 'structure':
                consume(name, value)
        result = self.run_race(candidate, structure, on_entry=consume, on_terminal=terminal)
        self.assertEqual(len(rejected), 4)
        self.assertEqual(committed, ['map-a'])
        self.assertEqual(result['status'], 'identity-committed')
        self.assertEqual(result['winner'], 'structure')

    def test_cancel_after_preview_keeps_owner_material_and_drops_late_callbacks(self):
        stop, release, finished = threading.Event(), threading.Event(), threading.Event()
        shown, late = [], []
        def candidate(observation, cancelled, progress, entry):
            progress(self.evidence('preview'))
        def structure(observation, cancelled, progress, entry):
            release.wait(2)
            try:
                entry(self.evidence('late'))
            finally:
                finished.set()
        def show(name, value):
            shown.append(value)
            stop.set()
        try:
            result = self.run_race(candidate, structure, cancelled=stop.is_set,
                on_progress=show, on_entry=lambda *args: late.append(args))
            self.assertEqual(result['status'], 'cancelled')
            self.assertEqual([value.map_id for value in shown], ['preview'])
            self.assertEqual(late, [])
        finally:
            release.set()
            self.drain()

    def test_completed_identity_is_not_undone_by_request_cancel_after_commit(self):
        stop = threading.Event()
        def candidate(observation, cancelled, progress, entry):
            entry(self.evidence())
        def commit(*args):
            self.committed = True
            stop.set()
            return False  # Only the explicit predicate, not this value, wins.
        result = self.run_race(candidate, lambda *args: None, on_entry=commit, cancelled=stop.is_set)
        self.assertEqual(result['status'], 'identity-committed')
        self.assertEqual(result['winner'], 'candidate')

    def test_worker_error_is_consumed_on_owner_and_does_not_hide_valid_other_lane(self):
        consumed, errors = threading.Event(), []
        error = ValueError('candidate-unavailable')
        def candidate(*args):
            raise error
        def structure(*args):
            self.assertTrue(consumed.wait(2))
            return self.evidence()
        def failed(name, value):
            self.assertEqual(threading.get_ident(), self.owner)
            self.assertIs(value, error)
            errors.append(name)
            consumed.set()
        def terminal(name, value):
            if name == 'structure':
                self.committed = True
        result = self.run_race(candidate, structure, on_error=failed, on_terminal=terminal)
        self.assertEqual(errors, ['candidate'])
        self.assertIs(result['lanes']['candidate']['error'], error)
        self.assertEqual(result['winner'], 'structure')

    def test_owner_callback_exception_releases_waiting_worker_without_withdrawing_material(self):
        finished, shown, failures = threading.Event(), [], []
        def candidate(observation, cancelled, progress, entry):
            try:
                progress(self.evidence('preview'))
                entry(self.evidence())
            finally:
                finished.set()
        def show(name, value):
            shown.append(value.map_id)
        def fail(*args):
            failures.append(args[0])
            raise RuntimeError('owner-commit-failed')
        with self.assertRaisesRegex(RuntimeError, 'owner-commit-failed'):
            self.run_race(candidate, lambda *args: None, on_progress=show, on_entry=fail)
        self.assertTrue(finished.wait(2))
        self.assertEqual(shown, ['preview'])
        self.assertEqual(failures, ['candidate'], 'owner callback must not be retried')
        self.drain()

    def test_owner_terminal_and_error_callback_exceptions_do_not_strand_queues(self):
        for kind in ('terminal', 'error'):
            with self.subTest(kind=kind):
                def lane(*args):
                    if kind == 'error':
                        raise ValueError('lane-error')
                    return object()
                def fail(*args):
                    raise RuntimeError('owner-'+kind)
                with self.assertRaisesRegex(RuntimeError, 'owner-'+kind):
                    self.run_race(lane, lambda *args: None, **{'on_'+kind: fail})
                self.drain()

    def test_fatal_worker_error_reaches_owner_and_releases_other_waiter(self):
        def candidate(*args):
            raise MemoryError('lane-memory')
        with self.assertRaisesRegex(MemoryError, 'lane-memory'):
            self.run_race(candidate, lambda *args: None)
        self.drain()

    def test_both_unresolved_return_both_real_results(self):
        left, right = dict(reason='budget'), dict(reason='ambiguous')
        shown = []
        def candidate(observation, cancelled, progress, entry):
            progress(self.evidence('preview'))
            return left
        result = self.run_race(candidate, lambda *args: right,
            on_progress=lambda name, value: shown.append(value.map_id))
        self.assertEqual(result['status'], 'unresolved')
        self.assertIs(result['lanes']['candidate']['result'], left)
        self.assertIs(result['lanes']['structure']['result'], right)
        self.assertEqual({row['state'] for row in result['lanes'].values()}, {'completed'})
        self.assertEqual(shown, ['preview'])

    def test_deadline_does_not_join_uncooperative_workers_or_withdraw_preview(self):
        barrier, release, shown = threading.Barrier(2), threading.Event(), []
        def candidate(observation, cancelled, progress, entry):
            barrier.wait(timeout=2)
            progress(self.evidence('preview'))
            release.wait(2)
        def structure(*args):
            barrier.wait(timeout=2)
            release.wait(2)
        try:
            result = self.run_race(candidate, structure, deadline=time.monotonic()+.1,
                on_progress=lambda name, value: shown.append(value.map_id))
            self.assertEqual(result['status'], 'unresolved')
            self.assertEqual(shown, ['preview'])
            self.assertTrue(all(row['state'] == 'running' for row in result['lanes'].values()))
            self.assertTrue(all(row['cancelRequested'] for row in result['lanes'].values()))
        finally:
            release.set()
            self.drain()

    def test_cancel_one_lane_keeps_the_other_running(self):
        cancelled = threading.Event()
        def candidate(observation, stopped, progress, entry):
            try:
                progress('cancel-me')
                while not stopped():
                    time.sleep(.001)
            finally:
                cancelled.set()
        def structure(*args):
            self.assertTrue(cancelled.wait(2))
            return self.evidence()
        def progress(name, value):
            self.race.cancel_lane(name)
        def terminal(name, value):
            if name == 'structure':
                self.committed = True
        result = self.run_race(candidate, structure, on_progress=progress, on_terminal=terminal)
        self.assertEqual(result['winner'], 'structure')
        self.assertTrue(result['lanes']['candidate']['cancelRequested'])

    def test_close_releases_owner_and_prevents_late_terminal_or_entry_callbacks(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        outputs, calls = [], []
        def blocked(observation, cancelled, progress, entry):
            entered.set()
            release.wait(2)
            entry(self.evidence())
        def owner():
            outputs.append(self.run_race(blocked, blocked,
                on_entry=lambda *args: calls.append(args), on_terminal=lambda *args: calls.append(args)))
            done.set()
        thread = threading.Thread(target=owner)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.race.close()
            self.assertTrue(done.wait(1), 'close waited for blocked workers')
            self.assertEqual(outputs[0]['status'], 'closed')
            self.assertEqual(calls, [])
        finally:
            release.set()
            thread.join(2)
            self.drain()
        self.assertEqual(calls, [])

    def test_close_during_owner_callback_unblocks_worker_before_callback_returns(self):
        entered, release, worker_finished = threading.Event(), threading.Event(), threading.Event()
        result, late = [], []
        def candidate(observation, cancelled, progress, entry):
            try:
                entry(self.evidence())
            finally:
                worker_finished.set()
        def consume(*args):
            entered.set()
            self.assertTrue(release.wait(2))
            return True
        owner = threading.Thread(target=lambda: result.append(self.run_race(candidate, lambda *args: None,
            on_entry=consume, on_terminal=lambda *args: late.append(args))))
        owner.start()
        try:
            self.assertTrue(entered.wait(2))
            self.race.close()
            self.assertTrue(worker_finished.wait(1), 'close left worker waiting on owner callback')
            self.assertTrue(owner.is_alive(), 'fixture callback was not still blocked')
        finally:
            release.set()
            owner.join(2)
            self.drain()
        self.assertEqual(result[0]['status'], 'closed')
        self.assertFalse(any(name == 'candidate' for name, *_ in late))

    def test_owner_does_not_treat_structure_progress_as_candidate_prealignment(self):
        shown, observed = [], []
        def structure(observation, cancelled, progress, entry):
            progress(self.evidence('structure-progress'))
        def progress(name, value):
            observed.append(name)
            if name == 'candidate':
                shown.append(value)
        result = self.run_race(lambda *args: None, structure, on_progress=progress)
        self.assertEqual(observed, ['structure'])
        self.assertEqual(shown, [])
        self.assertEqual(result['status'], 'unresolved')

    def test_new_opening_supersedes_old_and_replaces_only_latest_pending_work(self):
        entered, release = threading.Barrier(3), threading.Event()
        started, identities, results = [], [], []
        def old(name):
            def run(observation, stopped, progress, entry):
                started.append((name, 'old', threading.get_ident()))
                entered.wait(timeout=2)
                release.wait(2)
                entry('late-old')
            return run
        first = threading.Thread(target=lambda: results.append(self.run_race(old('candidate'), old('structure'),
            on_entry=lambda *args: identities.append(args))))
        first.start()
        entered.wait(timeout=2)
        try:
            for number in range(10):
                def latest(*args, number=number):
                    started.append(('latest', number, threading.get_ident()))
                result = self.run_race(latest, latest, deadline=time.monotonic()+.005)
                self.assertEqual(result['status'], 'unresolved')
            self.assertTrue(all(item[1] == 'old' for item in started))
            self.assertEqual(len({item[2] for item in started}), 2)
            self.assertEqual(len(self.race._sessions), 2, 'old running pair plus latest pending pair only')
            for queue in self.race._queues.values():
                self.assertIsNotNone(queue._pending)
        finally:
            release.set()
            first.join(2)
            self.drain()
        self.assertEqual(results[0]['status'], 'superseded')
        self.assertEqual(identities, [])
        self.assertTrue(all(item[1] == 'old' for item in started), 'cancelled pending jobs executed algorithms')

    def test_closed_or_cancelled_admission_does_not_start_algorithms(self):
        calls = []
        lane = lambda *args: calls.append(args)
        result = self.run_race(lane, lane, cancelled=lambda: True)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual({row['state'] for row in result['lanes'].values()}, {'not-started'})
        self.race.close()
        self.assertEqual(self.run_race(lane, lane)['status'], 'closed')
        self.assertEqual(calls, [])

    def test_current_new_opening_starts_after_old_workers_drain_without_new_threads(self):
        entered, release = threading.Barrier(3), threading.Event()
        old_results, new_results, started, old_callbacks = [], [], [], []
        def old(observation, stopped, progress, entry):
            started.append(('old', threading.get_ident()))
            entered.wait(timeout=2)
            release.wait(2)
            entry('late-old')
        old_owner = threading.Thread(target=lambda: old_results.append(self.run_race(old, old,
            on_entry=lambda *args: old_callbacks.append(args))))
        old_owner.start()
        entered.wait(timeout=2)
        def current(*args):
            started.append(('current', threading.get_ident()))
            return self.evidence()
        def terminal(name, value):
            self.assertTrue(self.observation.matches(value))
            self.committed = True
        new_owner = threading.Thread(target=lambda: new_results.append(self.run_race(current, current,
            on_terminal=terminal)))
        new_owner.start()
        try:
            limit = time.monotonic()+2
            while time.monotonic() < limit and not all(queue._pending is not None
                    for queue in self.race._queues.values()):
                time.sleep(.001)
            self.assertTrue(all(queue._pending is not None for queue in self.race._queues.values()))
            self.assertTrue(new_owner.is_alive())
            release.set()
            new_owner.join(2)
            old_owner.join(2)
            self.assertFalse(new_owner.is_alive())
            self.assertEqual(new_results[0]['status'], 'identity-committed')
            self.assertEqual(old_results[0]['status'], 'superseded')
            self.assertEqual(old_callbacks, [])
            self.assertTrue(any(kind == 'current' for kind, _ in started))
            self.assertEqual(len({thread for _, thread in started}), 2)
        finally:
            release.set()
            old_owner.join(2)
            new_owner.join(2)
            self.drain()

    def test_owner_identity_predicate_can_finish_without_a_new_lane_message(self):
        entered, release, finished = threading.Barrier(3), threading.Event(), []
        def blocked(*args):
            entered.wait(timeout=2)
            release.wait(2)
            finished.append(True)
        def acknowledge():
            entered.wait(timeout=2)
            self.committed = True
        def predicate():
            self.assertEqual(threading.get_ident(), self.owner)
            return self.committed
        ack = threading.Thread(target=acknowledge)
        ack.start()
        try:
            result = self.run_race(blocked, blocked, identity_committed=predicate,
                deadline=time.monotonic()+.1)
            self.assertEqual(result['status'], 'identity-committed')
            self.assertIsNone(result['winner'], 'an external owner commit must not invent a winning lane')
            self.assertEqual(finished, [], 'predicate completion joined blocked workers')
        finally:
            release.set()
            ack.join(2)
            self.drain()

    def test_non_boolean_identity_predicate_never_authorizes_admission_callback_or_idle_win(self):
        for phase in ('admission', 'callback', 'idle'):
            with self.subTest(phase=phase):
                changed, release = threading.Event(), threading.Event()
                def predicate():
                    return {'locked': False} if phase == 'admission' or changed.is_set() else False
                def candidate(observation, stopped, progress, entry):
                    if phase == 'callback':
                        entry('group-preserved')
                    elif phase == 'idle':
                        changed.set()
                        release.wait(2)
                def consume(*args):
                    changed.set()
                    return True
                try:
                    result = self.run_race(candidate, lambda *args: None,
                        identity_committed=predicate, on_entry=consume, deadline=time.monotonic()+.1)
                    self.assertEqual(result['status'], 'unresolved')
                    self.assertIsNone(result['winner'])
                finally:
                    release.set()
                    self.drain()

    def test_retirement_during_cancel_or_clock_hook_prevents_unclaimed_terminal_dispatch(self):
        for hook in ('cancelled', 'clock'):
            with self.subTest(hook=hook):
                paused, resume, release_worker = threading.Event(), threading.Event(), threading.Event()
                calls, output, cancellation_calls, armed = [], [], [0], [False]
                def pause():
                    paused.set()
                    self.assertTrue(resume.wait(2))
                def clock():
                    if armed[0]:
                        armed[0] = False
                        pause()
                    return time.monotonic()
                race = RecognitionRace(clock=clock)
                def cancelled():
                    cancellation_calls[0] += 1
                    if cancellation_calls[0] == 2:
                        # Keep the real queue populated before owner get(), so
                        # the next hook is precisely after dequeue and before
                        # the dispatch claim. No receipt or queue is replaced.
                        limit = time.monotonic()+2
                        while race._active.inbox.empty() and time.monotonic() < limit:
                            time.sleep(.001)
                        self.assertFalse(race._active.inbox.empty())
                    elif cancellation_calls[0] == 3:
                        if hook == 'cancelled':
                            pause()
                        else:
                            armed[0] = True
                    return False
                def blocked(*args):
                    release_worker.wait(2)
                def owner():
                    output.append(race.run(self.observation, lambda *args: object(), blocked,
                        identity_committed=lambda: False, cancelled=cancelled,
                        deadline=time.monotonic()+2,
                        on_terminal=lambda *args: calls.append(race._closed)))
                thread = threading.Thread(target=owner)
                thread.start()
                try:
                    self.assertTrue(paused.wait(2))
                    race.close()
                    self.assertTrue(thread.is_alive(), 'close waited for the external hook')
                    resume.set()
                    thread.join(2)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(output[0]['status'], 'closed')
                    self.assertEqual(calls, [], 'retired, unclaimed terminal reached owner')
                finally:
                    resume.set()
                    release_worker.set()
                    race.close()
                    thread.join(2)
                    limit = time.monotonic()+2
                    while race.busy and time.monotonic() < limit:
                        time.sleep(.001)
                    self.assertFalse(race.busy)


if __name__ == '__main__':
    unittest.main()
