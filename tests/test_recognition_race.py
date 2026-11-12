"""Real worker scheduling and single-consumer callbacks, not image accuracy."""
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

    def evidence(self, mid='map-a', **changes):
        fields = dict(map_id=mid, floor='1', resource_key='base-v1',
                      frame_key=self.observation.snapshot.frame_key, payload=dict(value=1))
        fields.update(changes)
        return RecognitionEvidence(**fields)

    def run_race(self, candidate, structure, **callbacks):
        return self.race.run(self.observation, candidate, structure,
                             deadline=time.monotonic()+2, **callbacks)

    def test_either_lock_wins_without_waiting_for_the_other_worker(self):
        for winning in ('candidate', 'structure'):
            with self.subTest(winning=winning):
                barrier, release, loser_finished = threading.Barrier(2), threading.Event(), threading.Event()
                seen = []
                def strategy(name):
                    def run(observation, cancelled, offer_lock, offer_preview):
                        self.assertIs(observation.snapshot, self.observation.snapshot)
                        self.assertFalse(observation.snapshot.pixels.flags.writeable)
                        barrier.wait(timeout=2)
                        if name == winning:
                            offer_lock(self.evidence())
                        else:
                            release.wait(timeout=2)
                            offer_lock(self.evidence('late-map'))
                            loser_finished.set()
                    return run
                try:
                    result = self.run_race(strategy('candidate'), strategy('structure'),
                        on_lock=lambda evidence, branch, preview: seen.append((evidence.map_id, branch)) or True)
                    self.assertEqual(result['status'], 'locked')
                    self.assertEqual(result['winner'], winning)
                    self.assertEqual(seen, [('map-a', winning)])
                    self.assertFalse(loser_finished.is_set())
                finally:
                    release.set()
                    self.assertTrue(loser_finished.wait(timeout=2))

    def test_candidate_preview_then_different_structure_lock_is_atomic_in_the_committer(self):
        published, release = threading.Event(), threading.Event()
        events, threads = [], []
        owner = threading.get_ident()
        def candidate(observation, cancelled, lock, preview):
            preview(self.evidence('preview-map'))
            release.wait(timeout=2)
            preview(self.evidence('late-map'))
        def structure(observation, cancelled, lock, preview):
            self.assertTrue(published.wait(timeout=2))
            lock(self.evidence('winner-map'))
        def show(evidence):
            threads.append(threading.get_ident())
            events.append(('preview', evidence.map_id))
            published.set()
            return True
        def commit(evidence, branch, preview):
            threads.append(threading.get_ident())
            self.assertEqual(preview.map_id, 'preview-map')
            events.append(('withdraw', preview.map_id))
            events.append(('lock', evidence.map_id))
            return True
        try:
            result = self.run_race(candidate, structure, on_lock=commit, on_preview=show)
            self.assertEqual(events, [('preview', 'preview-map'), ('withdraw', 'preview-map'), ('lock', 'winner-map')])
            self.assertEqual(threads, [owner, owner])
            self.assertIsNone(result['preview'])
        finally:
            release.set()

    def test_rejected_lock_keeps_preview_and_does_not_block_other_branch(self):
        rejected = threading.Event()
        withdrawals = []
        def candidate(observation, cancelled, lock, preview):
            preview(self.evidence())
            lock(self.evidence('rejected-map'))
        def structure(observation, cancelled, lock, preview):
            self.assertTrue(rejected.wait(timeout=2))
            lock(self.evidence())
        def commit(evidence, branch, preview):
            self.assertEqual(preview.map_id, 'map-a')
            if evidence.map_id == 'rejected-map':
                rejected.set()
                return False
            return True
        result = self.run_race(candidate, structure, on_lock=commit,
            on_preview=lambda value: True, withdraw_preview=withdrawals.append)
        self.assertEqual(result['winner'], 'structure')
        self.assertEqual(result['preview'].map_id, 'map-a')
        self.assertEqual(withdrawals, [])

    def test_foreign_frame_floor_and_resource_cannot_reach_committer(self):
        sent, committed = threading.Event(), []
        def candidate(observation, cancelled, lock, preview):
            for change in (dict(frame_key='old-frame'), dict(floor='2'), dict(resource_key='old-base')):
                lock(self.evidence('foreign', **change))
            sent.set()
        def structure(observation, cancelled, lock, preview):
            self.assertTrue(sent.wait(timeout=2))
            lock(self.evidence())
        result = self.run_race(candidate, structure,
            on_lock=lambda evidence, branch, preview: committed.append(evidence.map_id) or True)
        self.assertEqual(committed, ['map-a'])
        self.assertEqual(result['winner'], 'structure')

    def test_cancel_after_preview_withdraws_without_locking(self):
        stopped, withdrawals, commits = threading.Event(), [], []
        def candidate(observation, cancelled, lock, preview):
            preview(self.evidence())
        def structure(observation, cancelled, lock, preview):
            stopped.wait(timeout=2)
            lock(self.evidence())
        def show(evidence):
            stopped.set()
            return True
        result = self.run_race(candidate, structure, cancelled=stopped.is_set,
            on_preview=show, withdraw_preview=withdrawals.append,
            on_lock=lambda *args: commits.append(args) or True)
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['preview'])
        self.assertEqual(commits, [])
        self.assertEqual(withdrawals, ['recognition-cancelled'])

    def test_callback_error_cancels_and_withdraws_only_owned_preview(self):
        withdrawals = []
        def candidate(observation, cancelled, lock, preview):
            preview(self.evidence())
            lock(self.evidence())
        def structure(*args):
            return None
        def failed(*args):
            raise RuntimeError('commit failed')
        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
            self.run_race(candidate, structure, on_preview=lambda value: True,
                          on_lock=failed, withdraw_preview=withdrawals.append)
        self.assertEqual(withdrawals, ['recognition-error'])

    def test_worker_error_does_not_hide_a_valid_other_result(self):
        def candidate(*args):
            raise ValueError('candidate unavailable')
        def structure(observation, cancelled, lock, preview):
            lock(self.evidence())
        result = self.run_race(candidate, structure, on_lock=lambda *args: True)
        self.assertEqual(result['status'], 'locked')
        self.assertEqual(result['winner'], 'structure')

    def test_structure_preview_is_not_candidate_prealignment(self):
        seen = []
        def candidate(*args):
            return None
        def structure(observation, cancelled, lock, preview):
            preview(self.evidence())
        result = self.run_race(candidate, structure, on_lock=lambda *args: True,
                               on_preview=lambda value: seen.append(value) or True)
        self.assertEqual(result['status'], 'unresolved')
        self.assertEqual(seen, [])

    def test_accepted_lock_is_not_undone_by_cancellation_after_commit(self):
        stopped, withdrawals = threading.Event(), []
        def candidate(observation, cancelled, lock, preview):
            preview(self.evidence())
            lock(self.evidence())
        def structure(*args):
            return None
        def commit(*args):
            stopped.set()
            return True
        result = self.run_race(candidate, structure, cancelled=stopped.is_set,
            on_preview=lambda value: True, on_lock=commit, withdraw_preview=withdrawals.append)
        self.assertEqual(result['status'], 'locked')
        self.assertEqual(result['winner'], 'candidate')
        self.assertEqual(withdrawals, [])

    def test_withdrawal_failure_is_not_retried_or_replaced(self):
        stopped, withdrawals = threading.Event(), []
        def candidate(observation, cancelled, lock, preview):
            preview(self.evidence())
        def structure(*args):
            return None
        def show(value):
            stopped.set()
            return True
        def withdraw(reason):
            withdrawals.append(reason)
            raise RuntimeError('withdraw-'+str(len(withdrawals)))
        with self.assertRaisesRegex(RuntimeError, '^withdraw-1$'):
            self.run_race(candidate, structure, cancelled=stopped.is_set,
                on_preview=show, on_lock=lambda *args: True, withdraw_preview=withdraw)
        self.assertEqual(withdrawals, ['recognition-cancelled'])


if __name__ == '__main__':
    unittest.main()
