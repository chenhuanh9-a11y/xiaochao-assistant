"""Same-frame kernel reuse; independent real entry reducers and cancellation.

The entry fixtures supply measured corners and final pixel validators. Actual
source decoding, probe math, competitive bands and work lifecycle execute.
These are component tests, not game accuracy or speed acceptance.
"""
from copy import copy
from threading import Event, Thread
import unittest
from unittest.mock import patch

import numpy as np

from entry_evidence_work import FrameSnapshot, SharedObservationDependencies
from local_structure_index import LocalStructureIndex
from side_entry_evidence import SideEntryEvidence
import difficult_entry_identity
import test_main_entry_jobs as main_fixture
import test_side_entry_jobs as side_fixture


class SharedObservationTests(unittest.TestCase):
    def test_real_geometry_is_shared_without_reusing_a_mutated_capture(self):
        fixture = main_fixture.MainEntryJobTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.source()
        snapshot = FrameSnapshot(fixture.frame)
        shared = SharedObservationDependencies(snapshot, '2')
        first, second = copy(source), copy(source)
        first.observation_dependencies = second.observation_dependencies = shared
        actual = difficult_entry_identity.entry_geometry
        with patch.object(difficult_entry_identity, 'entry_geometry', wraps=actual) as geometry:
            self.assertIsNone(first.geometry(snapshot.pixels))
            self.assertIsNone(second.geometry(snapshot.pixels))
            self.assertEqual(geometry.call_count, 1)
            changed = fixture.frame.copy()
            changed[200, 300] = 255
            second.geometry(FrameSnapshot(changed).pixels)
            self.assertEqual(geometry.call_count, 2)

    def test_cancelled_waiter_cannot_cancel_or_poison_producer(self):
        shared = SharedObservationDependencies(FrameSnapshot(np.zeros((8, 8, 3), np.uint8)), '1')
        entered, release, cancelled = Event(), Event(), Event()
        values, errors, calls = [], [], []
        def compute():
            calls.append(True)
            entered.set()
            if not release.wait(3):
                raise AssertionError('test producer not released')
            return dict(array=np.arange(6))
        def producer():
            values.append(shared.get(('geometry',), compute))
        def waiter():
            try:
                shared.get(('geometry',), compute, cancelled=cancelled.is_set)
            except ValueError as error:
                errors.append(str(error))
        first, second = Thread(target=producer), Thread(target=waiter)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        cancelled.set()
        second.join(2)
        try:
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, ['entry-observation-consumer-cancelled'])
            self.assertTrue(first.is_alive())
        finally:
            release.set()
            first.join(2)
        self.assertFalse(first.is_alive())
        values[0]['array'][0] = 99
        np.testing.assert_array_equal(shared.get(('geometry',), compute)['array'], np.arange(6))
        self.assertEqual(len(calls), 1)

    def test_failed_or_incomplete_source_is_not_a_persistent_completed_value(self):
        shared = SharedObservationDependencies(FrameSnapshot(np.zeros((8, 8, 3), np.uint8)), '1')
        with self.assertRaisesRegex(OSError, 'source changed'):
            shared.get(('source',), lambda: (_ for _ in ()).throw(OSError('source changed')))
        self.assertEqual(shared.get(('source',), lambda: 3), 3)
        self.assertEqual(shared.get(('partial',), lambda: 4, reusable=lambda value: False), 4)
        self.assertEqual(shared.get(('partial',), lambda: 5), 5)

    def test_over_budget_value_is_returned_without_retaining_it(self):
        shared = SharedObservationDependencies(FrameSnapshot(np.zeros((8, 8, 3), np.uint8)), '1', max_bytes=0)
        self.assertEqual(shared.get(('source',), lambda: 1), 1)
        self.assertEqual(shared.get(('source',), lambda: 2), 2)

    def test_main_shared_sources_execute_once_but_keep_distinct_competition(self):
        fixture = main_fixture.MainEntryJobTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.source()
        fixture.observations(source)
        snapshot = FrameSnapshot(fixture.frame)
        shared = SharedObservationDependencies(snapshot, '2')
        first, second = copy(source), copy(source)
        first.observation_dependencies = second.observation_dependencies = shared
        actual = type(source)._probe_entry
        calls = []
        def probe(owner, frame, entry, *args):
            calls.append(entry[0]['mapId'])
            return actual(owner, frame, entry, *args)
        with patch.object(type(source), '_probe_entry', probe):
            _, (subset, sub_meta) = fixture.drain(first.prepare_rank(snapshot, '2', candidate_map_ids={'map-a'}))
            _, (complete, all_meta) = fixture.drain(second.prepare_rank(snapshot, '2'))
        self.assertEqual(calls, ['map-a', 'map-b'])
        self.assertTrue(sub_meta['complete'])
        self.assertTrue(subset[0]['identityVerified'])
        self.assertTrue(all_meta['complete'])
        self.assertEqual({row['mapId'] for row in complete}, {'map-a', 'map-b'})
        self.assertFalse(any(row['identityVerified'] for row in complete))
        self.assertNotIn('verification', shared.get(
            ('main-probe', tuple(id(part) for part in source.entries[0])), lambda: self.fail('lost real probe')))

    def test_side_shared_probes_keep_each_domain_and_directory_gap(self):
        fixture = side_fixture.SideEntryJobTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = fixture.source()
        fixture.observe()
        snapshot = FrameSnapshot(fixture.frame)
        shared = SharedObservationDependencies(snapshot, '2')
        index = LocalStructureIndex(source.entries, max_lower_bytes=0,
            competition_domain=(('map-a', '2'), ('map-b', '2'), ('missing-rival', '2')))
        first, second = copy(source), copy(source)
        first.observation_dependencies = second.observation_dependencies = shared
        actual, calls = SideEntryEvidence._probe_uncached, []
        def probe(owner, entry):
            calls.append(entry[0]['mapId'])
            return actual(owner, entry)
        with patch.object(SideEntryEvidence, '_probe_uncached', probe):
            _, (subset, sub_meta) = fixture.drain(first.prepare_rank(snapshot, '2',
                candidate_map_ids={'map-a'}, prepared_index=index))
            _, (complete, all_meta) = fixture.drain(second.prepare_rank(snapshot, '2', prepared_index=index))
        self.assertEqual(calls, ['map-a', 'map-b'])
        self.assertTrue(sub_meta['complete'])
        self.assertFalse(all_meta['complete'])
        self.assertEqual(all_meta['reason'], 'side-resource-directory-incomplete')
        self.assertEqual({row['mapId'] for row in complete}, {'map-a', 'map-b'})
        self.assertTrue(subset[0]['competitionComplete'])
        self.assertFalse(any(row['competitionComplete'] for row in complete))


if __name__ == '__main__':
    unittest.main()
