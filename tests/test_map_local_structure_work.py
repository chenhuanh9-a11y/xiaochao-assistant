"""Current local-structure work still refuses S1 live identity.

The old main:probe competition, prepare_rank plan and EntryObservationTests
fixture targeted a job API that is no longer on MapEntryGeometryIdentity.
"""
import unittest
from types import SimpleNamespace

import numpy as np

from entry_evidence_work import ObservationWorkScheduler
import map_observation_work


class LocalStructureWorkTests(unittest.TestCase):
    def test_s1_prefilter_refuses_live_identity_before_entry_jobs(self):
        engine = SimpleNamespace(qt_season='s1', qt_rank_ready=True,
            side_entry_identity=None, side_entry_error='unused',
            identity_floors=('-1', '-2', '-3', '-4'),
            collect_observation_rows=lambda work: [])
        frame = np.zeros((8, 8, 3), np.uint8)
        work = map_observation_work.LocalStructureObservationWork(
            engine, frame, '-1', deadline=float('inf'))
        with self.assertRaisesRegex(ValueError, 'proposal-only-entry-evidence'):
            ObservationWorkScheduler().drive(work)
        self.assertFalse(work.entry_complete)
        self.assertEqual(work.finish(), [])
