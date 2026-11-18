"""Independent source evidence after a canonical lane terminal; no identity grant."""
import copy
import unittest

import numpy as np

from s1_structure_selection import StructureCandidateScanner, StructureCatalog, StructureEvidenceSession
from test_s1_structure_selection import pose


class IndependentEntryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = StructureCatalog('s0', 'difficult', 'actual-test-base', ('a', 'b', 'c'), ('1', '2'))
        self.session = StructureEvidenceSession(self.catalog, 'round')
        self.ticket = self.session.begin_observation('1')

    def canonical(self, **options):
        return StructureCandidateScanner(self.catalog, lambda *args, **kwargs: pose()).scan(
            np.ones((8, 8, 3), np.uint8), floor='1', floor_source='operator-declared', **options)

    def entry(self, canonical):
        # Raw source evidence is stored separately, never adjudicated as identity here.
        return dict(role='identity-entry', resourceKey=self.catalog.resource_key, floor='1',
                    frameKey=canonical['frameKey'], rows=[dict(mapId='a', identityHardGatePassed=True)])

    def test_completed_candidate_does_not_close_the_current_entry_channel(self):
        canonical = self.canonical()
        self.assertTrue(self.session.commit(canonical, self.ticket))
        selected = self.session.choose_candidate('a', self.ticket)
        self.assertTrue(self.session.commit_entry_evidence(self.entry(canonical), self.ticket))
        after = self.session.choose_candidate('a', self.ticket)
        self.assertEqual({k: v for k, v in after.items() if k != 'pose'},
                         {k: v for k, v in selected.items() if k != 'pose'})
        np.testing.assert_array_equal(after['pose']['matrixSourceToClient'],
                                      selected['pose']['matrixSourceToClient'])
        self.assertEqual({k: v for k, v in after['pose'].items() if k != 'matrixSourceToClient'},
                         {k: v for k, v in selected['pose'].items() if k != 'matrixSourceToClient'})
        self.assertTrue(self.session.snapshot()['currentScanComplete'])
        self.assertFalse(self.session.snapshot()['uniqueIdentityVerified'])
        self.assertFalse(self.session.commit_entry_evidence(self.entry(canonical), self.ticket))
        self.assertFalse(self.session.commit(canonical, self.ticket))

    def test_unresolved_or_locally_cancelled_candidate_does_not_cancel_entry(self):
        for options in (dict(budget_seconds=0), dict(cancelled=lambda: True)):
            with self.subTest(options=options):
                self.setUp()
                canonical = self.canonical(**options)
                self.assertIn(canonical['status'], ('budget-unresolved', 'cancelled'))
                self.assertTrue(self.session.commit(canonical, self.ticket))
                self.assertTrue(self.session.commit_entry_evidence(self.entry(canonical), self.ticket))
                self.assertFalse(self.session.choose_candidate('a', self.ticket)['success'])

    def test_two_source_channels_are_independent_but_cannot_cross_frames_or_repeat(self):
        canonical = self.canonical()
        first = self.entry(canonical)
        self.assertTrue(self.session.commit_entry_evidence(first, self.ticket))
        foreign = dict(first, frameKey='0'*64)
        self.assertFalse(self.session.commit_entry_evidence(foreign, self.ticket, channel='structure'))
        self.assertTrue(self.session.commit_entry_evidence(first, self.ticket, channel='structure'))
        self.assertFalse(self.session.commit_entry_evidence(first, self.ticket, channel='structure'))
        self.assertFalse(self.session.commit_entry_evidence(first, self.ticket, channel='unregistered'))

    def test_actual_observation_retirement_still_rejects_late_entry(self):
        for retire in ('revoke', 'new-floor', 'end'):
            with self.subTest(retire=retire):
                self.setUp()
                canonical = self.canonical()
                self.assertTrue(self.session.commit(canonical, self.ticket))
                if retire == 'revoke':
                    self.session.revoke_current()
                elif retire == 'new-floor':
                    self.session.begin_observation('2')
                else:
                    self.session.end_round()
                self.assertFalse(self.session.commit_entry_evidence(self.entry(canonical), self.ticket))

    def test_foreign_frame_resource_floor_and_member_cannot_use_terminal_ticket(self):
        canonical = self.canonical()
        self.assertTrue(self.session.commit(canonical, self.ticket))
        for key, value in (('frameKey', 'f' * 64), ('resourceKey', 'other-base'),
                           ('floor', '2'), ('rows', [dict(mapId='foreign')])):
            with self.subTest(field=key):
                foreign = copy.deepcopy(self.entry(canonical))
                foreign[key] = value
                self.assertFalse(self.session.commit_entry_evidence(foreign, self.ticket))


if __name__ == '__main__':
    unittest.main()
